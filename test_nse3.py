import requests
import time

s = requests.Session()
s.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': '*/*',
    'Accept-Language': 'en-US,en;q=0.5',
})

print("Handshaking...")
r = s.get('https://www.nseindia.com', timeout=15)
time.sleep(2)

s.headers.update({
    'Referer': 'https://www.nseindia.com/companies-listing/corporate-filings-announcements'
})

# Try with symbol and index
symbol = 'RELIANCE'
api_url = f'https://www.nseindia.com/api/corporate-announcements?index=equities&symbol={symbol}'
print(f"Fetching {api_url}...")
r2 = s.get(api_url, timeout=15)
try:
    data = r2.json()
    print(f"Success! Got {len(data)} items for RELIANCE.")
except Exception as e:
    print(f"Failed: {r2.text[:100]}")

# Try with from_date and to_date
api_url2 = f'https://www.nseindia.com/api/corporate-announcements?index=equities&symbol={symbol}&from_date=01-01-2026&to_date=06-03-2026'
print(f"Fetching {api_url2}...")
r3 = s.get(api_url2, timeout=15)
try:
    data = r3.json()
    print(f"Success! Got {len(data)} items for RELIANCE with dates.")
    for item in data:
        if "financial results" in item.get('desc', '').lower() or "standalone" in item.get('desc', '').lower():
            print(f"Found: {item.get('desc')} - {item.get('attime')} - {item.get('attachment')}")
except Exception as e:
    print(f"Failed: {r3.text[:100]}")
