import pdfplumber
import pandas as pd
import sys
import os

pdf_path = "data/financial_results/RELIANCE/2026/Q3_FY26.pdf"

with pdfplumber.open(pdf_path) as pdf:
    print(f"Total Pages: {len(pdf.pages)}")
    for i, page in enumerate(pdf.pages):
        text = page.extract_text() or ""
        if any(kw in text.lower() for kw in ["financial results", "standalone", "consolidated", "statement of profit"]):
            print(f"--- POTENTIAL TABLE ON PAGE {i+1} ---")
            print(text[:500]) # Print first 500 chars of text
            
            tables = page.extract_tables()
            for j, table in enumerate(tables):
                print(f"--- Table {j+1} on Page {i+1} ---")
                df = pd.DataFrame(table)
                # Display more columns and rows for debugging
                pd.set_option('display.max_columns', None)
                pd.set_option('display.width', 1000)
                print(df.head(10).to_string())
            
            if i > 5: # Limit to first 6 potential pages to avoid too much output
                break
