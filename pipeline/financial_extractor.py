#!/usr/bin/env python3
"""
financial_extractor.py
----------------------
Pattern-based extractor for annual/quarterly reports from PDFs.
Processes raw text + structured tables to find core metrics.
"""

import re
import logging
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple

logger = logging.getLogger(__name__)

# ── Utility: helper to clean financial numbers ──────────────────────────────

def _to_float(s: str) -> Optional[float]:
    """Convert standard financial strings (1,234.56 or (123)) to float."""
    if not s or s.strip() in {"-", "—", "", "N.A.", "NA"}:
        return None
    
    s = s.strip()
    negative = False
    if s.startswith("(") and s.endswith(")"):
        s = s[1:-1]
        negative = True
    elif s.startswith("-"):
        s = s[1:]
        negative = True

    # Remove commas and spaces
    s_cleaned = re.sub(r"[,\s]", "", s)
    # Normalize minus signs
    s_cleaned = s_cleaned.replace("−", "-").replace("–", "-").replace("\u2212", "-")

    # Match the first sequence of digits/dots
    m = re.search(r"(-?\d+\.?\d*)", s_cleaned)
    if not m:
        return None

    try:
        val = float(m.group(1))
        return -val if negative else val
    except ValueError:
        return None


def _extract_row_numbers(line: str, next_line: str = "") -> List[Optional[float]]:
    """Extract column values from a financial table row."""
    # Pattern to match numbers like 1,23,456.78 or (12,345)
    # Support Indian numbering (e.g. 1,23,456) and US (1,234,567)
    num_pat = r"\(?\b\d{1,3}(?:,\d{2,3})*(?:\.\d+)?\b\)?"

    def _parse_string_for_nums(l: str) -> List[Optional[float]]:
        # Clean potential noise like Rs. or ₹ or Note refs
        l = l.replace("\u20b9", "").replace("Rs.", "").replace("INR", "")
        parts = re.findall(num_pat, l)
        res = []
        for p in parts:
            v = _to_float(p)
            if v is not None:
                res.append(v)
        return res

    floats = _parse_string_for_nums(line)
    
    # If the current line has very few numbers but the next line has several (common in multi-line labels)
    if len(floats) < 2 and next_line:
        extra = _parse_string_for_nums(next_line)
        if len(extra) >= 2:
            floats = extra

    return floats


# ── Pattern Definitions ────────────────────────────────────────────────────────

BS_PATTERNS: Dict[str, List[str]] = {
    "equity_share_capital": [r"^equity share capital\b", r"^share capital\b"],
    "reserves": [r"^other equity\b", r"^reserves and surplus\b"],
    "minority_interest": [r"non.controlling interest", r"minority interest"],
    "long_term_borrowings": [
        r"non.current borrowings",
        r"long.term borrowings",
        r"^borrowings.*non.current\b",
        r"^borrowings\s*\d*\s*$", # strictly to catch main table head
    ],
    "short_term_borrowings": [
        r"^current.*borrowings\b",
        r"^short.term borrowings\b",
        r"^borrowings.*current\b",
    ],
    "lease_liabilities": [r"lease liabilities"],
    "deferred_payment_liabilities": [r"deferred payment liabilities"],
    "trade_payables":    [r"trade payables"],
    "other_liabilities": [r"other liabilities", r"other current liabilities"],
    "total_liabilities": [
        r"total liabilities\b",
        r"total equity and liabilities\b",
    ],
    "cash_equivalents": [
        r"cash and cash equivalents\b",
        r"cash.*cash equivalents\b",
        r"balances with banks",
    ],
    "ppe": [
        r"property, plant and equipment\b",
        r"property.*plant.*equipment\b",
        r"fixed assets\b",
        r"tangible assets\b",
    ],
    "spectrum": [r"spectrum\b", r"spectrum.*intangible"],
    "capital_work_in_progress": [r"capital work.in.progress", r"cwip\b"],
    "goodwill":   [r"goodwill\b"],
    "intangibles": [r"other intangible assets", r"intangible assets\b"],
    "long_term_investments": [r"non.current investments\b", r"investments.*non.current"],
    "total_current_assets":    [r"total current assets\b"],
    "total_non_current_assets": [r"total non.current assets\b"],
    "total_assets": [
        r"total assets\b",
        r"^total equity and liabilities\b", 
    ],
}

CF_PATTERNS: Dict[str, List[str]] = {
    "cash_from_operations": [
        r"net cash (?:generated from|from) operating",
        r"net cash.*operating activities",
        r"cash.*from.*operating activities",
    ],
    "capital_expenditure": [
        r"purchase of property",
        r"purchase.*property.*plant",
        r"expenditure for property",
        r"capital expenditure",
    ],
    "cash_from_investing": [r"net cash.*from investing", r"net cash.*investing activities"],
    "cash_from_financing": [r"net cash.*from financing", r"net cash.*financing activities"],
    "net_cash_flow": [r"net increase.*decrease.*cash", r"net change in cash"],
    "opening_cash":  [r"opening balance of cash", r"cash.*beginning of"],
    "closing_cash":  [r"closing balance of cash", r"cash.*end of"],
}


# ── Extractor Implementation ──────────────────────────────────────────────────

