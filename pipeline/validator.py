#!/usr/bin/env python3
"""
validator.py  (REWRITTEN v4 - Strict Pro-Grade Validation)
---------------------------------------------------------
Validates exact compliance with the production financial schema.
Rules are extremely strict (1% tolerance).
"""

import logging
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)

# Very strict tolerance for BS and CF
THRESHOLD_PCT = 1.0


def _pct_diff(reported: float, computed: float) -> float:
    if computed == 0:
        return float("inf") if reported != 0 else 0.0
    return abs((reported - computed) / computed) * 100


def _flag(ok: bool, msg: str, issues: List[str], period: str):
    if not ok:
        full = f"[{period}] {msg}"
        logger.warning(f"  ✗ VALIDATION: {full}")
        issues.append(full)


def validate_pnl_period(period: str, f: Dict) -> Dict:
    """Validate a single P&L period structure."""
    issues = []
    
    sales  = f.get("revenue_from_operations")
    ebitda = f.get("ebitda")
    net    = f.get("net_profit")

    if sales is None or sales == 0:
        _flag(False, "Missing or Zero Revenue from Operations", issues, period)
    
    if sales is not None and ebitda is not None:
        _flag(sales >= ebitda, f"Revenue ({sales}) < EBITDA ({ebitda})", issues, period)
        
    if sales is not None and net is not None:
        _flag(sales >= net, f"Revenue ({sales}) < Net Profit ({net})", issues, period)

    valid = len(issues) == 0
    if valid:
        logger.info(f"  ✓ P&L [{period}]: OK")
    return {"valid": valid, "issues": issues}


def validate_balance_sheet(fy: str, f: Dict) -> Dict:
    """Validate balance sheet period (Strict 1% and Sum checks)."""
    issues = []

    assets = f.get("total_assets")
    liabs  = f.get("total_liabilities")
    eq     = f.get("total_equity")
    
    ca = f.get("total_current_assets")
    nca = f.get("total_non_current_assets")
    
    # Sub-components sum check (Current Assets)
    ca_sum = sum(filter(None, [
        f.get("cash_equivalents", 0),
        f.get("short_term_investments", 0),
        f.get("inventory", 0),
        f.get("receivables", 0),
        f.get("other_current_assets", 0)
    ]))
    
    if ca is not None and ca > 0:
        diff = _pct_diff(ca, ca_sum)
        _flag(diff <= THRESHOLD_PCT, f"Current Assets Sum mismatch: reported={ca}, components_sum={ca_sum}, diff={diff:.1f}%", issues, fy)

    # Assets = Current + Non-Current
    if assets is not None and ca is not None and nca is not None:
        computed_a = ca + nca
        diff = _pct_diff(assets, computed_a)
        _flag(diff <= THRESHOLD_PCT, f"Total Assets mismatch: reported={assets}, ca+nca={computed_a}, diff={diff:.1f}%", issues, fy)

    # Assets = Equity + Liabilities (< 1%)
    if assets is not None and liabs is not None and eq is not None:
        computed_bc = eq + liabs
        diff = _pct_diff(assets, computed_bc)
        _flag(
            diff <= THRESHOLD_PCT,
            f"BS Equation: Assets={assets}, Eq+Liab={computed_bc:.2f}, diff={diff:.1f}%",
            issues,
            fy,
        )

    # Sanity: Minority Interest shouldn't be larger than Total Assets
    mi = f.get("minority_interest")
    if mi is not None and assets is not None:
        _flag(mi < assets, f"Minority Interest ({mi}) is larger than Total Assets ({assets})", issues, fy)

    valid = len(issues) == 0
    if valid:
        logger.info(f"  ✓ BS [{fy}]: OK")
    return {"valid": valid, "issues": issues}


