import logging
import re
from typing import Dict, List, Optional, Tuple, Any, Literal

logger = logging.getLogger(__name__)

class PDFTableParser:
    """Intelligent parser for financial tables with institutional classification (Rule 2)."""
    
    def __init__(self, synonym_map: Dict[str, List[str]]):
        self.synonyms = synonym_map
        # Handles numbers like 1,23,456.00 or (1,23,456)
        self._NUM_PATTERN = re.compile(r"\(?\s*[\d,]{1,}(?:\.\d+)?\s*\)?")

    def classify_table(self, table: List[List[Optional[str]]]) -> Optional[Literal["pl", "bs", "cf"]]:
        """Rule 2: Classifies a table using keyword intensity."""
        if not table or len(table) < 3: return None
        
        full_text = ""
        for row in table[:25]:
             full_text += " " + " ".join([str(c) for c in row if c])
        full_text = full_text.lower()
        
        # Scoring system for robust classification
        scores = {"pl": 0, "bs": 0, "cf": 0}
        
        # P&L Indicators
        pl_keys = ["revenue from operations", "income from operations", "profit before tax", "finance costs", "tax expense", "profit for the year", "exceptional items"]
        # Balance Sheet Indicators
        bs_keys = ["total equity and liabilities", "non-current assets", "share capital", "other equity", "reserves and surplus", "trade receivables", "inventories", "deferred tax assets"]
        # Cash Flow Indicators
        cf_keys = ["net cash from operating activities", "adjustments for:", "working capital changes", "cash flow from investing", "increase / (decrease) in cash"]

        for k in pl_keys: 
            if k in full_text: scores["pl"] += 2
        for k in bs_keys: 
            if k in full_text: scores["bs"] += 2
        for k in cf_keys: 
            if k in full_text: scores["cf"] += 2
            
        # Tie break with headers
        header = " ".join([str(c) for c in table[0] if c]).lower()
        if "profit" in header and "loss" in header: scores["pl"] += 5
        if "balance" in header and "sheet" in header: scores["bs"] += 5
        if "cash" in header and "flow" in header: scores["cf"] += 5

        top_type = max(scores, key=scores.get)
        if scores[top_type] >= 3:
            return top_type
        return None

    def detect_years(self, table: List[List[Optional[str]]]) -> List[str]:
        """Detects years from the top rows, looking for '2025', '2024', etc."""
        year_pattern = re.compile(r"\b20\d{2}\b")
        found_years = []
        for row in table[:5]:
            text = " ".join([str(c) for c in row if c])
            years = year_pattern.findall(text)
            if years:
                for y in years:
                    if y not in found_years: found_years.append(y)
        
        if found_years:
            return sorted(list(set(found_years)), reverse=True)
        return ["2025", "2024"] # Default fallback

    def parse_table(self, table: List[List[Optional[str]]]) -> Dict[str, List[float]]:
        """Extracts field mappings. Handles labels split across cells or joined with numbers, and multiple fields per row."""
        results = {}
        for row in table:
            cells = [str(c).strip() if c is not None else "" for c in row]
            if not any(cells): continue
            
            current_field = None
            current_nums = []
            
            for cell in cells:
                cell_lower = cell.lower()
                field = self._match_label(cell_lower)
                
                if field:
                    # Save the previous field before starting a new one
                    if current_field:
                        final_nums = self._filter_note_refs(current_nums)
                        if final_nums:
                            results[current_field] = final_nums
                    current_field = field
                    current_nums = []
                
                # Extract numbers from the cell
                nums_in_cell = self._NUM_PATTERN.findall(cell)
                for n in nums_in_cell:
                    v = self._parse_num(n)
                    if v is not None and abs(v) > 0.1:
                        current_nums.append(v)
            
            # Save the last field from the row
            if current_field:
                final_nums = self._filter_note_refs(current_nums)
                if final_nums:
                    results[current_field] = final_nums
        return results

    def _filter_note_refs(self, nums: List[float]) -> List[float]:
        if not nums: return []
        if len(nums) <= 2: return nums
        
        # Common RIL/Indian format: [Note Ref, Current, Previous]
        # Note refs are usually small integers < 200
        if len(nums) >= 3:
            if abs(nums[0]) < 180 and (abs(nums[1]) > 500 or abs(nums[2]) > 500):
                return nums[1:3]
            # [Current, Previous, Variance] or similar
            return nums[:2]
        return nums[:2]

    def _match_label(self, text: str) -> Optional[str]:
        """Matches a string against synonyms with priority for longer phrases."""
        def _deep_clean(s: str) -> str:
            # Remove all non-alphanumeric chars for maximum robustness
            return re.sub(r"[^a-z0-9]", "", s.lower())

        clean_text = _deep_clean(text)
        if len(clean_text) < 3: return None

        # Sort synonyms by length descending to match full phrases first
        all_syns = []
        for f, syns in self.synonyms.items():
            for s in syns:
                all_syns.append((f, s.lower(), _deep_clean(s)))
        all_syns.sort(key=lambda x: len(x[1]), reverse=True)

        for field, original_syn, clean_syn in all_syns:
            # Match if the deep-cleaned text contains the deep-cleaned synonym
            if clean_syn in clean_text:
                return field
        return None

    def _parse_num(self, s: str) -> Optional[float]:
        neg = "(" in s or ")" in s
        clean = re.sub(r"[(),\s]", "", s)
        try:
            val = float(clean)
            return -val if neg else val
        except: return None
