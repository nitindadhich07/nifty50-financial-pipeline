#!/usr/bin/env python3
"""
export_json.py  (v4 - Screener-Style Output)
--------------------------------------------
Builds the final JSON with:
  - Full financial statements (P&L, BS, CF)
  - Computed ratios (ROE, ROCE, D/E, interest coverage, etc.)
  - Growth metrics (CAGR 1yr, 3yr, 5yr for revenue & profit)
  - Screener-style insights (strengths, concerns, summary)
"""

import json
import os
import math
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List, Tuple

logger = logging.getLogger(__name__)


def _sanitize(obj: Any) -> Any:
    """Recursively sanitize for JSON: NaN/Inf → null, round floats."""
    if obj is None:
        return None
    if isinstance(obj, float):
        if obj != obj or abs(obj) == float("inf"):
            return None
        return round(obj, 2)
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(v) for v in obj]
    return obj


# ─────────────────────────────────────────────────────────────────────────────
# RATIOS
# ─────────────────────────────────────────────────────────────────────────────

def compute_ratios(pnl_yearly: Dict, balance_sheet: Dict, cash_flow: Dict) -> Dict:
    ratios = {}
    for fy, pnl in pnl_yearly.items():
        bs = balance_sheet.get(fy, {})
        cf = cash_flow.get(fy, {})

        sales    = pnl.get("revenue_from_operations")
        ebitda   = pnl.get("ebitda")
        net_p    = pnl.get("net_profit")
        interest = pnl.get("interest")
        depr     = pnl.get("depreciation")
        pbt      = pnl.get("profit_before_tax")
        raw_mat  = pnl.get("raw_material_cost", 0) or 0
        emp_cost = pnl.get("employee_cost", 0) or 0

        assets  = bs.get("total_assets")
        eq      = bs.get("total_equity")
        debt    = bs.get("total_debt")
        curr_a  = bs.get("total_current_assets")
        curr_l  = bs.get("total_current_liabilities")
        inv     = bs.get("inventory")
        recv    = bs.get("receivables")
        payable = bs.get("trade_payables")

        fcf     = cf.get("free_cash_flow")
        cfo     = cf.get("cash_from_operations")

        r: Dict[str, Any] = {}

        # ── Profitability ──
        if sales and net_p is not None:
            r["net_profit_margin_pct"] = round((net_p / sales) * 100, 2)
        if sales and ebitda is not None:
            r["operating_margin_pct"] = round((ebitda / sales) * 100, 2)
        if sales and raw_mat:
            r["gross_margin_pct"] = round(((sales - raw_mat) / sales) * 100, 2)

        # ── Returns ──
        if net_p is not None and eq and eq != 0:
            r["roe_pct"] = round((net_p / eq) * 100, 2)
        if net_p is not None and assets and assets != 0:
            r["roa_pct"] = round((net_p / assets) * 100, 2)

        # ROCE = EBIT / Capital Employed
        ebit = None
        if pbt is not None and interest is not None:
            ebit = round(pbt + interest, 2)
        elif ebitda is not None and depr is not None:
            ebit = round(ebitda - depr, 2)
        cap_emp = (eq or 0) + (debt or 0)
        if ebit is not None and cap_emp > 0:
            r["roce_pct"] = round((ebit / cap_emp) * 100, 2)

        # ── Leverage ──
        if debt is not None and eq and eq != 0:
            r["debt_to_equity"] = round(debt / eq, 2)
        if debt is not None and ebitda and ebitda != 0:
            r["net_debt_to_ebitda"] = round(debt / ebitda, 2)
        if ebit is not None and interest and interest != 0:
            r["interest_coverage"] = round(ebit / interest, 2)

        # ── Efficiency ──
        if sales and assets and assets != 0:
            r["asset_turnover"] = round(sales / assets, 2)
        if curr_a is not None and curr_l and curr_l != 0:
            r["current_ratio"] = round(curr_a / curr_l, 2)
        if inv and sales and sales != 0:
            r["inventory_days"] = round((inv / sales) * 365, 1)
        if recv and sales and sales != 0:
            r["debtor_days"] = round((recv / sales) * 365, 1)
        if payable and sales and sales != 0:
            r["creditor_days"] = round((payable / sales) * 365, 1)

        # ── Cash Flow ──
        if fcf is not None and sales and sales != 0:
            r["free_cash_flow_margin"] = round((fcf / sales) * 100, 2)
        if cfo is not None and net_p and net_p != 0:
            r["cfo_to_net_profit"] = round(cfo / net_p, 2)

        # ── Debt metrics ──
        if debt is not None and assets and assets != 0:
            r["debt_to_assets"] = round(debt / assets, 2)

        # Fill missing with None for schema completeness
        for k in ["gross_margin_pct", "operating_margin_pct", "net_profit_margin_pct",
                   "roe_pct", "roce_pct", "roa_pct", "debt_to_equity", "interest_coverage",
                   "asset_turnover", "current_ratio", "free_cash_flow_margin",
                   "net_debt_to_ebitda", "inventory_days", "debtor_days", "creditor_days",
                   "cfo_to_net_profit", "debt_to_assets"]:
            r.setdefault(k, None)

        ratios[fy] = r

    return ratios


