import pdfplumber

pdf_path = "data/financial_results/RELIANCE/2026/Q3_FY26.pdf"

with pdfplumber.open(pdf_path) as pdf:
    print(f"Total Pages: {len(pdf.pages)}")
    text = pdf.pages[0].extract_text()
    if text:
        print("Text found on Page 1:")
        print(text[:200])
    else:
        print("No text found on Page 1. Document may be scanned.")
