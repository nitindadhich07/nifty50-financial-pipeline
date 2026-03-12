#!/usr/bin/env python3
"""
xbrl_parser.py  (FIXED v2)
---------------------------
Primary data source: NSE Results Comparison API (structured XBRL data as JSON).

FIX 1: NSE requires 3-step cookie handshake (homepage → /get-quotes → API).
        The old single-step handshake never got the required `nsit` / `nseappid`
        cookies, so every XBRL call silently returned {}.

FIX 2: Added retry logic with exponential back-off.
FIX 3: Raw response is saved for debugging when extraction yields no data.
"""

import time
import json
import logging
import requests
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)

NSE_HOME         = "https://www.nseindia.com"
NSE_XBRL_URL     = "https://www.nseindia.com/api/results-comparision"
UNITS_DIVISOR    = 100.0   # NSE reports values in hundreds → divide to get ₹ Crores

# ── NSE Session ────────────────────────────────────────────────────────────────

def _nse_session() -> requests.Session:
    """
    Build a session with the full 3-step NSE cookie handshake.

    NSE's CDN sets `nsit` on the homepage hit and `nseappid` on the
    /get-quotes page. Without both, the /api/* endpoints return 401/403
    or an empty JSON body.
    """
    s = requests.Session()
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "Connection":      "keep-alive",
    })

    try:
        # Step 1: homepage — sets nsit cookie
        logger.info("NSE handshake step 1/3: homepage...")
        s.get(NSE_HOME, timeout=15)
        time.sleep(2)

        # Step 2: equity quote page — sets nseappid cookie
        logger.info("NSE handshake step 2/3: quote page...")
        s.get(f"{NSE_HOME}/get-quotes/equity?symbol=RELIANCE", timeout=15)
        time.sleep(2)

        # Step 3: warm up the results API path (sets any remaining session keys)
        logger.info("NSE handshake step 3/3: warming results path...")
        s.get(
            f"{NSE_HOME}/companies-listing/corporate-filings-financial-results",
            timeout=15,
        )
        time.sleep(2)

        # Switch Accept header to JSON for subsequent API calls
        s.headers.update({
            "Accept":  "application/json, text/javascript, */*; q=0.01",
            "Referer": f"{NSE_HOME}/companies-listing/corporate-filings-financial-results",
            "X-Requested-With": "XMLHttpRequest",
        })

        logger.info("NSE session ready. Cookies: " + ", ".join(s.cookies.keys()))
    except Exception as e:
        logger.warning(f"NSE handshake issue (will try anyway): {e}")

    return s


# ── Helpers ────────────────────────────────────────────────────────────────────

def _safe_float(val: Any) -> Optional[float]:
    """Convert raw NSE field value (string or None) to float in ₹ Crores."""
    if val is None or val == "" or val == "null":
        return None
    try:
        return round(float(str(val)) / UNITS_DIVISOR, 2)
    except (ValueError, TypeError):
        return None


def _period_label(from_dt: str, to_dt: str) -> str:
    """'31-DEC-2025' → 'Dec 2025'"""
    for fmt in ["%d-%b-%Y", "%d-%B-%Y", "%d/%m/%Y"]:
        try:
            dt = datetime.strptime(to_dt.strip(), fmt)
            return dt.strftime("%b %Y")
        except ValueError:
            continue
    return to_dt


def _fy_label(to_dt: str) -> str:
    """'31-MAR-2025' → 'FY2025'"""
    for fmt in ["%d-%b-%Y", "%d-%B-%Y"]:
        try:
            dt = datetime.strptime(to_dt.strip(), fmt)
            return f"FY{dt.year}" if dt.month <= 3 else f"FY{dt.year + 1}"
        except ValueError:
            continue
    return to_dt


def _fy_label_from_item(from_dt: str, to_dt: str) -> str:
    return _fy_label(to_dt)


