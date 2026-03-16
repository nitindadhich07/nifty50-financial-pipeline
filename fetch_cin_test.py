import requests
import json
import time

s = requests.Session()
s.headers.update({"User-Agent": "Mozilla/5.0", "Accept-Language": "en-US,en;q=0.9"})
s.get("https://www.nseindia.com", timeout=10)
time.sleep(0.5)
resp = s.get("https://www.nseindia.com/api/quote-equity?symbol=RELIANCE", timeout=10)
data = resp.json()
print("NSE Result keys:", data.keys())
if 'info' in data:
    print(data['info'])
