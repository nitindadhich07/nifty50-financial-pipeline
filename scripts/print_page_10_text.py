import pdfplumber

pdf_path = "data/financial_results/RELIANCE/2026/Q3_FY26.pdf"

with pdfplumber.open(pdf_path) as pdf:
    page = pdf.pages[9] # Page 10
    print(page.extract_text())
