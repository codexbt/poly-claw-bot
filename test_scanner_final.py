#!/usr/bin/env python3
import sys
sys.path.insert(0, '.')

from tennis_edge_bot import CLOBClient, MarketScanner, CHAMP_PRICE_MIN, CHAMP_PRICE_MAX

print(f"Champion price range: {CHAMP_PRICE_MIN} - {CHAMP_PRICE_MAX}")

clob = CLOBClient()
scanner = MarketScanner(clob)

print("Scanning for markets...")
markets = scanner.scan(max_pages=5)
print(f"Found {len(markets)} tennis markets after filtering")

for i, m in enumerate(markets[:3]):
    print(f"\n{i+1}. {m.question}")
    print(f"   YES: {m.yes_price:.4f}, NO: {m.no_price:.4f}")
    print(f"   Players: {m.player_a} vs {m.player_b}")

if not markets:
    print("No markets found. Checking raw API data...")
    import requests
    r = requests.get("https://clob.polymarket.com/markets?limit=100&active=true", timeout=15)
    data = r.json()
    markets_data = data.get('data', [])
    tennis_count = 0
    for m in markets_data:
        question = m.get('question', '').lower()
        if 'tennis' in question or 'atp' in question or 'wta' in question:
            tennis_count += 1
    print(f"Raw API has {tennis_count} tennis markets")