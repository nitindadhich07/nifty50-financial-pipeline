#!/usr/bin/env python3
"""
pdf_parser.py
-------------
Reads financial PDFs and extracts structured tables + raw text.
"""

import re
import logging
from typing import List, Dict, Optional, Tuple
from pathlib import Path

import pdfplumber
import fitz  # PyMuPDF

logger = logging.getLogger(__name__)

class PDFParser:
    """
    Unified PDF parser supporting text + table extraction.
    """

    def __init__(self, table_settings: Optional[Dict] = None):
        self.table_settings = table_settings or {
            "vertical_strategy": "lines",
            "horizontal_strategy": "lines",
            "snap_tolerance": 4,
            "join_tolerance": 4,
            "edge_min_length": 10,
            "min_words_vertical": 1,
            "min_words_horizontal": 1,
            "intersection_tolerance": 5,
        }
        self.table_settings_text = {
            "vertical_strategy": "text",
            "horizontal_strategy": "text",
            "snap_tolerance": 4,
            "join_tolerance": 8,
        }

    def parse(self, pdf_path: str) -> Dict:
        path = Path(pdf_path)
        if not path.exists():
            logger.error(f"PDF not found: {pdf_path}")
            return self._empty_result()

        logger.info(f"Parsing: {pdf_path}")
        try:
            result = self._parse_with_pdfplumber(pdf_path)
            if result and result.get("num_pages", 0) > 0:
                return result
        except Exception as e:
            logger.warning(f"pdfplumber failed ({e}), falling back to PyMuPDF")

        return self._parse_with_pymupdf(pdf_path)

    def _is_consolidated(self, text: str) -> bool:
        """Determines if a page belongs to consolidated financial statements."""
        low = text.lower()
        header_area = low[:500]
        
        if "consolidated financial statements" in header_area or "consolidated balance sheet" in header_area:
            return True
        if "consolidated statement of" in header_area or "consolidated cash flow" in header_area:
            return True
            
        if "consolidated" in low:
            if "standalone" in low:
                con_idx = low.find("consolidated")
                sta_idx = low.find("standalone")
                return con_idx < sta_idx and con_idx < 1000
            return True
        return False

    def _parse_with_pdfplumber(self, pdf_path: str) -> Dict:
        target_keywords = ["consolidated", "balance sheet", "profit and loss", "profit & loss", "cash flow"]
        relevant_page_nos = []
        page_texts = {}
        
        try:
            doc = fitz.open(pdf_path)
            num_pages = len(doc)
            core_keywords = ["revenue", "income", "assets", "liabilities", "cash flow", "profit", "loss"]
            
            for i, page in enumerate(doc):
                text = page.get_text()
                text_l = text.lower()
                page_no = i + 1
                page_texts[page_no] = text
                
                if self._is_consolidated(text):
                    if any(kw in text_l for kw in target_keywords[1:] + core_keywords):
                        relevant_page_nos.append(page_no)
            doc.close()
        except Exception as e:
            logger.warning(f"Fast scan failed: {e}")
            return self._empty_result()

        pages_data = []
        full_text_parts = []
        with pdfplumber.open(pdf_path) as pdf:
            logger.info(f"  pdfplumber-optimized: {num_pages} pages (Identified {len(relevant_page_nos)} relevant)")
            for page in pdf.pages:
                page_no = page.page_number
                is_relevant = page_no in relevant_page_nos or num_pages < 15
                text = page_texts.get(page_no, "")
                tables = []
                if is_relevant:
                    raw_tables = page.extract_tables(self.table_settings)
                    if not raw_tables:
                        raw_tables = page.extract_tables(self.table_settings_text)
                    for raw in (raw_tables or []):
                        if raw and len(raw) >= 1:
                            cleaned = [[self._clean_cell(c) for c in row] for row in raw]
                            tables.append(cleaned)
                full_text_parts.append(text)
                pages_data.append({"page_no": page_no, "text": text, "tables": tables})

        return {
            "num_pages": num_pages,
            "pages": pages_data,
            "full_text": "\n".join(full_text_parts),
            "parser": "pdfplumber-optimized-v3",
        }

    def _parse_with_pymupdf(self, pdf_path: str) -> Dict:
        pages_data = []
        full_text_parts = []
        doc = fitz.open(pdf_path)
        num_pages = len(doc)
        logger.info(f"  PyMuPDF fallback: {num_pages} pages")
        for page_no, page in enumerate(doc, start=1):
            text = page.get_text("text") or ""
            full_text_parts.append(text)
            pages_data.append({"page_no": page_no, "text": text, "tables": []})
        doc.close()
        return {
            "num_pages": num_pages,
            "pages": pages_data,
            "full_text": "\n".join(full_text_parts),
            "parser": "pymupdf",
        }

    @staticmethod
    def _clean_cell(cell: Optional[str]) -> str:
        if cell is None: return ""
        return re.sub(r"\s+", " ", str(cell)).strip()

    @staticmethod
    def _empty_result() -> Dict:
        return {"num_pages": 0, "pages": [], "full_text": "", "parser": "none"}
