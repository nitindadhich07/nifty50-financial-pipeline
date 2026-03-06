import logging
import sys
import os
from datetime import datetime, timedelta
from typing import List, Dict

# Add parent directory to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from scrapers.exchange_scrapers import ExchangeScraper
from ingestion.parallel_downloader import ParallelDownloader, save_filings_to_db

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class Q4Downloader:
    def __init__(self):
        self.exchange_scraper = ExchangeScraper()
        self.downloader = ParallelDownloader(max_workers=20)
        # Q4 FY2025-26 usually means Jan 2026 to Mar 2026.
        # Since the current date is March 6, 2026, we look at reports from Jan 1, 2026.
        self.start_date = datetime(2026, 1, 1)
        self.end_date = datetime(2026, 3, 31)

    def run_historical_ingestion(self):
        """
        Runs a cycle to fetch reports from the start of the quarter.
        Note: The standard API implementation usually returns recent data.
        To get historical data, we'd typically need to hit specific end-points or scroll.
        For this task, we'll process what's available in the current feed first.
        """
        logger.info(f"Starting Q4 FY2025-26 historical ingestion (from {self.start_date.date()})...")
        
        # 1. Fetch from NSE
        # The default API returns recent announcements. 
        # In a real-world scenario, we'd iterate through dates.
        nse_results = self.exchange_scraper.fetch_nse_announcements()
        logger.info(f"Fetched {len(nse_results)} recent announcements from NSE")
        
        # 2. Fetch from BSE
        bse_results = self.exchange_scraper.fetch_bse_announcements()
        logger.info(f"Fetched {len(bse_results)} recent announcements from BSE")
        
        all_results = nse_results + bse_results
        
        # Filter by date range
        q4_results = [
            r for r in all_results 
            if self.start_date <= r['announcement_date'] <= self.end_date
        ]
        
        logger.info(f"Filtered {len(q4_results)} results specifically for Q4 FY2025-26")
        
        if not q4_results:
            logger.info("No Q4 announcements found in the current feed. In a production environment, we would use date-range specific API calls.")
            return

        # 3. Parallel Download
        logger.info(f"Processing {len(q4_results)} Q4 downloads...")
        download_results = self.downloader.process_downloads(q4_results)
        
        # 4. Save to DB
        save_filings_to_db(download_results)
        
        success_count = sum(1 for r in download_results if r['status'] == 'success')
        skipped_count = sum(1 for r in download_results if r['status'] == 'skipped')
        logger.info(f"Q4 Ingestion cycle completed. Success: {success_count}, Skipped: {skipped_count}")

if __name__ == "__main__":
    downloader = Q4Downloader()
    downloader.run_historical_ingestion()
