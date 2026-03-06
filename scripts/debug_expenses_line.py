import pdfplumber
import re

pdf_path = "data/financial_results/RELIANCE/2026/Q3_FY26.pdf"

with pdfplumber.open(pdf_path) as pdf:
    page = pdf.pages[9]
    text = page.extract_text()
    for line in text.split('\n'):
        if "total expenses" in line.lower():
            print(f"RAW LINE: '{line}'")
            # Avoid backslash in f-string
            matches = re.findall(r"[-+]?\d{1,3}(?:,\d{3})*(?:\.\d+)?", line)
            print("REGEX MATCHES:", matches)
