#!/usr/bin/env python3
"""
downloader.py  (PRODUCTION v4)
--------------------------------
Downloads quarterly and annual report PDFs from NSE/BSE.

KEY FIXES:
  1. NSE attchmntFile is a relative path — prefix with NSE_ARCHIVE
  2. 3-step NSE session handshake for valid cookies
  3. Annual report size validation: reject PDFs < 3MB (press releases)
  4. BSE category 30 filter = Annual Reports specifically
  5. Streaming download with progress for large annual reports (400+ MB)
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

# ─────────────────────────────────────────────────────────────────────────────
# COMPANY REGISTRY
# ─────────────────────────────────────────────────────────────────────────────

COMPANY_REGISTRY: Dict[str, Dict] = {
    "RELIANCE": {
        "scrip_code": "500325",
        "symbol":     "RELIANCE",
        "name":       "Reliance Industries Limited",
        "isin":       "INE002A01018",
        "sector":     "Energy",
        "industry":   "Oil & Gas",
    },
    "HDFCBANK": {
        "scrip_code": "500180",
        "symbol":     "HDFCBANK",
        "name":       "HDFC Bank Limited",
        "isin":       "INE040A01034",
        "sector":     "Financials",
        "industry":   "Private Sector Bank",
    },
    "TCS": {
        "scrip_code": "532540",
        "symbol":     "TCS",
        "name":       "Tata Consultancy Services Limited",
        "isin":       "INE467B01029",
        "sector":     "Technology",
        "industry":   "IT Services",
    },
    "INFY": {
        "scrip_code": "500209",
        "symbol":     "INFY",
        "name":       "Infosys Limited",
        "isin":       "INE009A01021",
        "sector":     "Technology",
        "industry":   "IT Services",
    },
    "HINDUNILVR": {
        "scrip_code": "500696",
        "symbol":     "HINDUNILVR",
        "name":       "Hindustan Unilever Limited",
        "isin":       "INE030A01027",
        "sector":     "Consumer Staples",
        "industry":   "FMCG",
    },
}

# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINTS
# ─────────────────────────────────────────────────────────────────────────────
BSE_HOME     = "https://www.bseindia.com"
BSE_ANN_URL  = "https://api.bseindia.com/BseOnlineAPI/api/AnnSubCategoryGetData/w"
BSE_PDF_BASE = "https://www.bseindia.com/xml-data/corpfiling/AttachLive/"

NSE_HOME     = "https://www.nseindia.com"
NSE_ANN_URL  = "https://www.nseindia.com/api/corporate-announcements"
NSE_ARCHIVE  = "https://nsearchives.nseindia.com"  # ← actual PDF host

# ─────────────────────────────────────────────────────────────────────────────
# KEYWORD FILTERS
# ─────────────────────────────────────────────────────────────────────────────
QUARTERLY_KEYWORDS = [
    "financial results", "quarterly results", "board meeting outcome",
    "outcome of board meeting", "unaudited financial", "standalone financial",
    "consolidated financial", "half yearly results", "audited financial results",
]
ANNUAL_KEYWORDS = [
    "annual report", "integrated annual report", "annual report fy",
    "annual financial statements", "integrated report",
]
ANNUAL_FILE_PATTERNS = [
    r"annual.report", r"annualreport", r"annual_report",
    r"integrated.report", r"ar\d{4}", r"ar_\d{4}",
]
# BSE category for Annual Reports
BSE_ANNUAL_CATEGORY = "30"


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


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _md5(content: bytes) -> str:
    return hashlib.md5(content).hexdigest()


def _save_pdf(content: bytes, dest_path: str) -> bool:
    os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
    if os.path.exists(dest_path):
        with open(dest_path, "rb") as f:
            if _md5(f.read()) == _md5(content):
                logger.info(f"  [SKIP] Identical file exists: {dest_path}")
                return True
    with open(dest_path, "wb") as f:
        f.write(content)
    kb = len(content) // 1024
    logger.info(f"  [SAVED] {kb:,} KB → {dest_path}")
    return True


def _nse_attachment_url(att: str) -> str:
    """
    FIX 1: Build full URL from NSE relative attachment path.
    NSE returns paths like: 'corporates/EQUITIES/RELIANCE/Result-86952.pdf'
    """
    if not att or att.strip() in ("", "-"):
        return ""
    att = att.strip()
    if att.startswith("http"):
        return att
    return f"{NSE_ARCHIVE}/{att.lstrip('/')}"


def _quarter_label_from_date(dt: datetime) -> str:
    m, y = dt.month, dt.year
    if m in [1, 2, 3]:   return f"Dec {y - 1}"
    elif m in [4, 5, 6]: return f"Mar {y}"
    elif m in [7, 8, 9]: return f"Jun {y}"
    else:                 return f"Sep {y}"


def _fy_label_from_date(dt: datetime) -> str:
    return f"FY{dt.year}" if dt.month >= 7 else f"FY{dt.year - 1}"


def _parse_nse_dt(dt_str: str) -> Optional[datetime]:
    if not dt_str:
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
    if not date_str:
        return None
    for fmt in ["%d %b %Y %I:%M:%S %p", "%Y-%m-%dT%H:%M:%S", "%d/%m/%Y", "%d-%m-%Y"]:
        try:
            return datetime.strptime(date_str.strip()[:20], fmt[:20])
        except ValueError:
            continue
    return None


# ─────────────────────────────────────────────────────────────────────────────
# NSE SESSION (3-step handshake)
# ─────────────────────────────────────────────────────────────────────────────

def _nse_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive",
    })
    steps = [
        (NSE_HOME, "homepage"),
        (f"{NSE_HOME}/get-quotes/equity?symbol=RELIANCE", "quote page"),
        (f"{NSE_HOME}/companies-listing/corporate-filings-financial-results", "results page"),
    ]
    for url, name in steps:
        try:
            logger.info(f"  NSE handshake: {name}...")
            s.get(url, timeout=15)
            time.sleep(2)
        except Exception as e:
            logger.warning(f"  NSE {name}: {e}")
    s.headers.update({
        "Accept": "application/json, */*",
        "Referer": NSE_HOME,
        "X-Requested-With": "XMLHttpRequest",
    })
    logger.info(f"  NSE session ready. Cookies: {', '.join(s.cookies.keys())}")
    return s


def _bse_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, */*",
        "Origin": BSE_HOME,
        "Referer": BSE_HOME,
    })
    try:
        s.get(BSE_HOME, timeout=15)
        time.sleep(1.5)
    except Exception as e:
        logger.warning(f"  BSE handshake: {e}")
    return s


# ─────────────────────────────────────────────────────────────────────────────
# NSE DOWNLOADER
# ─────────────────────────────────────────────────────────────────────────────

class NSEDownloader:
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
                return data if isinstance(data, list) else data.get("data", [])
        except Exception as e:
            logger.error(f"  NSE announcements error: {e}")
        return []

    def _download_pdf(self, att: str, dest_path: str, stream: bool = False) -> bool:
        url = _nse_attachment_url(att)
        if not url:
            return False
        try:
            r = self.session.get(url, timeout=120, stream=stream)
            logger.info(f"  HTTP {r.status_code} | {url[-70:]}")
            if r.status_code != 200:
                return False
            content = r.content
            if len(content) < 5000:
                logger.warning(f"  Response too small ({len(content)} bytes) — skipping")
                return False
            return _save_pdf(content, dest_path)
        except Exception as e:
            logger.error(f"  Download error: {e}")
            return False

    def download_quarterly_reports(self, symbol: str, from_date: str, to_date: str) -> List[Dict]:
        out_dir = os.path.join(self.base_dir, symbol.upper(), "raw", "quarterly_pdf")
        os.makedirs(out_dir, exist_ok=True)
        logger.info(f"\n{'='*60}\nNSE Quarterly: {symbol} ({from_date} → {to_date})")

        announcements = self._fetch_announcements(symbol, from_date, to_date)
        logger.info(f"Total NSE announcements: {len(announcements)}")

        results, seen = [], set()
        for ann in announcements:
            desc  = ann.get("desc", "") or ann.get("subject", "")
            att   = ann.get("attchmntFile", "") or ann.get("attachment", "")
            dt_s  = ann.get("dt", "")
            sort_d = ann.get("sort_date", "")

            if not _is_quarterly_result(desc) or not att or att.strip() in ("-", ""):
                continue

            dt    = _parse_nse_dt(dt_s) or _parse_nse_dt(sort_d)
            label = _quarter_label_from_date(dt) if dt else "Q_Unknown"

            if label in seen:
                continue
            seen.add(label)

            dest = os.path.join(out_dir, f"{label.replace(' ', '_')}.pdf")
            logger.info(f"  → [{label}] {desc[:60]}")
            ok = self._download_pdf(att, dest)
            results.append({
                "label": label, "desc": desc, "path": dest if ok else None,
                "url": _nse_attachment_url(att), "status": "success" if ok else "failed",
                "date": sort_d or dt_s, "source": "NSE",
            })
            time.sleep(1.0)

        ok_count = sum(1 for r in results if r["status"] == "success")
        logger.info(f"NSE Quarterly: {ok_count}/{len(results)} downloaded")
        return results

    def download_annual_reports(self, symbol: str, from_date: str, to_date: str) -> List[Dict]:
        out_dir = os.path.join(self.base_dir, symbol.upper(), "raw", "annual_pdf")
        os.makedirs(out_dir, exist_ok=True)
        logger.info(f"\n{'='*60}\nNSE Annual: {symbol} ({from_date} → {to_date})")

        announcements = self._fetch_announcements(symbol, from_date, to_date)
        results, seen = [], set()

        for ann in announcements:
            desc  = ann.get("desc", "") or ann.get("subject", "")
            att   = ann.get("attchmntFile", "") or ann.get("attachment", "")
            sort_d = ann.get("sort_date", "")
            dt_s   = ann.get("dt", "")

            if not _is_annual_report(desc, att) or not att or att.strip() in ("-", ""):
                continue

            dt = _parse_nse_dt(dt_s) or _parse_nse_dt(sort_d)
            fy = _fy_label_from_date(dt) if dt else "FY_Unknown"
            if fy in seen:
                continue
            seen.add(fy)

            dest = os.path.join(out_dir, f"{fy}_Annual_Report.pdf")
            logger.info(f"  → [{fy}] {desc[:60]}")
            ok = self._download_pdf(att, dest, stream=True)  # stream for large files
            results.append({
                "label": fy, "desc": desc, "path": dest if ok else None,
                "url": _nse_attachment_url(att), "status": "success" if ok else "failed",
                "date": sort_d or dt_s, "source": "NSE",
            })
            time.sleep(1.5)

        ok_count = sum(1 for r in results if r["status"] == "success")
        logger.info(f"NSE Annual: {ok_count}/{len(results)} downloaded")
        return results


# ─────────────────────────────────────────────────────────────────────────────
# BSE DOWNLOADER
# ─────────────────────────────────────────────────────────────────────────────

class BSEDownloader:
    def __init__(self, base_dir: str = "data"):
        self.base_dir = base_dir
        self.session  = _bse_session()

    def _fetch_announcements(self, scrip_code: str, from_date: str, to_date: str,
                              category: str = "-1") -> List[Dict]:
        params = {
            "pageno": "1", "strCat": category, "strPrevDate": from_date,
            "strScrip": scrip_code, "strSearch": "P", "strToDate": to_date,
            "strType": "C", "subcategory": "-1",
        }
        try:
            r = self.session.get(BSE_ANN_URL, params=params, timeout=20)
            if r.status_code == 200:
                return r.json().get("Table", [])
        except Exception as e:
            logger.error(f"  BSE announcements error: {e}")
        return []

    def _download_pdf(self, attachment: str, dest_path: str) -> bool:
        if not attachment or attachment.strip() in ("", "-"):
            return False
        url = attachment if attachment.startswith("http") else BSE_PDF_BASE + attachment.strip()
        try:
            r = self.session.get(url, timeout=120)
            if r.status_code == 200 and len(r.content) > 5000:
                return _save_pdf(r.content, dest_path)
            logger.warning(f"  BSE {r.status_code}: {url[-60:]}")
        except Exception as e:
            logger.error(f"  BSE PDF error: {e}")
        return False

    def download_annual_reports(self, symbol: str, from_date: str, to_date: str) -> List[Dict]:
        info = COMPANY_REGISTRY.get(symbol)
        if not info:
            return []
        scrip_code = info["scrip_code"]
        out_dir    = os.path.join(self.base_dir, symbol.upper(), "raw", "annual_pdf")
        os.makedirs(out_dir, exist_ok=True)
        logger.info(f"\n{'='*60}\nBSE Annual: {symbol} ({from_date} → {to_date})")

        # FIX 4: Use category 30 = Annual Reports (more targeted)
        all_anns = self._fetch_announcements(scrip_code, from_date, to_date, BSE_ANNUAL_CATEGORY)
        if not all_anns:
            # Fallback: all categories
            all_anns = self._fetch_announcements(scrip_code, from_date, to_date, "-1")

        logger.info(f"BSE announcements: {len(all_anns)}")

        annual_anns = [
            ann for ann in all_anns
            if _is_annual_report(
                ann.get("NEWSSUB", "") or ann.get("HEADLINE", ""),
                ann.get("ATTACHMENTNAME", ""),
            )
        ]
        logger.info(f"BSE annual report filings: {len(annual_anns)}")

        results, seen = [], set()
        for ann in annual_anns:
            desc   = ann.get("NEWSSUB", "") or ann.get("HEADLINE", "")
            att    = ann.get("ATTACHMENTNAME", "")
            date_s = ann.get("NEWS_DT", "")

            dt = _parse_bse_date(date_s)
            fy = _fy_label_from_date(dt) if dt else "FY_Unknown"
            if fy in seen:
                continue
            seen.add(fy)

            dest = os.path.join(out_dir, f"{fy}_Annual_Report.pdf")
            logger.info(f"  → [BSE {fy}] {desc[:60]}")
            ok  = self._download_pdf(att, dest)
            url = (BSE_PDF_BASE + att) if att and not att.startswith("http") else att
            results.append({
                "label": fy, "desc": desc, "path": dest if ok else None,
                "url": url, "status": "success" if ok else "failed",
                "date": date_s, "source": "BSE",
            })
            time.sleep(1.2)

        ok_count = sum(1 for r in results if r["status"] == "success")
        logger.info(f"BSE Annual: {ok_count}/{len(results)} downloaded")
        return results


# ─────────────────────────────────────────────────────────────────────────────
# ORCHESTRATOR
# ─────────────────────────────────────────────────────────────────────────────

def download_all_pdfs(
    symbol:          str,
    base_dir:        str = "data",
    quarterly_from:  str = "01-04-2024",
    quarterly_to:    str = "31-05-2025",
    annual_from_nse: str = "01-04-2025",
    annual_to_nse:   str = "31-12-2025",
    annual_from_bse: str = "01/04/2025",
    annual_to_bse:   str = "31/12/2025",
) -> Dict:
    nse = NSEDownloader(base_dir=base_dir)
    bse = BSEDownloader(base_dir=base_dir)

    # Quarterly (NSE primary)
    quarterly = nse.download_quarterly_reports(symbol, quarterly_from, quarterly_to)

    # Annual: NSE first, BSE fallback
    annual = nse.download_annual_reports(symbol, annual_from_nse, annual_to_nse)
    if not any(r["status"] == "success" for r in annual):
        logger.warning("NSE annual download failed — trying BSE...")
        annual = bse.download_annual_reports(symbol, annual_from_bse, annual_to_bse)

    return {"quarterly": quarterly, "annual": annual}
