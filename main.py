from fastapi import FastAPI, HTTPException, BackgroundTasks
from typing import List, Optional
import os
from sqlalchemy.orm import Session
from db.models import Filing, Company, SessionLocal
from analysis.pdf_parser import PDFParser
from analysis.metric_extractor import MetricExtractor
from analysis.insight_generator import InsightGenerator
from ingestion_engine import IngestionEngine

app = FastAPI(title="Financial Data Pipeline API")

# Initialize analysis components
pdf_parser = PDFParser()
metric_extractor = MetricExtractor()
insight_generator = InsightGenerator()
ingestion_engine = IngestionEngine()

@app.get("/downloaded-filings")
def get_filings():
    db: Session = SessionLocal()
    filings = db.query(Filing).all()
    db.close()
    return filings

@app.get("/company/{symbol}/latest-result")
def get_latest_result(symbol: str):
    db: Session = SessionLocal()
    company = db.query(Company).filter(Company.symbol == symbol.upper()).first()
    if not company:
        db.close()
        raise HTTPException(status_code=404, detail="Company not found")
    
    latest_filing = db.query(Filing).filter(Filing.company_id == company.id).order_by(Filing.announcement_date.desc()).first()
    db.close()
    
    if not latest_filing:
        raise HTTPException(status_code=404, detail="No filings found for company")
    
    return latest_filing

@app.post("/analyze/{symbol}/{quarter}")
def analyze_result(symbol: str, quarter: str):
    db: Session = SessionLocal()
    company = db.query(Company).filter(Company.symbol == symbol.upper()).first()
    if not company:
        db.close()
        raise HTTPException(status_code=404, detail="Company not found")
    
    # Simple logic to find the filing for that quarter
    # In a real app, title or a dedicated field would be used
    filing = db.query(Filing).filter(Filing.company_id == company.id, Filing.title.contains(quarter.upper())).first()
    
    if not filing or not filing.pdf_path:
        db.close()
        raise HTTPException(status_code=404, detail="PDF not found for specified quarter")
    
    # On-demand analysis
    pdf_path = filing.pdf_path
    if not os.path.exists(pdf_path):
        db.close()
        raise HTTPException(status_code=404, detail="PDF file missing on disk")
        
    parse_result = pdf_parser.parse_financial_document(pdf_path)
    
    metrics = {}
    if parse_result["type"] == "tables":
        for table in parse_result["tables"]:
            m = metric_extractor.extract_metrics_from_table(table)
            metrics.update(m)
    
    period = metric_extractor.identify_period(parse_result["tables"], parse_result["content"])
    
    # For growth, we'd look for previous filings in DB
    # Placeholder for comparison data
    growth = insight_generator.generate_growth_metrics(metrics, None) 
    
    summary = insight_generator.generate_human_readable_summary(symbol, period, metrics, growth)
    
    db.close()
    return {
        "symbol": symbol,
        "period": period,
        "metrics": metrics,
        "growth": growth,
        "summary": summary
    }

@app.post("/trigger-ingestion")
def trigger_ingestion(background_tasks: BackgroundTasks):
    background_tasks.add_task(ingestion_engine.run_cycle)
    return {"message": "Ingestion cycle triggered in background"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
