#!/usr/bin/env python3
"""
financial_extractor.py  (PRODUCTION v4 - Complete Rewrite)
-----------------------------------------------------------
ROOT CAUSE OF ALL PREVIOUS FAILURES (from bs_text.txt analysis):

  Indian annual reports (RIL format) put each financial row as:
      Label                 ← e.g., "Equity Share Capital"
      Note_Number           ← e.g., "14"  (1-2 digit integer ALONE on its line)
      Current_Year_Value    ← e.g., " 13,532 "  (Indian number format)
      Prev_Year_Value       ← e.g., " 6,766 "

  The OLD parser's _extract_row_numbers() was capturing "14" as the
  EQUITY SHARE CAPITAL VALUE instead of the actual "13,532". This also
  caused trade_payables = 21 (note), reserves = 15 (note), etc.

  THE FIX: Explicitly detect and skip note reference lines before
  collecting numeric values.

  UNIT: RIL uses "(C in crore)" where C encodes ₹. Detected and treated as
  unit_multiplier = 1.0 (values already in crores).
"""

import re
import logging
from typing import Dict, List, Optional, Tuple, Any

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# 1. UNIT DETECTION
# ─────────────────────────────────────────────────────────────────────────────

def detect_unit_multiplier(text: str) -> float:
    """Return multiplier to convert reported values → ₹ Crores."""
    t = re.sub(r"\s+", "", text.lower())
    if any(k in t for k in ["incrore", "crores", "crore", "₹crore", "ccrore", "cincrore"]):
        return 1.0
    if any(k in t for k in ["inmillion", "millions", "million"]):
        return 0.1      # 1M INR = 0.1 Cr
    if any(k in t for k in ["inlakh", "lakhs", "lakh"]):
        return 0.01
    if any(k in t for k in ["inthousand", "thousands", "thousand"]):
        return 0.001
    if any(k in t for k in ["inbillion", "billions", "billion"]):
        return 100.0
    return 1.0


# ─────────────────────────────────────────────────────────────────────────────
# 2. NUMBER LINE CLASSIFICATION
# ─────────────────────────────────────────────────────────────────────────────

# Indian number: digits + commas + optional decimal + optional parens (negative)
_INDIAN_NUM = re.compile(
    r"^\s*\(?\s*([\d,]+(?:\.\d+)?)\s*\)?\s*$"
)

# Note reference line: just 1-2 digits, or "A-C", or "1 to 45"
_NOTE_LINE = re.compile(
    r"^(\d{1,2}|[A-Z]-[A-Z]|\d{1,2}\s+to\s+\d{1,2}|[a-z]\)|[ivx]+)$",
    re.IGNORECASE,
)

# Section-divider labels that look like field labels but have NO values after them.
# If matched, the extractor would grab the NEXT row's values under the wrong key.
_SECTION_HEADER_LABELS = {
    "non-current assets",
    "current assets",
    "financial assets",
    "financial liabilities",
    "non-current liabilities",
    "current liabilities",
    "equity and liabilities",
    "equity",
    "liabilities",
    "assets",
    "income",
    "expenses",
    "notes",
    "net profit attributable to:",
    "profit for the year attributable to:",
    "other comprehensive income attributable to:",
    "total comprehensive income attributable to:",
    "earnings per equity share of face value of k 10 each",
    "earnings per equity share of face value of ₹ 10 each",
    "earnings per equity share of face value of rs. 10 each",
    "other comprehensive income:",
}

# Lines that should always be skipped
_SKIP_RE = [re.compile(p, re.I) for p in [
    r"^(reliance industries|integrated annual report|annual report 20)",
    r"^(chartered accountants|for and on behalf|as per our report)",
    r"^(din:|membership no\.|registration no\.)",
    r"^(material accounting|see accompanying|date:)",
    r"^(non-executive|executive director|chairman|managing director)",
    r"^consolidated (balance sheet|statement of profit|cash flow)",
    r"^standalone (balance sheet|statement of profit|cash flow)",
    r"^for the (year|quarter|period) ended",
    r"^(as at|for the year|year ended)\s+\d",
    r"^[─━═\-─=]+$",
    r"^[a-z]\)\s+",      # sub-item markers like "a) Owners..."
    r"^(i\.|ii\.|iii\.|iv\.)\s+",  # Roman numeral sub-items
]]


