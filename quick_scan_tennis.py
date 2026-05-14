from tennis_edge_bot import CLOBClient, MarketScanner

clob = CLOBClient()
scanner = MarketScanner(clob)
markets = scanner.scan(max_pages=3)
print('Found', len(markets), 'tennis markets (first 10):')
for m in markets[:10]:
    print('-', m.player_a, 'vs', m.player_b, '| yes_price=', m.yes_price, 'vol=', m.volume)
