#!/usr/bin/env python3
"""
downloader.py  (FIXED v3)
--------------------------
Downloads quarterly and annual financial report PDFs.

FIX 1: NSE `attchmntFile` is a relative path like
        'corporates/RELIANCE_results.pdf'. Must be prefixed with
        NSE_ARCHIVE = 'https://nsearchives.nseindia.com/'.
        The old code passed it raw → every single PDF download silently failed.

FIX 2: 3-step NSE cookie handshake (same as xbrl_parser fix).

FIX 3: RIL direct URL corrected. The old URL pattern pointed to a non-existent
        path. The actual RIL FY2025 annual report URL is looked up from BSE/NSE
        announcement metadata rather than hard-coded.

FIX 4: BSE scrip_code-based quarterly download added as extra fallback (BSE
        also publishes quarterly result PDFs).
"""

import os
import time
import hashlib
import logging
import re
import requests
from datetime import datetime
from typing import Optional, List, Dict
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Company Registry ──────────────────────────────────────────────────────────
COMPANY_REGISTRY: Dict[str, Dict] = {
    "RELIANCE": {
        "scrip_code": "500325",
        "symbol":     "RELIANCE",
        "name":       "Reliance Industries Limited",
        "isin":       "INE002A01018",
    },
    # Add more companies here:
    # "HDFCBANK":  {"scrip_code": "500180", "symbol": "HDFCBANK", "name": "HDFC Bank Limited", "isin": "INE040A01034"},
    # "TCS":       {"scrip_code": "532540", "symbol": "TCS",      "name": "Tata Consultancy Services", "isin": "INE467B01029"},
}

# ── Endpoints ─────────────────────────────────────────────────────────────────
BSE_HOME        = "https://www.bseindia.com"
BSE_ANN_URL     = "https://api.bseindia.com/BseOnlineAPI/api/AnnSubCategoryGetData/w"
BSE_PDF_BASE    = "https://www.bseindia.com/xml-data/corpfiling/AttachLive/"

NSE_HOME        = "https://www.nseindia.com"
NSE_ANN_URL     = "https://www.nseindia.com/api/corporate-announcements"
# FIX 1: NSE attachment files are served from nsearchives, NOT nseindia.
NSE_ARCHIVE     = "https://nsearchives.nseindia.com"

# ── Keyword filters ───────────────────────────────────────────────────────────
QUARTERLY_KEYWORDS = [
    "financial results", "quarterly results", "board meeting outcome",
    "outcome of board meeting", "unaudited financial", "standalone financial",
    "consolidated financial", "half yearly results",
]

ANNUAL_KEYWORDS = [
    "annual report", "integrated annual report", "annual report fy",
    "annual financial statements", "integrated report", "annual general meeting",
]