def _is_note_number(line: str) -> bool:
    return bool(_NOTE_LINE.match(line.strip()))


def _is_numeric_value(line: str) -> bool:
    s = line.strip()
    if not s or s in ("-", "–", "—", "nil", "Nil", "NIL", "N/A", "NA"):
        return False
    return bool(_INDIAN_NUM.match(s))


def _is_skip_line(line: str) -> bool:
    s = line.strip()
    if not s:
        return True
    # Note reference numbers (1-2 digits) are handled by _is_note_number; 
    # do NOT skip them here or the value-scan loop will break prematurely.
    # Only skip 3-4 digit page numbers (> 99 and < 2000 to avoid financial values).
    if re.match(r"^\d{3,4}$", s):
        try:
            v = int(s)
            if 100 <= v < 2000:  # page numbers are typically 100-999
                return True
        except ValueError:
            pass
    for pat in _SKIP_RE:
        if pat.search(s):
            return True
    return False


def _parse_indian_number(line: str) -> Optional[float]:
    """
    Parse Indian-format number to float in crores.
    " 6,83,102 " → 683102.0
    "(15,124)"   → -15124.0
    "51.47"      → 51.47
    """
    s = line.strip()
    if s in ("-", "–", "—", "", "nil", "Nil", "NIL", "N/A", "NA"):
        return None
    negative = s.startswith("(") and s.endswith(")")
    # Strip parens, commas, spaces
    clean = re.sub(r"[(),\s]", "", s)
    try:
        val = float(clean)
        return -val if negative else val
    except ValueError:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# 3. COMPREHENSIVE FIELD SYNONYM MAP
# ─────────────────────────────────────────────────────────────────────────────

