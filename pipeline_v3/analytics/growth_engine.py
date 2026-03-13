from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class GrowthEngine:
    """
    Computes simple growth metrics (YoY) from extracted financials.
    Output is best-effort and only computed when both base and prior values exist.
    """

    def compute_yoy(self, series: Dict[str, Any], field: str) -> Dict[str, float]:
        """
        series: {"FY2025": ProfitLoss|dict, "FY2024": ...}
        returns: {"FY2025": yoy_pct, ...} (no value for first year)
        """
        years = sorted(series.keys(), key=self._fy_sort_key, reverse=True)
        out: Dict[str, float] = {}
        for i in range(len(years) - 1):
            y = years[i]
            y_prev = years[i + 1]
            v = self._get(series.get(y), field)
            v_prev = self._get(series.get(y_prev), field)
            if v is None or v_prev is None:
                continue
            if v_prev == 0:
                continue
            out[y] = round(((v - v_prev) / abs(v_prev)) * 100.0, 2)
        return out

    def _get(self, obj: Any, field: str) -> Optional[float]:
        if obj is None:
            return None
        if isinstance(obj, dict):
            v = obj.get(field)
        else:
            v = getattr(obj, field, None)
        try:
            return float(v) if v is not None else None
        except Exception:
            return None

    def _fy_sort_key(self, fy: str) -> int:
        m = re.search(r"(\d{4})", fy)
        return int(m.group(1)) if m else 0

