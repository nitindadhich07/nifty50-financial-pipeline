import requests
import json
import logging
from datetime import datetime, timedelta
from typing import List, Dict
import time

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class ExchangeScraper:
    def __init__(self):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
        }
        self.keywords = ["Financial Results", "Quarterly Results", "Standalone Results", "Consolidated Results", "Earnings"]

    def is_financial_result(self, title: str) -> bool:
        return any(keyword.lower() in title.lower() for keyword in self.keywords)

    def fetch_nse_announcements(self) -> List[Dict]:
        """
        Fetches announcements from NSE API.
        Note: NSE requires specific headers and cookie handling.
        """
        url = "https://www.nseindia.com/api/corporate-announcements"
        # In a real scenario, we might need to hit the home page first to get cookies
        try:
            session = requests.Session()
            session.get("https://www.nseindia.com", headers=self.headers)
            response = session.get(url, headers=self.headers)
            response.raise_for_status()
            data = response.json()
            
            results = []
            for item in data:
                title = item.get('desc', '')
                if self.is_financial_result(title):
                    results.append({
                        "symbol": item.get('symbol'),
                        "company_name": item.get('companyName'),
                        "announcement_date": datetime.strptime(item.get('attime'), '%d-%b-%Y %H:%M:%S') if item.get('attime') else datetime.now(),
                        "announcement_title": title,
                        "pdf_url": f"https://www.nseindia.com/corporates/results/{item.get('attachment')}" if item.get('attachment') else None,
                        "category": item.get('category'),
                        "source": "NSE",
                        "announcement_id": item.get('seqId') # Example ID
                    })
            return results
        except Exception as e:
            logger.error(f"Error fetching NSE announcements: {e}")
            return []

    def fetch_bse_announcements(self) -> List[Dict]:
        """
        Fetches announcements from BSE API.
        """
        # BSE has a different API structure. Using a placeholder for demonstration.
        # Example URL: https://api.bseindia.com/BseOnlineAPI/api/AnnSubCategory/w?date=...
        url = "https://api.bseindia.com/BseOnlineAPI/api/AnnSubCategory/w"
        params = {
            "date": datetime.now().strftime('%Y%m%d'),
            "category": "Corporate Announcement"
        }
        try:
            response = requests.get(url, params=params, headers=self.headers)
            response.raise_for_status()
            data = response.json()
            
            results = []
            # Note: BSE API structure might differ. This is a generic mapping.
            for item in data:
                title = item.get('NEWS_TITLE', '')
                if self.is_financial_result(title):
                    results.append({
                        "symbol": item.get('SCRIP_CD'),
                        "company_name": item.get('SLONGNAME'),
                        "announcement_date": datetime.strptime(item.get('DT_TM'), '%Y-%m-%dT%H:%M:%S') if item.get('DT_TM') else datetime.now(),
                        "announcement_title": title,
                        "pdf_url": item.get('ATTACHMENTNAME'),
                        "category": item.get('CATEGORYNAME'),
                        "source": "BSE",
                        "announcement_id": item.get('NEWSID')
                    })
            return results
        except Exception as e:
            logger.error(f"Error fetching BSE announcements: {e}")
            return []

if __name__ == "__main__":
    scraper = ExchangeScraper()
    # nse_data = scraper.fetch_nse_announcements()
    # logger.info(f"Fetched {len(nse_data)} NSE results")