FIELD_SYNONYMS: Dict[str, List[str]] = {

    # ── P&L ──────────────────────────────────────────────────────────────────
    "revenue_from_operations": [
        "revenue from operations",
        "net revenue from operations",
        "revenue from operations (net of excise duty)",
        "revenue from operations (net)",
        "income from operations",
        "net sales",
        "revenues",
        "total revenue from operations",
        "value of sales & services (revenue)",
        "revenue (net of taxes)",
    ],
    "other_income": [
        "other income",
        "other operating income",
        "other non-operating income",
        "finance income",
        "income from investments",
        "miscellaneous income",
    ],
    "total_income": [
        "total income",
        "total revenue",
        "total income from operations",
        "gross income",
    ],
    "raw_material_cost": [
        "cost of materials consumed",
        "raw material cost",
        "raw materials consumed",
        "material cost",
        "cost of goods sold",
        "cost of products sold",
        "purchase of stock-in-trade",
        "purchases of stock-in-trade",
    ],
    "employee_cost": [
        "employee benefits expense",
        "employee benefit expenses",
        "employee benefits expenses",
        "staff costs",
        "personnel expenses",
        "salaries and wages",
        "manpower cost",
        "employee cost",
        "remuneration to employees",
    ],
    "depreciation": [
        "depreciation / amortisation and depletion expense",
        "depreciation and amortisation expense",
        "depreciation and amortisation",
        "depreciation, amortisation and impairment",
        "depreciation & amortisation",
        "depreciation and depletion",
        "depreciation expense",
        "depreciation",
        "amortisation",
    ],
    "interest": [
        "finance costs",
        "finance cost",
        "interest expense",
        "borrowing costs",
        "interest and finance charges",
        "finance charges",
        "interest on borrowings",
        "interest cost",
    ],
    "total_expenses": [
        "total expenses",
        "total expenditure",
        "total costs and expenses",
        "aggregate expenses",
        "total operating expenses",
    ],
    "profit_before_tax": [
        "profit before share of profit / (loss) of associates / joint ventures and tax",
        "profit before share of profit/(loss) of associates/joint ventures and tax",
        "profit before tax",
        "profit/(loss) before tax",
        "earnings before tax",
        "profit before exceptional items and tax",
        "profit before exceptional and extraordinary items and tax",
        "profit before exceptional item and tax",
        "pbt",
    ],
    "tax": [
        "total tax expense",
        "income tax expense",
        "tax charge",
    ],
    "current_tax": [
        "current tax",
        "current income tax",
    ],
    "deferred_tax": [
        "deferred tax",
        "deferred income tax",
    ],
    "profit_after_tax": [
        "profit after tax",
        "profit for the year",
        "profit/(loss) for the year",
        "net profit for the year",
        "profit after exceptional items and tax",
    ],
    "net_profit": [
        "owners of the company",
        "profit attributable to owners of the company",
        "profit attributable to equity holders of the company",
        "profit attributable to equity shareholders",
        "net profit attributable to shareholders",
        "profit for the period attributable to owners",
        "profit for the year attributable to owners",
    ],
    "eps_basic": [
        "basic (in c)",      # RIL: "Basic (in C)" where C = ₹
        "basic (in ₹)",
        "basic earnings per equity share",
        "basic earnings per share",
        "basic eps",
        "earnings per share - basic",
        "earnings per equity share - basic",
        "basic (in rs.)",
        "basic (in rs)",
    ],
    "eps_diluted": [
        "diluted (in c)",
        "diluted (in ₹)",
        "diluted earnings per equity share",
        "diluted earnings per share",
        "diluted eps",
        "earnings per share - diluted",
        "diluted (in rs.)",
    ],

    # ── Balance Sheet — Equity ───────────────────────────────────────────────
    "equity_share_capital": [
        "equity share capital",
        "share capital",
        "paid-up share capital",
        "paid up equity share capital",
        "issued, subscribed and paid-up share capital",
    ],
    "reserves": [
        "other equity",
        "reserves and surplus",
        "other reserves",
        "total other equity",
        "reserves & surplus",
        "capital and reserves",
    ],
    "minority_interest": [
        "non-controlling interests",
        "non-controlling interest",
        "minority interest",
        "interest of minority shareholders",
    ],
    "total_equity": [
        "total equity",
        "total shareholders equity",
        "shareholders equity",
        "total equity attributable to owners of the company",
    ],

    # ── Balance Sheet — Liabilities ──────────────────────────────────────────
    "long_term_borrowings": [
        "non-current borrowings",
        "long-term borrowings",
        "long term debt",
        "term loans",
        "debentures and bonds",
    ],
    "short_term_borrowings": [
        "short-term borrowings",
        "current borrowings",
        "current portion of long-term borrowings",
        "bank overdraft",
        "working capital borrowings",
    ],
    "lease_liabilities": [
        "lease liabilities",
        "right-of-use asset liabilities",
        "finance lease obligations",
    ],
    "trade_payables": [
        "trade payables",
        "accounts payable",
        "sundry creditors",
        "trade and other payables",
        "creditors",
    ],
    "other_current_liabilities": [
        "other current liabilities",
        "other financial liabilities",
        "accrued liabilities",
        "other payables",
    ],
    "total_current_liabilities": [
        "total current liabilities",
    ],
    "total_non_current_liabilities": [
        "total non-current liabilities",
    ],
    "total_liabilities": [
        "total liabilities",
        "total equity and liabilities",
    ],

    # ── Balance Sheet — Assets ───────────────────────────────────────────────
    "cash_equivalents": [
        "cash and cash equivalents",
        "cash and bank balances",
        "cash and short-term deposits",
        "cash equivalents",
    ],
    "short_term_investments": [
        "current investments",
        "short-term investments",
        "investments - current",
        "marketable securities",
    ],
    "inventory": [
        "inventories",
        "inventory",
        "stocks",
        "stock in trade",
        "stock-in-trade",
    ],
    "receivables": [
        "trade receivables",
        "accounts receivable",
        "debtors",
        "sundry debtors",
    ],
    "other_current_assets": [
        "other current assets",
        "other financial assets - current",
        "prepaid expenses and other assets",
    ],
    "total_current_assets": [
        "total current assets",
    ],
    "ppe": [
        "property, plant and equipment",
        "property, plant & equipment",
        "tangible assets",
        "fixed assets",
        "net block",
    ],
    "capital_work_in_progress": [
        "capital work-in-progress",
        "capital work in progress",
        "cwip",
        "assets under construction",
    ],
    "goodwill": [
        "goodwill",
        "goodwill on consolidation",
        "goodwill on acquisition",
    ],
    "intangibles": [
        "other intangible assets",
        "intangible assets",
        "spectrum",
        "computer software",
        "licenses and spectrum",
    ],
    "long_term_investments": [
        "investments",
        "non-current investments",
        "long-term investments",
        "investments in associates",
        "investments in subsidiaries",
    ],
    "total_non_current_assets": [
        "total non-current assets",
    ],
    "total_assets": [
        "total assets",
        "total non-current and current assets",
    ],

    # ── Cash Flow ────────────────────────────────────────────────────────────
    "cash_from_operations": [
        "net cash from operating activities",
        "net cash generated from operating activities",
        "net cash flows from operating activities",
        "net cash inflow from operating activities",
        "cash generated from operations",
        "net cash provided by operating activities",
    ],
    "capital_expenditure": [
        "purchase of property, plant and equipment",
        "purchase of property, plant & equipment",
        "capital expenditure",
        "purchase of fixed assets",
        "payments for property, plant and equipment",
        "additions to property, plant and equipment",
    ],
    "acquisitions": [
        "acquisition of business",
        "acquisition of subsidiaries",
        "business combinations",
    ],
    "investments_cf": [
        "purchase of investments",
        "payment for investments",
        "purchase of current investments",
    ],
    "cash_from_investing": [
        "net cash from investing activities",
        "net cash used in investing activities",
        "net cash flows from investing activities",
        "net cash outflow from investing activities",
    ],
    "debt_issued": [
        "proceeds from borrowings",
        "proceeds from long-term borrowings",
        "proceeds from issue of debentures",
    ],
    "debt_repaid": [
        "repayment of borrowings",
        "repayment of long-term borrowings",
    ],
    "dividend_paid": [
        "dividend paid",
        "dividends paid",
        "payment of dividend",
        "dividend paid to shareholders",
    ],
    "buyback": [
        "buyback of equity shares",
        "buy-back of equity shares",
        "repurchase of equity shares",
    ],
    "cash_from_financing": [
        "net cash from financing activities",
        "net cash used in financing activities",
        "net cash flows from financing activities",
    ],
    "net_cash_flow": [
        "net increase in cash and cash equivalents",
        "net decrease in cash and cash equivalents",
        "net increase/(decrease) in cash and cash equivalents",
        "net change in cash and cash equivalents",
    ],
    "opening_cash": [
        "cash and cash equivalents at the beginning of the year",
        "cash and cash equivalents at beginning of year",
        "opening balance of cash and cash equivalents",
        "balance at beginning of the year",
    ],
    "closing_cash": [
        "cash and cash equivalents at the end of the year",
        "cash and cash equivalents at end of year",
        "closing balance of cash and cash equivalents",
    ],
    "working_capital_change": [
        "working capital changes",
        "changes in working capital",
        "movement in working capital",
    ],
}

