#!/usr/bin/env python3
"""
Nifty 50 Financial Results Downloader
Downloads original Q3 FY26 (Oct-Dec 2025) PDFs from NSE India for all Nifty 50 companies.
Uses session-based cookie handshake + nsearchives.nseindia.com as the PDF server.
"""

import requests
import time
import os
import logging
import json

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# All current Nifty 50 symbols (as of Mar 2026)
NIFTY50_SYMBOLS = [
    "HDFCBANK", "ICICIBANK", "RELIANCE", "SBIN", "BHARTIARTL",
    "AXISBANK", "BEL", "INFY", "TCS", "ETERNAL",
    "LT", "ONGC", "SHRIRAMFIN", "HINDALCO", "INDIGO",
    "KOTAKBANK", "M&M", "ITC", "SUNPHARMA", "BAJFINANCE",
    "NTPC", "TATASTEEL", "MARUTI", "COALINDIA", "POWERGRID",
    "BAJAJFINSV", "GRASIM", "ADANIPORTS", "BAJAJ-AUTO", "HINDUNILVR",
    "ADANIENT", "ULTRACEMCO", "MAXHEALTH", "JIOFIN", "EICHERMOT",
    "HCLTECH", "TITAN", "WIPRO", "ASIANPAINT", "HDFCLIFE",
    "APOLLOHOSP", "SBILIFE", "JSWSTEEL", "TECHM", "TRENT",
    "DRREDDY", "NESTLEIND", "TATACONSUM", "CIPLA", "TMPV"
]

# Financial result keywords to detect the right PDF
FR_KEYWORDS = [
    "financial results", "board meeting outcome", "outcome of board meeting",
    "unaudited", "quarterly results", "half yearly results", "nine months",
    "standalone financial", "consolidated financial"
]

# Attachment filename patterns for financial results
FR_FILENAME_PATTERNS = ["_FR_", "FinancialResults", "fin_results", "results_"]

FROM_DATE = "01-01-2026"
TO_DATE   = "06-03-2026"
OUTPUT_DIR = "data/financial_results"


def create_session():
    """Create a requests session with NSE cookie handshake."""
    s = requests.Session()
    s.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': '*/*',
        'Accept-Language': 'en-US,en;q=0.5',
        'Connection': 'keep-alive',
        'Referer': 'https://www.nseindia.com/companies-listing/corporate-filings-announcements',
    })
    logger.info("Performing NSE cookie handshake...")
    try:
        s.get('https://www.nseindia.com', timeout=15)
        time.sleep(2)
    except Exception as e:
        logger.warning(f"Handshake step returned error (may still work): {e}")
    return s


def fetch_announcements(session, symbol):
    """Fetch announcements for a symbol filtered by Q3 FY26 date range."""
    url = (
        f"https://www.nseindia.com/api/corporate-announcements"
        f"?index=equities&symbol={symbol}"
        f"&from_date={FROM_DATE}&to_date={TO_DATE}"
    )
    try:
        r = session.get(url, timeout=15)
        if r.status_code == 200 and r.text.strip():
            return r.json()
    except Exception as e:
        logger.error(f"Error fetching {symbol}: {e}")
    return []


def is_financial_result(item):
    """Check if an announcement item is a financial result."""
    desc = (item.get('desc') or '').lower()
    attachment = item.get('attchmntFile') or ''
    
    # Check by description keyword
    for kw in FR_KEYWORDS:
        if kw in desc:
            return True
    
    # Check by PDF filename pattern
    for pattern in FR_FILENAME_PATTERNS:
        if pattern in attachment:
            return True
    
    return False


def download_pdf(session, url, dest_path):
    """Download a PDF from the archive server and save to dest_path."""
    if not url or url == '-':
        return False
    try:
        r = session.get(url, timeout=30)
        if r.status_code == 200 and len(r.content) > 1000:
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            with open(dest_path, 'wb') as f:
                f.write(r.content)
            size_kb = len(r.content) // 1024
            logger.info(f"  ✓ Saved {size_kb} KB → {dest_path}")
            return True
        else:
            logger.warning(f"  ✗ Bad response ({r.status_code}, {len(r.content)} bytes) from {url}")
    except Exception as e:
        logger.error(f"  ✗ Download error: {e}")
    return False


def download_all():
    session = create_session()
    results = {}

    for symbol in NIFTY50_SYMBOLS:
        logger.info(f"\n{'='*50}")
        logger.info(f"Processing {symbol}...")
        
        announcements = fetch_announcements(session, symbol)
        logger.info(f"  Got {len(announcements)} announcements")
        
        downloaded = False
        for item in announcements:
            if is_financial_result(item):
                pdf_url = item.get('attchmntFile', '')
                if not pdf_url or pdf_url == '-':
                    continue
                
                dest_path = os.path.join(OUTPUT_DIR, symbol, "2026", "Q3_FY26.pdf")
                logger.info(f"  → Found result: {item.get('desc')} | {item.get('attime')}")
                logger.info(f"  → PDF: {pdf_url}")
                
                if download_pdf(session, pdf_url, dest_path):
                    results[symbol] = {"status": "success", "url": pdf_url, "path": dest_path}
                    downloaded = True
                    break  # take the first match

        if not downloaded:
            logger.warning(f"  ✗ No Q3 FY26 financial result PDF found for {symbol}")
            results[symbol] = {"status": "not_found"}
        
        # Polite delay between companies
        time.sleep(1.5)

    # Summary
    success = [s for s, v in results.items() if v['status'] == 'success']
    not_found = [s for s, v in results.items() if v['status'] == 'not_found']
    
    logger.info(f"\n{'='*50}")
    logger.info(f"DOWNLOAD SUMMARY")
    logger.info(f"Successfully downloaded: {len(success)}/{len(NIFTY50_SYMBOLS)}")
    logger.info(f"✓ Success: {', '.join(success)}")
    logger.info(f"✗ Not found: {', '.join(not_found)}")

    with open("data/download_results.json", 'w') as f:
        json.dump(results, f, indent=2)
    logger.info("Results saved to data/download_results.json")


if __name__ == "__main__":
    download_all()
