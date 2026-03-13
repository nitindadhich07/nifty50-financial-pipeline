# Nifty 50 Financial Data Ingestion & Analysis Pipeline

A scalable system to automate the recovery and analysis of financial PDFs from the Indian stock market (NSE/BSE). Built for accuracy, performance, and "Screener-style" data extraction.

## Institutional-Grade Multi-Source Extraction (pipeline_v3)

This repo now includes a hierarchical, multi-source extraction engine targeting ~95-97% accuracy by prioritizing structured sources:

1. MCA AOC-4 XBRL (annual PL/BS/CF) via local artifacts
2. Exchange APIs (NSE/BSE) for quarterly/annual result series
3. Company IR HTML tables
4. PDF parsing (fallback)

Run (hierarchical pipeline):

```bash
python3 -m pipeline_v3.hierarchical_pipeline --symbol RELIANCE
```

Optional inputs:
- MCA XBRL local store: `storage/raw/mca_xbrl/<CIN>/*.zip|*.xml|*.xbrl`
- Universe config: `pipeline_v3/config/nifty50_universe.json` (fill `cin` and `ir_urls` to unlock Tier-1/Tier-3)
- PDF fallback: `--pdf /path/to/annual_report.pdf` (repeatable)

## 🚀 Features

- **Layer 1: Intelligent Ingestion**
  - Session-based cookie handshake to bypass exchange anti-bot measures.
  - Parallel PDF downloader (FastAPI + multithreading).
  - Deduplication via MD5 hashing.
  - Automated scheduler for daily updates.

- **Layer 2: High-Fidelity Analysis**
  - **Noise-Resilient Parser**: Recovers data from PDFs with corrupted text layers (e.g., Reliance Q3).
  - **Screener-style Extraction**: Provides raw quarterly data (Sales, Expenses, OP, Interest, Tax, etc.) in web-ready JSON.
  - **Segment Analysis**: Detailed breakdown (e.g., O2C, Retail, Jio for Reliance).

- **RESTful API**
  - Endpoints to list filings, fetch latest results, and trigger on-demand analysis.

## 📁 System Architecture

```text
├── main.py                 # API Entrypoint (FastAPI)
├── ingestion_engine.py      # Data Ingestion & Scheduler
├── scrapers/               # NSE/BSE & Fallback Scrapers
├── ingestion/              # Parallel Downloader Logic
├── analysis/               # PDF Parsing & Metric Extraction
├── db/                     # SQLAlchemy Models (SQLite/Postgres)
└── scripts/                # Specialized Downloader & Analysis Scripts
```

## 🛠 Setup & Usage

1. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

2. **Run Ingestion**:
   ```bash
   python3 scripts/nifty50_downloader.py
   ```

3. **Start API**:
   ```bash
   python3 main.py
   ```

## 📊 Sample Output (RELIANCE Q3 FY26)
Matches official filings:
- **Revenue**: ₹2,58,898 Cr
- **Net Profit**: ₹18,165 Cr
- **Extraction Method**: Hybrid Regex-Text Fallback

---
Created by Antigravity for nitindadhich07.
