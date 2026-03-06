import pdfplumber
import re
import json

def clean_val(val_str):
    if not val_str: return 0.0
    # Handle values like (1,234) as -1234
    val_str = val_str.replace(',', '').replace('(', '-').replace(')', '').strip()
    try:
        return float(val_str)
    except ValueError:
        return 0.0

def extract_numbers_from_line(line):
    # Regex for numbers with commas and decimals
    return re.findall(r"[-+]?\d{1,3}(?:,\d{3})*(?:\.\d+)?", line)

def extract_screener_data(pdf_path):
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[9] # Page 10
        text = page.extract_text() or ""
        lines = text.split('\n')
        
    # Standard Screener line items
    metrics = {
        "Sales": [r"Revenue from Operations"],
        "Expenses": [r"Total Expenses"],
        "Other Income": [r"Other Income"],
        "Interest": [r"Finance costs"],
        "Depreciation": [r"Depreciation and amortisation"],
        "Profit before tax": [r"Profit before tax"],
        "Tax": [r"Tax expense"],
        "Net Profit": [r"Owners of the Company"],
        "EPS": [r"Earnings per equity share"]
    }
    
    # We want to extract columns: [Current Quarter, Previous Quarter, Year Ago Quarter]
    table_data = {}
    for key in metrics.keys():
        table_data[key] = [0.0, 0.0, 0.0]

    for line in lines:
        for key, patterns in metrics.items():
            for pattern in patterns:
                if re.search(pattern, line, re.I):
                    nums = extract_numbers_from_line(line)
                    if len(nums) >= 3:
                        table_data[key] = [clean_val(n) for n in nums[:3]]
                    elif len(nums) > 0:
                        # If partial numbers found, maybe they are on next line? hard to say
                        pass
                    break
                    
    # Calculate Operating Profit = Sales - Expenses
    op_profit = []
    for s, e in zip(table_data["Sales"], table_data["Expenses"]):
        op_profit.append(round(s - e, 2))
    table_data["Operating Profit"] = op_profit
    
    # OPM % = (Operating Profit / Sales) * 100
    opm = []
    for op, s in zip(table_data["Operating Profit"], table_data["Sales"]):
        if s != 0:
            opm.append(f"{round((op / s) * 100, 2)}%")
        else:
            opm.append("0%")
    table_data["OPM %"] = opm

    return table_data

if __name__ == "__main__":
    pdf_path = "data/financial_results/RELIANCE/2026/Q3_FY26.pdf"
    results = extract_screener_data(pdf_path)
    
    # Formating for Web UI
    headers = ["Dec 2025", "Sep 2025", "Dec 2024"]
    formatted_rows = []
    
    # Reorder keys to match Screener flow
    order = ["Sales", "Expenses", "Operating Profit", "OPM %", "Other Income", "Interest", "Depreciation", "Profit before tax", "Tax", "Net Profit", "EPS"]
    
    for key in order:
        formatted_rows.append({
            "Particulars": key,
            "Values": results[key]
        })

    final_output = {
        "company": "Reliance Industries Limited",
        "type": "Quarterly Results",
        "unit": "Cr",
        "headers": headers,
        "rows": formatted_rows
    }
    print(json.dumps(final_output, indent=2))
