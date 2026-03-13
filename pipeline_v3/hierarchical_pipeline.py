import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from .analytics.growth_engine import GrowthEngine
from .analytics.ratio_engine import RatioEngine
from .core.storage import write_json
from .data_sources.bse_api_client import BSEAPIClient
from .data_sources.ir_scraper import IRScraper
from .data_sources.mca_xbrl_client import MCAXBRLClient
from .data_sources.nse_api_client import NSEAPIClient
from .data_sources.pdf_parser_wrapper import PDFParser
from .parsers.table_parser import HTMLTableParser
from .parsers.pdf_table_parser import PDFTableParser
from .parsers.pdf_text_parser import PDFTextParser
from .parsers.xbrl_parser import MCAXBRLInstanceParser
from .transformers.financial_mapper import CompanyFinancials
from .transformers.schema_normalizer import SchemaNormalizer
from .utils.logger import setup_logger
from .utils.synonyms import FIELD_SYNONYMS
from .utils.universe import Company, load_universe
from .validation.financial_validator import FinancialValidator

logger = setup_logger("FinancialPipelineV3.Hierarchical")


class HierarchicalFinancialPipeline:
    """
    Production-grade, multi-source hierarchical extraction:
    MCA XBRL > Exchange APIs > IR tables > PDF fallback.
    """

    def __init__(self, *, mca_base_dir: str = "storage/raw/mca_xbrl"):
        self.mca_client = MCAXBRLClient(base_dir=mca_base_dir)
        self.mca_parser = MCAXBRLInstanceParser(target_unit="INR_CRORE", prefer_consolidated=True)
        self.nse_client = NSEAPIClient()
        self.bse_client = BSEAPIClient()
        self.ir_scraper = IRScraper()
        self.pdf_parser = PDFParser()
        self.pdf_table_parser = PDFTableParser(FIELD_SYNONYMS)
        self.pdf_text_parser = PDFTextParser(FIELD_SYNONYMS)
        self.html_table_parser = HTMLTableParser(FIELD_SYNONYMS)
        self.normalizer = SchemaNormalizer()
        self.ratio_engine = RatioEngine()
        self.growth_engine = GrowthEngine()
        self.validator = FinancialValidator()

    def process_company(self, company: Company, *, pdf_files: Optional[List[str]] = None) -> CompanyFinancials:
        symbol = company.symbol.upper()
        logger.info(f"Hierarchical extraction: {symbol}")

        fin = CompanyFinancials()
        fin.company_info = {
            "ticker": symbol,
            "company_name": company.name or symbol,
            "isin": company.isin,
            "cin": company.cin,
            "unit": "INR Crores",
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        }

        # Tier 1: MCA XBRL (local artifacts)
        if company.cin:
            artifacts = self.mca_client.list_artifacts(cin=company.cin)
            for art in artifacts:
                xml_bytes, meta = self.mca_client.load_xbrl_xml_bytes(art.path)
                if not xml_bytes:
                    continue
                parsed = self.mca_parser.parse_bytes(xml_bytes)
                self._merge_mca_parsed(fin, parsed, source_meta=meta)
                write_json(
                    f"storage/parsed/{symbol}/mca_xbrl/{Path(meta.get('path','mca')).name}.json",
                    parsed,
                )
            if artifacts:
                logger.info(f"Tier 1 (MCA XBRL): merged {len(artifacts)} artifact(s)")
        else:
            logger.info("Tier 1 (MCA XBRL): skipped (missing CIN)")

        # Tier 2: Exchange APIs
        nse_q = self.nse_client.fetch_results(symbol, period="Quarterly")
        if nse_q:
            pnl = self.normalizer.normalize_nse_pnl(nse_q)
            for qlabel, pl in pnl.items():
                self.normalizer.merge_financials(fin, {"pl": pl}, qlabel, period_type="quarterly", source_name="NSE_API")
            logger.info(f"Tier 2 (NSE API): merged {len(pnl)} quarter(s)")

        nse_a = self.nse_client.fetch_results(symbol, period="Annual")
        if nse_a:
            apnl = self.normalizer.normalize_nse_pnl(nse_a)
            for alabel, pl in apnl.items():
                self.normalizer.merge_financials(fin, {"pl": pl}, alabel, period_type="annual", source_name="NSE_API")
            logger.info(f"Tier 2 (NSE API): merged {len(apnl)} annual item(s)")

        # Tier 3: IR tables
        if company.ir_urls:
            for url in company.ir_urls:
                tables = self.ir_scraper.scrape_tables(url)
                parsed = self.html_table_parser.parse_tables(tables)
                write_json(f"storage/parsed/{symbol}/ir_tables/{self._safe_slug(url)}.json", {"url": url, "parsed": parsed})
                for fy, stmts in parsed.items():
                    norm = self.normalizer.normalize_statement_dict(stmts)
                    self.normalizer.merge_financials(fin, norm, fy, period_type="annual", source_name="IR_TABLE", source_meta={"url": url})

        # Tier 4: PDF fallback
        if pdf_files:
            for pdf_path in pdf_files:
                if not Path(pdf_path).exists():
                    continue
                self._extract_from_pdf(pdf_path, fin)

        # Analytics
        annual_years = sorted(
            list(set(fin.profit_loss["annual"].keys()) | set(fin.balance_sheet["annual"].keys()) | set(fin.cash_flow["annual"].keys())),
            reverse=True,
        )
        for fy in annual_years:
            pl = fin.profit_loss["annual"].get(fy)
            bs = fin.balance_sheet["annual"].get(fy)
            cf = fin.cash_flow["annual"].get(fy)
            fin.ratios["annual"][fy] = self.ratio_engine.compute_all(pl, bs, cf)

        fin.growth["annual"]["revenue_yoy_pct"] = self.growth_engine.compute_yoy(fin.profit_loss["annual"], "revenue_from_operations")
        fin.growth["annual"]["net_profit_yoy_pct"] = self.growth_engine.compute_yoy(fin.profit_loss["annual"], "net_profit")

        # Validation
        anomalies = self.validator.validate(asdict(fin))
        if anomalies:
            fin.metadata.setdefault("anomalies", []).extend(anomalies)
            fin.insights.append(f"Validation flagged {len(anomalies)} anomaly(ies) for review.")

        # Output
        out_path = f"data/{symbol.lower()}/final/company_financials.json"
        write_json(out_path, fin)
        logger.info(f"Saved: {out_path}")
        return fin

    def _merge_mca_parsed(self, fin: CompanyFinancials, parsed: Dict[str, Any], *, source_meta: Dict[str, Any]) -> None:
        stmts = (parsed or {}).get("statements") or {}
        for stmt_type in ("pl", "bs", "cf"):
            by_fy = stmts.get(stmt_type) or {}
            for fy, payload in by_fy.items():
                if not isinstance(payload, dict):
                    continue
                payload_clean = {k: v for k, v in payload.items() if not str(k).startswith("_")}
                norm = self.normalizer.normalize_statement_dict({stmt_type: payload_clean})
                self.normalizer.merge_financials(
                    fin,
                    norm,
                    fy,
                    period_type="annual",
                    source_name="MCA_XBRL",
                    source_meta={"mca": source_meta, "parser": (parsed.get("provenance") or {})},
                )

    def _extract_from_pdf(self, pdf_path: str, fin: CompanyFinancials) -> None:
        key_pages = set()
        key_pages.update(self.pdf_parser.find_pages_by_keywords(pdf_path, ["consolidated", "statement"], must_contain_all=False))
        key_pages.update(self.pdf_parser.find_pages_by_keywords(pdf_path, ["balance sheet"], must_contain_all=False))
        key_pages.update(self.pdf_parser.find_pages_by_keywords(pdf_path, ["cash flow"], must_contain_all=False))

        for page_no in sorted(key_pages)[:80]:
            tables = self.pdf_parser.extract_all_tables_from_page(pdf_path, page_no)
            for table in tables or []:
                stmt = self.pdf_table_parser.classify_table(table)
                if not stmt:
                    continue
                extracted = self.pdf_table_parser.parse_table(table)
                years = self.pdf_table_parser.detect_years(table)
                fys = [f"FY{y}" if "FY" not in y else y for y in years]
                for i, fy in enumerate(fys):
                    row_payload = {k: v[i] for k, v in extracted.items() if isinstance(v, list) and len(v) > i}
                    if not row_payload:
                        continue
                    norm_layer = self.normalizer.normalize_pdf_data({"current": row_payload, "prev": {}})["current"]
                    self.normalizer.merge_financials(
                        fin,
                        {stmt: norm_layer[stmt]},
                        fy,
                        period_type="annual",
                        source_name="PDF",
                        source_meta={"pdf": pdf_path, "page": page_no},
                    )

        # Text fallback: only if annual PL still missing after table scan.
        if not fin.profit_loss["annual"]:
            pages = self.pdf_parser.find_pages_by_keywords(pdf_path, ["profit before tax"], must_contain_all=False)
            text = self.pdf_parser.extract_text(pdf_path, pages=pages[:20] if pages else None)
            parsed = self.pdf_text_parser.parse_text(text)
            norm = self.normalizer.normalize_pdf_data(parsed)
            self.normalizer.merge_financials(fin, norm["current"], "Unknown", period_type="annual", source_name="PDF", source_meta={"pdf": pdf_path, "pages": pages[:20]})

    def _safe_slug(self, s: str) -> str:
        return "".join(ch if ch.isalnum() else "_" for ch in s)[:120]


