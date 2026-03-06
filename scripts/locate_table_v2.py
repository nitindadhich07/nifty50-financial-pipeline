import pdfplumber
import re

pdf_path = "data/financial_results/RELIANCE/2026/Q3_FY26.pdf"
keywords = ["Particulars", "Income", "Net Profit", "Earnings per share", "Quarter ended"]

with pdfplumber.open(pdf_path) as pdf:
    for i, page in enumerate(pdf.pages):
        text = page.extract_text() or ""
        matches = [kw for kw in keywords if re.search(kw, text, re.I)]
        if len(matches) >= 3:
            print(f"PAGE {i+1}: Matches {matches}")
            # Identify if it's the consolidated results
            if "CONSOLIDATED" in text.upper():
                print(f"PAGE {i+1} is likely CONSOLIDATED.")
            elif "STANDALONE" in text.upper():
                print(f"PAGE {i+1} is likely STANDALONE.")
