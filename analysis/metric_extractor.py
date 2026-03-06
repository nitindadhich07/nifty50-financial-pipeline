import pandas as pd
import re
import logging
from typing import List, Dict, Optional

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class MetricExtractor:
    def __init__(self):
        # Keywords for identifying rows
        self.metric_map = {
            "Revenue": ["revenue", "total income", "turnover", "sales"],
            "EBITDA": ["ebitda", "operating profit", "pbitda"],
            "Net Profit": ["net profit", "pat", "profit after tax"],
            "EPS": ["eps", "earnings per share"],
            "Total Expenses": ["total expenses", "expenditure"]
        }

    def clean_value(self, val: str) -> float:
        """
        Cleans a string representation of a financial value.
        """
        if pd.isna(val) or val is None:
            return 0.0
        
        val_str = str(val).lower().replace(',', '').replace('(', '-').replace(')', '')
        # Handle signs and non-numeric chars
        numbers = re.findall(r"[-+]?\d*\.\d+|\d+", val_str)
        if numbers:
            return float(numbers[0])
        return 0.0

    def extract_metrics_from_table(self, df: pd.DataFrame) -> Dict:
        """
        Heuristic-based extraction of metrics from a DataFrame.
        """
        extracted = {}
        # Fill NaN to avoid issues
        df = df.fillna('')
        
        # Flatten table to find matches in first few columns
        for _, row in df.iterrows():
            row_text = ' '.join(str(x).lower() for x in row)
            for metric, keywords in self.metric_map.items():
                if metric in extracted:
                    continue
                
                if any(kw in row_text for kw in keywords):
                    # Found a potential row. Assume the first numeric value in the row is the current period
                    for col_val in row[1:]: # Skip the description column
                        val = self.clean_value(col_val)
                        if val != 0.0:
                            extracted[metric] = val
                            break
        
        return extracted

    def identify_period(self, tables: List, text: str) -> Dict:
        """
        Identifies the quarter and financial year from the document.
        """
        # Search for patterns like "Q3 FY25", "Quarter ended December", etc.
        combined_text = text
        for table in tables[:3]:
            if isinstance(table, pd.DataFrame):
                combined_text += " " + table.to_string()
            else:
                combined_text += " " + str(table)
        
        res = {"quarter": "Unknown", "financial_year": "Unknown"}
        
        q_match = re.search(r"Q[1-4]", combined_text, re.IGNORECASE)
        if q_match:
            res["quarter"] = q_match.group(0).upper()
        
        fy_match = re.search(r"FY\s?20?\d{2}", combined_text, re.IGNORECASE)
        if fy_match:
            res["financial_year"] = fy_match.group(0).upper()
            
        return res

if __name__ == "__main__":
    pass
