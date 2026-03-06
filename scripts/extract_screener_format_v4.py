import pdfplumber
import re
import json

def ultra_clean_line(line):
    # This function removes RIL-specific PDF noise to reconstruct numbers
    # Replace '<' with '' as it's often used as a thousand separator artifact
    # Replace ':' with '' in numbers
    # Replace '#' or other weird chars
    line = line.replace('<', '').replace(':', '').replace(';', '').replace('SQ', '').replace('~', '')
    return line

def extract_numbers_from_line(line):
    line = ultra_clean_line(line)
    # Find all number-like chunks. We split by spaces if there are multiple.
    # We want things that look like 258,898 or 18165
    found = re.findall(r"[-+]?\d{1,3}(?:,\d{3,})*(?:\.\d+)?", line)
    # Some numbers might not have commas but are long
    found_no_commas = re.findall(r"[-+]?\d{4,}(?:\.\d+)?", line)
    
    # Combine and clean
    results = []
    for item in found + found_no_commas:
        val = item.replace(',', '')
        try:
            results.append(float(val))
        except:
            continue
    return results

def extract_screener_data(pdf_path):
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[9] # Page 10
        text = page.extract_text() or ""
        lines = text.split('\n')
        
    data = {}
    
    mapping = {
        "Sales": r"Revenue from Operations",
        "Total Income": r"Total Income",
        "Total Expenses": r"Total Expenses",
        "Net Profit": r"Owners of the Company",
        "Finance Costs": r"Finance Costs",
        "Depreciation": r"Depreciation / Amortisation",
        "PBT": r"Profit before tax",
        "Tax": r"Tax Expenses",
        "EPS": r"Earnings per equity share"
    }
    
    # Store found rows
    rows = {}
    for i, line in enumerate(lines):
        for key, pattern in mapping.items():
            if re.search(pattern, line, re.I):
                nums = extract_numbers_from_line(line)
                if not nums and i+1 < len(lines):
                     nums = extract_numbers_from_line(lines[i+1])
                
                # Special check for 'Owners' to avoid the Comprehensive Income section
                if key == "Net Profit":
                    context = "".join(lines[max(0, i-4):i]).lower()
                    if "comprehensive" in context:
                        continue
                
                if nums:
                    rows[key] = nums[:2] # Current and Previous Quarter

    # Screener specific derivations
    # Screener Expenses = Total Expenses - Finance - Depreciation - Tax
    # Sales = Sales
    sales = rows.get("Sales", [0, 0])
    tot_exp = rows.get("Total Expenses", [0, 0])
    finance = rows.get("Finance Costs", [0, 0])
    depr = rows.get("Depreciation", [0, 0])
    tax = rows.get("Tax", [0, 0])
    net_profit = rows.get("Net Profit", [0, 0])
    pbt = rows.get("PBT", [0, 0])
    eps = rows.get("EPS", [0, 0])
    other_inc = [round(t - s, 2) for t, s in zip(rows.get("Total Income", [0,0]), sales)]

    # Operating Profit = Sales - (Total Expenses - Interest - Depreciation)
    # Note: In Screener, Expenses line is usually 'Core Op Expenses'
    op_exp = []
    for te, fi, de in zip(tot_exp, finance, depr):
        op_exp.append(round(te - fi - de, 2))
        
    op_profit = []
    for s, oe in zip(sales, op_exp):
        op_profit.append(round(s - oe, 2))

    # OPM % 
    opm = []
    for op, s in zip(op_profit, sales):
        opm.append(f"{round((op/s)*100, 2)}%" if s != 0 else "0%")

    # Final table rows
    screener_rows = [
        {"Particulars": "Sales", "Dec 2025": sales[0], "Sep 2025": sales[1]},
        {"Particulars": "Expenses", "Dec 2025": op_exp[0], "Sep 2025": op_exp[1]},
        {"Particulars": "Operating Profit", "Dec 2025": op_profit[0], "Sep 2025": op_profit[1]},
        {"Particulars": "OPM %", "Dec 2025": opm[0], "Sep 2025": opm[1]},
        {"Particulars": "Other Income", "Dec 2025": other_inc[0], "Sep 2025": other_inc[1]},
        {"Particulars": "Interest", "Dec 2025": finance[0], "Sep 2025": finance[1]},
        {"Particulars": "Depreciation", "Dec 2025": depr[0], "Sep 2025": depr[1]},
        {"Particulars": "Profit before tax", "Dec 2025": pbt[0], "Sep 2025": pbt[1]},
        {"Particulars": "Tax", "Dec 2025": tax[0], "Sep 2025": tax[1]},
        {"Particulars": "Net Profit", "Dec 2025": net_profit[0], "Sep 2025": net_profit[1]},
        {"Particulars": "EPS in Rs", "Dec 2025": eps[0], "Sep 2025": eps[1]},
    ]
    
    return screener_rows

if __name__ == "__main__":
    pdf_path = "data/financial_results/RELIANCE/2026/Q3_FY26.pdf"
    data = extract_screener_data(pdf_path)
    print(json.dumps({
        "company": "Reliance Industries Limited",
        "quarterly_results": data
    }, indent=2))