_SYNS_LOWER: Dict[str, List[str]] = {
    f: [s.lower() for s in syns] for f, syns in FIELD_SYNONYMS.items()
}


def match_label_to_field(label: str) -> Optional[str]:
    """Match a row label to canonical field name using synonym lookup."""
    clean = re.sub(r"\s+", " ", label.lower().strip())
    clean = re.sub(r"[\*†‡#]", "", clean).rstrip(":.,-")

    # 1. Reject pure section headers — they have no values of their own
    if clean in _SECTION_HEADER_LABELS:
        return None

    # 2. Reject lines that are clearly subsection dividers or noise
    #    (short lines that are structurally titles, not row labels)
    if clean in ("other financial assets", "financial liabilities", "financial assets",
                 "non-current liabilities", "current liabilities", "other liabilities"):
        return None

    # 3. Reject "changes in inventories..." — it's an EXPENSE, not the inventory asset
    if clean.startswith("changes in inventories"):
        return None

    # 4. Reject "other non-current assets/liabilities" matching wrong field
    #    (they are not the same as current totals)
    if clean in ("other non-current assets", "other non-current liabilities"):
        return None

    # 5. "Value of Sales & Services (Revenue)" is BEFORE GST deduction → skip,
    #    let "Revenue from Operations" (post-GST) be matched instead.
    if "value of sales" in clean and "services" in clean:
        return None

    # 6. "Tax Expenses" header (without values — sub-items follow)
    #    Only match the total "Tax Expense" / "Income Tax Expense", not the header
    if clean in ("tax expenses",):
        return None

    # 7. "Owners of the Company" appears twice (net profit, then OCI) — only first is net profit.
    #    Handled naturally by the `found` set in the extractor loop.

    # 8. Exact match
    for field, synonyms in _SYNS_LOWER.items():
        if clean in synonyms:
            return field

    # 9. Prefix / substring (longer synonyms first to reduce false positives)
    for field, synonyms in _SYNS_LOWER.items():
        for syn in sorted(synonyms, key=len, reverse=True):
            if len(syn) < 10:
                continue
            if clean.startswith(syn) or syn in clean:
                return field

    return None


