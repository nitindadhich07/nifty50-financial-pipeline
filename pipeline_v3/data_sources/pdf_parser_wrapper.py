import logging
import pdfplumber
from typing import List, Dict, Any, Optional
from pathlib import Path

logger = logging.getLogger(__name__)

class PDFParser:
    """High-fidelity PDF parser using pdfplumber."""
    
    def find_pages_by_keywords(self, pdf_path: str, keywords: List[str], must_contain_all: bool = True) -> List[int]:
        matching_pages = []
        try:
            with pdfplumber.open(pdf_path) as pdf:
                for i, page in enumerate(pdf.pages):
                    text = (page.extract_text() or "").upper()
                    if must_contain_all:
                        if all(k.upper() in text for k in keywords):
                            matching_pages.append(i + 1)
                    else:
                        if any(k.upper() in text for k in keywords):
                            matching_pages.append(i + 1)
            return matching_pages
        except Exception as e:
            logger.error(f"Error finding pages: {e}")
            return []

    def extract_all_tables_from_page(self, pdf_path: str, page_num: int) -> List[List[List[Optional[str]]]]:
        """Extracts tables with a fallback strategy suitable for financial reports."""
        try:
            with pdfplumber.open(pdf_path) as pdf:
                if page_num > len(pdf.pages): return []
                page = pdf.pages[page_num - 1]
                
                # Strategy 1: Default (Best for most well-formed tables)
                tables = page.extract_tables()
                
                # Strategy 2: Text-layout based "table"
                # If the PDF tables don't have enough data (e.g., missing labels)
                # We yield the whole page's text lines as a table.
                def is_good_extraction(tbl_list):
                    if not tbl_list: return False
                    # Find the largest table
                    largest = max(tbl_list, key=len)
                    # Check if the largest table has at least one financial keyword
                    for row in largest:
                        for c in row:
                            if c and any(k in str(c).lower() for k in ["revenue", "asset", "equity", "profit", "cash", "operating"]):
                                return True
                    return False
                
                if not is_good_extraction(tables):
                    text = page.extract_text(layout=True)
                    if text:
                        import re
                        text_table = [[c.strip() for c in re.split(r'\s{3,}', line) if c.strip()] for line in text.split("\n") if len(line.strip()) > 2]
                        if text_table:
                            tables = [text_table] # Overwrite garbage tables
                    else:
                        text_simple = page.extract_text()
                        if text_simple:
                            text_table = [[line.strip()] for line in text_simple.split("\n") if len(line.strip()) > 2]
                            if text_table:
                                tables = [text_table]
                
                return [t for t in tables if t and len(t) > 1]
        except Exception as e:
            logger.error(f"Error extracting tables: {e}")
            return []

    def extract_text(self, pdf_path: str, pages: Optional[List[int]] = None) -> str:
        text_content = []
        try:
            with pdfplumber.open(pdf_path) as pdf:
                target_pages = [pdf.pages[p-1] for p in pages] if pages else pdf.pages
                for page in target_pages:
                    text_content.append(page.extract_text() or "")
            return "\n".join(text_content)
        except Exception as e:
            logger.error(f"Error: {e}")
            return ""
