import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import requests

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HTTPResponse:
    status_code: int
    url: str
    headers: Dict[str, str]
    text: str


class HTTPClient:
    """
    Small wrapper around requests.Session with retries and polite throttling.
    Keep this dependency-light so unit tests can mock it easily.
    """

    def __init__(
        self,
        session: Optional[requests.Session] = None,
        timeout_s: float = 20.0,
        max_retries: int = 2,
        backoff_s: float = 1.0,
        min_interval_s: float = 0.0,
    ):
        self.session = session or requests.Session()
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.backoff_s = backoff_s
        self.min_interval_s = min_interval_s
        self._last_request_ts: float = 0.0

    def _throttle(self) -> None:
        if self.min_interval_s <= 0:
            return
        now = time.time()
        sleep_s = (self._last_request_ts + self.min_interval_s) - now
        if sleep_s > 0:
            time.sleep(sleep_s)
        self._last_request_ts = time.time()

    def get(
        self,
        url: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> HTTPResponse:
        last_err: Optional[BaseException] = None
        for attempt in range(self.max_retries + 1):
            try:
                self._throttle()
                r = self.session.get(url, params=params, headers=headers, timeout=self.timeout_s)
                return HTTPResponse(
                    status_code=r.status_code,
                    url=str(r.url),
                    headers=dict(r.headers),
                    text=r.text or "",
                )
            except Exception as e:
                last_err = e
                if attempt >= self.max_retries:
                    break
                time.sleep(self.backoff_s * (2**attempt))
        raise RuntimeError(f"GET failed for {url}: {last_err}") from last_err

    def get_json(
        self,
        url: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        try:
            resp = self.get(url, params=params, headers=headers)
            if resp.status_code != 200:
                return None, f"HTTP {resp.status_code}"
            try:
                return requests.models.complexjson.loads(resp.text), None
            except Exception:
                # Some endpoints return non-JSON error pages
                return None, "Invalid JSON"
        except Exception as e:
            return None, str(e)

