import os
import requests
import hashlib
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict
from datetime import datetime
from sqlalchemy.orm import Session
from db.models import Filing, SessionLocal, Company

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'financial_results')

class ParallelDownloader:
    def __init__(self, max_workers: int = 15):
        self.max_workers = max_workers
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        }
        os.makedirs(DATA_DIR, exist_ok=True)

    def calculate_hash(self, content: bytes) -> str:
        return hashlib.md5(content).hexdigest()

    def get_structured_path(self, symbol: str, date: datetime) -> str:
        year = date.year
        # Simple quarter calculation
        quarter = f"Q{(date.month - 1) // 3 + 1}"
        path = os.path.join(DATA_DIR, symbol, str(year))
        os.makedirs(path, exist_ok=True)
        return os.path.join(path, f"{quarter}.pdf")

    def download_pdf(self, filing_data: Dict) -> Dict:
        """
        Downloads a single PDF and returns the updated filing data.
        """
        url = filing_data.get('pdf_url')
        if not url:
            return {"status": "error", "message": "No PDF URL", "data": filing_data}

        try:
            response = requests.get(url, headers=self.headers, timeout=30)
            response.raise_for_status()
            content = response.content
            
            pdf_hash = self.calculate_hash(content)
            
            # Check for deduplication in DB
            db: Session = SessionLocal()
            existing = db.query(Filing).filter(Filing.pdf_hash == pdf_hash).first()
            if existing:
                db.close()
                return {"status": "skipped", "message": "Duplicate PDF hash", "data": filing_data}
            
            symbol = filing_data.get('symbol', 'UNKNOWN')
            date = filing_data.get('announcement_date', datetime.now())
            file_path = self.get_structured_path(symbol, date)
            
            with open(file_path, 'wb') as f:
                f.write(content)
            
            filing_data['pdf_path'] = file_path
            filing_data['pdf_hash'] = pdf_hash
            db.close()
            return {"status": "success", "data": filing_data}
            
        except Exception as e:
            logger.error(f"Error downloading {url}: {e}")
            return {"status": "error", "message": str(e), "data": filing_data}

    def process_downloads(self, filings: List[Dict]):
        """
        Parallel processing of PDF downloads.
        """
        results = []
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_filing = {executor.submit(self.download_pdf, filing): filing for filing in filings}
            for future in as_completed(future_to_filing):
                results.append(future.result())
        
        return results

def save_filings_to_db(results: List[Dict]):
    db: Session = SessionLocal()
    for res in results:
        if res['status'] == 'success':
            data = res['data']
            # Get or create company
            company = db.query(Company).filter(Company.symbol == data['symbol']).first()
            if not company:
                company = Company(symbol=data['symbol'], name=data['company_name'])
                db.add(company)
                db.commit()
                db.refresh(company)
            
            # Create filing
            new_filing = Filing(
                company_id=company.id,
                announcement_id=data.get('announcement_id'),
                announcement_date=data.get('announcement_date'),
                title=data.get('announcement_title'),
                source=data.get('source'),
                pdf_path=data.get('pdf_path'),
                pdf_hash=data.get('pdf_hash'),
                category=data.get('category')
            )
            db.add(new_filing)
    db.commit()
    db.close()

if __name__ == "__main__":
    # Test logic
    pass
