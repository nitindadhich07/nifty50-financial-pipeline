import logging
import time
from typing import Any, Dict, Optional

import requests

from .http_client import HTTPClient

logger = logging.getLogger(__name__)


class BSEAPIClient:
    """
    Client for BSE corporate filings and (when available) structured financial results.

    Notes:
    - BSE endpoints and parameters can change; keep all URLs and params centralized.
    - This client is designed to be resilient and return empty dicts on failure.
    """

    BSE_HOME = "https://www.bseindia.com"
    ANN_URL = "https://api.bseindia.com/BseOnlineAPI/api/AnnSubCategoryGetData/w"
    ATTACH_BASE = "https://www.bseindia.com/xml-data/corpfiling/AttachLive/"

    def __init__(self, session: Optional[requests.Session] = None):
        s = session or self._init_session()
        self.http = HTTPClient(session=s, timeout_s=20.0, max_retries=2, backoff_s=1.0)

    def _init_session(self) -> requests.Session:
        s = requests.Session()
        s.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/122.0.0.0 Safari/537.36"
                ),
                "Accept": "*/*",
                "Accept-Language": "en-US,en;q=0.9",
                "Origin": self.BSE_HOME,
                "Referer": self.BSE_HOME + "/",
            }
        )
        try:
            # Warm cookies
            s.get(self.BSE_HOME, timeout=10)
            time.sleep(0.5)
        except Exception as e:
            logger.warning(f"BSE handshake failed: {e}")
        return s

    def fetch_announcements(
        self,
        *,
        scrip_cd: str,
        from_date: str,
        to_date: str,
        category: Optional[str] = None,
        subcategory: Optional[str] = None,
        page_no: int = 1,
        page_size: int = 50,
    ) -> Dict[str, Any]:
        """
        Fetch corporate announcements metadata.

        Typical usage:
        - Annual reports: category="30" (BSE uses category ids)
        - Financial results: depends on BSE taxonomy; often discover via text filters.
        """
        params: Dict[str, Any] = {
            "pageno": page_no,
            "pagesize": page_size,
            "strScrip": scrip_cd,
            "strFromDate": from_date,
            "strToDate": to_date,
        }
        if category:
            params["strCat"] = category
        if subcategory:
            params["strSubCat"] = subcategory

        data, err = self.http.get_json(self.ANN_URL, params=params)
        if err:
            logger.warning(f"BSE announcements fetch failed ({scrip_cd}): {err}")
            return {}
        return data or {}

    @classmethod
    def attachment_url(cls, path_or_name: str) -> str:
        if not path_or_name:
            return ""
        p = path_or_name.strip()
        if p.startswith("http"):
            return p
        return cls.ATTACH_BASE + p.lstrip("/")
