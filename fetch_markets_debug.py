from dotenv import load_dotenv
import os, requests, json, sys

load_dotenv('.env.tennis')
CLOB_HOST = os.getenv('POLYMARKET_HOST', 'https://clob.polymarket.com')
print('CLOB_HOST=', CLOB_HOST)
try:
    r = requests.get(f"{CLOB_HOST}/markets", timeout=15)
    print('HTTP', r.status_code)
    try:
        data = r.json()
        print('Top-level keys:', list(data.keys()))
        with open('markets_raw.json', 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
        print('Saved markets_raw.json (first 200 chars):')
        s = json.dumps(data)[:200]
        print(s)
    except Exception as e:
        print('JSON parse error:', e)
        print(r.text[:1000])
except Exception as e:
    print('Request error:', e)
    sys.exit(2)
