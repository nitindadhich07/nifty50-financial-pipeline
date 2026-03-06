import pdfplumber
import pandas as pd
import sys
import os

pdf_path = "data/financial_results/RELIANCE/2026/Q3_FY26.pdf"

with pdfplumber.open(pdf_path) as pdf:
    for i, page in enumerate(pdf.pages[:3]):
        tables = page.extract_tables()
        for j, table in enumerate(tables):
            print(f"--- Page {i+1} Table {j+1} ---")
            df = pd.DataFrame(table)
            print(df.head(20).to_string())
            print("\n")
