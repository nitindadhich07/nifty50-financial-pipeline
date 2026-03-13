import logging
from typing import Dict, List, Any

logger = logging.getLogger(__name__)

class FinancialValidator:
    """Validates financial data for institutional-grade accuracy."""
    
    def validate(self, financials: Dict[str, Any]) -> List[str]:
        anomalies = []
        
        # Helper to get numeric values safely
        def g(d, k): return d.get(k) if isinstance(d, dict) else getattr(d, k, None)

        # 1) Balance sheet identity check
        bs_struct = financials.get('balance_sheet', {})
        for period in ["annual", "quarterly"]:
            data_map = bs_struct.get(period, {})
            for year, data in data_map.items():
                assets = g(data, 'total_assets')
                equity = g(data, 'total_equity')
                liabs = g(data, 'total_liabilities')
                
                if assets and equity and liabs:
                    # RIL Fact: Often 'Total Liabilities' includes Equity in the label
                    # But we check for both interpretations
                    diff1 = abs(assets - (equity + liabs))
                    diff2 = abs(assets - liabs)
                    
                    if diff1 > 10.0 and diff2 > 10.0: # 10 Cr tolerance for rounding/crore-lakh mix
                        anomalies.append(f"[{period.upper()} {year}] BS Mismatch: Assets {assets} != Eq+Liab {equity+liabs}")

        # 2) P&L sanity checks
        pl_struct = financials.get('profit_loss', {})
        for period in ["annual", "quarterly"]:
            data_map = pl_struct.get(period, {})
            for year, data in data_map.items():
                rev = g(data, 'revenue_from_operations')
                net = g(data, 'net_profit')
                if rev and net and net > rev:
                    anomalies.append(f"[{period.upper()} {year}] P&L Anomaly: Net Profit {net} > Revenue {rev}")

                ebitda = g(data, "ebitda")
                pbt = g(data, "profit_before_tax")
                interest = g(data, "interest")
                depr = g(data, "depreciation")
                if ebitda is not None and pbt is not None and interest is not None and depr is not None:
                    # EBITDA ~ PBT + interest + depreciation (best-effort)
                    est = (pbt or 0) + (interest or 0) + (depr or 0)
                    if abs(ebitda - est) > max(25.0, 0.05 * (abs(est) or 1.0)):
                        anomalies.append(f"[{period.upper()} {year}] EBITDA mismatch: EBITDA {ebitda} vs PBT+Int+Depr {round(est,2)}")

        # 3) Cash flow reconciliation (best-effort)
        cf_struct = financials.get("cash_flow", {})
        for period in ["annual", "quarterly"]:
            data_map = cf_struct.get(period, {})
            for year, data in data_map.items():
                cfo = g(data, "cash_from_operations")
                cfi = g(data, "cash_from_investing")
                cff = g(data, "cash_from_financing")
                net_cf = g(data, "net_cash_flow")
                if cfo is not None and cfi is not None and cff is not None and net_cf is not None:
                    est = (cfo or 0) + (cfi or 0) + (cff or 0)
                    if abs(net_cf - est) > max(25.0, 0.05 * (abs(est) or 1.0)):
                        anomalies.append(f"[{period.upper()} {year}] Cash flow mismatch: Net {net_cf} vs CFO+CFI+CFF {round(est,2)}")

                capex = g(data, "capital_expenditure")
                fcf = g(data, "free_cash_flow")
                if cfo is not None and capex is not None and fcf is not None:
                    est_fcf = (cfo or 0) - abs(capex or 0)
                    if abs(fcf - est_fcf) > max(10.0, 0.05 * (abs(est_fcf) or 1.0)):
                        anomalies.append(f"[{period.upper()} {year}] FCF mismatch: FCF {fcf} vs CFO-Capex {round(est_fcf,2)}")

        # 4) Extreme revenue YoY growth (annual)
        annual_pl = pl_struct.get("annual", {}) if isinstance(pl_struct, dict) else {}
        years_sorted = sorted(annual_pl.keys(), reverse=True)
        for i in range(len(years_sorted) - 1):
            y = years_sorted[i]
            yp = years_sorted[i + 1]
            rev = g(annual_pl.get(y), "revenue_from_operations")
            revp = g(annual_pl.get(yp), "revenue_from_operations")
            if rev is None or revp is None or revp == 0:
                continue
            yoy = ((rev - revp) / abs(revp)) * 100.0
            if abs(yoy) > 200.0:
                anomalies.append(f"[ANNUAL {y}] Extreme revenue YoY: {round(yoy,2)}% (rev {rev} vs {revp})")

        return anomalies
