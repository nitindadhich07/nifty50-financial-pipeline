import logging
import json
from pathlib import Path
from typing import Dict, Any, List, Optional, Literal

from pipeline_v3.utils.synonyms import FIELD_SYNONYMS
from pipeline_v3.data_sources.nse_api_client import NSEAPIClient
from pipeline_v3.data_sources.pdf_parser_wrapper import PDFParser
from pipeline_v3.parsers.pdf_text_parser import PDFTextParser
from pipeline_v3.parsers.pdf_table_parser import PDFTableParser
from pipeline_v3.transformers.schema_normalizer import SchemaNormalizer
from pipeline_v3.transformers.financial_mapper import CompanyFinancials, ProfitLoss, BalanceSheet, CashFlow
from pipeline_v3.analytics.ratio_engine import RatioEngine
from pipeline_v3.validation.financial_validator import FinancialValidator

logger = logging.getLogger("FinancialPipelineV3")
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

class FinancialExtractorPipeline:
    def __init__(self, output_dir: str = "data/reliance/final"):
        self.output_dir = Path(output_dir)
        self.nse_client = NSEAPIClient()
        self.pdf_parser = PDFParser()
        self.pdf_text_parser = PDFTextParser(FIELD_SYNONYMS)
        self.pdf_table_parser = PDFTableParser(FIELD_SYNONYMS)
        self.normalizer = SchemaNormalizer()
        self.ratio_engine = RatioEngine()
        self.validator = FinancialValidator()

    def process_company(self, symbol: str, pdf_file: Optional[str] = None):
        logger.info(f"🚀 INSTITUTIONAL EXTRACTION ENGINE: {symbol} 🚀")
        
        final_financials = CompanyFinancials(
            company_info={"ticker": symbol, "company_name": "Reliance Industries Limited", "unit": "₹ Crores"}
        )

        # ── Tier 1: NSE API (Consolidated + Standalone) ─────────────────────────
        # Market Data (Price, Market Cap, Shares)
        quote = self.nse_client.fetch_equity_quote(symbol)
        if quote:
            price_info = quote.get("priceInfo", {})
            security_info = quote.get("securityInfo", {})
            
            last_price = price_info.get("lastPrice")
            issued_shares = security_info.get("issuedSize")
            
            final_financials.company_info["price"] = last_price
            final_financials.company_info["shares_outstanding"] = issued_shares
            
            if last_price and issued_shares:
                # Market Cap in Crores
                mcap_crores = round((float(last_price) * float(issued_shares)) / 10_000_000, 2)
                final_financials.company_info["market_cap"] = mcap_crores
                
            logger.info(f"✅ Market data fetched: ₹{last_price} | Mkt Cap: ₹{final_financials.company_info.get('market_cap')} Cr")

        # Quarterly Consolidated
        nse_con_q = self.nse_client.fetch_results(symbol, period="Quarterly", consolidated=True)
        if nse_con_q:
            nse_pnl = self.normalizer.normalize_nse_pnl(nse_con_q, requested_period="quarterly")
            for year, pl in nse_pnl.items():
                self.normalizer.merge_financials(
                    final_financials, {"pl": pl}, year, period_type="quarterly", is_standalone=False
                )

        # Quarterly Standalone
        nse_std_q = self.nse_client.fetch_results(symbol, period="Quarterly", consolidated=False)
        if nse_std_q:
            nse_pnl = self.normalizer.normalize_nse_pnl(nse_std_q, requested_period="quarterly")
            for year, pl in nse_pnl.items():
                self.normalizer.merge_financials(
                    final_financials, {"pl": pl}, year, period_type="quarterly", is_standalone=True
                )

        # Annual Consolidated (NSE API fallback/supplement)
        nse_con_a = self.nse_client.fetch_results(symbol, period="Annual", consolidated=True)
        if nse_con_a:
            nse_pnl = self.normalizer.normalize_nse_pnl(nse_con_a, requested_period="annual")
            for year, pl in nse_pnl.items():
                self.normalizer.merge_financials(
                    final_financials, {"pl": pl}, year, period_type="annual", is_standalone=False, source_name="NSE_API"
                )

        # Annual Standalone (NSE API)
        nse_std_a = self.nse_client.fetch_results(symbol, period="Annual", consolidated=False)
        if nse_std_a:
            nse_pnl = self.normalizer.normalize_nse_pnl(nse_std_a, requested_period="annual")
            for year, pl in nse_pnl.items():
                self.normalizer.merge_financials(
                    final_financials, {"pl": pl}, year, period_type="annual", is_standalone=True, source_name="NSE_API"
                )
            logger.info("✅ Tier 1 (NSE Annual Standalone) integrated")

        # ── Tier 2: Consolidated PDF → annual ─────────────────────────────────
        if pdf_file and Path(pdf_file).exists():
            self._extract_from_pdf(pdf_file, final_financials)
        else:
            logger.warning("⚠️  No PDF found – skipping PDF extraction")

        # ── Tier 3: Ratios (annual) ────────────────────────────────────────────
        annual_years = sorted(
            set(final_financials.profit_loss["annual"]) |
            set(final_financials.balance_sheet["annual"]),
            reverse=True
        )
        for year in annual_years:
            pl = final_financials.profit_loss["annual"].get(year)
            bs = final_financials.balance_sheet["annual"].get(year)
            cf = final_financials.cash_flow["annual"].get(year)
            if pl or bs or cf:
                final_financials.ratios["annual"][year] = self.ratio_engine.compute_all(pl, bs, cf)

        # ── Tier 4: Validation ─────────────────────────────────────────────────
        anomalies = self.validator.validate(final_financials.__dict__)
        final_financials.metadata["anomalies"] = anomalies

        # ── Save ───────────────────────────────────────────────────────────────
        self.output_dir.mkdir(parents=True, exist_ok=True)
        output_file = self.output_dir / "company_financials.json"
        
        # Prepare data for export by removing metadata if present
        export_data = {k: v for k, v in final_financials.__dict__.items() if k != "metadata"}
        
        with open(output_file, "w") as f:
            json.dump(
                export_data, f, indent=4,
                default=lambda o: getattr(o, "__dict__", str(o))
            )
        logger.info(f"✨ EXTRACTION COMPLETE → {output_file}")
        return final_financials

    def _extract_from_pdf(self, pdf_path: str, target: CompanyFinancials):
        logger.info("  Scanning Consolidated section (pages 95-115)…")

        collected: Dict[str, Dict[str, List[float]]] = {"pl": {}, "bs": {}, "cf": {}}
        year_map: Dict[str, List[str]] = {}

        for page_num in range(95, 116):
            tables = self.pdf_parser.extract_all_tables_from_page(pdf_path, page_num)
            if not tables:
                continue

            for table in tables:
                stmt_type = self.pdf_table_parser.classify_table(table)
                if stmt_type is None:
                    continue

                logger.info(f"    Page {page_num}: {stmt_type.upper()} table detected")

                if stmt_type not in year_map or len(year_map[stmt_type]) < 2:
                    detected = self.pdf_table_parser.detect_years(table)
                    if len(detected) >= 2 or stmt_type not in year_map:
                        year_map[stmt_type] = detected

                raw = self.pdf_table_parser.parse_table(table)
                for field, nums in raw.items():
                    if field not in collected[stmt_type]:
                        collected[stmt_type][field] = nums

        for stmt_type, field_data in collected.items():
            if not field_data:
                logger.warning(f"    No data collected for {stmt_type.upper()} via tables – trying text fallback")
                field_data = self._text_fallback(pdf_path, stmt_type)
                if not field_data:
                    continue

            years = year_map.get(stmt_type, ["2025", "2024"])
            fy_labels = [f"FY{y}" if not y.startswith("FY") else y for y in years]

            for i, year_label in enumerate(fy_labels):
                payload = {k: v[i] for k, v in field_data.items() if len(v) > i}
                if not payload:
                    continue

                pl_obj = self._build_pl(payload, stmt_type)
                bs_obj = self._build_bs(payload, stmt_type)
                cf_obj = self._build_cf(payload, stmt_type)

                self.normalizer.merge_financials(
                    target, {"pl": pl_obj, "bs": bs_obj, "cf": cf_obj},
                    year_label, period_type="annual",
                    source_name="PDF", source_priority=100
                )
                logger.info(f"    ✅ {stmt_type.upper()} merged for {year_label}")

    def _text_fallback(self, pdf_path: str, stmt_type: str) -> Dict[str, List[float]]:
        keyword_map = {
            "pl":  ["CONSOLIDATED STATEMENT OF PROFIT", "REVENUE FROM OPERATIONS"],
            "bs":  ["CONSOLIDATED BALANCE SHEET", "NON-CURRENT ASSETS"],
            "cf":  ["CONSOLIDATED STATEMENT OF CASH FLOW", "OPERATING ACTIVITIES"],
        }
        pages = self.pdf_parser.find_pages_by_keywords(
            pdf_path, keyword_map[stmt_type], must_contain_all=True
        )
        if not pages:
            pages = self.pdf_parser.find_pages_by_keywords(
                pdf_path, [keyword_map[stmt_type][0]], must_contain_all=False
            )

        target_pages = [p for p in pages if 90 <= p <= 115]
        if not target_pages:
            return {}

        # Extract text from up to 3 contiguous pages for the statement
        text = ""
        for p in target_pages[:3]:
            text += self.pdf_parser.extract_text(pdf_path, pages=[p]) + "\n"
            
        raw = self.pdf_text_parser.parse_text(text)

        result: Dict[str, List[float]] = {}
        for field, val in raw.get("current", {}).items():
            result[field] = [val]
        for field, val in raw.get("prev", {}).items():
            if field in result:
                result[field].append(val)
            else:
                result[field] = [0.0, val]
        return result

    def _build_pl(self, payload: Dict[str, float], stmt_type: str) -> ProfitLoss:
        if stmt_type != "pl": return ProfitLoss()
        pl_fields = set(ProfitLoss.__dataclass_fields__)
        data = {k: v for k, v in payload.items() if k in pl_fields}
        pl = ProfitLoss(**data)
        self.normalizer._apply_pnl_math(pl)
        return pl

    def _build_bs(self, payload: Dict[str, float], stmt_type: str) -> BalanceSheet:
        if stmt_type != "bs": return BalanceSheet()
        bs_fields = set(BalanceSheet.__dataclass_fields__)
        data = {k: v for k, v in payload.items() if k in bs_fields}
        if data.get("inventory") is not None and data["inventory"] < 0:
            data["inventory"] = None
        bs = BalanceSheet(**data)
        self.normalizer._apply_bs_math(bs)
        return bs

    def _build_cf(self, payload: Dict[str, float], stmt_type: str) -> CashFlow:
        if stmt_type != "cf": return CashFlow()
        cf_fields = set(CashFlow.__dataclass_fields__)
        data = {k: v for k, v in payload.items() if k in cf_fields}
        cf = CashFlow(**data)
        self.normalizer._apply_cf_math(cf)
        return cf

if __name__ == "__main__":
    extractor = FinancialExtractorPipeline()
    pdf_path = "data/reliance/annual/FY2025_Annual_Report.pdf"
    extractor.process_company("RELIANCE", pdf_file=pdf_path)
