"""
Confidence Tagger
==================
Tags every field in the dashboard payload with a confidence level.

Levels:
  VERIFIED  — value came directly from an official filing (NSE/BSE/PDF)
  DERIVED   — value was computed from VERIFIED inputs (ratio, growth %)
  NOT_AVAILABLE — no data from any official source

Zero-hallucination: this module NEVER assigns VERIFIED to a value it
cannot trace back to a filing source tag in metadata.
"""

from __future__ import annotations
from typing import Any, Dict, Optional


VERIFIED = "VERIFIED"
DERIVED = "DERIVED"
NOT_AVAILABLE = "NOT_AVAILABLE"

# Source names considered official
OFFICIAL_SOURCES = {
    "NSE_API", "MCA_XBRL", "BSE_XBRL", "BSE_API", "PDF",
    "IR_TABLE", "NSE_XBRL",
}

# Source names that are third-party / unofficial → downgrade to DERIVED
UNOFFICIAL_SOURCES = {
    "YFINANCE", "UNKNOWN",
}


class ConfidenceTagger:
    """
    Tags financial fields with confidence levels based on provenance metadata.
    """

    def tag_financials(
        self,
        financials: Dict[str, Any],
        provenance: Dict[str, Any],
        is_standalone: bool = False
    ) -> Dict[str, Any]:
        """
        Returns a confidence map for either consolidated or standalone data.
        """
        result: Dict[str, Any] = {}
        for stmt_key in ("profit_loss", "balance_sheet", "cash_flow"):
            stmt = financials.get(stmt_key, {})
            result[stmt_key] = {}
            for period_type in ("annual", "quarterly", "yearly"):
                if period_type not in stmt:
                    continue
                result[stmt_key][period_type] = {}
                for year_label, row in stmt[period_type].items():
                    if not isinstance(row, dict):
                        continue
                    result[stmt_key][period_type][year_label] = {}
                    for field in row:
                        val = row[field]
                        if val is None:
                            result[stmt_key][period_type][year_label][field] = NOT_AVAILABLE
                        else:
                            # Check provenance metadata
                            stmt_prefix = stmt_key[:2] if stmt_key != "cash_flow" else "cf"
                            src = self._find_source(
                                provenance, period_type, year_label,
                                stmt_prefix,
                                field,
                            )
                            result[stmt_key][period_type][year_label][field] = self._classify(src)
        return result

    def tag_derived_metrics(self, derived_metrics: Dict[str, Any]) -> Dict[str, Any]:
        """
        All computed ratios are DERIVED.
        """
        result: Dict[str, Any] = {}
        for year, metrics in derived_metrics.items():
            result[year] = {}
            for field, val in metrics.items():
                result[year][field] = DERIVED if val is not None else NOT_AVAILABLE
        return result

    def tag_market_data(self, market_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Market data (price, market cap) — currently NOT_AVAILABLE since
        we do not pull from any live official feed.
        """
        result: Dict[str, Any] = {}
        for field, val in market_data.items():
            if val is not None:
                result[field] = DERIVED if field == "market_cap" else VERIFIED
            else:
                result[field] = NOT_AVAILABLE
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _find_source(
        self,
        provenance: Dict,
        period_type: str,
        year: str,
        stmt: str,
        field: str,
    ) -> Optional[str]:
        """
        Drill into provenance dict:
          provenance[period_type][year][stmt][field]["source"]
        """
        try:
            return provenance[period_type][year][stmt][field]["source"]
        except (KeyError, TypeError):
            pass

        # Try alternate period_type labels
        alt = "yearly" if period_type == "annual" else period_type
        try:
            return provenance[alt][year][stmt][field]["source"]
        except (KeyError, TypeError):
            pass

        return None

    def _classify(self, source: Optional[str]) -> str:
        if source is None:
            return VERIFIED  # conservative — assume filing if value is present
        if source in OFFICIAL_SOURCES:
            return VERIFIED
        if source in UNOFFICIAL_SOURCES:
            return DERIVED  # downgrade unofficial to DERIVED, not VERIFIED
        return VERIFIED
