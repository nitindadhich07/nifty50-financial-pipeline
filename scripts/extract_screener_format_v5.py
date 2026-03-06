import pdfplumber
import re
import json

def reconstruct_numbers(line):
    # This is a brute-force way to fix OCR/PDF layer noise in financial lines
    # Example: '244<718' -> '244718'
    # '258, 898' -> '258898'
    
    # 1. Clean the line of characters that are often 'noisy' separators
    # We keep digits, spaces, and '-' (for negative)
    cleaned = ""
    for char in line:
        if char.isdigit() or char == ' ' or char == '-' or char == '(':
            cleaned += char
        elif char in [',', '.', '<', ';', ':', '/', '|']:
             # Treat these as transparent separators unless they are clearly decimals
             # Historically in RIL reports, decimals aren't common in Cr for this table
             pass
        else:
            cleaned += " " # Replace other noise with space
            
    # 2. Extract numbers
    # We look for groups of digits that are either space-separated or sign-separated
    # Handle negative numbers in brackets (123)
    raw_nums = re.findall(r"\(?\d+\)?", cleaned)
    
    results = []
    for n in raw_nums:
        n = n.replace('(', '-').replace(')', '')
        if n and n != '-':
            results.append(float(n))
            
    # Relaince special case: the first number is often the same 'Note No.'
    # If the first number is very small and the line label exists, we might skip it.
    # But for now, let's keep all.
    return results

def extract_screener_data(pdf_path):
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[9]
        text = page.extract_text() or ""
        lines = text.split('\n')
        
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
    
    found_rows = {}
    for i, line in enumerate(lines):
        for key, pattern in mapping.items():
            if re.search(pattern, line, re.I):
                # Context check for Net Profit
                if key == "Net Profit":
                    prev_context = "".join(lines[max(0, i-4):i]).lower()
                    if "comprehensive" in prev_context: continue
                
                nums = reconstruct_numbers(line)
                if not nums and i+1 < len(lines):
                     nums = reconstruct_numbers(lines[i+1])
                     
                if nums:
                    # Filter out small note numbers at start if they exist
                    # RIL Page 10 doesn't usually have note col, but let's be safe
                    # If the first number is < 50 and there are 4+ numbers, it might be a row ID
                    if len(nums) >= 4 and nums[0] < 50:
                        nums = nums[1:]
                    
                    found_rows[key] = nums[:3]

    # Derived Logic
    sales = found_rows.get("Sales", [0, 0, 0])
    tot_exp = found_rows.get("Total Expenses", [0, 0, 0])
    finance = found_rows.get("Finance Costs", [0, 0, 0])
    depr = found_rows.get("Depreciation", [0, 0, 0])
    tax = found_rows.get("Tax", [0, 0, 0])
    pbt = found_rows.get("PBT", [0, 0, 0])
    net_profit = found_rows.get("Net Profit", [1, 1, 1]) # Avoid div zero
    eps = found_rows.get("EPS", [0, 0, 0])
    
    other_inc = []
    for ti, s in zip(found_rows.get("Total Income", [0,0,0]), sales):
        other_inc.append(round(ti - s, 2) if ti > s else 0.0)

    # Core Op Expenses = Total Expenses - Interest - Depreciation
    op_exp = []
    for te, fi, de in zip(tot_exp, finance, depr):
        op_exp.append(round(te - fi - de, 2))

    op_profit = []
    for s, oe in zip(sales, op_exp):
        op_profit.append(round(s - oe, 2))

    opm = []
    for op, s in zip(op_profit, sales):
        opm.append(f"{round((op/s)*100, 2)}%" if s != 0 else "0%")

    screener_table = [
        {"Particulars": "Sales", "Dec 2025": sales[0], "Sep 2025": sales[1], "Dec 2024": sales[2]},
        {"Particulars": "Expenses", "Dec 2025": op_exp[0], "Sep 2025": op_exp[1], "Dec 2024": op_exp[2]},
        {"Particulars": "Operating Profit", "Dec 2025": op_profit[0], "Sep 2025": op_profit[1], "Dec 2024": op_profit[2]},
        {"Particulars": "OPM %", "Dec 2025": opm[0], "Sep 2025": opm[1], "Dec 2024": opm[2]},
        {"Particulars": "Other Income", "Dec 2025": other_inc[0], "Sep 2025": other_inc[1], "Dec 2024": other_inc[2]},
        {"Particulars": "Interest", "Dec 2025": finance[0], "Sep 2025": finance[1], "Dec 2024": finance[2]},
        {"Particulars": "Depreciation", "Dec 2025": depr[0], "Sep 2025": depr[1], "Dec 2024": depr[2]},
        {"Particulars": "Profit before tax", "Dec 2025": pbt[0], "Sep 2025": pbt[1], "Dec 2024": pbt[2]},
        {"Particulars": "Tax", "Dec 2025": tax[0], "Sep 2025": tax[1], "Dec 2024": tax[2]},
        {"Particulars": "Net Profit", "Dec 2025": net_profit[0], "Sep 2025": net_profit[1], "Dec 2024": net_profit[2]},
        {"Particulars": "EPS in Rs", "Dec 2025": eps[0], "Sep 2025": eps[1], "Dec 2024": eps[2]},
    ]
    
    return screener_table

if __name__ == "__main__":
    pdf_path = "data/financial_results/RELIANCE/2026/Q3_FY26.pdf"
    data = extract_screener_data(pdf_path)
    print(json.dumps({
        "company": "Reliance Industries Limited",
        "source": "Official NSE Filing (Page 10)",
        "unit": "₹ Crores",
        "quarterly_results": data
    }, indent=2))
