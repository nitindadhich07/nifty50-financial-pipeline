"""
Insight Engine — RULE-BASED ONLY
=================================
Zero-hallucination policy: every insight is derived from a deterministic
conditional rule applied to verified/derived financial metrics.
NO LLM, NO GUESSING, NO ESTIMATES.

Severity levels:
  positive  — green badge (strong signal)
  warning   — amber badge (caution)
  negative  — red badge (risk)
  neutral   — grey badge (informational)
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _safe(d: Dict, *keys) -> Optional[float]:
    """Drill into nested dicts safely; return None if any key is missing."""
    cur = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    if cur is None:
        return None
    try:
        return float(cur)
    except (TypeError, ValueError):
        return None


def _pct_change(new: float, old: float) -> Optional[float]:
    """Compute % change; None if base is zero."""
    if old == 0:
        return None
    return round(((new - old) / abs(old)) * 100, 2)


def _latest_two_years(annual: Dict[str, Any]):
    """Return (current_year_label, prev_year_label) sorted descending."""
    years = sorted(annual.keys(), reverse=True)
    if len(years) >= 2:
        return years[0], years[1]
    elif len(years) == 1:
        return years[0], None
    return None, None


# ---------------------------------------------------------------------------
# Rule Definitions
# ---------------------------------------------------------------------------

class InsightEngine:
    """
    Applies deterministic rules to derived_metrics and financials.
    Returns a list of insight dicts.

    Each insight:
    {
        "rule_id": str,
        "message": str,
        "severity": "positive" | "warning" | "negative" | "neutral",
        "basis": str   # which field/value triggered this rule
    }
    """

    # -------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------

    def generate(
        self,
        derived_metrics: Dict[str, Dict],
        financials: Dict[str, Any],
    ) -> List[Dict[str, str]]:
        insights: List[Dict[str, str]] = []

        # Work on most recent year's metrics
        dm_years = sorted(derived_metrics.keys(), reverse=True)
        if not dm_years:
            return insights

        latest_year = dm_years[0]
        prev_year = dm_years[1] if len(dm_years) > 1 else None
        dm = derived_metrics[latest_year]

        # Pull annual P&L bucket
        pl_annual: Dict = (
            financials.get("profit_loss", {}).get("annual", {})
            or financials.get("profit_loss", {}).get("yearly", {})
        )

        insights += self._profitability_rules(dm, latest_year)
        insights += self._growth_rules(derived_metrics, latest_year, prev_year)
        insights += self._leverage_rules(dm, latest_year)
        insights += self._liquidity_rules(dm, latest_year)
        insights += self._cashflow_rules(dm, latest_year, pl_annual)
        insights += self._quality_rules(dm, latest_year, pl_annual)

        return insights

    # -------------------------------------------------------------------
    # Rule groups
    # -------------------------------------------------------------------

    def _profitability_rules(self, dm: Dict, year: str) -> List[Dict]:
        out = []

        roe = _safe(dm, "roe")
        npm = _safe(dm, "net_profit_margin")
        roa = _safe(dm, "roa")
        op_margin = _safe(dm, "operating_margin")

        # ROE rules
        if roe is not None:
            if roe >= 20:
                out.append({
                    "rule_id": "roe_excellent",
                    "message": f"Excellent capital efficiency — ROE {roe:.1f}% (≥20%) in {year}",
                    "severity": "positive",
                    "basis": f"ROE={roe:.1f}%",
                })
            elif 15 <= roe < 20:
                out.append({
                    "rule_id": "roe_good",
                    "message": f"Good return on equity — ROE {roe:.1f}% in {year}",
                    "severity": "positive",
                    "basis": f"ROE={roe:.1f}%",
                })
            elif 10 <= roe < 15:
                out.append({
                    "rule_id": "roe_moderate",
                    "message": f"Moderate capital efficiency — ROE {roe:.1f}% in {year}",
                    "severity": "neutral",
                    "basis": f"ROE={roe:.1f}%",
                })
            elif 0 <= roe < 10:
                out.append({
                    "rule_id": "roe_low",
                    "message": f"Low capital efficiency — ROE {roe:.1f}% (<10%) in {year}",
                    "severity": "warning",
                    "basis": f"ROE={roe:.1f}%",
                })
            else:
                out.append({
                    "rule_id": "roe_negative",
                    "message": f"Negative return on equity — ROE {roe:.1f}% in {year}",
                    "severity": "negative",
                    "basis": f"ROE={roe:.1f}%",
                })

        # Net Profit Margin
        if npm is not None:
            if npm >= 20:
                out.append({
                    "rule_id": "npm_excellent",
                    "message": f"High-margin business — Net Profit Margin {npm:.1f}% in {year}",
                    "severity": "positive",
                    "basis": f"NPM={npm:.1f}%",
                })
            elif npm < 0:
                out.append({
                    "rule_id": "npm_negative",
                    "message": f"Company reported a net loss ({npm:.1f}% margin) in {year}",
                    "severity": "negative",
                    "basis": f"NPM={npm:.1f}%",
                })
            elif npm < 5:
                out.append({
                    "rule_id": "npm_thin",
                    "message": f"Thin profit margins — NPM {npm:.1f}% in {year}",
                    "severity": "warning",
                    "basis": f"NPM={npm:.1f}%",
                })

        return out

    def _growth_rules(self, derived_metrics: Dict, latest_year: str, prev_year: Optional[str]) -> List[Dict]:
        out = []
        if prev_year is None:
            return out

        dm_cur = derived_metrics[latest_year]
        dm_prev = derived_metrics[prev_year]

        rev_growth = _safe(dm_cur, "revenue_growth_pct")
        profit_growth = _safe(dm_cur, "profit_growth_pct")

        # Strong growth
        if rev_growth is not None and profit_growth is not None:
            if rev_growth > 15 and profit_growth > 15:
                out.append({
                    "rule_id": "strong_growth",
                    "message": (
                        f"Strong growth momentum — Revenue +{rev_growth:.1f}% & "
                        f"Profit +{profit_growth:.1f}% YoY in {latest_year}"
                    ),
                    "severity": "positive",
                    "basis": f"RevGrowth={rev_growth:.1f}%, ProfitGrowth={profit_growth:.1f}%",
                })
            elif rev_growth > 15 and profit_growth <= 0:
                out.append({
                    "rule_id": "topline_not_bottomline",
                    "message": (
                        f"Revenue grew {rev_growth:.1f}% but profits declined "
                        f"{profit_growth:.1f}% in {latest_year} — margin compression risk"
                    ),
                    "severity": "warning",
                    "basis": f"RevGrowth={rev_growth:.1f}%, ProfitGrowth={profit_growth:.1f}%",
                })
            elif rev_growth <= 0 and profit_growth <= 0:
                out.append({
                    "rule_id": "revenue_contraction",
                    "message": f"Revenue declined {rev_growth:.1f}% YoY in {latest_year}",
                    "severity": "negative",
                    "basis": f"RevGrowth={rev_growth:.1f}%",
                })

        # Profit growth alone
        if profit_growth is not None and rev_growth is None:
            if profit_growth > 20:
                out.append({
                    "rule_id": "profit_strong",
                    "message": f"Profit grew strongly +{profit_growth:.1f}% YoY in {latest_year}",
                    "severity": "positive",
                    "basis": f"ProfitGrowth={profit_growth:.1f}%",
                })
            elif profit_growth < -20:
                out.append({
                    "rule_id": "profit_sharp_decline",
                    "message": f"Profit fell sharply {profit_growth:.1f}% YoY in {latest_year}",
                    "severity": "negative",
                    "basis": f"ProfitGrowth={profit_growth:.1f}%",
                })

        return out

    def _leverage_rules(self, dm: Dict, year: str) -> List[Dict]:
        out = []
        d2e = _safe(dm, "debt_to_equity")
        d2a = _safe(dm, "debt_to_assets")

        if d2e is not None:
            if d2e > 2.0:
                out.append({
                    "rule_id": "high_leverage",
                    "message": f"High leverage risk — Debt/Equity ratio {d2e:.2f}x (>2x) in {year}",
                    "severity": "negative",
                    "basis": f"D/E={d2e:.2f}x",
                })
            elif 1.0 < d2e <= 2.0:
                out.append({
                    "rule_id": "moderate_leverage",
                    "message": f"Moderate leverage — Debt/Equity {d2e:.2f}x in {year}",
                    "severity": "warning",
                    "basis": f"D/E={d2e:.2f}x",
                })
            elif d2e <= 0.3:
                out.append({
                    "rule_id": "low_leverage",
                    "message": f"Virtually debt-free — Debt/Equity {d2e:.2f}x in {year}",
                    "severity": "positive",
                    "basis": f"D/E={d2e:.2f}x",
                })
            elif d2e <= 1.0:
                out.append({
                    "rule_id": "comfortable_leverage",
                    "message": f"Comfortable leverage — Debt/Equity {d2e:.2f}x in {year}",
                    "severity": "neutral",
                    "basis": f"D/E={d2e:.2f}x",
                })

        return out

    def _liquidity_rules(self, dm: Dict, year: str) -> List[Dict]:
        out = []
        cr = _safe(dm, "current_ratio")

        if cr is not None:
            if cr >= 2.0:
                out.append({
                    "rule_id": "strong_liquidity",
                    "message": f"Strong liquidity position — Current Ratio {cr:.2f}x in {year}",
                    "severity": "positive",
                    "basis": f"CurrentRatio={cr:.2f}x",
                })
            elif 1.0 <= cr < 2.0:
                out.append({
                    "rule_id": "adequate_liquidity",
                    "message": f"Adequate liquidity — Current Ratio {cr:.2f}x in {year}",
                    "severity": "neutral",
                    "basis": f"CurrentRatio={cr:.2f}x",
                })
            elif cr < 1.0:
                out.append({
                    "rule_id": "liquidity_concern",
                    "message": f"Liquidity concern — Current Ratio {cr:.2f}x (<1.0) in {year}",
                    "severity": "negative",
                    "basis": f"CurrentRatio={cr:.2f}x",
                })

        return out

    def _cashflow_rules(self, dm: Dict, year: str, pl_annual: Dict) -> List[Dict]:
        out = []
        cfo_ratio = _safe(dm, "cfo_to_net_profit")
        fcf_margin = _safe(dm, "free_cash_flow_margin")

        if cfo_ratio is not None:
            if cfo_ratio >= 1.0:
                out.append({
                    "rule_id": "cash_backed_profits",
                    "message": (
                        f"Profits are cash-backed — CFO/Net Profit ratio {cfo_ratio:.2f}x in {year}"
                    ),
                    "severity": "positive",
                    "basis": f"CFO/NP={cfo_ratio:.2f}x",
                })
            elif cfo_ratio < 0:
                out.append({
                    "rule_id": "poor_cash_conversion",
                    "message": f"Poor cash conversion — negative CFO despite reported profit in {year}",
                    "severity": "negative",
                    "basis": f"CFO/NP={cfo_ratio:.2f}x",
                })

        if fcf_margin is not None:
            if fcf_margin < -5:
                out.append({
                    "rule_id": "negative_fcf",
                    "message": f"Negative Free Cash Flow margin {fcf_margin:.1f}% in {year} — heavy capex phase",
                    "severity": "warning",
                    "basis": f"FCFMargin={fcf_margin:.1f}%",
                })
            elif fcf_margin >= 10:
                out.append({
                    "rule_id": "strong_fcf",
                    "message": f"Strong Free Cash Flow generation — {fcf_margin:.1f}% margin in {year}",
                    "severity": "positive",
                    "basis": f"FCFMargin={fcf_margin:.1f}%",
                })

        return out

    def _quality_rules(self, dm: Dict, year: str, pl_annual: Dict) -> List[Dict]:
        out = []
        roce = _safe(dm, "roce")
        roa = _safe(dm, "roa")

        if roce is not None:
            if roce >= 15:
                out.append({
                    "rule_id": "roce_good",
                    "message": f"Efficient capital deployment — ROCE {roce:.1f}% in {year}",
                    "severity": "positive",
                    "basis": f"ROCE={roce:.1f}%",
                })
            elif roce < 8:
                out.append({
                    "rule_id": "roce_poor",
                    "message": f"Poor capital deployment — ROCE {roce:.1f}% (<8%) in {year}",
                    "severity": "warning",
                    "basis": f"ROCE={roce:.1f}%",
                })

        return out
