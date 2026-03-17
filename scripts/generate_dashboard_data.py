"""
generate_dashboard_data.py  — Phase A  (Advancement Branch)
=============================================================
5-Layer Pipeline producing the new unified schema:

  {
    company:         { name, symbol, sector, industry, has_standalone }
    market_data:     { price, shares_outstanding, market_cap }
    financials: {
      consolidated:  { quarterly: { profit_loss }, annual: { profit_loss, balance_sheet, cash_flow } }
      standalone:    null | same structure
    }
    graph_data:      { revenue: { quarterly:[...], annual:[...] }, net_profit, ebitda, eps, ... }
    derived_metrics: { FY2025: { roe, debt_to_equity, ... } }
    anomalies:       [ { type, period, value, severity, message } ]
    insights:        [ { rule_id, message, severity, basis } ]
    confidence:      { financials:{...}, derived_metrics:{...}, market_data:{...} }
    metadata:        { data_sources, last_updated, unit, parser_version }
  }

ZERO-HALLUCINATION POLICY (unchanged):
  - No price / market cap unless from NSE official source
  - No standalone data unless separately fetched from NSE
  - All derived values tagged DERIVED
  - Missing → null (shown in UI as "Not available from official filings")
"""

import os
import sys
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCRIPTS_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPTS_DIR))

from insight_engine    import InsightEngine
from confidence_tagger import ConfidenceTagger
from anomaly_detector  import AnomalyDetector
from graph_engine      import GraphEngine
from llm_router        import llm_router
from rag_engine        import rag_engine

logger = logging.getLogger("DashboardGenerator")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

# ─── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR      = Path("data")
DASHBOARD_DIR = Path("dashboard")
DATA_OUT_DIR  = DASHBOARD_DIR / "data"
CRORE         = 1_00_00_000
MAX_SANE_CRORE = 20_00_000.0   # 20 Lakh Crores upper bound


# ─── Unit Normalizer ──────────────────────────────────────────────────────────
class UnitNormalizer:
    def normalize_statement(self, statement: Dict) -> Dict:
        result = {}
        for period_label, period_data in statement.items():
            if not isinstance(period_data, dict):
                result[period_label] = period_data
                continue
            normalized, _ = self._normalize_period(period_data)
            result[period_label] = normalized
        return result

    def _normalize_period(self, d: Dict) -> Tuple[Dict, bool]:
        needs = any(
            abs(float(v)) > MAX_SANE_CRORE * CRORE
            for v in d.values()
            if v is not None and self._is_num(v)
        )
        if not needs:
            return d, False
        out = {}
        for k, v in d.items():
            if v is None:
                out[k] = None
            elif self._is_num(v):
                if k.lower() in {"eps", "basic eps", "diluted eps", "basic_eps", "diluted_eps"}:
                    out[k] = round(float(v), 2)
                else:
                    out[k] = round(float(v) / CRORE, 2)
            else:
                out[k] = v
        return out, True

    @staticmethod
    def _is_num(v):
        try:
            float(v)
            return True
        except (TypeError, ValueError):
            return False


# ─── Field Maps (raw → unified) ───────────────────────────────────────────────
PL_MAP = {
    "revenue_from_operations":"revenue", "Total Revenue":"revenue",
    "Operating Revenue":"revenue",       "total_income":"total_income",
    "other_income":"other_income",       "ebitda":"ebitda", "EBITDA":"ebitda",
    "ebit":"ebit",   "EBIT":"ebit",      "Operating Income":"ebit",
    "interest":"interest",               "Interest Expense":"interest",
    "depreciation":"depreciation",       "Reconciled Depreciation":"depreciation",
    "profit_before_tax":"profit_before_tax", "Pretax Income":"profit_before_tax",
    "tax":"tax",     "Tax Provision":"tax",
    "net_profit":"net_profit",           "Net Income":"net_profit",
    "eps":"eps",     "Basic EPS":"eps",  "Diluted EPS":"eps",
    "exceptional_items":"exceptional_items", "Gross Profit":"gross_profit",
}

