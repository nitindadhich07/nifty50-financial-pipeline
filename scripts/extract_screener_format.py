import pdfplumber
import re
import json

def clean_val(val_str):
    if not val_str: return 0.0
    val_str = val_str.replace(',', '').replace('(', '-').replace(')', '').strip()
    try:
        return float(val_str)
    except ValueError:
        return 0.0

def extract_screener_data(pdf_path):
    with pdfplumber.open(pdf_path) as pdf:
        # Page 10 is current index 9
        page = pdf.pages[9]
        text = page.extract_text() or ""
        lines = text.split('\n')
        
    data = {
        "Sales": 0.0,
        "Expenses": 0.0,
        "Operating Profit": 0.0,
        "Other Income": 0.0,
        "Interest": 0.0,
        "Depreciation": 0.0,
        "Profit before tax": 0.0,
        "Tax": 0.0,
        "Net Profit": 0.0,
        "EPS": 0.0
    }
    
    # Mapping keywords to data keys
    mapping = {
        "Sales": [r"Value of Sales and Services", r"Revenue from Operations"],
        "Expenses": [r"Total Expenses"],
        "Operating Profit": [r"Profit before finance costs and tax"], # EBITDA/EBIT approx
        "Interest": [r"Finance costs"],
        "Depreciation": [r"Depreciation and amortisation"],
        "Other Income": [r"Other income"],
        "Profit before tax": [r"Profit before tax"],
        "Tax": [r"Tax expense"],
        "Net Profit": [r"Net Profit attributable to:\s+a\) Owners of the Company", r"Profit for the period"],
        "EPS": [r"Earnings per equity share"]
    }
    
    for i, line in enumerate(lines):
        for key, patterns in mapping.items():
            for pattern in patterns:
                if re.search(pattern, line, re.I):
                    # Find numbers in this line or subsequent line if it's multi-line
                    nums = re.findall(r"(\d{1,3}(?:,\d{3})*(?:\.\d+)?)", line)
                    if not nums and i+1 < len(lines):
                        nums = re.findall(r"(\d{1,3}(?:,\d{3})*(?:\.\d+)?)", lines[i+1])
                    
                    if nums:
                        # Grab the first number (current quarter column)
                        data[key] = clean_val(nums[0])
                    break
    
    # Simple Derived OP: Sales - Expenses if Operating Profit search fails
    if data["Operating Profit"] == 0 and data["Sales"] > 0 and data["Expenses"] > 0:
        data["Operating Profit"] = data["Sales"] - data["Expenses"]

    return data

if __name__ == "__main__":
    pdf_path = "data/financial_results/RELIANCE/2026/Q3_FY26.pdf"
    result = extract_screener_data(pdf_path)
    
    final_output = {
        "company": "Reliance Industries Limited",
        "quarter": "Dec 2025 (Q3 FY26)",
        "unit": "Cr",
        "rows": result
    }
    print(json.dumps(final_output, indent=2))
