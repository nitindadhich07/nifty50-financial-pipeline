import pdfplumber
import re
import json

def clean_val(val_str):
    if not val_str: return 0.0
    val_str = val_str.replace(',', '').replace('(', '-').replace(')', '').replace(';', '').strip()
    try:
        return float(val_str)
    except ValueError:
        return 0.0

def extract_numbers_from_line(line):
    # Match numbers with optional commas and decimals, excluding leading note numbers (small integers)
    found = re.findall(r"[-+]?\d{1,3}(?:,\d{3})*(?:\.\d+)?", line)
    return found

def extract_screener_data(pdf_path):
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[9] # Page 10
        text = page.extract_text() or ""
        lines = text.split('\n')
        
    table_data = {}
    
    current_section = None
    
    sections = {
        "SALES": ["Revenue from Operations", "Value of Sales"],
        "EXPENSES": ["Total Expenses"],
        "NP": ["Net Profit attributable to", "Profit for the period"],
        "INT": ["Finance costs"],
        "DEP": ["Depreciation and amortisation"],
        "OTHER": ["Other income"],
        "TAX": ["Tax expense"],
        "PBT": ["Profit before tax"]
    }

    # Helper to find row values
    def get_row_values(line_list, index, keyword):
        line = line_list[index]
        nums = extract_numbers_from_line(line)
        # Filter out Note numbers (usually the first number if it's very small and separated)
        # In many reports, the 1st column after the label is the Note No.
        # But here the numbers look like: 258,898 ...
        
        # If the first number is very small (e.g. < 50) and there are many columns, it might be a note.
        # However, RELIANCE often doesn't have a Note column in this concise view.
        
        # Let's try to identify if the first number is a year or note.
        # Check for values > 100 for main items.
        
        filtered = []
        for n in nums:
            val = clean_val(n)
            # Heuristic: Financial metrics in Cr for RIL are large. Note numbers are small.
            # But EPS could be small.
            filtered.append(val)
        
        if len(filtered) < 3 and index + 1 < len(line_list):
            # Try next line if columns are wrapped
            more_nums = extract_numbers_from_line(line_list[index+1])
            filtered.extend([clean_val(n) for n in more_nums])
            
        return filtered[:3]

    results = {k: [0.0, 0.0, 0.0] for k in sections.keys()}

    for i, line in enumerate(lines):
        # Specific search for Net Profit (a) Owners of the Company)
        if "Owners of the Company" in line:
            # Check context from previous lines
            context = "".join(lines[max(0, i-3):i]).lower()
            if "net profit" in context and not "comprehensive" in context:
                results["NP"] = get_row_values(lines, i, "Owners")
            elif "comprehensive" in context and results["NP"] == [0.0, 0.0, 0.0]:
                # Fallback if Net Profit search fails
                pass 

        for key, keywords in sections.items():
            if results[key] != [0.0, 0.0, 0.0]: continue
            if any(re.search(kw, line, re.I) for kw in keywords):
                vals = get_row_values(lines, i, key)
                if vals:
                    # RIL special case: 'Total Expenses' line often starts with '2' or 'II'
                    if key == "EXPENSES" and len(vals) > 3 and vals[0] < 10:
                        results[key] = vals[1:4]
                    else:
                        results[key] = vals[:3]

    # Post-processing and calculations
    # Operating Profit = Sales - Expenses
    results["OP"] = [round(s - e, 2) for s, e in zip(results["SALES"], results["EXPENSES"])]
    
    # Final cleanup to Cr
    final = {
        "Sales": results["SALES"],
        "Expenses": results["EXPENSES"],
        "Operating Profit": results["OP"],
        "Other Income": results["OTHER"],
        "Interest": results["INT"],
        "Depreciation": results["DEP"],
        "Profit before tax": results["PBT"],
        "Tax": results["TAX"],
        "Net Profit": results["NP"]
    }
    
    return final

if __name__ == "__main__":
    pdf_path = "data/financial_results/RELIANCE/2026/Q3_FY26.pdf"
    data = extract_screener_data(pdf_path)
    
    headers = ["Dec 2025", "Sep 2025", "Dec 2024"]
    rows = []
    order = ["Sales", "Expenses", "Operating Profit", "Other Income", "Interest", "Depreciation", "Profit before tax", "Tax", "Net Profit"]
    
    for key in order:
        rows.append({
            "Particulars": key,
            "Values": data[key]
        })

    print(json.dumps({
        "company": "Reliance Industries Limited",
        "unit": "Cr",
        "headers": headers,
        "rows": rows
    }, indent=2))
