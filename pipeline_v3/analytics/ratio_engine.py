import logging
from typing import Dict, Optional, Any

logger = logging.getLogger(__name__)

class RatioEngine:
    """Computes financial ratios with institutional guardrails (Rule 5)."""
    
    def compute_all(self, pl: Any, bs: Any, cf: Any) -> Dict[str, float]:
        ratios = {}
        
        # Helper to get numeric values safely from dataclasses or dicts
        def g(obj, k):
            if obj is None: return None
            return getattr(obj, k, None) if not isinstance(obj, dict) else obj.get(k)

        rev = g(pl, 'revenue_from_operations')
        net_profit = g(pl, 'net_profit')
        pbt = g(pl, 'profit_before_tax')
        ebitda = g(pl, 'ebitda')
        ebit = g(pl, 'ebit')
        equity = g(bs, 'total_equity')
        assets = g(bs, 'total_assets')
        debt = g(bs, 'total_debt')
        current_assets = g(bs, 'current_assets')
        current_liabilities = g(bs, 'current_liabilities')
        cfo = g(cf, 'cash_from_operations')
        fcf = g(cf, 'free_cash_flow')
        shares = g(bs, 'shares_outstanding')

        # 1. Profitability
        if rev and net_profit:
            ratios['net_profit_margin'] = round((net_profit / rev) * 100, 2)
        
        if rev and ebitda:
            ratios['operating_margin'] = round((ebitda / rev) * 100, 2)
            
        if equity and net_profit:
            roe = round((net_profit / equity) * 100, 2)
            # Rule 5: ROE Guardrail
            if roe > 100:
                logger.warning(f"⚠️ Extreme ROE detected ({roe}%). Flagging equity extraction error.")
                ratios['roe_anomaly'] = roe
            else:
                ratios['roe'] = roe
            
        if assets and net_profit:
            ratios['roa'] = round((net_profit / assets) * 100, 2)

        # ROCE = EBIT / (Total Assets - Current Liabilities) (best-effort).
        if ebit is not None and assets is not None and current_liabilities is not None:
            capital_employed = assets - current_liabilities
            if capital_employed:
                ratios["roce"] = round((ebit / capital_employed) * 100, 2)

        # 2. Leverage
        if equity and debt is not None:
            ratios['debt_to_equity'] = round(debt / (equity or 1), 2)
            
        if assets and debt is not None:
            ratios['debt_to_assets'] = round(debt / assets, 2)

        # 3. Liquidity
        if current_assets is not None and current_liabilities:
            ratios["current_ratio"] = round(current_assets / (current_liabilities or 1), 2)

        # 4. Cash Flow
        if rev and fcf is not None:
            ratios["free_cash_flow_margin"] = round((fcf / rev) * 100, 2)

        # CFO to net profit
        if equity and shares:
            ratios['book_value'] = round(equity / shares, 2)
            
        if net_profit and cfo:
            ratios['cfo_to_net_profit'] = round(cfo / net_profit, 2)

        return ratios