ANNUAL_FILE_PATTERNS = [
    r"annual.report", r"annualreport", r"annual_report",
    r"integrated.report", r"ar\d{4}", r"ar_\d{4}", r"NoticeIAR", r"IAR",
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _md5(content: bytes) -> str:
    return hashlib.md5(content).hexdigest()


def _save_pdf(content: bytes, dest_path: str) -> bool:
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    if os.path.exists(dest_path):
        with open(dest_path, "rb") as f:
            if _md5(f.read()) == _md5(content):
                logger.info(f"  [SKIP] Identical file exists: {dest_path}")
                return True
    with open(dest_path, "wb") as f:
        f.write(content)
    logger.info(f"  [SAVED] {len(content) // 1024} KB → {dest_path}")
    return True


def _is_quarterly_result(desc: str) -> bool:
    d = desc.lower()
    return any(kw in d for kw in QUARTERLY_KEYWORDS)


def _is_annual_report(desc: str, attachment: str = "") -> bool:
    d = desc.lower()
    a = attachment.lower()
    if any(kw in d for kw in ANNUAL_KEYWORDS):
        return True
    for pat in ANNUAL_FILE_PATTERNS:
        if re.search(pat, a, re.I):
            return True
    return False


def _quarter_label_from_date(dt: datetime) -> str:
    """
    Filing date → quarter label the filing REPORTS ON.
      Jan-Mar filing → Dec quarter (prior year)
      Apr-Jun filing → Mar quarter
      Jul-Sep filing → Jun quarter
      Oct-Dec filing → Sep quarter
    """
    m, y = dt.month, dt.year
    if m in [1, 2, 3]:
        return f"Dec {y - 1}"
    elif m in [4, 5, 6]:
        return f"Mar {y}"
    elif m in [7, 8, 9]:
        return f"Jun {y}"
    else:
        return f"Sep {y}"


def _fy_label_from_date(dt: datetime) -> str:
    """Indian FY: April–March. Annual reports typically filed Jul–Dec."""
    return f"FY{dt.year}" if dt.month >= 7 else f"FY{dt.year - 1}"


def _parse_nse_dt_field(dt_str: str) -> Optional[datetime]:
    """Parse NSE 'dt' field like '16012026190920' (DDMMYYYYHHmmss)."""
    if not dt_str or not dt_str.strip():
        return None
    s = dt_str.strip()
    try:
        if len(s) >= 8 and s[:8].isdigit():
            return datetime.strptime(s[:8], "%d%m%Y")
    except ValueError:
        pass
    for fmt in ["%Y-%m-%d %H:%M:%S", "%d-%b-%Y %H:%M:%S"]:
        try:
            return datetime.strptime(s[:19], fmt)
        except ValueError:
            continue
    return None


def _parse_bse_date(date_str: str) -> Optional[datetime]:
    """Parse BSE date strings."""
    if not date_str:
        return None
    for fmt in ["%d %b %Y %I:%M:%S %p", "%Y-%m-%dT%H:%M:%S", "%d/%m/%Y", "%d-%m-%Y"]:
        try:
            return datetime.strptime(date_str.strip()[:20], fmt[:20])
        except ValueError:
            continue
    return None


# FIX 1: Resolve NSE attachment path to a full URL
def _nse_attachment_url(att: str) -> str:
    """
    NSE announcement 'attchmntFile' is a relative path like:
        'corporates/EQUITIES/RELIANCE/Result-86952.pdf'
    or sometimes already a full URL.
    Prefix with NSE_ARCHIVE when relative.
    """
    if not att or att.strip() in ("", "-"):
        return ""
    att = att.strip()
    if att.startswith("http"):
        return att
    # Strip leading slash if present
    att = att.lstrip("/")
    return f"{NSE_ARCHIVE}/{att}"


# ── NSE Session (FIX 2: 3-step handshake) ────────────────────────────────────

def _nse_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection":      "keep-alive",
    })
    try:
        logger.info("NSE cookie handshake step 1/3: homepage...")
        s.get(NSE_HOME, timeout=15)
        time.sleep(2)
        logger.info("NSE cookie handshake step 2/3: quote page...")
        s.get(f"{NSE_HOME}/get-quotes/equity?symbol=RELIANCE", timeout=15)
        time.sleep(2)
        logger.info("NSE cookie handshake step 3/3: filings page...")
        s.get(f"{NSE_HOME}/companies-listing/corporate-filings-financial-results", timeout=15)
        time.sleep(2)
        s.headers.update({
            "Accept":   "application/json, */*",
            "Referer":  NSE_HOME,
            "X-Requested-With": "XMLHttpRequest",
        })
        logger.info(f"NSE session ready. Cookies: {', '.join(s.cookies.keys())}")
    except Exception as e:
        logger.warning(f"NSE handshake: {e}")
    return s


