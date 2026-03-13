import logging
import requests
import time
from pathlib import Path
from typing import Dict, Any, Optional
from .http_client import HTTPClient

logger = logging.getLogger(__name__)

NSE_HOME = "https://www.nseindia.com"
NSE_XBRL_URL = "https://www.nseindia.com/api/results-comparision"
NSE_CORP_FILINGS_URL = "https://www.nseindia.com/api/corporate-announcements"

class NSEAPIClient:
    """Client for NSE Financial APIs."""
    
    def __init__(self, session=None):
        self.session = session or self._init_session()
        self.http = HTTPClient(session=self.session, timeout_s=20.0, max_retries=2, backoff_s=1.0, min_interval_s=0.2)

    def _init_session(self):
        s = requests.Session()
        s.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
        })
        # Handshake
        try:
            s.get(NSE_HOME, timeout=10)
            time.sleep(1)
            # Second hit to get more cookies
            s.get(f"{NSE_HOME}/get-quotes/equity?symbol=RELIANCE", timeout=10)
        except Exception as e:
            logger.warning(f"NSE Handshake failed: {e}")
        return s

    def fetch_results(self, symbol: str, period: str = "Quarterly") -> Dict[str, Any]:
        """Fetches results comparison from NSE."""
        params = {
            "index": "equities",
            "symbol": symbol.upper(),
            "period": period
        }
        data, err = self.http.get_json(NSE_XBRL_URL, params=params)
        if err:
            logger.warning(f"NSE results-comparision failed ({symbol}): {err}")
            return {}
        return data or {}

    def fetch_corporate_filings(
        self,
        *,
        symbol: str,
        from_date: str,
        to_date: str,
        index: str = "equities",
    ) -> Dict[str, Any]:
        """
        Corporate announcements (includes financial results, annual reports, presentations).
        Dates expected by NSE are typically DD-MM-YYYY.
        """
        params = {
            "index": index,
            "symbol": symbol.upper(),
            "from_date": from_date,
            "to_date": to_date,
        }
        data, err = self.http.get_json(NSE_CORP_FILINGS_URL, params=params)
        if err:
            logger.warning(f"NSE corporate-announcements failed ({symbol}): {err}")
            return {}
        return data or {}
