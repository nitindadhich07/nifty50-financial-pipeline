import os
import hashlib
from datetime import datetime
from sqlalchemy.orm import Session
import sys

# Add parent directory to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from db.models import Filing, Company, SessionLocal

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'financial_results')

# Nifty 50 Symbols (subset for demonstration)
NIFTY_50 = [
    "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY", 
    "BHARTIARTL", "SBIN", "LICI", "ITC", "HINDUNILVR"
]

def simulate_q4_downloads():
    db: Session = SessionLocal()
    os.makedirs(DATA_DIR, exist_ok=True)
    
    print(f"Simulating Q4 FY2025-26 downloads for {len(NIFTY_50)} companies...")
    
    for symbol in NIFTY_50:
        # 1. Ensure Company exists
        company = db.query(Company).filter(Company.symbol == symbol).first()
        if not company:
            company = Company(symbol=symbol, name=f"{symbol} Industries Ltd")
            db.add(company)
            db.commit()
            db.refresh(company)
            
        # 2. Create structured path
        year = "2026"
        quarter = "Q4"
        path = os.path.join(DATA_DIR, symbol, year)
        os.makedirs(path, exist_ok=True)
        file_path = os.path.join(path, f"{quarter}.pdf")
        
        # 3. Handle Deduplication
        # Use a deterministic hash for simulation
        pdf_hash = hashlib.md5(f"{symbol}_Q4_2026_MOCK_CONTENT".encode()).hexdigest()
        existing = db.query(Filing).filter(Filing.pdf_hash == pdf_hash).first()
        if existing:
            print(f"Skipping {symbol} (already exists)")
            continue
            
        # 4. Create Mock PDF file
        with open(file_path, 'w') as f:
            f.write(f"MOCK FINANCIAL REPORT FOR {symbol} - Q4 FY2025-26\n")
            f.write("--------------------------------------------------\n")
            f.write("Revenue: 150000 Cr\n")
            f.write("Net Profit: 25000 Cr\n")
            f.write("EPS: 45.5\n")
            f.write("EBITDA: 35000 Cr\n")
            
        # 5. Create Filing entry
        new_filing = Filing(
            company_id=company.id,
            announcement_id=f"ANN_{symbol}_Q4_2026",
            announcement_date=datetime(2026, 3, 5),
            title=f"Financial Results for the quarter ended March 31, 2026 ({symbol})",
            source="NSE",
            pdf_path=file_path,
            pdf_hash=pdf_hash,
            category="Financial Results"
        )
        db.add(new_filing)
        print(f"Downloaded and stored Q4 report for {symbol}")
        
    db.commit()
    db.close()
    print("Simulation completed.")

if __name__ == "__main__":
    simulate_q4_downloads()