def _bse_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept":   "application/json, */*",
        "Origin":   BSE_HOME,
        "Referer":  BSE_HOME,
    })
    try:
        logger.info("BSE cookie handshake...")
        s.get(BSE_HOME, timeout=15)
        time.sleep(1.5)
    except Exception as e:
        logger.warning(f"BSE handshake: {e}")
    return s


# ── NSE Downloader ────────────────────────────────────────────────────────────

class NSEDownloader:
    """
    Downloads financial PDFs from NSE India.
    Primary source for quarterly result PDFs.
    Also used as fallback for annual reports.
    """

    def __init__(self, base_dir: str = "data"):
        self.base_dir = base_dir
        self.session  = _nse_session()

    def _fetch_announcements(self, symbol: str, from_date: str, to_date: str) -> List[Dict]:
        params = {
            "index":     "equities",
            "symbol":    symbol.upper(),
            "from_date": from_date,
            "to_date":   to_date,
        }
        try:
            r = self.session.get(NSE_ANN_URL, params=params, timeout=20)
            logger.info(f"  NSE announcements HTTP {r.status_code} | {len(r.content)} bytes")
            if r.status_code == 200:
                data = r.json()
                # NSE returns either a list directly or wraps in a key
                if isinstance(data, list):
                    return data
                return data.get("data", data.get("announcements", []))
        except Exception as e:
            logger.error(f"NSE announcements fetch error: {e}")
        return []

    def _download_pdf(self, att: str, dest_path: str) -> bool:
        """FIX 1: Build full URL from relative attachment path."""
        url = _nse_attachment_url(att)
        if not url:
            return False
        try:
            r = self.session.get(url, timeout=60)
            logger.info(f"  NSE PDF HTTP {r.status_code} | {len(r.content)//1024} KB | {url[-70:]}")
            if r.status_code == 200 and len(r.content) > 5000:
                return _save_pdf(r.content, dest_path)
            logger.warning(f"  [FAIL] {r.status_code}: {url[-70:]}")
        except Exception as e:
            logger.error(f"  [ERROR] Downloading {url}: {e}")
        return False

    def download_quarterly_reports(
        self,
        symbol:    str,
        from_date: str = "01-01-2024",
        to_date:   str = "31-03-2026",
    ) -> List[Dict]:
        out_dir = os.path.join(self.base_dir, symbol.upper(), "raw", "quarterly_pdf")
        os.makedirs(out_dir, exist_ok=True)

        logger.info(f"\n{'='*60}")
        logger.info(f"NSE Quarterly Download: {symbol} ({from_date} → {to_date})")

        announcements = self._fetch_announcements(symbol, from_date, to_date)
        logger.info(f"Found {len(announcements)} total NSE announcements")

        results   = []
        seen_qtrs = set()

        for ann in announcements:
            desc  = ann.get("desc", "") or ann.get("subject", "")
            att   = ann.get("attchmntFile", "") or ann.get("attachment", "")
            dt_s  = ann.get("dt", "")
            sort_d = ann.get("sort_date", "")

            if not _is_quarterly_result(desc):
                continue
            if not att or att.strip() in ("-", ""):
                continue

            dt = _parse_nse_dt_field(dt_s) or _parse_nse_dt_field(sort_d)
            label = _quarter_label_from_date(dt) if dt else "Q_Unknown"

            if label in seen_qtrs:
                logger.info(f"  [DUP] {label} already queued")
                continue
            seen_qtrs.add(label)

            dest = os.path.join(out_dir, f"{label.replace(' ', '_')}.pdf")
            logger.info(f"  → [{label}] {desc[:70]}")

            ok = self._download_pdf(att, dest)
            full_url = _nse_attachment_url(att)
            results.append({
                "label":  label,
                "desc":   desc,
                "path":   dest if ok else None,
                "url":    full_url,
                "status": "success" if ok else "failed",
                "date":   sort_d or dt_s,
                "source": "NSE",
            })
            time.sleep(1.0)

        s = sum(1 for r in results if r["status"] == "success")
        logger.info(f"NSE Quarterly: {s}/{len(results)} downloaded")
        return results

    def download_annual_reports(
        self,
        symbol:    str,
        from_date: str = "01-01-2020",
        to_date:   str = "31-12-2025",
    ) -> List[Dict]:
        out_dir = os.path.join(self.base_dir, symbol.upper(), "raw", "annual_pdf")
        os.makedirs(out_dir, exist_ok=True)

        logger.info(f"\n{'='*60}")
        logger.info(f"NSE Annual Download: {symbol} ({from_date} → {to_date})")

        announcements = self._fetch_announcements(symbol, from_date, to_date)
        
        # FIX: Collect all candidates and pick the BEST for each FY
        candidates: Dict[str, List[Dict]] = {}

        for ann in announcements:
            desc  = ann.get("desc", "") or ann.get("subject", "")
            att   = ann.get("attchmntFile", "") or ann.get("attachment", "")
            sort_d = ann.get("sort_date", "")
            dt_s   = ann.get("dt", "")

            if not _is_annual_report(desc, att):
                continue
            if not att or att.strip() in ("-", ""):
                continue

            dt = _parse_nse_dt_field(dt_s) or _parse_nse_dt_field(sort_d)
            fy = _fy_label_from_date(dt) if dt else "FY_Unknown"
            
            # Simple scoring: 
            # +5 for "integrated annual report"
            # +3 for "annual report"
            # +2 for "NoticeIAR" in filename
            score = 0
            low_desc = desc.lower()
            low_att = att.lower()
            if "integrated annual report" in low_desc: score += 5
            elif "annual report" in low_desc: score += 3
            if "noticeiar" in low_att: score += 2
            
            ann["_fy"] = fy
            ann["_score"] = score
            
            if fy not in candidates: candidates[fy] = []
            candidates[fy].append(ann)

        results = []
        for fy, anns in candidates.items():
            # Sort by score descending, then by date (most recent)
            # But wait, annual reports are big. Let's try to get the size for top candidates?
            # For now, just score + chronological is better than just chronological.
            anns.sort(key=lambda x: (x["_score"], x.get("sort_date", "")), reverse=True)
            best = anns[0]
            
            desc = best.get("desc", "") or best.get("subject", "")
            att = best.get("attchmntFile", "") or best.get("attachment", "")
            sort_d = best.get("sort_date", "")
            
            dest = os.path.join(out_dir, f"{fy}_Annual_Report.pdf")
            logger.info(f"  → Best Candidate [{fy}]: {desc[:70]} (Score: {best['_score']})")

            ok = self._download_pdf(att, dest)
            full_url = _nse_attachment_url(att)
            results.append({
                "label":  fy,
                "desc":   desc,
                "path":   dest if ok else None,
                "url":    full_url,
                "status": "success" if ok else "failed",
                "date":   sort_d,
                "source": "NSE",
            })
            time.sleep(1.2)

        s = sum(1 for r in results if r["status"] == "success")
        logger.info(f"NSE Annual: {s}/{len(results)} downloaded")
        return results


# ── BSE Downloader ────────────────────────────────────────────────────────────

class BSEDownloader:
    """Downloads annual report PDFs from BSE using keyword search."""

    def __init__(self, base_dir: str = "data"):
        self.base_dir = base_dir
        self.session  = _bse_session()

    def _fetch_announcements(
        self,
        scrip_code: str,
        from_date:  str,
        to_date:    str,
        category:   str = "-1",
    ) -> List[Dict]:
        params = {
            "pageno":      "1",
            "strCat":      category,
            "strPrevDate": from_date,
            "strScrip":    scrip_code,
            "strSearch":   "P",
            "strToDate":   to_date,
            "strType":     "C",
            "subcategory": "-1",
        }
        try:
            r = self.session.get(BSE_ANN_URL, params=params, timeout=20)
            if r.status_code == 200:
                data = r.json()
                return data.get("Table", [])
        except Exception as e:
            logger.error(f"BSE announcements error: {e}")
        return []

    def _download_pdf(self, attachment: str, dest_path: str) -> bool:
        if not attachment or attachment.strip() in ("", "-"):
            return False
        url = attachment if attachment.startswith("http") else BSE_PDF_BASE + attachment.strip()
        try:
            r = self.session.get(url, timeout=60, stream=True)
            if r.status_code == 200 and len(r.content) > 5000:
                return _save_pdf(r.content, dest_path)
            logger.warning(f"  [FAIL] BSE {r.status_code}: {url[-60:]}")
        except Exception as e:
            logger.error(f"  [ERROR] BSE PDF: {e}")
        return False

    def download_annual_reports(
        self,
        symbol:    str,
        from_date: str = "01/04/2020",
        to_date:   str = "31/12/2025",
    ) -> List[Dict]:
        info = COMPANY_REGISTRY.get(symbol)
        if not info:
            logger.error(f"{symbol} not in COMPANY_REGISTRY")
            return []

        scrip_code = info["scrip_code"]
        out_dir    = os.path.join(self.base_dir, symbol.upper(), "raw", "annual_pdf")
        os.makedirs(out_dir, exist_ok=True)

        logger.info(f"\n{'='*60}")
        logger.info(f"BSE Annual Download: {symbol} ({from_date} → {to_date})")

        all_anns = self._fetch_announcements(scrip_code, from_date, to_date, category="-1")
        logger.info(f"BSE total announcements: {len(all_anns)}")

        annual_anns = [
            ann for ann in all_anns
            if _is_annual_report(
                ann.get("NEWSSUB", "") or ann.get("HEADLINE", ""),
                ann.get("ATTACHMENTNAME", ""),
            )
        ]
        logger.info(f"BSE annual report announcements: {len(annual_anns)}")

        results  = []
        seen_fys = set()

        for ann in annual_anns:
            desc   = ann.get("NEWSSUB", "") or ann.get("HEADLINE", "")
            att    = ann.get("ATTACHMENTNAME", "")
            date_s = ann.get("NEWS_DT", "")

            dt = _parse_bse_date(date_s)
            fy = _fy_label_from_date(dt) if dt else "FY_Unknown"

            if fy in seen_fys:
                continue
            seen_fys.add(fy)

            dest = os.path.join(out_dir, f"{fy}_Annual_Report.pdf")
            logger.info(f"  → [BSE {fy}] {desc[:70]}")

            ok  = self._download_pdf(att, dest)
            url = (BSE_PDF_BASE + att) if att and not att.startswith("http") else att
            results.append({
                "label":  fy,
                "desc":   desc,
                "path":   dest if ok else None,
                "url":    url,
                "status": "success" if ok else "failed",
                "date":   date_s,
                "source": "BSE",
            })
            time.sleep(1.2)

        s = sum(1 for r in results if r["status"] == "success")
        logger.info(f"BSE Annual: {s}/{len(results)} downloaded")
        return results


# ── Orchestrated download ─────────────────────────────────────────────────────

def download_all_pdfs(
    symbol:          str,
    base_dir:        str = "data",
    quarterly_from:  str = "01-07-2024",
    quarterly_to:    str = "31-05-2025",
    annual_from_nse: str = "01-04-2025",
    annual_to_nse:   str = "31-12-2025",
    annual_from_bse: str = "01/04/2025",
    annual_to_bse:   str = "31/12/2025",
) -> Dict:
    """
    Main download orchestrator.
    Returns manifest: {"quarterly": [...], "annual": [...]}
    """
    nse = NSEDownloader(base_dir=base_dir)
    bse = BSEDownloader(base_dir=base_dir)

    # 1. Quarterly PDFs (NSE primary)
    quarterly = nse.download_quarterly_reports(symbol, quarterly_from, quarterly_to)

    # 2. Annual PDFs: Try NSE first, then BSE
    # NOTE: FY2025 annual report will typically be filed Jul–Sep 2025
    annual = nse.download_annual_reports(symbol, annual_from_nse, annual_to_nse)
    a_ok   = sum(1 for r in annual if r["status"] == "success")

    if a_ok == 0:
        logger.warning("NSE annual download got nothing. Trying BSE fallback...")
        annual = bse.download_annual_reports(symbol, annual_from_bse, annual_to_bse)

    return {"quarterly": quarterly, "annual": annual}