# ─────────────────────────────────────────────────────────────────────────────
# 4. CORE TEXT-LINE EXTRACTOR
# ─────────────────────────────────────────────────────────────────────────────

def _extract_values_from_lines(
    lines: List[str],
    unit_multiplier: float = 1.0,
) -> Tuple[Dict[str, float], Dict[str, float]]:
    """
    THE KEY FIX: Extract current and previous year values from
    Indian annual report text lines.

    Algorithm:
      For each label line:
        - Match to a canonical field
        - Scan next lines: SKIP note-number lines (1-2 digit integers)
        - Collect up to 2 numeric values (current year, prev year)

    Returns (current_year_dict, prev_year_dict).
    """
    current: Dict[str, float] = {}
    prev:    Dict[str, float] = {}
    found:   set = set()

    # Section context for disambiguating "Borrowings" (appears in both NC and C sections)
    in_nc_liab = False  # inside Non-Current Liabilities block
    in_c_liab  = False  # inside Current Liabilities block

    i = 0
    while i < len(lines):
        line = lines[i].strip()

        if not line or _is_skip_line(line) or _is_numeric_value(line) or _is_note_number(line):
            i += 1
            continue

        # Update section context
        ll = line.lower()
        if "total non-current liabilities" in ll:
            in_nc_liab = False
        elif "total current liabilities" in ll:
            in_c_liab  = False
        elif "non-current liabilities" in ll:
            in_nc_liab, in_c_liab = True, False
        elif "current liabilities" in ll and "non-current" not in ll:
            in_nc_liab, in_c_liab = False, True
        elif ll in ("equity", "assets", "equity and liabilities", "non-current assets", "current assets"):
            in_nc_liab, in_c_liab = False, False

        # Disambiguate "Borrowings" by section
        effective_label = line
        if ll == "borrowings":
            effective_label = "non-current borrowings" if in_nc_liab else "short-term borrowings"

        field = match_label_to_field(effective_label)
        if field and field not in found:
            values: List[float] = []
            j = i + 1
            lookahead = 0

            while j < len(lines) and lookahead < 7 and len(values) < 2:
                nl = lines[j].strip()
                lookahead += 1

                if not nl:
                    j += 1
                    continue

                # FIX: check note_number FIRST — note refs should be skipped (continue),
                # NOT treated as section-break (break). The old code had _is_skip_line
                # before _is_note_number which caused break on note refs like "14".
                if _is_note_number(nl):
                    j += 1
                    continue

                if _is_skip_line(nl):
                    break  # actual section header or page number

                if _is_numeric_value(nl):
                    v = _parse_indian_number(nl)
                    if v is not None:
                        values.append(round(v * unit_multiplier, 2))
                    j += 1
                    continue
                # Another label → stop
                break

            if len(values) >= 1:
                current[field] = values[0]
                found.add(field)
                if len(values) >= 2:
                    prev[field] = values[1]

        i += 1

    return current, prev


def _extract_from_text_block(text: str) -> Tuple[Dict[str, float], Dict[str, float]]:
    unit = detect_unit_multiplier(text)
    if unit != 1.0:
        logger.info(f"  Unit multiplier detected: {unit}")
    return _extract_values_from_lines(text.split("\n"), unit)


# ─────────────────────────────────────────────────────────────────────────────
# 5. DERIVED FIELD BUILDERS
# ─────────────────────────────────────────────────────────────────────────────

