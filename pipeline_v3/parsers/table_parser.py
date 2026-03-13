import logging
import re
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)


class HTMLTableParser:
    """
    Parses investor-relations HTML tables (pandas DataFrames) into canonical statement dicts.
    Best-effort: IR tables vary heavily across companies.
    """

    _YEAR_RE = re.compile(r"(20\d{2})")

    def __init__(self, synonym_map: Dict[str, List[str]]):
        self.synonyms = synonym_map

    def parse_tables(self, tables: List[pd.DataFrame]) -> Dict[str, Dict[str, Dict[str, float]]]:
        """
        Returns: {"FY2025": {"pl": {...}}, "FY2024": {"bs": {...}}, ...}
        """
        out: Dict[str, Dict[str, Dict[str, float]]] = {}
        for df in tables:
            stmt = self._classify_df(df)
            if not stmt:
                continue
            years, numeric_cols = self._detect_year_columns(df)
            if not years or not numeric_cols:
                continue

            # First column is assumed to be the line item label.
            for idx, row in df.iterrows():
                label = str(row.iloc[0]) if len(row) > 0 else ""
                field = self._match_label(label)
                if not field:
                    continue
                for y, col in zip(years, numeric_cols):
                    v = self._parse_number(row.get(col))
                    if v is None:
                        continue
                    out.setdefault(y, {}).setdefault(stmt, {})
                    # Prefer first seen value per field; higher-tier merge decides conflicts later.
                    out[y][stmt].setdefault(field, v)
        return out

    def _classify_df(self, df: pd.DataFrame) -> Optional[str]:
        sample = (str(list(df.columns)) + " " + str(df.head(10))).lower()
        if any(k in sample for k in ["profit", "loss", "revenue", "income", "expenses", "eps"]):
            return "pl"
        if any(k in sample for k in ["balance sheet", "assets", "liabilities", "equity", "borrowings"]):
            return "bs"
        if any(k in sample for k in ["cash flow", "operating activities", "investing activities", "financing activities"]):
            return "cf"
        return None

    def _detect_year_columns(self, df: pd.DataFrame) -> Tuple[List[str], List[Any]]:
        years: List[str] = []
        cols: List[Any] = []
        for c in df.columns[1:]:
            s = str(c)
            m = self._YEAR_RE.search(s)
            if not m:
                continue
            year = int(m.group(1))
            # Assume FY ends in Mar of that calendar year: label FY{year}
            years.append(f"FY{year}")
            cols.append(c)
        # If headers do not have years, attempt to infer from first row.
        if not years:
            first_row = " ".join([str(x) for x in df.iloc[0].tolist()]) if len(df) else ""
            found = sorted(set(int(y) for y in self._YEAR_RE.findall(first_row)), reverse=True)
            for y in found:
                years.append(f"FY{y}")
            # Guess numeric cols are last N columns.
            if years and len(df.columns) >= 1 + len(years):
                cols = list(df.columns[-len(years) :])

        return years, cols

    def _match_label(self, label: str) -> Optional[str]:
        if not label:
            return None
        clean = label.lower().strip()
        clean = re.sub(r"\s+", " ", clean)
        clean = re.sub(r"[\*†‡#•]", "", clean).rstrip(":.,- ")
        for field, syns in self.synonyms.items():
            for s in syns:
                target = s.lower()
                if clean == target or clean.startswith(target + " ") or (" " + target + " " in " " + clean + " "):
                    return field
        return None

    def _parse_number(self, v: Any) -> Optional[float]:
        if v is None:
            return None
        s = str(v).strip()
        if not s or s.lower() in {"nan", "none", "null", "-"}:
            return None
        neg = s.startswith("(") and s.endswith(")")
        s = s.strip("()")
        s = re.sub(r"[,\s]", "", s)
        # Remove stray currency symbols / footnotes.
        s = re.sub(r"[^0-9\.\-]", "", s)
        try:
            val = float(s)
            return -val if neg else val
        except Exception:
            return None