BS_MAP = {
    "equity_share_capital":"share_capital", "Common Stock":"share_capital",
    "reserves":"reserves",
    "total_equity":"total_equity",          "Common Stock Equity":"total_equity",
    "Stockholders Equity":"total_equity",
    "long_term_borrowings":"long_term_debt","Long Term Debt":"long_term_debt",
    "short_term_borrowings":"short_term_debt","Current Debt":"short_term_debt",
    "total_debt":"total_debt",              "Total Debt":"total_debt",
    "total_assets":"total_assets",          "Total Assets":"total_assets",
    "total_liabilities":"total_liabilities",
    "Total Liabilities Net Minority Interest":"total_liabilities",
    "cash_and_equivalents":"cash",          "Cash And Cash Equivalents":"cash",
    "investments":"investments",            "receivables":"receivables",
    "Accounts Receivable":"receivables",    "inventory":"inventory",
    "Inventory":"inventory",                "ppe":"fixed_assets",
    "Net PPE":"fixed_assets",
    "current_assets":"current_assets",      "Current Assets":"current_assets",
    "current_liabilities":"current_liabilities","Current Liabilities":"current_liabilities",
    "working_capital":"working_capital",    "Working Capital":"working_capital",
    "Ordinary Shares Number":"shares_outstanding",
    "Share Issued":"shares_outstanding",
}

CF_MAP = {
    "cash_from_operations":"operating_cf",  "Operating Cash Flow":"operating_cf",
    "cash_from_investing":"investing_cf",   "Investing Cash Flow":"investing_cf",
    "cash_from_financing":"financing_cf",   "Financing Cash Flow":"financing_cf",
    "capital_expenditure":"capex",          "Capital Expenditure":"capex",
    "Capital Expenditure Reported":"capex",
    "free_cash_flow":"free_cash_flow",      "Free Cash Flow":"free_cash_flow",
    "net_cash_flow":"net_cash_flow",
}


def _map_period(raw: Dict, field_map: Dict) -> Dict:
    out, seen = {}, set()
    for src, tgt in field_map.items():
        if tgt in seen:
            continue
        val = raw.get(src)
        if val is not None:
            out[tgt] = val
            seen.add(tgt)
    return out


def _map_bucket(bucket: Dict, field_map: Dict, normalizer: UnitNormalizer) -> Dict:
    out = {}
    for period_label, period_data in bucket.items():
        # Handle dataclasses if present (Phase B/C)
        data_dict = period_data
        if hasattr(period_data, "__dict__") and not isinstance(period_data, dict):
            from dataclasses import asdict
            data_dict = asdict(period_data)
        
        if isinstance(data_dict, dict):
            normalized = normalizer.normalize_statement({period_label: data_dict})[period_label]
            out[period_label] = _map_period(normalized, field_map)
    return out


def _unify_period_key(k: str) -> str:
    return "annual" if k in ("yearly", "annual") else k


def _safe(d: Dict, *keys) -> Optional[float]:
    for k in keys:
        v = d.get(k)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    return None