def _derive_pnl(raw: Dict) -> Dict:
    sales    = raw.get("revenue_from_operations")
    other    = raw.get("other_income")
    tot_inc  = raw.get("total_income")
    tot_exp  = raw.get("total_expenses")
    interest = raw.get("interest")
    depr     = raw.get("depreciation")
    pbt      = raw.get("profit_before_tax")
    tax      = raw.get("tax")
    net_p    = raw.get("net_profit")
    pat      = raw.get("profit_after_tax")
    raw_mat  = raw.get("raw_material_cost")
    emp_cost = raw.get("employee_cost")
    eps_b    = raw.get("eps_basic")
    eps_d    = raw.get("eps_diluted")

    # Compute total tax from sub-components if not directly captured
    cur_tax  = raw.get("current_tax")
    def_tax  = raw.get("deferred_tax")
    if tax is None and (cur_tax is not None or def_tax is not None):
        tax = round((cur_tax or 0) + (def_tax or 0), 2)

    # EBITDA = PBT + Interest + Depreciation (most robust approach)
    ebitda = None
    if pbt is not None and interest is not None and depr is not None:
        ebitda = round(pbt + interest + depr, 2)
    elif sales is not None and tot_exp is not None and interest is not None and depr is not None:
        op_exp = round(tot_exp - interest - depr, 2)
        ebitda = round(sales - op_exp, 2)

    ebitda_margin = None
    if ebitda is not None and sales and sales != 0:
        ebitda_margin = round((ebitda / sales) * 100, 2)

    # net_profit: prefer owner-attributed; fall back to PAT
    if net_p is None:
        net_p = pat

    if tot_inc is None and sales is not None and other is not None:
        tot_inc = round(sales + other, 2)

    op_exp_out = None
    if tot_exp is not None and interest is not None and depr is not None:
        op_exp_out = round(tot_exp - interest - depr, 2)

    return {
        "revenue_from_operations":   sales,
        "other_income":              other,
        "total_income":              tot_inc,
        "raw_material_cost":         raw_mat,
        "power_fuel_cost":           None,
        "employee_cost":             emp_cost,
        "selling_general_admin":     None,
        "other_expenses":            None,
        "total_operating_expenses":  op_exp_out,
        "total_expenses":            tot_exp,
        "ebitda":                    ebitda,
        "ebitda_margin_pct":         ebitda_margin,
        "depreciation":              depr,
        "interest":                  interest,
        "profit_before_tax":         pbt,
        "tax":                       tax,
        "net_profit":                net_p,
        "minority_interest":         None,
        "net_profit_after_minority": net_p,
        "eps":                       eps_b,
        "eps_diluted":               eps_d,
    }


def _derive_balance_sheet(raw: Dict) -> Dict:
    eq       = raw.get("equity_share_capital")
    reserves = raw.get("reserves")
    mi       = raw.get("minority_interest")
    lt_debt  = raw.get("long_term_borrowings")
    st_debt  = raw.get("short_term_borrowings")
    lease    = raw.get("lease_liabilities")
    assets   = raw.get("total_assets")
    liabs    = raw.get("total_liabilities")
    ca       = raw.get("total_current_assets")
    nca      = raw.get("total_non_current_assets")

    total_eq = raw.get("total_equity")
    if total_eq is None and eq is not None and reserves is not None:
        total_eq = round(eq + reserves + (mi or 0), 2)

    total_debt = None
    if lt_debt is not None or st_debt is not None:
        total_debt = round((lt_debt or 0) + (st_debt or 0) + (lease or 0), 2)

    # FIX: Compute total_assets from components if missing
    if assets is None and ca is not None and nca is not None:
        assets = round(ca + nca, 2)
        logger.info(f"  Computed total_assets = {ca} + {nca} = {assets}")

    if liabs is None and assets is not None and total_eq is not None:
        liabs = round(assets - total_eq, 2)

    return {
        "equity_share_capital":          eq,
        "reserves":                      reserves,
        "minority_interest":             mi,
        "total_equity":                  total_eq,
        "long_term_borrowings":          lt_debt,
        "short_term_borrowings":         st_debt,
        "lease_liabilities":             lease,
        "total_debt":                    total_debt,
        "trade_payables":                raw.get("trade_payables"),
        "other_liabilities":             raw.get("other_current_liabilities"),
        "total_current_liabilities":     raw.get("total_current_liabilities"),
        "total_non_current_liabilities": raw.get("total_non_current_liabilities"),
        "total_liabilities":             liabs,
        "cash_equivalents":              raw.get("cash_equivalents"),
        "short_term_investments":        raw.get("short_term_investments"),
        "inventory":                     raw.get("inventory"),
        "receivables":                   raw.get("receivables"),
        "other_current_assets":          raw.get("other_current_assets"),
        "total_current_assets":          ca,
        "ppe":                           raw.get("ppe"),
        "capital_work_in_progress":      raw.get("capital_work_in_progress"),
        "goodwill":                      raw.get("goodwill"),
        "intangibles":                   raw.get("intangibles"),
        "long_term_investments":         raw.get("long_term_investments"),
        "total_non_current_assets":      nca,
        "total_assets":                  assets,
    }


