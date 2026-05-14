import requests

url = 'https://gamma-api.polymarket.com/markets'
params = {'active':'true', 'closed':'false', 'limit':5, 'order':'volume24hr', 'ascending':'false'}
response = requests.get(url, params=params, timeout=10)
print('status', response.status_code)
data = response.json()
print('type', type(data))
if isinstance(data, dict):
    print('keys', list(data.keys()))
    results = data.get('results')
    print('results len', len(results) if results else None)
    if results:
        first = results[0]
        print('first keys', list(first.keys()))
        print('question', first.get('question'))
        print('resolution fields', first.get('resolution'), first.get('time_resolution'), first.get('interval'), first.get('timeframe'))
        print('volume24hr', first.get('volume24hr'), first.get('volume_24hr'))
        print('tokens', first.get('tokens'))
        print('clob_token_ids', first.get('clob_token_ids'))
elif isinstance(data, list):
    print('len', len(data))
    if data:
        print('first keys', list(data[0].keys()))