def _map_item_to_pnl(item: Dict) -> Dict[str, Optional[float]]:
    """Map a single NSE XBRL result item to our standard P&L schema."""
    sales      = _safe_float(item.get("re_net_sale"))
    other_inc  = _safe_float(item.get("re_oth_inc_new"))
    total_inc  = _safe_float(item.get("re_total_inc")) or _safe_float(item.get("re_tot_inc"))
    tot_exp    = _safe_float(item.get("re_oth_tot_exp"))
    interest   = _safe_float(item.get("re_int_new"))
    depr       = _safe_float(item.get("re_depr_und_exp"))
    pbt        = _safe_float(item.get("re_pro_loss_bef_tax"))
    tax        = _safe_float(item.get("re_tax"))
    net_profit = _safe_float(item.get("re_net_profit"))
    raw_mat    = _safe_float(item.get("re_raw_mat_con"))
    emp_cost   = _safe_float(item.get("re_emp_ben_exp"))

    # EPS: prefer basic
    eps_raw = (
        item.get("re_basic_eps_for_cont_dic_opr")
        or item.get("re_basic_eps")
        or item.get("re_diluted_eps")
    )
    eps = None
    if eps_raw:
        try:
            eps = round(float(eps_raw), 2)
        except (ValueError, TypeError):
            pass

    # Operating expenses = Total Expenses − Finance Costs − Depreciation
    op_exp = None
    if tot_exp is not None and interest is not None and depr is not None:
        op_exp = round(tot_exp - interest - depr, 2)
    elif tot_exp is not None:
        op_exp = tot_exp

    # EBITDA = Sales − Operating Expenses
    ebitda = None
    if sales is not None and op_exp is not None:
        ebitda = round(sales - op_exp, 2)

    ebitda_margin_pct = None
    if ebitda is not None and sales and sales != 0:
        ebitda_margin_pct = round((ebitda / sales) * 100, 2)

    if total_inc is None and sales is not None and other_inc is not None:
        total_inc = round(sales + other_inc, 2)

    return {
        "revenue_from_operations":   sales,
        "other_income":              other_inc,
        "total_income":              total_inc,
        "raw_material_cost":         raw_mat,
        "power_fuel_cost":           None,
        "employee_cost":             emp_cost,
        "selling_general_admin":     None,
        "other_expenses":            None,
        "total_operating_expenses":  op_exp,
        "ebitda":                    ebitda,
        "ebitda_margin_pct":         ebitda_margin_pct,
        "depreciation":              depr,
        "interest":                  interest,
        "profit_before_tax":         pbt,
        "tax":                       tax,
        "net_profit":                net_profit,
        "minority_interest":         None,
        "net_profit_after_minority": net_profit,
        "eps":                       eps,
    }


# ── Client ─────────────────────────────────────────────────────────────────────

class NSEXBRLClient:
    """Fetches structured quarterly and annual P&L from NSE's XBRL API."""

    def __init__(self, debug_dir: str = "data/debug"):
        self.session   = _nse_session()
        self.debug_dir = debug_dir
        Path(debug_dir).mkdir(parents=True, exist_ok=True)

    def fetch_quarterly_pnl(self, symbol: str) -> Dict[str, Dict]:
        return self._fetch_pnl(symbol, period="Quarterly", label_fn=_period_label)

    def fetch_annual_pnl(self, symbol: str) -> Dict[str, Dict]:
        return self._fetch_pnl(symbol, period="Annual", label_fn=_fy_label_from_item)

    def _fetch_pnl(self, symbol: str, period: str, label_fn) -> Dict[str, Dict]:
        params = {
            "index":  "equities",
            "symbol": symbol.upper(),
            "period": period,
        }

        for attempt in range(1, 4):
            try:
                logger.info(f"Fetching NSE XBRL {period} for {symbol} (attempt {attempt})...")
                r = self.session.get(NSE_XBRL_URL, params=params, timeout=25)
                logger.info(f"  HTTP {r.status_code} | {len(r.content)} bytes")

                if r.status_code == 401 or r.status_code == 403:
                    logger.warning("  NSE returned auth error — refreshing session...")
                    self.session = _nse_session()
                    time.sleep(5 * attempt)
                    continue

                if r.status_code != 200:
                    logger.warning(f"  NSE XBRL returned {r.status_code}")
                    time.sleep(3 * attempt)
                    continue

                # Save raw response for debugging
                debug_file = Path(self.debug_dir) / f"nse_xbrl_{symbol}_{period.lower()}_raw.json"
                try:
                    debug_file.write_text(r.text, encoding="utf-8")
                    logger.info(f"  Raw response saved → {debug_file}")
                except Exception:
                    pass

                data  = r.json()
                items = data.get("resCmpData", [])

                if not items:
                    logger.warning(
                        f"  NSE XBRL returned 0 items for {symbol}/{period}. "
                        f"Keys in response: {list(data.keys())}. "
                        f"Check {debug_file} for full response."
                    )
                    # One retry after a fresh session
                    if attempt < 3:
                        self.session = _nse_session()
                        time.sleep(5)
                        continue
                    return {}

                logger.info(f"  Got {len(items)} {period} periods")
                result = {}
                for item in items:
                    from_dt = item.get("re_from_dt", "")
                    to_dt   = item.get("re_to_dt", "")
                    label   = label_fn(from_dt, to_dt)
                    pnl     = _map_item_to_pnl(item)
                    result[label] = pnl
                    logger.info(
                        f"  [{label}] Rev={pnl.get('revenue_from_operations')} | "
                        f"NP={pnl.get('net_profit')} | "
                        f"EBITDA={pnl.get('ebitda_margin_pct')}%"
                    )
                return result

            except requests.exceptions.Timeout:
                logger.warning(f"  Timeout on attempt {attempt}")
                time.sleep(5 * attempt)
            except Exception as e:
                logger.error(f"  NSE XBRL error: {e}", exc_info=True)
                time.sleep(3 * attempt)

        logger.error(f"NSE XBRL: all attempts failed for {symbol}/{period}")
        return {}
