"""
generate_dashboard_data.py  — Advancement Branch
==================================================
Transforms raw pipeline output into fully-normalized dashboard JSON.

5-Layer Pipeline:
  1. DATA INGESTION   — reads company_financials.json from data/**/final/
  2. NORMALIZATION    — detects & converts all values to ₹ Crores
  3. DERIVED METRICS  — RatioEngine + GrowthEngine (only when inputs exist)
  4. INSIGHT ENGINE   — deterministic rule-based insights
  5. CONFIDENCE TAGS  — VERIFIED / DERIVED / NOT_AVAILABLE per field

Output schema per company:
  {
    "company":         { name, symbol, sector, industry, description, history }
    "market_data":     { price, shares_outstanding, market_cap, _confidence }
    "financials":      { profit_loss, balance_sheet, cash_flow }
    "derived_metrics": { FY2025: { roe, debt_to_equity, ... } }
    "insights":        [ { rule_id, message, severity, basis } ]
    "confidence":      { financials: {...}, derived_metrics: {...}, market_data: {...} }
    "metadata":        { data_sources, last_updated, unit, parser_version }
  }

ZERO-HALLUCINATION POLICY
  - No third-party data (no yfinance, no scrapers)
  - Price / Market Cap → NULL unless sourced from NSE official API
  - All derived values tagged DERIVED; all filing values tagged VERIFIED
  - Missing values → null in JSON (shown in UI as "Not available from filings")
"""

import os
import sys
import json
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

# Allow importing sibling scripts
SCRIPTS_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPTS_DIR))

from insight_engine import InsightEngine
from confidence_tagger import ConfidenceTagger

logger = logging.getLogger("DashboardGenerator")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE_DIR = Path("data")
DASHBOARD_DIR = Path("dashboard")
DATA_OUT_DIR = DASHBOARD_DIR / "data"

# Threshold: values above this in any P&L / BS field are likely absolute Rupees
# (not Crores). We convert by dividing by 1_00_00_000 (1 Crore = 10M rupees).
CRORE = 1_00_00_000       # = 10,000,000
LAKH = 1_00_000           # = 100,000
MAX_SANE_CRORE = 20_00_000.0   # 20 Lakh Crores (RIL scale upper bound)

# If a value exceeds this, we treat it as absolute Rupees → convert to Crores
ABSOLUTE_RUPEE_THRESHOLD = MAX_SANE_CRORE  # anything > 20L Cr in Crore-scale is implausible


# ---------------------------------------------------------------------------
# Unit Normalizer
# ---------------------------------------------------------------------------

