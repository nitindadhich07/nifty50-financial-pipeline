import pdfplumber
import pandas as pd

pdf_path = "data/financial_results/RELIANCE/2026/Q3_FY26.pdf"

with pdfplumber.open(pdf_path) as pdf:
    for i, page in enumerate(pdf.pages):
        text = page.extract_text() or ""
        if "UNAUDITED CONSOLIDATED FINANCIAL RESULTS" in text.upper():
            print(f"FOUND MAIN TABLE ON PAGE {i+1}")
            tables = page.extract_tables()
            for j, table in enumerate(tables):
                df = pd.DataFrame(table)
                print(f"Table {j+1}: {df.shape}")
                print(df.head(10).to_string())
            break