# ─────────────────────────────────────────────────────────────────────────────
# GROWTH METRICS
# ─────────────────────────────────────────────────────────────────────────────

def _cagr(start: Optional[float], end: Optional[float], years: int) -> Optional[float]:
    """Compute CAGR %."""
    if start is None or end is None or years <= 0 or start <= 0:
        return None
    try:
        result = (pow(end / start, 1 / years) - 1) * 100
        return round(result, 2)
    except (ValueError, ZeroDivisionError):
        return None


def compute_growth(pnl_yearly: Dict) -> Dict:
    """Compute YoY and CAGR growth for revenue, profit, EPS."""
    # Sort FYs descending
    fys = sorted(pnl_yearly.keys(), reverse=True)

    def _val(fy: str, field: str) -> Optional[float]:
        return pnl_yearly.get(fy, {}).get(field)

    growth: Dict[str, Any] = {}

    # YoY (1-year)
    if len(fys) >= 2:
        rev_new = _val(fys[0], "revenue_from_operations")
        rev_old = _val(fys[1], "revenue_from_operations")
        np_new  = _val(fys[0], "net_profit")
        np_old  = _val(fys[1], "net_profit")

        growth["revenue_yoy_pct"] = _cagr(rev_old, rev_new, 1)
        growth["profit_yoy_pct"]  = _cagr(np_old,  np_new,  1)

        if rev_old and rev_old != 0:
            growth["revenue_yoy_pct"] = round(((rev_new or 0) - rev_old) / rev_old * 100, 2)
        if np_old and np_old != 0:
            growth["profit_yoy_pct"] = round(((np_new or 0) - np_old) / np_old * 100, 2)

    # 3-year CAGR
    if len(fys) >= 4:
        growth["revenue_cagr_3yr"] = _cagr(_val(fys[3], "revenue_from_operations"),
                                            _val(fys[0], "revenue_from_operations"), 3)
        growth["profit_cagr_3yr"]  = _cagr(_val(fys[3], "net_profit"),
                                            _val(fys[0], "net_profit"), 3)
        growth["eps_cagr_3yr"]     = _cagr(_val(fys[3], "eps"), _val(fys[0], "eps"), 3)
    else:
        growth.update({"revenue_cagr_3yr": None, "profit_cagr_3yr": None, "eps_cagr_3yr": None})

    # 5-year CAGR
    if len(fys) >= 6:
        growth["revenue_cagr_5yr"] = _cagr(_val(fys[5], "revenue_from_operations"),
                                            _val(fys[0], "revenue_from_operations"), 5)
        growth["profit_cagr_5yr"]  = _cagr(_val(fys[5], "net_profit"),
                                            _val(fys[0], "net_profit"), 5)
        growth["eps_cagr_5yr"]     = _cagr(_val(fys[5], "eps"), _val(fys[0], "eps"), 5)
    else:
        growth.update({"revenue_cagr_5yr": None, "profit_cagr_5yr": None, "eps_cagr_5yr": None})

    return growth


# ─────────────────────────────────────────────────────────────────────────────
# SCREENER-STYLE INSIGHTS
# ─────────────────────────────────────────────────────────────────────────────