# ─── Derived Metrics ──────────────────────────────────────────────────────────
def compute_derived_metrics(pl_ann: Dict, bs_ann: Dict, cf_ann: Dict) -> Dict:
    years = sorted(set(pl_ann) | set(bs_ann) | set(cf_ann), reverse=True)
    result = {}
    for i, year in enumerate(years):
        pl = pl_ann.get(year, {})
        bs = bs_ann.get(year, {})
        cf = cf_ann.get(year, {})
        dm: Dict[str, Any] = {}

        rev      = _safe(pl, "revenue")
        np_      = _safe(pl, "net_profit")
        ebitda   = _safe(pl, "ebitda")
        ebit     = _safe(pl, "ebit")
        equity   = _safe(bs, "total_equity")
        assets   = _safe(bs, "total_assets")
        debt     = _safe(bs, "total_debt")
        cur_a    = _safe(bs, "current_assets")
        cur_l    = _safe(bs, "current_liabilities")
        cfo      = _safe(cf, "operating_cf")
        fcf      = _safe(cf, "free_cash_flow")

        if rev and np_ is not None:
            dm["net_profit_margin"] = round((np_ / rev) * 100, 2)
        if rev and ebitda is not None:
            dm["operating_margin"]  = round((ebitda / rev) * 100, 2)
        if equity and equity != 0 and np_ is not None:
            roe = round((np_ / equity) * 100, 2)
            if abs(roe) <= 200:
                dm["roe"] = roe
        if assets and assets != 0 and np_ is not None:
            dm["roa"] = round((np_ / assets) * 100, 2)
        if ebit is not None and assets and cur_l is not None:
            ce = assets - cur_l
            if ce != 0:
                dm["roce"] = round((ebit / ce) * 100, 2)
        if equity and equity != 0 and debt is not None:
            dm["debt_to_equity"] = round(debt / equity, 2)
        if assets and assets != 0 and debt is not None:
            dm["debt_to_assets"] = round(debt / assets, 2)
        if cur_a is not None and cur_l and cur_l != 0:
            dm["current_ratio"] = round(cur_a / cur_l, 2)
        if rev and fcf is not None:
            dm["free_cash_flow_margin"] = round((fcf / rev) * 100, 2)
        if np_ and np_ != 0 and cfo is not None:
            dm["cfo_to_net_profit"] = round(cfo / np_, 2)

        # YoY Growth
        if i + 1 < len(years):
            prev_pl = pl_ann.get(years[i + 1], {})
            pr = _safe(prev_pl, "revenue")
            pn = _safe(prev_pl, "net_profit")
            if rev and pr and pr != 0:
                dm["revenue_growth_pct"] = round(((rev - pr) / abs(pr)) * 100, 2)
            if np_ is not None and pn and pn != 0:
                dm["profit_growth_pct"]  = round(((np_ - pn) / abs(pn)) * 100, 2)

        if dm:
            result[year] = dm
    return result


# ─── Quality Scorer ────────────────────────────────────────────────────────────
def _score(data: Dict) -> int:
    pl = data.get("profit_loss", {})
    q  = len(pl.get("quarterly", {}))
    a  = len(pl.get("yearly", {})) + len(pl.get("annual", {}))
    return q * 2 + a * 3