def _company_by_symbol(universe: List[Company], symbol: str) -> Optional[Company]:
    s = symbol.upper()
    for c in universe:
        if c.symbol.upper() == s:
            return c
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", help="NSE symbol (e.g., RELIANCE)")
    ap.add_argument("--all", action="store_true", help="Process all companies in universe")
    ap.add_argument("--universe", default="pipeline_v3/config/nifty50_universe.json", help="Universe JSON")
    ap.add_argument("--mca-base-dir", default="storage/raw/mca_xbrl", help="Local MCA XBRL store")
    ap.add_argument("--pdf", action="append", default=[], help="Fallback PDF path (repeatable)")
    args = ap.parse_args()

    universe = load_universe(args.universe)
    if not universe:
        logger.error("Universe empty. Provide a valid universe file.")
        return 2

    pipe = HierarchicalFinancialPipeline(mca_base_dir=args.mca_base_dir)

    if args.all:
        for c in universe:
            pipe.process_company(c, pdf_files=args.pdf or None)
        return 0

    if not args.symbol:
        logger.error("Provide --symbol or --all")
        return 2

    c = _company_by_symbol(universe, args.symbol)
    if not c:
        logger.error(f"Symbol not found in universe: {args.symbol}")
        return 2

    pipe.process_company(c, pdf_files=args.pdf or None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

