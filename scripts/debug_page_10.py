import pdfplumber
import pandas as pd

pdf_path = "data/financial_results/RELIANCE/2026/Q3_FY26.pdf"

with pdfplumber.open(pdf_path) as pdf:
    page = pdf.pages[9] # Page 10
    tables = page.extract_tables()
    if tables:
        df = pd.DataFrame(tables[0])
        pd.set_option('display.max_columns', None)
        pd.set_option('display.width', 1000)
        print(df.to_string())