def validate_cash_flow(fy: str, f: Dict) -> Dict:
    """Validate cash flow period (Strict 1%)."""
    issues = []
    cfo = f.get("cash_from_operations")
    cfi = f.get("cash_from_investing")
    cff = f.get("cash_from_financing")
    net = f.get("net_cash_flow")
    
    capex = f.get("capital_expenditure")
    if capex is not None:
        # Sanity: Capex shouldn't be millions of crores for RIL
        _flag(abs(capex) < 500000, f"Abnormal Capital Expenditure detected: {capex}", issues, fy)

    if cfo is not None and cfi is not None and cff is not None and net is not None:
        computed = cfo + cfi + cff
        diff = _pct_diff(net, computed)
        _flag(
            diff <= THRESHOLD_PCT, 
            f"CF Balance: reported_net={net}, cfo+cfi+cff={computed:.2f}, diff={diff:.1f}%",
            issues,
            fy,
        )

    valid = len(issues) == 0
    if valid:
        logger.info(f"  ✓ CF [{fy}]: OK")
    return {"valid": valid, "issues": issues}


def validate_all(company_data: Dict) -> Dict:
    """Run all validations and return full report."""
    report: Dict[str, Any] = {
        "company":           company_data.get("company_info", {}).get("ticker"),
        "validation_passed": True,
        "period_validity":   {},
        "issues":            {},
    }

    pnl_q = company_data.get("profit_loss", {}).get("quarterly", {})
    pnl_y = company_data.get("profit_loss", {}).get("yearly", {})

    # Cross-period sanity rules:
    for q_period, q_fields in pnl_q.items():
        q_sales = q_fields.get("revenue_from_operations")
        
        # Check against FY Annual
        fy_fields = pnl_y.get("FY2025", {})
        fy_sales = fy_fields.get("revenue_from_operations")
        
        q_issues = []
        if q_sales and fy_sales:
            # Rule: Single quarter should be roughly 20-35% of annual, definitely not >= annual
            if q_sales >= fy_sales:
                _flag(False, f"Quarter revenue ({q_sales}) >= Annual ({fy_sales}) - check Standalone vs Consolidated", q_issues, f"cross_check_{q_period}")
            elif q_sales < fy_sales * 0.10:
                _flag(False, f"Quarter revenue ({q_sales}) is < 10% of Annual ({fy_sales})", q_issues, f"cross_check_{q_period}")
                
        if q_issues:
            report["issues"][f"cross_check_{q_period}"] = q_issues
            report["validation_passed"] = False

    # Quarterly P&L format
    for period, fields in pnl_q.items():
        r = validate_pnl_period(period, fields)
        report["period_validity"][f"quarterly_{period}"] = r["valid"]
        if r["issues"]:
            report["issues"][f"quarterly_{period}"] = r["issues"]
            report["validation_passed"] = False

    # Annual P&L format
    for fy, fields in pnl_y.items():
        r = validate_pnl_period(fy, fields)
        report["period_validity"][f"annual_{fy}"] = r["valid"]
        if r["issues"]:
            report["issues"][f"annual_{fy}"] = r["issues"]
            report["validation_passed"] = False

    # Balance Sheet
    bs = company_data.get("balance_sheet", {}).get("yearly", {})
    for fy, fields in bs.items():
        r = validate_balance_sheet(fy, fields)
        report["period_validity"][f"bs_{fy}"] = r["valid"]
        if r["issues"]:
            report["issues"][f"bs_{fy}"] = r["issues"]
            report["validation_passed"] = False

    # Cash Flow
    cf = company_data.get("cash_flow", {}).get("yearly", {})
    for fy, fields in cf.items():
        r = validate_cash_flow(fy, fields)
        report["period_validity"][f"cf_{fy}"] = r["valid"]
        if r["issues"]:
            report["issues"][f"cf_{fy}"] = r["issues"]
            report["validation_passed"] = False

    status = "✓ PASSED" if report["validation_passed"] else "✗ ISSUES FOUND"
    logger.info(f"\nVALIDATION SUMMARY: {status} | {len(report['issues'])} issue categories")
    return report
