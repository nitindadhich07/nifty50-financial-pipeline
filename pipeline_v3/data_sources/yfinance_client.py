import logging
from typing import Any, Dict
import yfinance as yf

logger = logging.getLogger(__name__)

class YFinanceClient:
    """Fetches high-quality institutional balance sheet, P&L, and cash flow data."""

    def fetch_financials(self, symbol: str) -> Dict[str, Any]:
        yf_symbol = f"{symbol.upper()}.NS"
        logger.info(f"Fetching from Yahoo Finance: {yf_symbol}")
        try:
            ticker = yf.Ticker(yf_symbol)
            return {
                "income_stmt": ticker.income_stmt,
                "balance_sheet": ticker.balance_sheet,
                "cash_flow": ticker.cash_flow,
                "quarterly_income_stmt": ticker.quarterly_income_stmt,
                "quarterly_balance_sheet": ticker.quarterly_balance_sheet,
                "quarterly_cash_flow": ticker.quarterly_cash_flow,
                "info": ticker.info
            }
        except Exception as e:
            logger.warning(f"Failed to fetch {yf_symbol} via yfinance: {e}")
            return {}
