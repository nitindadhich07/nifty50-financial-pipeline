import pdfplumber
import pandas as pd

pdf_path = "data/financial_results/RELIANCE/2026/Q3_FY26.pdf"

with pdfplumber.open(pdf_path) as pdf:
    page = pdf.pages[1] # Page 2
    text = page.extract_text() or ""
    print("--- PAGE 2 TEXT ---")
    print(text)
    
    print("\n--- PAGE 2 TABLES ---")
    tables = page.extract_tables()
    for i, table in enumerate(tables):
        print(f"Table {i+1}:")
        df = pd.DataFrame(table)
        print(df.to_string())