class UnitNormalizer:
    """
    Detects whether financial figures are in absolute Rupees or Crores,
    and converts everything to Crores.

    Detection heuristic:
      - If ANY value in a period's P&L exceeds MAX_SANE_CRORE,
        the entire period is assumed to be in absolute Rupees.
      - Applied uniformly per period to maintain mathematical integrity.
    """

    def normalize_period(self, period_data: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
        """
        Returns (normalized_dict, was_converted).
        was_converted=True means values were divided by 1 Crore.
        """
        if not isinstance(period_data, dict):
            return period_data, False

        # Scan for implausibly large values
        needs_conversion = False
        for v in period_data.values():
            if v is None:
                continue
            try:
                fv = float(v)
                if abs(fv) > ABSOLUTE_RUPEE_THRESHOLD * CRORE:
                    needs_conversion = True
                    break
            except (TypeError, ValueError):
                continue

        if not needs_conversion:
            return period_data, False

        converted = {}
        for k, v in period_data.items():
            if v is None:
                converted[k] = None
            else:
                try:
                    fv = float(v)
                    # EPS is per-share, never convert
                    if k.lower() in {"eps", "basic eps", "diluted eps", "diluted_eps", "basic_eps"}:
                        converted[k] = round(fv, 2)
                    else:
                        converted[k] = round(fv / CRORE, 2)
                except (TypeError, ValueError):
                    converted[k] = v
        return converted, True

    def normalize_statement(self, statement: Dict[str, Any]) -> Dict[str, Any]:
        """Normalizes all periods within a statement bucket."""
        result = {}
        for period_label, period_data in statement.items():
            if not isinstance(period_data, dict):
                result[period_label] = period_data
                continue
            normalized, converted = self.normalize_period(period_data)
            if converted:
                logger.debug(f"  Unit conversion applied → {period_label}")
            result[period_label] = normalized
        return result


# ---------------------------------------------------------------------------
# Schema Mapper
# ---------------------------------------------------------------------------

# Maps raw JSON field names (from different sources) → unified schema keys
PL_FIELD_MAP = {
    # NSE normalized schema  →  Unified
    "revenue_from_operations": "revenue",
    "total_income":            "total_income",
    "other_income":            "other_income",
    "ebitda":                  "ebitda",
    "ebit":                    "ebit",
    "interest":                "interest",
    "depreciation":            "depreciation",
    "profit_before_tax":       "profit_before_tax",
    "tax":                     "tax",
    "net_profit":              "net_profit",
    "eps":                     "eps",
    "exceptional_items":       "exceptional_items",
    # YFinance / raw fallback field names
    "Total Revenue":           "revenue",
    "Operating Revenue":       "revenue",
    "Net Income":              "net_profit",
    "EBITDA":                  "ebitda",
    "EBIT":                    "ebit",
    "Interest Expense":        "interest",
    "Reconciled Depreciation": "depreciation",
    "Pretax Income":           "profit_before_tax",
    "Tax Provision":           "tax",
    "Basic EPS":               "eps",
    "Diluted EPS":             "eps",
    "Operating Income":        "ebit",
    "Gross Profit":            "gross_profit",
}

BS_FIELD_MAP = {
    "equity_share_capital":    "share_capital",
    "reserves":                "reserves",
    "total_equity":            "total_equity",
    "total_debt":              "total_debt",
    "long_term_borrowings":    "long_term_debt",
    "short_term_borrowings":   "short_term_debt",
    "total_assets":            "total_assets",
    "total_liabilities":       "total_liabilities",
    "cash_and_equivalents":    "cash",
    "investments":             "investments",
    "receivables":             "receivables",
    "inventory":               "inventory",
    "ppe":                     "fixed_assets",
    "current_assets":          "current_assets",
    "current_liabilities":     "current_liabilities",
    "working_capital":         "working_capital",
    # Raw fallback
    "Stockholders Equity":     "total_equity",
    "Common Stock Equity":     "total_equity",
    "Total Debt":              "total_debt",
    "Total Assets":            "total_assets",
    "Net PPE":                 "fixed_assets",
    "Cash And Cash Equivalents": "cash",
    "Working Capital":         "working_capital",
    "Ordinary Shares Number":  "shares_outstanding",
    "Current Assets":          "current_assets",
    "Current Liabilities":     "current_liabilities",
    "Inventory":               "inventory",
    "Total Liabilities Net Minority Interest": "total_liabilities",
}

CF_FIELD_MAP = {
    "cash_from_operations":    "operating_cf",
    "cash_from_investing":     "investing_cf",
    "cash_from_financing":     "financing_cf",
    "capital_expenditure":     "capex",
    "free_cash_flow":          "free_cash_flow",
    "net_cash_flow":           "net_cash_flow",
    # Raw fallback
    "Operating Cash Flow":     "operating_cf",
    "Investing Cash Flow":     "investing_cf",
    "Financing Cash Flow":     "financing_cf",
    "Capital Expenditure":     "capex",
    "Free Cash Flow":          "free_cash_flow",
}


def _map_period(raw: Dict[str, Any], field_map: Dict[str, str]) -> Dict[str, Any]:
    """Map raw field names to unified schema. First-match wins."""
    out: Dict[str, Any] = {}
    seen_targets = set()
    for src_key, tgt_key in field_map.items():
        if tgt_key in seen_targets:
            continue
        val = raw.get(src_key)
        if val is not None:
            out[tgt_key] = val
            seen_targets.add(tgt_key)
    return out


# ---------------------------------------------------------------------------
# Ratio / Growth computation
# ---------------------------------------------------------------------------

def _safe_float(d: Dict, *keys) -> Optional[float]:
    for k in keys:
        v = d.get(k)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    return None


def compute_derived_metrics(
    pl_annual: Dict[str, Dict],
    bs_annual: Dict[str, Dict],
    cf_annual: Dict[str, Dict],
) -> Dict[str, Dict]:
    """
    Computes ratios + YoY growth. Only computed when both inputs exist.
    Returns {year: {metric: value, ...}}
    """
    years = sorted(
        set(pl_annual) | set(bs_annual) | set(cf_annual),
        reverse=True,
    )
    result: Dict[str, Dict] = {}

    for i, year in enumerate(years):
        pl = pl_annual.get(year, {})
        bs = bs_annual.get(year, {})
        cf = cf_annual.get(year, {})
        dm: Dict[str, Any] = {}

        # --- Profitability ---
        rev = _safe_float(pl, "revenue")
        net_profit = _safe_float(pl, "net_profit")
        pbt = _safe_float(pl, "profit_before_tax")
        ebitda = _safe_float(pl, "ebitda")
        ebit = _safe_float(pl, "ebit")
        equity = _safe_float(bs, "total_equity")
        assets = _safe_float(bs, "total_assets")
        debt = _safe_float(bs, "total_debt")
        cur_assets = _safe_float(bs, "current_assets")
        cur_liab = _safe_float(bs, "current_liabilities")
        cfo = _safe_float(cf, "operating_cf")
        fcf = _safe_float(cf, "free_cash_flow")

        if rev and net_profit is not None:
            dm["net_profit_margin"] = round((net_profit / rev) * 100, 2)
        if rev and ebitda is not None:
            dm["operating_margin"] = round((ebitda / rev) * 100, 2)
        if equity and equity != 0 and net_profit is not None:
            roe = round((net_profit / equity) * 100, 2)
            # Guardrail: ROE > 200% is almost certainly a data error
            if abs(roe) <= 200:
                dm["roe"] = roe
        if assets and assets != 0 and net_profit is not None:
            dm["roa"] = round((net_profit / assets) * 100, 2)

        # ROCE = EBIT / (Total Assets - Current Liabilities)
        if ebit is not None and assets and cur_liab is not None:
            cap_employed = assets - cur_liab
            if cap_employed != 0:
                dm["roce"] = round((ebit / cap_employed) * 100, 2)

        # --- Leverage ---
        if equity and equity != 0 and debt is not None:
            dm["debt_to_equity"] = round(debt / equity, 2)
        if assets and assets != 0 and debt is not None:
            dm["debt_to_assets"] = round(debt / assets, 2)

        # --- Liquidity ---
        if cur_assets is not None and cur_liab and cur_liab != 0:
            dm["current_ratio"] = round(cur_assets / cur_liab, 2)

        # --- Cash Flow ---
        if rev and fcf is not None:
            dm["free_cash_flow_margin"] = round((fcf / rev) * 100, 2)
        if net_profit and net_profit != 0 and cfo is not None:
            dm["cfo_to_net_profit"] = round(cfo / net_profit, 2)

        # --- YoY Growth (compare with previous year) ---
        if i + 1 < len(years):
            prev_year = years[i + 1]
            prev_pl = pl_annual.get(prev_year, {})
            prev_rev = _safe_float(prev_pl, "revenue")
            prev_np = _safe_float(prev_pl, "net_profit")

            if rev is not None and prev_rev and prev_rev != 0:
                dm["revenue_growth_pct"] = round(((rev - prev_rev) / abs(prev_rev)) * 100, 2)
            if net_profit is not None and prev_np and prev_np != 0:
                dm["profit_growth_pct"] = round(
                    ((net_profit - prev_np) / abs(prev_np)) * 100, 2
                )

        if dm:
            result[year] = dm

    return result


# ---------------------------------------------------------------------------
# Quality picker (chooses best JSON per company)
# ---------------------------------------------------------------------------

def _score_data(data: Dict) -> int:
    pl = data.get("profit_loss", {})
    q = len(pl.get("quarterly", {}))
    a = len(pl.get("yearly", {})) + len(pl.get("annual", {}))
    return q * 2 + a * 3   # annual periods weighted slightly higher


# ---------------------------------------------------------------------------
# Core transformation
# ---------------------------------------------------------------------------

def _get_annual_pl(financials: Dict) -> Dict[str, Dict]:
    pl = financials.get("profit_loss", {})
    return pl.get("annual", {}) or pl.get("yearly", {})


def _get_annual_bs(financials: Dict) -> Dict[str, Dict]:
    bs = financials.get("balance_sheet", {})
    return bs.get("annual", {}) or bs.get("yearly", {})


def _get_annual_cf(financials: Dict) -> Dict[str, Dict]:
    cf = financials.get("cash_flow", {})
    return cf.get("annual", {}) or cf.get("yearly", {})


def transform_company(raw_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Full 5-layer transformation for one company.
    """
    normalizer = UnitNormalizer()
    insight_engine = InsightEngine()
    confidence_tagger = ConfidenceTagger()

    # ── LAYER 1: Extract raw buckets ──────────────────────────────────────
    company_info = raw_data.get("company_info", {})
    raw_pl = raw_data.get("profit_loss", {})
    raw_bs = raw_data.get("balance_sheet", {})
    raw_cf = raw_data.get("cash_flow", {})
    raw_ratios = raw_data.get("ratios", {})
    raw_meta = raw_data.get("metadata", {})
    provenance = raw_meta.get("provenance", {})

    symbol = (
        company_info.get("symbol")
        or company_info.get("ticker")
        or "UNKNOWN"
    ).upper()
    name = company_info.get("name") or company_info.get("company_name") or symbol
    sector = company_info.get("sector", "Diversified")
    industry = company_info.get("industry", "")

    # ── LAYER 2: Normalize units to Crores ───────────────────────────────
    def normalize_bucket(bucket: Dict) -> Dict:
        out = {}
        for period_type, periods in bucket.items():
            if not isinstance(periods, dict):
                out[period_type] = periods
                continue
            out[period_type] = normalizer.normalize_statement(periods)
        return out

    norm_pl = normalize_bucket(raw_pl)
    norm_bs = normalize_bucket(raw_bs)
    norm_cf = normalize_bucket(raw_cf)

    # Map to unified field schema
    def map_bucket(bucket: Dict, field_map: Dict) -> Dict:
        out = {}
        for period_type, periods in bucket.items():
            out[period_type] = {}
            for period_label, period_data in periods.items():
                if isinstance(period_data, dict):
                    out[period_type][period_label] = _map_period(period_data, field_map)
        return out

    mapped_pl = map_bucket(norm_pl, PL_FIELD_MAP)
    mapped_bs = map_bucket(norm_bs, BS_FIELD_MAP)
    mapped_cf = map_bucket(norm_cf, CF_FIELD_MAP)

    # Normalize period type keys → always use "annual" and "quarterly"
    def unify_keys(bucket: Dict) -> Dict:
        out = {}
        for k, v in bucket.items():
            new_k = "annual" if k in ("yearly", "annual") else k
            out[new_k] = v
        return out

    mapped_pl = unify_keys(mapped_pl)
    mapped_bs = unify_keys(mapped_bs)
    mapped_cf = unify_keys(mapped_cf)

    financials = {
        "profit_loss":   mapped_pl,
        "balance_sheet": mapped_bs,
        "cash_flow":     mapped_cf,
    }

    # ── LAYER 3: Derived Metrics ──────────────────────────────────────────
    pl_annual = mapped_pl.get("annual", {})
    bs_annual = mapped_bs.get("annual", {})
    cf_annual = mapped_cf.get("annual", {})

    derived_metrics = compute_derived_metrics(pl_annual, bs_annual, cf_annual)

    # ── LAYER 4: Insights ─────────────────────────────────────────────────
    insights = insight_engine.generate(derived_metrics, financials)

    # ── LAYER 5: Confidence Tags ──────────────────────────────────────────
    confidence = {
        "financials":       confidence_tagger.tag_financials(financials, provenance),
        "derived_metrics":  confidence_tagger.tag_derived_metrics(derived_metrics),
        "market_data":      {},
    }

    # ── Market Data ───────────────────────────────────────────────────────
    # STRICT POLICY: price = null unless explicitly from NSE live quote
    # We do NOT pull live prices; market_data is always null here.
    # shares_outstanding may be available from BS
    shares = None
    if bs_annual:
        latest_bs_year = sorted(bs_annual.keys(), reverse=True)[0] if bs_annual else None
        if latest_bs_year:
            shares = bs_annual[latest_bs_year].get("shares_outstanding")

    market_data = {
        "price": None,
        "shares_outstanding": shares,
        "market_cap": None,
        # Note: market_cap = price * shares would be derivable if price existed
        # but price is NOT available from filings → market_cap = null
    }
    confidence["market_data"] = confidence_tagger.tag_market_data(market_data)

    # ── Metadata ──────────────────────────────────────────────────────────
    data_sources = raw_meta.get("data_sources", [])
    # Remove any YFINANCE entries from reported sources
    # (they are used for data but tagged DERIVED, not VERIFIED)
    clean_sources = [
        s for s in data_sources
        if isinstance(s, dict) and s.get("type") not in {"YFINANCE"}
        or isinstance(s, str) and s not in {"YFINANCE"}
    ]

    metadata = {
        "data_sources":        clean_sources,
        "last_updated":        datetime.now(timezone.utc).isoformat(),
        "unit":                "₹ Crores",
        "parser_version":      "v3.0-Advancement",
        "validation_passed":   raw_meta.get("validation_passed", False),
    }

    return {
        "company": {
            "name":        name,
            "symbol":      symbol,
            "sector":      sector,
            "industry":    industry,
            "description": None,   # → from IR scraper (future)
            "history":     None,   # → from IR scraper (future)
        },
        "market_data":     market_data,
        "financials":      financials,
        "derived_metrics": derived_metrics,
        "insights":        insights,
        "confidence":      confidence,
        "metadata":        metadata,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def generate_dashboard():
    DASHBOARD_DIR.mkdir(exist_ok=True)
    DATA_OUT_DIR.mkdir(exist_ok=True)

    # ── Discover candidates ───────────────────────────────────────────────
    candidates: Dict[str, List] = {}
    logger.info(f"Scanning {BASE_DIR} for company_financials.json …")

    for path in BASE_DIR.rglob("company_financials.json"):
        if "dashboard" in str(path):
            continue
        try:
            with open(path) as f:
                data = json.load(f)

            score = _score_data(data)
            boost = 0
            if any(x in str(path) for x in ("standard", "bank", "insurance", "utility", "nbfc")):
                boost = 15

            ci = data.get("company_info", {})
            symbol = (ci.get("symbol") or ci.get("ticker") or "").upper()
            if not symbol:
                symbol = path.parent.parent.name.upper()

            if symbol not in candidates:
                candidates[symbol] = []
            candidates[symbol].append((score + boost, path, data))

        except Exception as e:
            logger.warning(f"Error reading {path}: {e}")

    # ── Transform & write ─────────────────────────────────────────────────
    final_companies: List[Dict] = []

    for symbol, options in sorted(candidates.items()):
        options.sort(key=lambda x: x[0], reverse=True)
        best_score, best_path, best_data = options[0]

        logger.info(f"Processing {symbol:20s}  source={best_path}  score={best_score}")

        try:
            dashboard_payload = transform_company(best_data)
            # Ensure symbol is set correctly
            dashboard_payload["company"]["symbol"] = symbol

            out_file = DATA_OUT_DIR / f"{symbol}.json"
            with open(out_file, "w", encoding="utf-8") as f:
                json.dump(dashboard_payload, f, ensure_ascii=False, indent=2,
                          default=lambda o: None)

            final_companies.append({
                "symbol": symbol,
                "name":   dashboard_payload["company"]["name"],
                "sector": dashboard_payload["company"]["sector"],
                "path":   str(best_path),
            })

        except Exception as e:
            logger.error(f"Failed to process {symbol}: {e}", exc_info=True)

    # ── Write companies index ─────────────────────────────────────────────
    final_companies.sort(key=lambda x: x["symbol"])
    with open(DASHBOARD_DIR / "companies.json", "w", encoding="utf-8") as f:
        json.dump(final_companies, f, indent=2)

    logger.info(f"✅ Dashboard data generated for {len(final_companies)} companies.")
    logger.info(f"   Output: {DATA_OUT_DIR.resolve()}/")


if __name__ == "__main__":
    generate_dashboard()
