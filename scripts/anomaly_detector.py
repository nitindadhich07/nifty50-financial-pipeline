"""
Anomaly Detector — Deterministic Only
======================================
Detects abnormal patterns in financial data using threshold-based rules.
Zero LLM, zero estimation. Every flag is traceable to filing values.

Anomaly types:
  revenue_spike      — YoY revenue growth > 30%
  revenue_drop       — YoY revenue decline > 15%
  profit_collapse    — Profit growth < -30% or swing from profit to loss
  margin_collapse    — EBITDA margin dropped > 8pp YoY
  debt_spike         — Total debt grew > 40% YoY
  negative_fcf       — Free cash flow negative for 2+ consecutive years
  eps_anomaly        — EPS dropped > 40% YoY
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional


# ─── Thresholds ──────────────────────────────────────────────────────────────
THRESHOLDS = {
    "revenue_spike":   30.0,   # % YoY
    "revenue_drop":    15.0,   # % YoY (absolute)
    "profit_collapse": 30.0,   # % YoY decline
    "margin_collapse":  8.0,   # percentage points drop in EBITDA margin
    "debt_spike":      40.0,   # % YoY
    "eps_anomaly":     40.0,   # % YoY drop
}

SEVERITY_MAP = {
    "revenue_spike":   "info",
    "revenue_drop":    "warning",
    "profit_collapse": "negative",
    "margin_collapse": "negative",
    "debt_spike":      "warning",
    "negative_fcf":    "warning",
    "eps_anomaly":     "warning",
}


def _g(d: Dict, key: str) -> Optional[float]:
    v = d.get(key)
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _pct(new: float, old: float) -> Optional[float]:
    if old == 0:
        return None
    return round(((new - old) / abs(old)) * 100, 2)


class AnomalyDetector:
    """
    Runs deterministic anomaly checks on annual P&L, BS, and CF derived data.
    Returns list of anomaly dicts with type, period, value, severity, message.
    """

    def detect(
        self,
        pl_annual: Dict[str, Dict],
        bs_annual: Dict[str, Dict],
        cf_annual: Dict[str, Dict],
    ) -> List[Dict[str, Any]]:
        anomalies: List[Dict[str, Any]] = []
        years = sorted(pl_annual.keys(), reverse=True)

        consecutive_neg_fcf = 0

        for i, year in enumerate(years):
            pl = pl_annual.get(year, {})
            bs = bs_annual.get(year, {})
            cf = cf_annual.get(year, {})

            if i + 1 >= len(years):
                break  # need previous year to compare

            prev_year = years[i + 1]
            prev_pl = pl_annual.get(prev_year, {})
            prev_bs = bs_annual.get(prev_year, {})
            prev_cf = cf_annual.get(prev_year, {})

            # ── Revenue anomalies ─────────────────────────────────────────
            rev = _g(pl, "revenue")
            prev_rev = _g(prev_pl, "revenue")
            if rev is not None and prev_rev and prev_rev != 0:
                chg = _pct(rev, prev_rev)
                if chg is not None:
                    if chg > THRESHOLDS["revenue_spike"]:
                        anomalies.append(self._make(
                            "revenue_spike", year, chg,
                            f"Revenue grew +{chg:.1f}% YoY in {year} — significantly above normal range (threshold: >{THRESHOLDS['revenue_spike']}%)"
                        ))
                    elif chg < -THRESHOLDS["revenue_drop"]:
                        anomalies.append(self._make(
                            "revenue_drop", year, chg,
                            f"Revenue fell {chg:.1f}% YoY in {year} — sharp contraction detected"
                        ))

            # ── Profit collapse ───────────────────────────────────────────
            np_ = _g(pl, "net_profit")
            prev_np = _g(prev_pl, "net_profit")
            if np_ is not None and prev_np is not None:
                if prev_np > 0 and np_ < 0:
                    anomalies.append(self._make(
                        "profit_collapse", year, None,
                        f"Swung from profit to loss in {year} — net profit turned negative"
                    ))
                elif prev_np != 0:
                    chg = _pct(np_, prev_np)
                    if chg is not None and chg < -THRESHOLDS["profit_collapse"]:
                        anomalies.append(self._make(
                            "profit_collapse", year, chg,
                            f"Net profit fell {chg:.1f}% YoY in {year} — significant earnings deterioration"
                        ))

            # ── EBITDA margin collapse ────────────────────────────────────
            ebitda = _g(pl, "ebitda")
            prev_ebitda = _g(prev_pl, "ebitda")
            if ebitda is not None and rev and prev_ebitda is not None and prev_rev:
                margin_cur  = (ebitda / rev) * 100
                margin_prev = (prev_ebitda / prev_rev) * 100
                margin_drop = margin_prev - margin_cur
                if margin_drop > THRESHOLDS["margin_collapse"]:
                    anomalies.append(self._make(
                        "margin_collapse", year, round(margin_drop, 1),
                        f"EBITDA margin compressed by {margin_drop:.1f}pp in {year} "
                        f"({margin_prev:.1f}% → {margin_cur:.1f}%)"
                    ))

            # ── Debt spike ────────────────────────────────────────────────
            debt = _g(bs, "total_debt")
            prev_debt = _g(prev_bs, "total_debt")
            if debt is not None and prev_debt and prev_debt != 0:
                chg = _pct(debt, prev_debt)
                if chg is not None and chg > THRESHOLDS["debt_spike"]:
                    anomalies.append(self._make(
                        "debt_spike", year, chg,
                        f"Total debt surged +{chg:.1f}% YoY in {year} — significant leverage increase"
                    ))

            # ── EPS anomaly ───────────────────────────────────────────────
            eps = _g(pl, "eps")
            prev_eps = _g(prev_pl, "eps")
            if eps is not None and prev_eps and prev_eps != 0 and eps != 0:
                chg = _pct(eps, prev_eps)
                if chg is not None and chg < -THRESHOLDS["eps_anomaly"]:
                    anomalies.append(self._make(
                        "eps_anomaly", year, chg,
                        f"EPS dropped {chg:.1f}% YoY in {year} — earnings per share deteriorated sharply"
                    ))

            # ── Negative FCF tracking ─────────────────────────────────────
            fcf = _g(cf, "free_cash_flow")
            if fcf is not None and fcf < 0:
                consecutive_neg_fcf += 1
            else:
                consecutive_neg_fcf = 0

            if consecutive_neg_fcf >= 2:
                anomalies.append(self._make(
                    "negative_fcf", year, None,
                    f"Free cash flow has been negative for {consecutive_neg_fcf}+ consecutive years ending {year} — sustained cash burn"
                ))

        # Deduplicate on (type, period)
        seen = set()
        unique = []
        for a in anomalies:
            key = (a["type"], a["period"])
            if key not in seen:
                seen.add(key)
                unique.append(a)

        return unique

    def _make(
        self,
        atype: str,
        period: str,
        value: Optional[float],
        message: str,
    ) -> Dict[str, Any]:
        return {
            "type":     atype,
            "period":   period,
            "value":    value,
            "severity": SEVERITY_MAP.get(atype, "neutral"),
            "message":  message,
            "source":   "DETERMINISTIC_RULE",
        }