def generate_insights(output: Dict) -> Dict:
    """
    Generate screener.in-style insights: strengths, concerns, and summary.
    """
    strengths  = []
    concerns   = []

    latest_fy  = sorted(output["profit_loss"]["yearly"].keys(), reverse=True)
    if not latest_fy:
        return {"strengths": [], "concerns": [], "summary": "Insufficient data"}

    fy = latest_fy[0]
    pnl    = output["profit_loss"]["yearly"].get(fy, {})
    ratios = output["ratios"].get(fy, {})
    growth = output.get("growth", {})

    # ── Revenue
    rev = pnl.get("revenue_from_operations")
    if rev and rev > 500_000:
        strengths.append(f"Large-cap company with revenue of ₹{rev:,.0f} Cr ({fy})")

    # ── Profitability
    npm = ratios.get("net_profit_margin_pct")
    if npm is not None:
        if npm > 10:
            strengths.append(f"Healthy net profit margin of {npm:.1f}% ({fy})")
        elif npm < 3:
            concerns.append(f"Thin net profit margin of {npm:.1f}% ({fy})")

    opm = ratios.get("operating_margin_pct")
    if opm is not None:
        if opm > 15:
            strengths.append(f"Strong EBITDA margin of {opm:.1f}% ({fy})")
        elif opm < 8:
            concerns.append(f"Low EBITDA margin of {opm:.1f}% — cost pressure ({fy})")

    # ── Returns
    roe = ratios.get("roe_pct")
    if roe is not None:
        if roe > 15:
            strengths.append(f"Strong ROE of {roe:.1f}% — efficient use of equity ({fy})")
        elif roe < 8:
            concerns.append(f"Low ROE of {roe:.1f}% ({fy})")

    roce = ratios.get("roce_pct")
    if roce is not None:
        if roce > 12:
            strengths.append(f"Good ROCE of {roce:.1f}% — capital deployed efficiently ({fy})")

    # ── Leverage
    de = ratios.get("debt_to_equity")
    if de is not None:
        if de < 0.5:
            strengths.append(f"Low debt-to-equity of {de:.2f}x — conservative balance sheet ({fy})")
        elif de > 1.5:
            concerns.append(f"High debt-to-equity of {de:.2f}x — leverage risk ({fy})")

    ic = ratios.get("interest_coverage")
    if ic is not None:
        if ic > 4:
            strengths.append(f"Strong interest coverage of {ic:.1f}x — comfortable debt servicing ({fy})")
        elif ic < 2:
            concerns.append(f"Low interest coverage of {ic:.1f}x — debt servicing risk ({fy})")

    # ── Growth
    rev_yoy = growth.get("revenue_yoy_pct")
    if rev_yoy is not None:
        if rev_yoy > 10:
            strengths.append(f"Revenue grew {rev_yoy:.1f}% YoY")
        elif rev_yoy < 0:
            concerns.append(f"Revenue declined {abs(rev_yoy):.1f}% YoY")

    rev_3yr = growth.get("revenue_cagr_3yr")
    if rev_3yr is not None and rev_3yr > 8:
        strengths.append(f"Consistent revenue CAGR of {rev_3yr:.1f}% over 3 years")

    # ── Cash Flow
    fcf_margin = ratios.get("free_cash_flow_margin")
    if fcf_margin is not None:
        if fcf_margin > 5:
            strengths.append(f"Positive free cash flow margin of {fcf_margin:.1f}% ({fy})")
        elif fcf_margin < 0:
            concerns.append(f"Negative free cash flow — capex intensive phase ({fy})")

    # ── Dividend (from CF)
    # bs_years = list(output.get("balance_sheet", {}).get("yearly", {}).keys())

    # ── Summary
    total_score = len(strengths) - len(concerns)
    if total_score >= 3:
        verdict = "Financially strong company with solid fundamentals."
    elif total_score >= 0:
        verdict = "Moderate financial profile with some areas to monitor."
    else:
        verdict = "Several financial concerns need attention."

    rev_str = f"₹{rev/100000:.2f} lakh crore" if rev and rev > 100000 else (f"₹{rev:,.0f} Cr" if rev else "N/A")
    np  = pnl.get("net_profit")
    np_str = f"₹{np:,.0f} Cr" if np else "N/A"

    summary = (
        f"{fy} Overview: Revenue {rev_str}, Net Profit {np_str}. "
        f"EBITDA margin {opm:.1f}%" if opm else "."
    )
    summary += f" {verdict}"

    return {
        "strengths":  strengths,
        "concerns":   concerns,
        "summary":    summary,
        "as_of_fy":   fy,
    }


# ─────────────────────────────────────────────────────────────────────────────
# BUILD OUTPUT
# ─────────────────────────────────────────────────────────────────────────────

def build_output(
    symbol:        str,
    company_name:  str,
    quarterly_pnl: Dict,
    annual_pnl:    Dict,
    balance_sheet: Dict,
    cash_flow:     Dict,
    validation:    Optional[Dict] = None,
    data_sources:  Optional[List] = None,
    sector:        str = "Energy",
    industry:      str = "Oil & Gas",
) -> Dict:
    ratios = compute_ratios(annual_pnl, balance_sheet, cash_flow)
    growth = compute_growth(annual_pnl)

    output = {
        "company_info": {
            "ticker":       symbol.upper(),
            "company_name": company_name,
            "exchange":     "NSE/BSE",
            "sector":       sector,
            "industry":     industry,
            "currency":     "INR",
            "unit":         "₹ Crores",
        },
        "profit_loss": {
            "quarterly": quarterly_pnl,
            "yearly":    annual_pnl,
        },
        "balance_sheet": {
            "yearly": balance_sheet,
        },
        "cash_flow": {
            "yearly": cash_flow,
        },
        "ratios":  ratios,
        "growth":  growth,
        "metadata": {
            "data_sources":       data_sources or [],
            "last_updated":       datetime.now(timezone.utc).isoformat(),
            "parser_version":     "v4.0",
            "validation_passed":  (validation or {}).get("validation_passed", False),
            "validation_details": validation,
        },
    }

    # Add insights after output is assembled
    sanitized = _sanitize(output)
    sanitized["insights"] = generate_insights(sanitized)
    return sanitized


