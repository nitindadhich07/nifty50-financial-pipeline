import asyncio
import os
import json
from playwright.async_api import async_playwright
import urllib.request
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

async def get_pdf_links():
    symbols = ["RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY"]
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        
        # Go to NSE home page to set cookies
        await page.goto("https://www.nseindia.com", wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)
        
        for symbol in symbols:
            try:
                # url = f"https://www.nseindia.com/api/corporate-announcements?symbol={symbol}&index=equities"
                url = f"https://www.nseindia.com/api/corporate-announcements?symbol={symbol}&category=financial%20results"
                
                logger.info(f"Fetching data for {symbol}...")
                response = await page.goto(url)
                
                content = await response.text()
                try:
                    data = json.loads(content)
                except json.JSONDecodeError:
                    logger.error(f"Failed to parse JSON for {symbol}")
                    continue
                
                found = False
                for item in data:
                    desc = item.get('desc', '').lower()
                    if "financial results" in desc or "standalone" in desc or "consolidated" in desc:
                        attachment = item.get('attachment')
                        attime = item.get('attime', '')
                        # Filter for 2026 filings (covers Q3 FY26)
                        if attachment and ("2026" in attime or "Jan" in attime or "Feb" in attime or "Mar" in attime):
                            pdf_url = f"https://nsearchives.nseindia.com/corporate/{attachment}"
                            logger.info(f"Found PDF for {symbol}: {pdf_url}")
                            
                            # Download the PDF
                            path = f"data/financial_results/{symbol}/2026"
                            os.makedirs(path, exist_ok=True)
                            file_path = os.path.join(path, "Q3.pdf")
                            
                            # We can try to download it via standard urllib now that we have the URL
                            # Or we can use page.request.get
                            pdf_response = await context.request.get(pdf_url)
                            if pdf_response.ok:
                                pdf_data = await pdf_response.body()
                                with open(file_path, "wb") as f:
                                    f.write(pdf_data)
                                logger.info(f"Successfully downloaded {symbol} PDF to {file_path}")
                                found = True
                                break
                            else:
                                logger.error(f"Failed to download PDF from {pdf_url}")

                if not found:
                    logger.warning(f"No suitable 2026 financial result found for {symbol}")
                    
            except Exception as e:
                logger.error(f"Error processing {symbol}: {e}")
                
            await page.wait_for_timeout(2000)
            
        await browser.close()

if __name__ == "__main__":
    asyncio.run(get_pdf_links())