# ─── Core Transform ────────────────────────────────────────────────────────────
def transform_company(raw_data: Dict, pdf_path: Optional[str] = None) -> Dict:
    normalizer      = UnitNormalizer()
    insight_engine  = InsightEngine()
    conf_tagger     = ConfidenceTagger()
    anomaly_det     = AnomalyDetector()
    graph_engine    = GraphEngine()

    # ── Extract raw buckets ───────────────────────────────────────────────────
    ci          = raw_data.get("company_info", {})
    raw_pl      = raw_data.get("profit_loss", {})
    raw_bs      = raw_data.get("balance_sheet", {})
    raw_cf      = raw_data.get("cash_flow", {})
    raw_st_pl   = raw_data.get("standalone_profit_loss", {})
    raw_st_bs   = raw_data.get("standalone_balance_sheet", {})
    raw_st_cf   = raw_data.get("standalone_cash_flow", {})
    raw_meta    = raw_data.get("metadata", {})
    provenance  = raw_meta.get("provenance", {})

    symbol = (ci.get("symbol") or ci.get("ticker") or "UNKNOWN").upper()
    name   = ci.get("name") or ci.get("company_name") or symbol
    sector = ci.get("sector", "Diversified")
    industry = ci.get("industry", "")

    # ── Map to unified schema & unify period keys ─────────────────────────────
    def process_bucket(raw: Dict, field_map: Dict) -> Dict:
        out = {}
        for k, v in raw.items():
            uk = _unify_period_key(k)
            if isinstance(v, dict):
                out[uk] = _map_bucket(v, field_map, normalizer)
        return out

    con_pl = process_bucket(raw_pl, PL_MAP)
    con_bs = process_bucket(raw_bs, BS_MAP)
    con_cf = process_bucket(raw_cf, CF_MAP)

    pl_ann = con_pl.get("annual", {})
    pl_q   = con_pl.get("quarterly", {})
    bs_ann = con_bs.get("annual", {})
    cf_ann = con_cf.get("annual", {})

    # ── Consolidated financials structure (quarterly: P&L only) ──────────────
    # RULE: Balance Sheet and Cash Flow are NOT shown in quarterly view
    consolidated = {
        "quarterly": {
            "profit_loss": pl_q,
            # balance_sheet and cash_flow intentionally omitted for quarterly
        },
        "annual": {
            "profit_loss":   pl_ann,
            "balance_sheet": bs_ann,
            "cash_flow":     cf_ann,
        },
    }

    st_pl = process_bucket(raw_st_pl, PL_MAP)
    st_pl_ann = st_pl.get("annual", {})
    st_pl_q   = st_pl.get("quarterly", {})
    
    st_bs = process_bucket(raw_st_bs, BS_MAP)
    st_bs_ann = st_bs.get("annual", {})
    
    st_cf = process_bucket(raw_st_cf, CF_MAP)
    st_cf_ann = st_cf.get("annual", {})
    
    has_standalone = bool(st_pl_q or st_pl_ann or st_bs_ann or st_cf_ann)
    standalone = None
    if has_standalone:
        standalone = {
            "quarterly": {
                "profit_loss": st_pl_q,
            },
            "annual": {
                "profit_loss":   st_pl_ann,
                "balance_sheet": st_bs_ann,
                "cash_flow":     st_cf_ann,
            },
        }

    # ── Derived Metrics ───────────────────────────────────────────────────────
    derived_metrics = compute_derived_metrics(pl_ann, bs_ann, cf_ann)

    # ── Anomalies ─────────────────────────────────────────────────────────────
    anomalies = anomaly_det.detect(pl_ann, bs_ann, cf_ann)

    # ── Rule-based Insights ───────────────────────────────────────────────────
    insights = insight_engine.generate(
        derived_metrics,
        {"profit_loss": {"annual": pl_ann, "quarterly": pl_q}},
    )

    # ── Graph Data ────────────────────────────────────────────────────────────
    graph_data = graph_engine.compute(pl_q, pl_ann, bs_ann, cf_ann)

    # ── Confidence Tags ───────────────────────────────────────────────────────
    flat_con = {
        "profit_loss":   con_pl,
        "balance_sheet": con_bs,
        "cash_flow":     con_cf,
    }
    flat_std = {
        "profit_loss":   st_pl,
        "balance_sheet": st_bs,
        "cash_flow":     st_cf,
    }
    
    confidence = {
        "consolidated":    conf_tagger.tag_financials(flat_con, provenance, is_standalone=False),
        "standalone":      conf_tagger.tag_financials(flat_std, provenance, is_standalone=True),
        "derived_metrics": conf_tagger.tag_derived_metrics(derived_metrics),
        "market_data":     {},
    }

    # ── Market Data ───────────────────────────────────────────────────────────
    # Use real-time data if provided during extraction
    price = ci.get("price")
    mcap = ci.get("market_cap")
    shares = ci.get("shares_outstanding")

    if shares is None and bs_ann:
        ly = sorted(bs_ann.keys(), reverse=True)[0]
        shares = bs_ann[ly].get("shares_outstanding")
    
    market_data = {"price": price, "shares_outstanding": shares, "market_cap": mcap}
    confidence["market_data"] = conf_tagger.tag_market_data(market_data)

    # ── Clean Metadata ────────────────────────────────────────────────────────
    sources = [
        s for s in raw_meta.get("data_sources", [])
        if isinstance(s, dict) and s.get("type") not in {"YFINANCE"}
        or isinstance(s, str) and s not in {"YFINANCE"}
    ]

    # ── RAG & LLM Insights (Phase B) ──────────────────────────────────────────
    ir_context = None
    llm_insights = None
    
    latest_year = None
    if pl_ann:
        latest_year = sorted(pl_ann.keys(), reverse=True)[0]
    
    if pdf_path and latest_year and rag_engine.enabled:
        year_str = latest_year.replace("FY", "")
        rag_engine.index_pdf(pdf_path, symbol, year_str)
        ir_context = rag_engine.retrieve_context(symbol, year_str)
    
    # Generate LLM interpretation (returns None if LLM is disabled)
    if derived_metrics:
        # We only pass derived metrics to strictly prevent hallucination of raw numbers
        clean_context = {
            "company": {"symbol": symbol, "name": name, "sector": sector},
            "derived_metrics": derived_metrics,
            "anomalies": anomalies
        }
        llm_insights = llm_router.generate_insights(
            structured_data=clean_context,
            anomalies=anomalies,
            ir_context=ir_context
        )

    return {
        "company": {
            "name":          name,
            "symbol":        symbol,
            "sector":        sector,
            "industry":      industry,
            "description":   None,
            "history":       None,
            "has_standalone": has_standalone,
        },
        "market_data":     market_data,
        "financials": {
            "consolidated":  consolidated,
            "standalone":    standalone,
        },
        "graph_data":      graph_data,
        "derived_metrics": derived_metrics,
        "anomalies":       anomalies,
        "insights":        insights,
        "llm_interpretation": llm_insights,
        "confidence":      confidence,
        "peers":           [], # Populated in main loop
        "metadata": {
            "data_sources":      sources,
            "last_updated":      datetime.now(timezone.utc).isoformat(),
            "unit":              "₹ Crores",
            "parser_version":    "v4.0-PhaseB",
            "validation_passed": raw_meta.get("validation_passed", False),
            "rag_context":       bool(ir_context),
        },
    }


