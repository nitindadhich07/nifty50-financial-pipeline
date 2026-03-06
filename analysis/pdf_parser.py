import pdfplumber
import pytesseract
from PIL import Image
import io
import logging
from typing import List, Optional, Dict
import pandas as pd

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class PDFParser:
    def __init__(self):
        # Tesseract path might need to be configured depending on the environment
        # pytesseract.pytesseract.tesseract_cmd = r'/usr/bin/tesseract'
        pass

    def extract_tables(self, pdf_path: str) -> List[pd.DataFrame]:
        """
        Extracts tables from a PDF using pdfplumber.
        """
        tables = []
        try:
            with pdfplumber.open(pdf_path) as pdf:
                for page in pdf.pages:
                    extracted_tables = page.extract_tables()
                    for table in extracted_tables:
                        if table:
                            df = pd.DataFrame(table[1:], columns=table[0])
                            tables.append(df)
            return tables
        except Exception as e:
            logger.error(f"Error extracting tables from {pdf_path}: {e}")
            return []

    def extract_text_via_ocr(self, pdf_path: str) -> str:
        """
        Fallback OCR extraction for scanned PDFs.
        """
        # This is a basic implementation. For production, use better image processing.
        text = ""
        try:
            with pdfplumber.open(pdf_path) as pdf:
                for page in pdf.pages:
                    # Convert page to image
                    img = page.to_image(resolution=300).original
                    text += pytesseract.image_to_string(img)
            return text
        except Exception as e:
            logger.error(f"OCR failed for {pdf_path}: {e}")
            return ""

    def parse_financial_document(self, pdf_path: str) -> Dict:
        """
        Unified method to parse a financial PDF.
        """
        tables = self.extract_tables(pdf_path)
        if not tables:
            logger.info(f"No tables found in {pdf_path}, trying OCR fallback...")
            text = self.extract_text_via_ocr(pdf_path)
            return {"type": "text", "content": text, "tables": []}
        
        return {"type": "tables", "tables": tables, "content": ""}

if __name__ == "__main__":
    # Test logic
    pass
