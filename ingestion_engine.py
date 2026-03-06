import logging
import time
from datetime import datetime
from typing import List, Dict

from scrapers.exchange_scrapers import ExchangeScraper
from scrapers.fallback_scraper import FallbackScraper
from ingestion.parallel_downloader import ParallelDownloader, save_filings_to_db

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class IngestionEngine:
    def __init__(self):
        self.exchange_scraper = ExchangeScraper()
        self.fallback_scraper = FallbackScraper()
        self.downloader = ParallelDownloader(max_workers=20)

    def run_cycle(self):
        """
        Runs a full ingestion cycle.
        """
        logger.info("Starting ingestion cycle...")
        
        # 1. Fetch from NSE
        nse_results = self.exchange_scraper.fetch_nse_announcements()
        logger.info(f"Fetched {len(nse_results)} announcements from NSE")
        
        # 2. Fetch from BSE
        bse_results = self.exchange_scraper.fetch_bse_announcements()
        logger.info(f"Fetched {len(bse_results)} announcements from BSE")
        
        all_results = nse_results + bse_results
        
        if not all_results:
            logger.info("No new announcements found.")
            return

        # 3. Parallel Download
        logger.info(f"Processing {len(all_results)} downloads...")
        download_results = self.downloader.process_downloads(all_results)
        
        # 4. Save to DB
        save_filings_to_db(download_results)
        
        logger.info("Ingestion cycle completed.")

def run_scheduler():
    engine = IngestionEngine()
    while True:
        try:
            engine.run_cycle()
        except Exception as e:
            logger.error(f"Error in ingestion cycle: {e}")
        
        logger.info("Sleeping for 30 minutes...")
        time.sleep(30 * 60)

if __name__ == "__main__":
    engine = IngestionEngine()
    engine.run_cycle()