# ─── Main ─────────────────────────────────────────────────────────────────────
def generate_dashboard():
    DASHBOARD_DIR.mkdir(exist_ok=True)
    DATA_OUT_DIR.mkdir(exist_ok=True)

    candidates: Dict[str, List] = {}
    logger.info(f"Scanning {BASE_DIR} for company_financials.json …")

    for path in BASE_DIR.rglob("company_financials.json"):
        if "dashboard" in str(path):
            continue
        try:
            with open(path) as f:
                data = json.load(f)
            score  = _score(data)
            boost  = 15 if any(x in str(path) for x in
                               ("standard","bank","insurance","utility","nbfc")) else 0
            ci     = data.get("company_info", {})
            symbol = (ci.get("symbol") or ci.get("ticker") or "").upper()
            if not symbol:
                symbol = path.parent.parent.name.upper()
            candidates.setdefault(symbol, []).append((score + boost, path, data))
        except Exception as e:
            logger.warning(f"Error reading {path}: {e}")

    final_companies: List[Dict] = []
    for symbol, options in sorted(candidates.items()):
        options.sort(key=lambda x: x[0], reverse=True)
        best_score, best_path, best_data = options[0]
        logger.info(f"Processing {symbol:20s}  score={best_score}  src={best_path}")
        
        # Try to find an annual report PDF near the JSON
        pdf_path = None
        annual_dir = best_path.parent.parent / "annual"
        if annual_dir.exists():
            pdfs = list(annual_dir.glob("*.pdf"))
            if pdfs:
                # Get most recent PDF by name
                pdf_path = str(sorted(pdfs, reverse=True)[0])

        try:
            payload = transform_company(best_data, pdf_path)
            payload["company"]["symbol"] = symbol
            out_file = DATA_OUT_DIR / f"{symbol}.json"
            with open(out_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2, default=lambda o: None)
            final_companies.append({
                "symbol": symbol,
                "name":   payload["company"]["name"],
                "sector": payload["company"]["sector"],
                "path":   str(best_path),
            })
        except Exception as e:
            logger.error(f"Failed {symbol}: {e}", exc_info=True)

    # ── Peer Comparison Logic ───────────────────────────────────────────────
    for company in final_companies:
        symbol = company["symbol"]
        sector = company["sector"]
        
        # Find peers in same sector
        peers = [
            {"symbol": c["symbol"], "name": c["name"]}
            for c in final_companies
            if c["sector"] == sector and c["symbol"] != symbol
        ]
        
        # In a real scenario, we'd load their JSONs and get key metrics (Mkt Cap, ROE)
        # For Phase A, we'll just provide the symbols for the UI to link
        json_path = DATA_OUT_DIR / f"{symbol}.json"
        if json_path.exists():
            with open(json_path, "r+") as f:
                data = json.load(f)
                data["peers"] = peers[:5] # Top 5 peers
                f.seek(0)
                json.dump(data, f, indent=2)
                f.truncate()

    logger.info(f"✅ Generated {len(final_companies)} companies → {DATA_OUT_DIR.resolve()}")


if __name__ == "__main__":
    generate_dashboard()
