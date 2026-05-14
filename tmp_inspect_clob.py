import requests, json
url = 'https://clob.polymarket.com/markets'
params = {'active': 'true', 'limit': '200'}
r = requests.get(url, params=params, timeout=15)
r.raise_for_status()
data = r.json()
items = data if isinstance(data, list) else data.get('data', data.get('markets', []))
print('items', len(items))
for m in items[:200]:
    if not (m.get('endDate') or m.get('end_date_iso') or m.get('end_date') or m.get('closeTime') or m.get('close_time')):
        print('--- no end field ---')
        print(json.dumps({k: m.get(k) for k in ['id', 'condition_id', 'question', 'title', 'endDate', 'end_date_iso', 'end_date', 'closeTime', 'close_time', 'tokens', 'clobTokenIds']}, indent=2)[:2000])
        break
