import logging
import re
from typing import Dict, List, Optional, Tuple, Any

logger = logging.getLogger(__name__)

class PDFTextParser:
    """High-accuracy financial data extractor from PDF text."""
    
    def __init__(self, synonym_map: Dict[str, List[str]]):
        self.synonyms = synonym_map
        # Matches numbers like 1,23,456.78 or (1,23,456)
        self._NUM_PATTERN = re.compile(r"\(?\s*[\d,]{2,}(?:\.\d+)?\s*\)?")

    def parse_text(self, text: str) -> Dict[str, Any]:
        """Parses a block of text into current/prev mappings."""
        lines = text.split("\n")
        unit_multiplier = self._detect_unit(text)
        logger.info(f"Using unit multiplier: {unit_multiplier}")
        current, prev = self._extract_values(lines, unit_multiplier)
        
        return {
            "current": current,
            "prev": prev
        }

    def _detect_unit(self, text: str) -> float:
        t = re.sub(r"\s+", "", text.lower())[:10000]
        if any(k in t for k in ["incrore", "crores", "crore", "₹crore"]): return 1.0
        if any(k in t for k in ["inmillion", "millions", "million"]): return 0.1
        if any(k in t for k in ["inlakh", "lakhs", "lakh"]): return 0.01
        return 1.0

    def _extract_values(self, lines: List[str], multiplier: float) -> Tuple[Dict[str, float], Dict[str, float]]:
        current: Dict[str, float] = {}
        prev: Dict[str, float] = {}
        extracted_raw = {} 

        for i, line in enumerate(lines):
            line = line.strip()
            if not line or len(line) < 3: continue
            
            # Noise filter: Skip lines with DIN/directors/PAN info which often pollutes headers/footers
            if any(k in line.upper() for k in ["DIN:", "DIN ", "PAN:", "SIGNATURE", "DIRECTOR"]):
                continue

            field = self._match_label(line)
            if field:
                all_nums = self._extract_numbers_from_string(line)
                if not all_nums and i+1 < len(lines):
                    all_nums = self._extract_numbers_from_string(lines[i+1])
                
                if all_nums:
                    final_nums = self._filter_note_refs(all_nums)
                    if final_nums:
                        if field not in extracted_raw:
                            extracted_raw[field] = []
                        extracted_raw[field].append(final_nums)
                        logger.info(f"      Matched {field}: {final_nums}")

        # Decide which extracted values to use
        for field, instances in extracted_raw.items():
            # Heuristic 1: If multiple instances, default to first or specific logic
            best_instance = instances[0]
            
            # Heuristic 2: For Cash Flow, prefer later instances usually (the 'Total' line)
            if field.startswith("cash_from_") and len(instances) > 1:
                # But prefer ones that aren't extreme outliers if we can
                best_instance = instances[-1]

            if field == "net_profit" and len(instances) > 1:
                rev = current.get("revenue_from_operations", 1000000)
                for inst in instances:
                    val = inst[0] * multiplier
                    if 10000 < val < rev * 0.3:
                        best_instance = inst
                        break
            
            current[field] = round(best_instance[0] * multiplier, 2)
            if len(best_instance) > 1:
                prev[field] = round(best_instance[1] * multiplier, 2)
                
        return current, prev

    def _extract_numbers_from_string(self, s: str) -> List[float]:
        # Remove common non-numeric chars
        clean = re.sub(r"[₹$J]", "", s)
        
        # Mask DIN-like strings to avoid capturing them as numbers
        # DIN is usually 8 digits, often starting with 0
        clean = re.sub(r"DIN:\s*\d{8}", "", clean)
        clean = re.sub(r"DIN\s*\d{8}", "", clean)
        
        matches = self._NUM_PATTERN.findall(clean)
        nums = []
        for m in matches:
            v = self._parse_num(m)
            if v is not None:
                # Sanity: 8-digit numbers in Crores are rare for line items (usually meta data if exactly 8 digits)
                if abs(v) > 5000000 and len(re.sub(r"\D", "", m)) >= 8:
                    continue
                nums.append(v)
        return nums

    def _filter_note_refs(self, nums: List[float]) -> List[float]:
        if not nums: return []
        if len(nums) >= 3:
            # If [Note, Curr, Prev]
            if abs(nums[0]) < 180 and (abs(nums[1]) > 500 or abs(nums[2]) > 500):
                return nums[1:3]
        return nums[:2]

    def _match_label(self, label: str) -> Optional[str]:
        def _deep_clean(s: str) -> str:
            return re.sub(r"[^a-z0-9]", "", s.lower())

        clean_label = _deep_clean(label)
        if len(clean_label) < 3: return None
        
        sorted_fields = []
        for f, syns in self.synonyms.items():
            for s in syns:
                sorted_fields.append((f, s, _deep_clean(s)))
        sorted_fields.sort(key=lambda x: len(x[1]), reverse=True)

        for field, original_syn, clean_syn in sorted_fields:
            if clean_syn in clean_label:
                return field
        return None

    def _parse_num(self, s: str) -> Optional[float]:
        neg = s.strip().startswith("(") and s.strip().endswith(")")
        clean = re.sub(r"[(),\s]", "", s)
        try:
            return -float(clean) if neg else float(clean)
        except: return None
