import pdfplumber
import re

pdf_path = "data/financial_results/RELIANCE/2026/Q3_FY26.pdf"

with pdfplumber.open(pdf_path) as pdf:
    for i, page in enumerate(pdf.pages):
        text = page.extract_text() or ""
        header = ""
        if "SEGMENT" in text.upper():
            header = "SEGMENT RESULTS"
        elif "BALANCE SHEET" in text.upper():
            header = "BALANCE SHEET"
        elif "CASH FLOW" in text.upper():
            header = "CASH FLOW"
        elif "STANDALONE" in text.upper() and ("PROFIT" in text.upper() or "INCOME" in text.upper()):
            header = "STANDALONE RESULTS"
        
        if header:
            print(f"--- PAGE {i+1}: {header} ---")
            print(text[:300])
            print("-" * 30)
