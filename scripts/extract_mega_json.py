import pdfplumber
import re
import json

def clean_val(val_str):
    if not val_str: return 0.0
    val_str = val_str.replace('<', '').replace(':', '').replace(';', '').replace(',', '').replace('(', '-').replace(')', '').strip()
    try:
        return float(val_str)
    except ValueError:
        return 0.0

def extract_nums(line):
    # Noise cleanup first
    line = line.replace('<', '').replace(':', '').replace(';', '').replace('~', '').replace('|', '')
    found = re.findall(r"[-+]?\d{1,3}(?:,\d{3,})*(?:\.\d+)?", line)
    found_no_commas = re.findall(r"[-+]?\d{4,}(?:\.\d+)?", line)
    res = []
    for item in found + found_no_commas:
        item = item.replace(',', '')
        try: res.append(float(item))
        except: pass
    return res

def get_mega_data(pdf_path):
    with pdfplumber.open(pdf_path) as pdf:
        # P&L on Page 10 (index 9)
        p10 = pdf.pages[9].extract_text() or ""
        # Segment on Page 11 (index 10)
        p11 = pdf.pages[10].extract_text() or ""
        # More segment on Page 12 (index 11)
        p12 = pdf.pages[11].extract_text() or ""

    # Part 1: Quarterly Table (Screener style) - Extracting Dec 25, Sep 25, Dec 24, 9M Dec 25, 9M Dec 24, FY25
    metrics_map = {
        "Sales": r"Revenue from Operations",
        "Total Expenses": r"Total Expenses",
        "Other Income": r"Other income",
        "Finance Costs": r"Finance costs",
        "Depreciation": r"Depreciation / Amortisation",
        "PBT": r"Profit before tax",
        "Tax": r"Tax expense",
        "Net Profit": r"Owners of the Company",
        "EPS": r"Earnings per equity share"
    }
    
    headers = ["Dec 2025", "Sep 2025", "Dec 2024", "9M Dec 2025", "9M Dec 2024", "FY 2025"]
    pnl_raw = {k: [0.0]*6 for k in metrics_map.keys()}
    
    lines = p10.split('\n')
    for i, line in enumerate(lines):
        for key, pattern in metrics_map.items():
            if re.search(pattern, line, re.I):
                if key == "Net Profit":
                    context = "".join(lines[max(0, i-4):i]).lower()
                    if "comprehensive" in context: continue
                
                nums = extract_nums(line)
                if not nums and i+1 < len(lines):
                    nums = extract_nums(lines[i+1])
                
                if nums:
                    # RIL P10 has columns: CurrentQ, PrevQ, YearAgoQ, 9M_Current, 9M_Prev, FullYear
                    # Sometimes a Note col exists or row ID, filter if small
                    if len(nums) > 6 and nums[0] < 100: nums = nums[1:]
                    for idx in range(min(len(nums), 6)):
                        pnl_raw[key][idx] = nums[idx]

    # Part 2: Segment Results (Sales & EBITDA) - Extracting Current Q and Prev Q
    segment_data = []
    segments = {
        "Oil to Chemicals (O2C)": r"Oil to Chemicals",
        "Oil and Gas": r"Oil and Gas",
        "Retail": r"Retail",
        "Digital Services": r"Digital Services",
        "Financial Services": r"Financial Services",
        "Others": r"Others"
    }
    
    # Looking for 'Segment Revenue' section in p11
    seg_lines = p11.split('\n')
    for i, line in enumerate(seg_lines):
        for name, pattern in segments.items():
            if re.search(pattern, line, re.I):
                nums = extract_nums(line)
                if nums:
                    if len(nums) > 3 and nums[0] < 50: nums = nums[1:]
                    segment_data.append({
                        "Segment": name,
                        "Dec 2025": nums[0] if len(nums) > 0 else 0,
                        "Sep 2025": nums[1] if len(nums) > 1 else 0
                    })
                break
    
    # Peer Comparison (Mocked based on top peers)
    peers = [
        {"Company": "Reliance Industries", "Price": "1,245.00", "P/E": "26.5", "Market Cap": "18,50,000", "Div Yield": "0.8%"},
        {"Company": "ONGC", "Price": "285.50", "P/E": "8.2", "Market Cap": "3,58,000", "Div Yield": "4.5%"},
        {"Company": "Indian Oil Corp", "Price": "172.10", "P/E": "9.1", "Market Cap": "2,42,000", "Div Yield": "5.2%"},
        {"Company": "BPCL", "Price": "615.30", "P/E": "10.4", "Market Cap": "1,33,000", "Div Yield": "3.8%"}
    ]
    
    # Final Screener rows calculation
    rows = []
    # Particulars flow: Sales, Expenses, Operating Profit, Other Incm, Interest, Depre, PBT, Tax, Net Prof
    s = pnl_raw["Sales"]
    e = pnl_raw["Total Expenses"]
    # Adjust expenses for Screener (Expenses = Total - Interest - Depr)
    fc = pnl_raw["Finance Costs"]
    dp = pnl_raw["Depreciation"]
    op_exp = [round(v_e - v_f - v_d, 2) for v_e, v_f, v_d in zip(e, fc, dp)]
    op_prof = [round(v_s - v_oe, 2) for v_s, v_oe in zip(s, op_exp)]
    
    order = [
        ("Sales", s),
        ("Expenses", op_exp),
        ("Operating Profit", op_prof),
        ("Other Income", pnl_raw["Other Income"]),
        ("Interest", fc),
        ("Depreciation", dp),
        ("Profit before tax", pnl_raw["PBT"]),
        ("Tax", pnl_raw["Tax"]),
        ("Net Profit", pnl_raw["Net Profit"])
    ]
    
    for label, vals in order:
        rows.append({"Particulars": label, "Values": vals})

    # Shareholding Pattern (Mocked for Dec 2025 based on historical)
    shareholding = [
        {"Category": "Promoters", "Dec 2025": "50.39%", "Sep 2025": "50.39%"},
        {"Category": "FIIs", "Dec 2025": "21.85%", "Sep 2025": "22.01%"},
        {"Category": "DIIs", "Dec 2025": "16.12%", "Sep 2025": "15.98%"},
        {"Category": "Public", "Dec 2025": "11.64%", "Sep 2025": "11.62%"}
    ]

    return {
        "company": "Reliance Industries Limited",
        "unit": "₹ Crores",
        "headers": headers,
        "quarters": rows,
        "segment_results": segment_data,
        "peer_comparison": peers,
        "shareholding_pattern": shareholding,
        "reference_documents": [
            {"Type": "NSE Filing", "Date": "16-Jan-2026", "Link": "https://nsearchives.nseindia.com/corporate/kavinavora_16012026190810_FR_1.pdf"},
            {"Type": "BSE Filing", "Date": "16-Jan-2026", "Link": "https://www.bseindia.com/xml-data/corpfiling/AttachLive/RELIANCE_16012026.pdf"}
        ]
    }

if __name__ == "__main__":
    pdf_path = "data/financial_results/RELIANCE/2026/Q3_FY26.pdf"
    mega = get_mega_data(pdf_path)
    print(json.dumps(mega, indent=2))
