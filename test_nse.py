import requests
import time

s = requests.Session()
s.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
    'Sec-Fetch-Dest': 'document',
    'Sec-Fetch-Mode': 'navigate',
    'Sec-Fetch-Site': 'none',
    'Sec-Fetch-User': '?1',
})

print("Handshaking...")
r = s.get('https://www.nseindia.com', timeout=15)
print(f"Handshake status: {r.status_code}")
time.sleep(2)

s.headers.update({
    'Accept': '*/*',
    'Referer': 'https://www.nseindia.com/companies-listing/corporate-filings-announcements?symbol=RELIANCE',
    'Sec-Fetch-Dest': 'empty',
    'Sec-Fetch-Mode': 'cors',
    'Sec-Fetch-Site': 'same-origin',
    'X-Requested-With': 'XMLHttpRequest'
})
del s.headers['Upgrade-Insecure-Requests']
del s.headers['Sec-Fetch-User']

symbol = 'RELIANCE'
api_url = f'https://www.nseindia.com/api/corporate-announcements?symbol={symbol}'
print(f"Fetching {api_url}...")
r2 = s.get(api_url, timeout=15)
print(f"API status: {r2.status_code}")
try:
    data = r2.json()
    print(f"Success! Got {len(data)} items.")
except Exception as e:
    print(f"Failed to decode JSON: {e}")
    print(r2.text[:500])
