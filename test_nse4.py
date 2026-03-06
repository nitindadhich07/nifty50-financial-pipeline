import requests
import time, json

s = requests.Session()
s.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': '*/*',
    'Accept-Language': 'en-US,en;q=0.5',
    'Referer': 'https://www.nseindia.com/companies-listing/corporate-filings-announcements',
})
s.get('https://www.nseindia.com', timeout=15)
time.sleep(2)

symbol = 'RELIANCE'
url = f'https://www.nseindia.com/api/corporate-announcements?index=equities&symbol={symbol}&from_date=01-01-2026&to_date=06-03-2026'
data = s.get(url, timeout=15).json()
print(f"Total items: {len(data)}")
for item in data:
    desc = item.get('desc','').lower()
    if any(k in desc for k in ['financial results','standalone','consolidated','unaudited']):
        print(json.dumps({
            'symbol': item.get('symbol'),
            'desc': item.get('desc'),
            'attchmntFile': item.get('attchmntFile'),
            'attachment': item.get('attachment'),
            'attime': item.get('attime'),
            'pdfurl': item.get('pdfurl'),
        }, indent=2))