def _derive_cash_flow(raw: Dict) -> Dict:
    cfo    = raw.get("cash_from_operations")
    cfi    = raw.get("cash_from_investing")
    cff    = raw.get("cash_from_financing")
    capex  = raw.get("capital_expenditure")
    net_cf = raw.get("net_cash_flow")

    fcf = None
    if cfo is not None and capex is not None:
        fcf = round(cfo - abs(capex), 2)

    if net_cf is None and cfo is not None and cfi is not None and cff is not None:
        net_cf = round(cfo + cfi + cff, 2)

    return {
        "cash_from_operations":        cfo,
        "capital_expenditure":         capex,
        "acquisitions":                raw.get("acquisitions"),
        "investments":                 raw.get("investments_cf"),
        "cash_from_investing":         cfi,
        "debt_issued":                 raw.get("debt_issued"),
        "debt_repaid":                 raw.get("debt_repaid"),
        "dividend_paid":               raw.get("dividend_paid"),
        "buyback":                     raw.get("buyback"),
        "cash_from_financing":         cff,
        "free_cash_flow":              fcf,
        "net_cash_flow":               net_cf,
        "opening_cash":                raw.get("opening_cash"),
        "closing_cash":                raw.get("closing_cash"),
        "working_capital_change":      raw.get("working_capital_change"),
        "depreciation":                raw.get("depreciation"),
    }


# ─────────────────────────────────────────────────────────────────────────────
# 6. FY / QUARTER HEADER DETECTION
# ─────────────────────────────────────────────────────────────────────────────

def _detect_fy_headers(text: str) -> Tuple[str, str]:
    years = []
    # Prioritize years mentioned with "March" (standard for FY headers)
    march_years = []
    for m in re.finditer(r"31\s*(?:st\s*)?march[,\s]+(\d{4})", text, re.I):
        yr = int(m.group(1))
        if 2020 <= yr <= 2025 and yr not in march_years:
            march_years.append(yr)
    
    # Second priority: Year ranges like 2024-25
    range_years = []
    for m in re.finditer(r"(20\d{2})[–\-](20\d{2}|\d{2})", text):
        pre = int(m.group(1))
        yr_end = pre + 1
        if 2020 <= yr_end <= 2025 and yr_end not in range_years:
            range_years.append(yr_end)
            
    # Combine but prioritize march_years
    all_years = sorted(list(set(march_years)), reverse=True)
    if len(all_years) < 2:
        # Add range years if we don't have enough from March dates
        for yr in sorted(range_years, reverse=True):
            if yr not in all_years:
                all_years.append(yr)
    
    # Sort and take top 2
    all_years.sort(reverse=True)
    
    final_years = []
    for i in range(len(all_years)):
        if i < 2:
            final_years.append(all_years[i])
            
    if len(final_years) >= 2:
        return f"FY{final_years[0]}", f"FY{final_years[1]}"
    elif len(final_years) == 1:
        return f"FY{final_years[0]}", f"FY{final_years[0]-1}"
    
    return "FY2025", "FY2024"


