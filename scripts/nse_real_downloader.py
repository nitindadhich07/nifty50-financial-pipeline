import requests
import os
import time
import logging
from datetime import datetime

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class NSEDirectDownloader:
    def __init__(self):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.nseindia.com/companies-listing/corporate-filings-announcements",
            "X-Requested-With": "XMLHttpRequest"
        }
        self.session = requests.Session()
        self.session.headers.update(self.headers)
        # Initial call to get cookies
        self.session.get("https://www.nseindia.com", timeout=15)

    def fetch_announcements(self, symbol):
        """
        Fetches results for a specific symbol.
        """
        # url = f"https://www.nseindia.com/api/corporate-announcements?symbol={symbol}&index=equities"
        # The financial results specifically:
        url = f"https://www.nseindia.com/api/corporate-announcements?symbol={symbol}&category=financial%20results"
        
        try:
            response = self.session.get(url, timeout=15)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Failed to fetch for {symbol}: {e}")
            return []

    def download_pdf(self, symbol, attachment_name):
        """
        Downloads the actual PDF from the attachment name.
        """
        # NSE PDF URLs usually follow this pattern for corporate announcements
        url = f"https://nsearchives.nseindia.com/corporate/{attachment_name}"
        
        path = f"data/financial_results/{symbol}/2026"
        os.makedirs(path, exist_ok=True)
        file_path = os.path.join(path, "Q3.pdf")
        
        try:
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            with open(file_path, 'wb') as f:
                f.write(response.content)
            logger.info(f"Successfully downloaded {symbol} PDF to {file_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to download PDF for {symbol}: {e}")
            return False

def main():
    symbols = ["RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY"]
    downloader = NSEDirectDownloader()
    
    for symbol in symbols:
        data = downloader.fetch_announcements(symbol)
        found = False
        for item in data:
            desc = item.get('desc', '').lower()
            if "financial results" in desc or "standalone" in desc or "consolidated" in desc:
                attachment = item.get('attachment')
                attime = item.get('attime', '')
                # Filter for 2026 filings (covers Q3 FY26)
                if attachment and "2026" in attime or "Jan" in attime or "Feb" in attime or "Mar" in attime:
                    if downloader.download_pdf(symbol, attachment):
                        found = True
                        break
        if not found:
            logger.warning(f"No suitable 2026 financial result found for {symbol}")
        time.sleep(2) # Avoid aggressive rate limiting

if __name__ == "__main__":
    main()
