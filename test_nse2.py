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
print(f"Handshake status: {r.status_code}")
time.sleep(2)

s.headers.update({
    'Referer': 'https://www.nseindia.com/companies-listing/corporate-filings-announcements'
})

api_url = 'https://www.nseindia.com/api/corporate-announcements?index=equities'
print(f"Fetching {api_url}...")
r2 = s.get(api_url, timeout=15)
print(f"API status: {r2.status_code}")
try:
    data = r2.json()
    print(f"Success! Got {len(data)} items.")
    print("Sample:", data[0].get('symbol'), data[0].get('desc'))
except Exception as e:
    print(f"Failed to decode JSON: {e}")
    print(r2.text[:500])