def _detect_quarter_headers(text: str) -> List[str]:
    found = []
    for m in re.finditer(
        r"(?:31(?:st)?|30(?:th)?|29(?:th)?|28(?:th)?)\s*"
        r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z']*,?\s*(\d{4})",
        text, re.I
    ):
        label = f"{m.group(1).capitalize()} {m.group(2)}"
        if label not in found:
            found.append(label)
    for m in re.finditer(r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)['\s]+(\d{2,4})", text, re.I):
        yr = m.group(2)
        yr = f"20{yr}" if len(yr) == 2 else yr
        label = f"{m.group(1).capitalize()} {yr}"
        if label not in found:
            found.append(label)
    return sorted(set(found), reverse=True)[:6] if found else ["Period 1"]


# ─────────────────────────────────────────────────────────────────────────────
# 7. PAGE SELECTION — PREFER CONSOLIDATED
# ─────────────────────────────────────────────────────────────────────────────

def _get_best_text(parse_result: Dict, section_hint: str) -> str:
    """Return text from consolidated pages for the given section, or all pages."""
    section_kw = {
        "balance":    ["balance sheet", "total assets", "total equity and liabilities"],
        "pnl":        ["revenue from operations", "profit and loss", "value of sales", "finance costs"],
        "cash_flow":  ["cash flow", "cash from operating", "net cash"],
    }
    kws = section_kw.get(section_hint, [])

    cons_pages, fallback_pages = [], []
    for page in parse_result.get("pages", []):
        tl = page["text"].lower()
        has_kw  = any(k in tl for k in kws)
        has_con = "consolidated" in tl
        if has_con and has_kw:
            cons_pages.append(page["text"])
        elif has_kw:
            fallback_pages.append(page["text"])

    if cons_pages:
        logger.info(f"  [{section_hint}] Using {len(cons_pages)} consolidated pages")
        return "\n".join(cons_pages)
    elif fallback_pages:
        logger.info(f"  [{section_hint}] Using {len(fallback_pages)} fallback pages")
        return "\n".join(fallback_pages)
    # Last resort: full text
    return parse_result.get("full_text", "")


# ─────────────────────────────────────────────────────────────────────────────
# 8. PUBLIC API
# ─────────────────────────────────────────────────────────────────────────────

class FinancialExtractor:
    """
    Extract financial statements from parsed PDF documents.
    Uses text-line algorithm that correctly skips Indian annual report
    note-column numbers before extracting actual financial values.
    """

    def extract_balance_sheet(self, parse_result: Dict) -> Dict[str, Dict]:
        text = _get_best_text(parse_result, "balance")
        cur, prv = _extract_from_text_block(text)
        fy_cur, fy_prv = _detect_fy_headers(text)
        result = {}
        if cur:
            result[fy_cur] = _derive_balance_sheet(cur)
        if prv:
            result[fy_prv] = _derive_balance_sheet(prv)
        logger.info(f"  BS → {list(result.keys())} | {len(cur)} fields")
        return result

    def extract_annual_pnl_from_pdf(self, parse_result: Dict) -> Dict[str, Dict]:
        text = _get_best_text(parse_result, "pnl")
        cur, prv = _extract_from_text_block(text)
        fy_cur, fy_prv = _detect_fy_headers(text)
        result = {}
        if cur:
            result[fy_cur] = _derive_pnl(cur)
        if prv:
            result[fy_prv] = _derive_pnl(prv)
        logger.info(f"  Annual P&L → {list(result.keys())} | {len(cur)} fields")
        return result

    def extract_cash_flow(self, parse_result: Dict) -> Dict[str, Dict]:
        text = _get_best_text(parse_result, "cash_flow")
        cur, prv = _extract_from_text_block(text)
        fy_cur, fy_prv = _detect_fy_headers(text)
        result = {}
        if cur:
            result[fy_cur] = _derive_cash_flow(cur)
        if prv:
            result[fy_prv] = _derive_cash_flow(prv)
        logger.info(f"  CF → {list(result.keys())} | {len(cur)} fields")
        return result

    def extract_quarterly_pnl_from_pdf(self, parse_result: Dict) -> Dict[str, Dict]:
        text = _get_best_text(parse_result, "pnl")
        cur, prv = _extract_from_text_block(text)
        headers = _detect_quarter_headers(text)
        result = {}
        if cur:
            result[headers[0] if headers else "Q_Unknown"] = _derive_pnl(cur)
        if prv and len(headers) >= 2:
            result[headers[1]] = _derive_pnl(prv)
        logger.info(f"  Quarterly P&L → {list(result.keys())}")
        return result
