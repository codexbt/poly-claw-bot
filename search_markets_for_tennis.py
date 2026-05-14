import json

with open('markets_raw.json','r',encoding='utf-8') as f:
    data = json.load(f)
arr = data.get('data', [])
count=0
for m in arr:
    q = (m.get('question') or '').lower()
    desc = (m.get('description') or '').lower()
    tags_val = m.get('tags', [])
    if isinstance(tags_val, list):
        tags = ' '.join(t for t in tags_val if isinstance(t, str)).lower()
    else:
        tags = str(tags_val).lower()
    if 'tennis' in q or 'tennis' in desc or 'tennis' in tags or 'atp' in q or 'wta' in q:
        count+=1
        print('FOUND:', m.get('question'))
print('total tennis-like markets found:', count)
print('total markets:', len(arr))