def save_json(data: Dict, output_path: str) -> str:
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    kb = os.path.getsize(output_path) // 1024
    logger.info(f"JSON saved → {output_path} ({kb} KB)")
    return output_path


def print_summary(data: Dict):
    print(f"\n{'='*70}")
    info = data.get("company_info", {})
    print(f"  {info.get('company_name')} ({info.get('ticker')})  |  {info.get('unit')}")
    print(f"  Updated: {data.get('metadata', {}).get('last_updated', '')[:19]}")
    print(f"{'='*70}")

    pnl = data.get("profit_loss", {})

    q = pnl.get("quarterly", {})
    print(f"\n📊 Quarterly P&L ({len(q)} periods):")
    for p in sorted(q.keys(), reverse=True)[:6]:
        f = q[p]
        rev = f.get("revenue_from_operations")
        np  = f.get("net_profit")
        ebm = f.get("ebitda_margin_pct")
        print(f"  {p:<12} | Rev: {_fmt(rev):>18} | NP: {_fmt(np):>16} | EBITDA: {_fmtp(ebm)}")

    y = pnl.get("yearly", {})
    print(f"\n📅 Annual P&L ({len(y)} years):")
    for p in sorted(y.keys(), reverse=True):
        f = y[p]
        print(f"  {p:<8} | Rev: {_fmt(f.get('revenue_from_operations')):>18} | NP: {_fmt(f.get('net_profit')):>16} | EBITDA: {_fmtp(f.get('ebitda_margin_pct'))}")

    bs = data.get("balance_sheet", {}).get("yearly", {})
    print(f"\n📋 Balance Sheet ({len(bs)} years):")
    for p in sorted(bs.keys(), reverse=True):
        f = bs[p]
        print(f"  {p:<8} | Assets: {_fmt(f.get('total_assets')):>16} | Equity: {_fmt(f.get('total_equity')):>16} | Debt: {_fmt(f.get('total_debt')):>14}")

    cf = data.get("cash_flow", {}).get("yearly", {})
    print(f"\n💰 Cash Flow ({len(cf)} years):")
    for p in sorted(cf.keys(), reverse=True):
        f = cf[p]
        print(f"  {p:<8} | CFO: {_fmt(f.get('cash_from_operations')):>18} | FCF: {_fmt(f.get('free_cash_flow')):>18}")

    ratios = data.get("ratios", {})
    if ratios:
        latest = sorted(ratios.keys(), reverse=True)[0]
        r = ratios[latest]
        print(f"\n📐 Key Ratios ({latest}):")
        print(f"  ROE: {_fmtp(r.get('roe_pct'))}  |  ROCE: {_fmtp(r.get('roce_pct'))}  |  D/E: {_fmtr(r.get('debt_to_equity'))}x")
        print(f"  Net Margin: {_fmtp(r.get('net_profit_margin_pct'))}  |  EBITDA Margin: {_fmtp(r.get('operating_margin_pct'))}  |  Int.Coverage: {_fmtr(r.get('interest_coverage'))}x")

    g = data.get("growth", {})
    print(f"\n📈 Growth:")
    print(f"  Revenue YoY: {_fmtp(g.get('revenue_yoy_pct'))}  |  Profit YoY: {_fmtp(g.get('profit_yoy_pct'))}")
    print(f"  Revenue 3yr CAGR: {_fmtp(g.get('revenue_cagr_3yr'))}  |  Profit 3yr CAGR: {_fmtp(g.get('profit_cagr_3yr'))}")

    insights = data.get("insights", {})
    if insights:
        print(f"\n💡 Insights:")
        print(f"  {insights.get('summary', '')}")
        for s in insights.get("strengths", [])[:3]:
            print(f"  ✅ {s}")
        for c in insights.get("concerns", [])[:3]:
            print(f"  ⚠️  {c}")

    val = data.get("metadata", {})
    status = "✓ PASSED" if val.get("validation_passed") else "✗ ISSUES"
    print(f"\n🔍 Validation: {status}")
    print(f"{'='*70}\n")


def _fmt(val: Optional[float]) -> str:
    if val is None:
        return "        N/A"
    if abs(val) >= 100000:
        return f"₹{val/100000:>7.2f} LCr"
    return f"₹{val:>9,.0f} Cr"

def _fmtp(val: Optional[float]) -> str:
    return f"{val:.1f}%" if val is not None else "N/A"

def _fmtr(val: Optional[float]) -> str:
    return f"{val:.2f}" if val is not None else "N/A"
