import argparse
import concurrent.futures
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
            "exchange": "NSE/BSE",
            "sector": company.sector or "",
            "industry": company.industry or "",
            "currency": "INR",
            "unit": "\u20b9 Crores",
        }

        # Market Data (Institutional Source: NSE)
        quote = self.nse_client.fetch_equity_quote(symbol)
        if quote:
            price_info = quote.get("priceInfo", {})
            security_info = quote.get("securityInfo", {})
            last_price = price_info.get("lastPrice")
            issued_shares = security_info.get("issuedSize")
            fin.company_info["price"] = last_price
            fin.company_info["shares_outstanding"] = issued_shares
            if last_price and issued_shares:
                fin.company_info["market_cap"] = round((float(last_price) * float(issued_shares)) / 10_000_000, 2)
            logger.info(f"✅ Market data fetched: ₹{last_price}")

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

        # Tier 2: Exchange APIs (NSE fallback for structure)
        nse_q = self.nse_client.fetch_results(symbol, period="Quarterly")
        if nse_q:
            pnl = self.normalizer.normalize_nse_pnl(nse_q, requested_period="quarterly")
            for qlabel, pl in pnl.items():
                self.normalizer.merge_financials(fin, {"pl": pl}, qlabel, period_type="quarterly", source_name="NSE_API")
            logger.info(f"Tier 1.5 (NSE API): merged {len(pnl)} quarter(s)")

        nse_a = self.nse_client.fetch_results(symbol, period="Annual")
        if nse_a:
            apnl = self.normalizer.normalize_nse_pnl(nse_a, requested_period="annual")
            for alabel, pl in apnl.items():
                self.normalizer.merge_financials(fin, {"pl": pl}, alabel, period_type="annual", source_name="NSE_API")
            if apnl:
                logger.info(f"Tier 1.5 (NSE API): merged {len(apnl)} annual item(s)")

        # Tier 1.6: NSE Standalone (Consolidated = False)
        nse_std_q = self.nse_client.fetch_results(symbol, period="Quarterly", consolidated=False)
        if nse_std_q:
            spnl = self.normalizer.normalize_nse_pnl(nse_std_q, requested_period="quarterly")
            for qlabel, pl in spnl.items():
                self.normalizer.merge_financials(fin, {"pl": pl}, qlabel, period_type="quarterly", source_name="NSE_API", is_standalone=True)
            logger.info(f"Tier 1.6 (NSE Standalone): merged {len(spnl)} quarter(s)")
            fin.company_info["has_standalone"] = True

        nse_std_a = self.nse_client.fetch_results(symbol, period="Annual", consolidated=False)
        if nse_std_a:
            sapnl = self.normalizer.normalize_nse_pnl(nse_std_a, requested_period="annual")
            for alabel, pl in sapnl.items():
                self.normalizer.merge_financials(fin, {"pl": pl}, alabel, period_type="annual", source_name="NSE_API", is_standalone=True)
            fin.company_info["has_standalone"] = True

        # Tier 3: IR tables
        if company.ir_urls:
            for url in company.ir_urls:
                tables = self.ir_scraper.scrape_tables(url)
                parsed = self.html_table_parser.parse_tables(tables)
                write_json(f"storage/parsed/{symbol}/ir_tables/{self._safe_slug(url)}.json", {"url": url, "parsed": parsed})
                for fy, stmts in parsed.items():
                    norm = self.normalizer.normalize_statement_dict(stmts)
                    self.normalizer.merge_financials(fin, norm, fy, period_type="annual", source_name="IR_TABLE", source_meta={"url": url})
        
        # Tier 3: PDF fallback (Always run to fill gaps in XBRL/APIs)
        if pdf_files:
            logger.info(f"Tier 3 (PDF): Attempting to fill missing fields from {len(pdf_files)} PDF(s)...")
            for pdf_path in pdf_files:
                if not Path(pdf_path).exists():
                    continue
                self._extract_from_pdf(pdf_path, fin)

        # Tier 4: Yahoo Finance API (Final fallback for remaining missing institutional data)
        try:
            import yfinance as yf
            ticker = yf.Ticker(f"{symbol}.NS")
            # Fetch statements
            yf_data = {
                "annual_income": ticker.financials,
                "quarterly_income": ticker.quarterly_financials,
                "annual_balance": ticker.balance_sheet,
                "annual_cashflow": ticker.cashflow,
                "info": ticker.info
            }
            if yf_data.get("annual_income") is not None and not yf_data["annual_income"].empty:
                logger.info(f"Tier 4 (YFinance): Final fallback for remaining gaps...")
                self._merge_yfinance_data(fin, yf_data)
        except Exception as e:
            logger.warning(f"Tier 4 (YFinance) failed: {e}")

        # Analytics
        annual_years = sorted(
            list(set(fin.profit_loss["annual"].keys()) | set(fin.balance_sheet["annual"].keys()) | set(fin.cash_flow["annual"].keys())),
            reverse=True,
        )
        for fy in annual_years:
            pl = fin.profit_loss["annual"].get(fy)
            bs = fin.balance_sheet["annual"].get(fy)
            cf = fin.cash_flow["annual"].get(fy)
            
            # ── Automatic EPS Calculation if missing ─────────────────────────
            if pl and pl.net_profit and pl.eps is None:
                # Use current shares as fallback, or BS shares if available
                shares = (bs.shares_outstanding if bs else None) or fin.company_info.get("shares_outstanding")
                if shares:
                    pl.eps = round((pl.net_profit * 10_000_000.0) / shares, 2)
            
            fin.ratios["annual"][fy] = self.ratio_engine.compute_all(pl, bs, cf)
            
        # Same for standalone if it exists
        st_annual_years = sorted(list(fin.standalone_profit_loss["annual"].keys()), reverse=True)
        for fy in st_annual_years:
            pl = fin.standalone_profit_loss["annual"].get(fy)
            if pl and pl.net_profit and pl.eps is None:
                shares = fin.company_info.get("shares_outstanding")
                if shares:
                    pl.eps = round((pl.net_profit * 10_000_000.0) / shares, 2)

        fin.growth["annual"]["revenue_yoy_pct"] = self.growth_engine.compute_yoy(fin.profit_loss["annual"], "revenue_from_operations")
        fin.growth["annual"]["net_profit_yoy_pct"] = self.growth_engine.compute_yoy(fin.profit_loss["annual"], "net_profit")

        # Validation
        anomalies = self.validator.validate(asdict(fin))
        if anomalies:
            fin.metadata.setdefault("anomalies", []).extend(anomalies)
            fin.insights.append(f"Validation flagged {len(anomalies)} anomaly(ies) for review.")

        # ── Build clean output document ────────────────────────────────────────
        # Collect data_sources from provenance metadata
        data_sources: List[Dict[str, Any]] = []
        prov = fin.metadata.get("provenance", {})
        seen_sources: set = set()
        for period_type, years in prov.items():
            for year, stmts in years.items():
                for stmt, fields in stmts.items():
                    for field_name, field_meta in fields.items():
                        src = field_meta.get("source", "UNKNOWN")
                        if src not in seen_sources:
                            seen_sources.add(src)
                            data_sources.append({"type": src, "label": year})
                        break  # one representative per statement per year is enough
                    break

        clean_metadata = {
            "data_sources": data_sources,
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "parser_version": "v2.0",
            "validation_passed": len(anomalies) == 0,
        }

        # Flatten ratios: {"annual": {"FY2025": {...}}} → {"FY2025": {...}}
        flat_ratios: Dict[str, Any] = {}
        for fy, ratio_dict in fin.ratios.get("annual", {}).items():
            flat_ratios[fy] = ratio_dict

        # Build final export document (target schema)
        export_doc = {
            "company_info": fin.company_info,
            "profit_loss": {
                "quarterly": fin.profit_loss.get("quarterly", {}),
                "yearly": fin.profit_loss.get("annual", {}),
            },
            "standalone_profit_loss": {
                "quarterly": fin.standalone_profit_loss.get("quarterly", {}),
                "yearly": fin.standalone_profit_loss.get("annual", {}),
            },
            "standalone_balance_sheet": {
                "yearly": fin.standalone_balance_sheet.get("annual", {}),
            },
            "standalone_cash_flow": {
                "yearly": fin.standalone_cash_flow.get("annual", {}),
            },
            "balance_sheet": {
                "yearly": fin.balance_sheet.get("annual", {}),
            },
            "cash_flow": {
                "yearly": fin.cash_flow.get("annual", {}),
            },
            "ratios": flat_ratios,
            "metadata": clean_metadata,
        }

        # Output
        from .utils.sector_mapper import get_sector
        sector_slug = get_sector(symbol)
        out_path = f"data/{sector_slug}/{symbol.lower()}/final/company_financials.json"
        write_json(out_path, export_doc)
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

    def _merge_yfinance_data(self, fin: CompanyFinancials, yf_data: Dict[str, Any]) -> None:
        """Processes Yahoo Finance data frames into CompanyFinancials."""
        mappings = [
            ("annual_income", "pl", "annual"),
            ("quarterly_income", "pl", "quarterly"),
            ("annual_balance", "bs", "annual"),
            ("annual_cashflow", "cf", "annual"),
        ]
        
        # Determine divisor: Nifty 50 tickers on Yahoo are in absolute INR
        # but sometimes millions if currency is USD (ADRs). 
        # Standard .NS tickers are in absolute INR.
        # We target ₹ Crores (10^7 INR).
        raw_currency = (yf_data.get("info") or {}).get("currency", "INR")
        global_divisor = 10000000.0 if raw_currency == "INR" else 1.0
        
        for key, bucket_key, ptype in mappings:
            df = yf_data.get(key)
            if df is not None and not df.empty:
                for ts in df.columns:
                    label = self.normalizer._label_from_nse_period(ts.isoformat(), ptype)
                    col_data = df[ts].dropna().to_dict()
                    payload = {bucket_key: self._map_yfinance_fields(bucket_key, col_data)}
                    
                    # Pass the global_divisor to normalize_statement_dict
                    # This ensures P&L math (EBITDA etc) is done on correctly scaled units.
                    norm_layer = self.normalizer.normalize_statement_dict(payload, divisor=global_divisor)
                    
                    # Prove the merge
                    self.normalizer.merge_financials(fin, norm_layer, label, period_type=ptype, source_name="YFINANCE")

    def _map_yfinance_fields(self, stmt_type: str, raw_data: Dict[str, Any]) -> Dict[str, Any]:
        """Maps Yahoo Finance DataFrame index names to canonical dataclass fields."""
        maps = {
            "pl": {
                "revenue_from_operations": ["Total Revenue", "Operating Revenue", "Revenue"],
                "other_income": ["Other Income Expense", "Other Income", "Non Operating Income Net"],
                "interest": ["Interest Expense", "Interest Expense Non Operating", "Finance Costs"],
                "depreciation": ["Depreciation And Amortization", "Depreciation", "Amortization"],
                "profit_before_tax": ["Pretax Income", "Profit Before Tax", "Income Before Tax"],
                "tax": ["Tax Provision", "Income Tax Expense", "Tax Expense"],
                "net_profit": ["Net Income", "Net Income Common Stockholders", "Net Income From Continuing Operations"],
                "eps": ["Basic EPS", "Earnings Per Share Basic", "Basic Earnings Per Share"],
                "diluted_eps": ["Diluted EPS", "Earnings Per Share Diluted"],
            },
            "bs": {
                "total_assets": ["Total Assets"],
                "total_equity": ["Stockholders Equity", "Total Equity Gross Minority Interest"],
                "total_liabilities": ["Total Liabilities Net Minority Interest", "Total Liabilities"],
                "total_debt": ["Total Debt"],
                "long_term_borrowings": ["Long Term Debt"],
                "short_term_borrowings": ["Current Debt"],
                "shares_outstanding": ["Ordinary Shares Number", "Share Issued"],
                "cash_and_equivalents": ["Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"],
                "inventory": ["Inventory", "Inventories"],
                "receivables": ["Receivables", "Accounts Receivable"],
            },
            "cf": {
                "cash_from_operations": ["Operating Cash Flow", "Cash Flow From Continuing Operating Activities"],
                "cash_from_investing": ["Investing Cash Flow", "Cash Flow From Continuing Investing Activities"],
                "cash_from_financing": ["Financing Cash Flow", "Cash Flow From Continuing Financing Activities"],
                "free_cash_flow": ["Free Cash Flow"],
                "capital_expenditure": ["Capital Expenditure"],
            }
        }
        
        sm = maps.get(stmt_type, {})
        mapped = {}
        for field, suspects in sm.items():
            for s in suspects:
                if s in raw_data:
                    mapped[field] = raw_data[s]
                    break
        return mapped

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
        with concurrent.futures.ThreadPoolExecutor(max_workers=15) as executor:
            futures = [executor.submit(pipe.process_company, c, pdf_files=args.pdf or None) for c in universe]
            concurrent.futures.wait(futures)
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

