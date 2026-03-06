import pdfplumber

pdf_path = "data/financial_results/RELIANCE/2026/Q3_FY26.pdf"

with pdfplumber.open(pdf_path) as pdf:
    for i, page in enumerate(pdf.pages):
        text = page.extract_text() or ""
        if "PARTICULARS" in text.upper() and ("QUARTER ENDED" in text.upper() or "3 MONTHS ENDED" in text.upper()):
            print(f"FOUND TABLE ON PAGE {i+1}")
            # Try to extract table to be sure
            tables = page.extract_tables()
            if tables:
                print(f"Page {i+1} has {len(tables)} tables.")
                # Print a bit of the first table's first column
                for j, row in enumerate(tables[0][:5]):
                     print(f"Row {j}: {row[:2]}")
