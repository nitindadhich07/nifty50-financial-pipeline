#!/usr/bin/env python3
"""
export_json.py  (REWRITTEN v3 - Production Schema)
--------------------------------------------------
Exports extracted data exactly as per the new rigorous schema.
"""

import json
import os
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List

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


def compute_ratios(pnl_yearly: Dict, balance_sheet: Dict, cash_flow: Dict) -> Dict:
    """Compute key financial ratios per FY."""
    ratios = {}
    for fy, pnl in pnl_yearly.items():
        bs = balance_sheet.get(fy, {})
        cf = cash_flow.get(fy, {})
        r  = {
            "gross_margin_pct": None,
            "operating_margin_pct": None,
            "net_profit_margin_pct": None,
            "roe_pct": None,
            "roce_pct": None,
            "roa_pct": None,
            "debt_to_equity": None,
            "interest_coverage": None,
            "asset_turnover": None,
            "current_ratio": None,
            "free_cash_flow_margin": None
        }

        sales  = pnl.get("revenue_from_operations")
        ebitda = pnl.get("ebitda")
        net_p  = pnl.get("net_profit")
        interest = pnl.get("interest")
        
        assets = bs.get("total_assets")
        eq     = bs.get("total_equity")
        debt   = bs.get("total_debt")
        curr_a = bs.get("total_current_assets")
        curr_l = bs.get("total_liabilities") # Approximate if exact current liabilities isn't strictly there
        fcf    = cf.get("free_cash_flow")

        if sales and net_p:
            r["net_profit_margin_pct"] = round((net_p / sales) * 100, 2)
        if sales and ebitda:
            r["operating_margin_pct"]  = round((ebitda / sales) * 100, 2)
        
        # Gross margin approx
        raw_mat = pnl.get("raw_material_cost", 0) or 0
        if sales and raw_mat:
            r["gross_margin_pct"] = round(((sales - raw_mat) / sales) * 100, 2)

        if net_p and eq and eq != 0:
            r["roe_pct"] = round((net_p / eq) * 100, 2)
        if net_p and assets and assets != 0:
            r["roa_pct"] = round((net_p / assets) * 100, 2)
        
        # ROCE approx (EBIT / Capital Employed)
        pbt = pnl.get("profit_before_tax")
        ebit = None
        if pbt is not None or interest is not None:
            ebit = (pbt or 0) + (interest or 0)

        cap_emp = (eq or 0) + (debt or 0)
        if ebit is not None and cap_emp and cap_emp != 0:
            r["roce_pct"] = round((ebit / cap_emp) * 100, 2)

        if debt is not None and eq and eq != 0:
            r["debt_to_equity"] = round(debt / eq, 2)
        
        if ebit and interest and interest != 0:
            r["interest_coverage"] = round(ebit / interest, 2)

        if sales and assets and assets != 0:
            r["asset_turnover"] = round(sales / assets, 2)
        
        # Approximate current ratio
        if curr_a and curr_l and curr_l != 0:
            r["current_ratio"] = round(curr_a / curr_l, 2)
        
        if fcf is not None and sales and sales != 0:
            r["free_cash_flow_margin"] = round((fcf / sales) * 100, 2)

        ratios[fy] = r

    return ratios


def build_output(
    symbol:         str,
    company_name:   str,
    quarterly_pnl:  Dict,
    annual_pnl:     Dict,
    balance_sheet:  Dict,
    cash_flow:      Dict,
    validation:     Optional[Dict] = None,
    data_sources:   Optional[List] = None,
) -> Dict:
    """Assemble the full output JSON using the new schema."""
    ratios = compute_ratios(annual_pnl, balance_sheet, cash_flow)

    output = {
        "company_info": {
            "ticker": symbol.upper(),
            "company_name": company_name,
            "exchange": "NSE/BSE",
            "sector": "Energy",       # Defaulted as requested, but ideally dynamic
            "industry": "Oil & Gas",  # Defaulted as requested
            "currency": "INR",
            "unit": "₹ Crores"
        },
        "profit_loss": {
            "quarterly": quarterly_pnl,
            "yearly": annual_pnl
        },
        "balance_sheet": {
            "yearly": balance_sheet
        },
        "cash_flow": {
            "yearly": cash_flow
        },
        "ratios": ratios,
        "growth": {
            "revenue_cagr_3yr": None,
            "revenue_cagr_5yr": None,
            "profit_cagr_3yr": None,
            "profit_cagr_5yr": None,
            "eps_cagr_3yr": None,
            "eps_cagr_5yr": None
        },
        "metadata": {
            "data_sources": data_sources or [],
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "parser_version": "v1.0",
            "validation_passed": validation.get("validation_passed", False) if validation else False,
            "validation_details": validation
        }
    }
    return _sanitize(output)


def save_json(data: Dict, output_path: str) -> str:
    """Save JSON to disk; returns path."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    kb = os.path.getsize(output_path) // 1024
    logger.info(f"JSON saved → {output_path} ({kb} KB)")
    return output_path


def print_summary(data: Dict):
    """Pretty-print extraction summary."""
    print(f"\n{'='*65}")
    info = data.get("company_info", {})
    print(f"  {info.get('company_name')} ({info.get('ticker')})")
    print(f"  Unit: {info.get('unit')}   |   Updated: {data.get('metadata', {}).get('last_updated', '')[:19]}")
    print(f"{'='*65}")

    pnl = data.get("profit_loss", {})

    q = pnl.get("quarterly", {})
    print(f"\n📊 Quarterly P&L ({len(q)} periods):")
    for p in sorted(q.keys(), reverse=True):
        f = q[p]
        print(f"  {p:12s} | Rev: {_fmt(f.get('revenue_from_operations')):>18} | NP: {_fmt(f.get('net_profit')):>15} | EBITDA: {_fmt_pct(f.get('ebitda_margin_pct'))}")

    y = pnl.get("yearly", {})
    print(f"\n📅 Annual P&L ({len(y)} years):")
    for p in sorted(y.keys(), reverse=True):
        f = y[p]
        print(f"  {p:8s} | Rev: {_fmt(f.get('revenue_from_operations')):>18} | NP: {_fmt(f.get('net_profit')):>15}")

    bs = data.get("balance_sheet", {}).get("yearly", {})
    print(f"\n📋 Balance Sheet ({len(bs)} years):")
    for p in sorted(bs.keys(), reverse=True):
        f = bs[p]
        print(f"  {p:8s} | Assets: {_fmt(f.get('total_assets')):>16} | Debt: {_fmt(f.get('total_debt')):>16}")

    cf = data.get("cash_flow", {}).get("yearly", {})
    print(f"\n💰 Cash Flow ({len(cf)} years):")
    for p in sorted(cf.keys(), reverse=True):
        f = cf[p]
        print(f"  {p:8s} | CFO: {_fmt(f.get('cash_from_operations')):>18} | FCF: {_fmt(f.get('free_cash_flow')):>18}")

    val = data.get("metadata", {})
    status = "✓ PASSED" if val.get("validation_passed") else "✗ ISSUES"
    print(f"\n🔍 Validation: {status}")

    print(f"\n{'='*65}\n")


def _fmt(val: Optional[float]) -> str:
    return f"₹{val:>9,.0f} Cr" if val is not None else "        N/A"

def _fmt_pct(val: Optional[float]) -> str:
    return f"{val:.1f}%" if val is not None else "N/A"
