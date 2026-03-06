import pdfplumber

pdf_path = "data/financial_results/RELIANCE/2026/Q3_FY26.pdf"
keywords = ["Revenue from Operations", "Owners of the Company", "Tax expense", "Finance costs", "Depreciation"]

with pdfplumber.open(pdf_path) as pdf:
    page = pdf.pages[9]
    text = page.extract_text()
    for line in text.split('\n'):
        if any(kw.lower() in line.lower() for kw in keywords):
            print(f"RAW LINE: '{line}'")