class FinancialExtractor:
    """Extracts financial metrics with robust column alignment for reliance-style reports."""

    def extract_balance_sheet(self, parse_result: Dict) -> Dict[str, Dict]:
        return self._extract_section(parse_result, BS_PATTERNS, "BS")

    def extract_cash_flow(self, parse_result: Dict) -> Dict[str, Dict]:
        return self._extract_section(parse_result, CF_PATTERNS, "CF")

    def _extract_section(self, parse_result: Dict, patterns: Dict, type_label: str) -> Dict[str, Dict]:
        # First, scan all pages to find a page where the statement actually begins
        # Then detect headers on THAT page.
        pages = parse_result.get("pages", [])
        
        # Priority sort: Consolidated-only > Mixed > others
        sorted_pages = sorted(pages, key=lambda p: 0 if "consolidated" in p.get("text","").lower() and "standalone" not in p.get("text","").lower() else 1 if "consolidated" in p.get("text","").lower() else 2)

        # Build global text from first 50 pages to find global headers if local ones fail
        global_text = "\n".join([p.get("text","") for p in sorted_pages[:50]])
        global_headers = self._find_fy_headers(global_text)
        
        raw_rows = {}
        headers_to_use = global_headers
        
        found_any = False
        for page in sorted_pages:
            text = page.get("text", "")
            if not text: continue
            
            # Local header check (e.g. at top of page 125)
            local_heads = self._find_fy_headers(text[:3000])
            if local_heads:
                headers_to_use = local_heads
            
            if not headers_to_use:
                continue

            lines = text.split("\n")
            for i, line in enumerate(lines):
                l_low = line.lower()
                for field, pats in patterns.items():
                    if field in raw_rows: continue
                    for pat in pats:
                        if re.search(pat, l_low, re.I):
                            next_l = lines[i+1] if i+1 < len(lines) else ""
                            vals = _extract_row_numbers(line, next_l)
                            if vals:
                                raw_rows[field] = vals
                                found_any = True
                            break
            # If we found significant fields on this page, we might stay with its headers
            if len(raw_rows) > 5: break

        # Map raw rows to columns
        if not headers_to_use:
            return {}

        results = {h: {} for h in headers_to_use}
        for field, vals in raw_rows.items():
            # Alignment trick: if vals count > headers count, check for note col
            offset = 0
            if len(vals) > len(headers_to_use):
                # If first value is small and matches a Note pattern, skip it
                if vals[0] is not None and abs(vals[0]) < 150:
                    offset = 1
            
            for idx, h in enumerate(headers_to_use):
                real_idx = idx + offset
                if real_idx < len(vals):
                    results[h][field] = vals[real_idx]

        # Post-process
        consolidated = {}
        for h, data in results.items():
            if type_label == "BS": consolidated[h] = self._derive_balance_sheet(data)
            elif type_label == "CF": consolidated[h] = self._derive_cash_flow(data)
        
        return consolidated

    def _find_fy_headers(self, text: str) -> List[str]:
        """Detect FY headers like '31st March, 2025' and '31st March, 2024'."""
        found = []
        # Look for "31st March, 20XX"
        for m in re.finditer(r"31\s*(?:st|nd|rd|th)?\s*march\s*,?\s*(20\d{2})", text, re.I):
            label = f"FY{m.group(1)}"
            if label not in found: found.append(label)
        
        # Look for "2024-25" or "2024-2025"
        for m in re.finditer(r"\b(20\d{2})[–\-](20\d{2}|\d{2})\b", text):
            # If it's a date range, label as the ending year
            yr_start = int(m.group(1))
            yr_end_str = m.group(2)
            if len(yr_end_str) == 2:
                yr_end = (yr_start // 100) * 100 + int(yr_end_str)
            else:
                yr_end = int(yr_end_str)
            label = f"FY{yr_end}"
            if label not in found: found.append(label)

        curr_yr = datetime.now().year
        # Restrict to realistic range for annual report
        valid = [f for f in found if (curr_yr - 5) <= int(f.replace("FY","")) <= (curr_yr + 1)]
        valid = sorted(list(set(valid)), reverse=True)
        return valid[:2]

    def _derive_balance_sheet(self, f: Dict) -> Dict:
        out = {k: v for k, v in f.items()}
        # Total Equity = Capital + Reserves + Minority
        eq = f.get("equity_share_capital", 0) or 0
        res = f.get("reserves", 0) or 0
        mi = f.get("minority_interest", 0) or 0
        out["total_equity"] = round(eq + res + mi, 2) if (eq or res) else None

        # Total Debt = Long-term + Short-term + Lease + Deferred Payment
        debt = 0
        found_d = False
        for k in ["long_term_borrowings", "short_term_borrowings", "lease_liabilities", "deferred_payment_liabilities"]:
            if f.get(k) is not None:
                debt += f[k]
                found_d = True
        out["total_debt"] = round(debt, 2) if found_d else None
        return out

    def _derive_cash_flow(self, f: Dict) -> Dict:
        out = {k: v for k, v in f.items()}
        cfo = f.get("cash_from_operations")
        capex = f.get("capital_expenditure")
        if cfo is not None and capex is not None:
            out["free_cash_flow"] = round(cfo - abs(capex), 2)
        return out

    # Quarterly P&L usually comes from XBRL but keep stubs
    def extract_annual_pnl_from_pdf(self, parse_result: Dict) -> Dict[str, Dict]: return {}
    def extract_quarterly_pnl_from_pdf(self, parse_result: Dict) -> Dict[str, Dict]: return {}
