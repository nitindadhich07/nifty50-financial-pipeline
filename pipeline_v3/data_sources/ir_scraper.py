import logging
import requests
from bs4 import BeautifulSoup
import pandas as pd
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

class IRScraper:
    """Scrapes financial tables from company Investor Relations pages."""
    
    def __init__(self):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        }

    def scrape_tables(self, url: str) -> List[pd.DataFrame]:
        """Detects and extracts HTML tables from a given IR URL."""
        logger.info(f"Scraping IR tables from: {url}")
        try:
            r = requests.get(url, headers=self.headers, timeout=20)
            r.raise_for_status()
            
            # Use pandas to read all tables
            tables = pd.read_html(r.text)
            logger.info(f"Found {len(tables)} tables on page")
            
            # Filter tables that look like financial statements
            financial_tables = []
            for df in tables:
                if self._is_financial_table(df):
                    financial_tables.append(df)
            
            return financial_tables
        except Exception as e:
            logger.error(f"Error scraping IR page {url}: {e}")
            return []

    def _is_financial_table(self, df: pd.DataFrame) -> bool:
        """Determines if a dataframe looks like a financial statement."""
        # Check for common financial keywords in the first column or headers
        keywords = {"revenue", "profit", "assets", "liabilities", "equity", "expenditure", "income"}
        df_str = str(df.iloc[:, 0]).lower() + str(df.columns).lower()
        
        matches = sum(1 for k in keywords if k in df_str)
        return matches >= 2
