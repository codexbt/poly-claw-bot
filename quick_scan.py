#!/usr/bin/env python3
import sys
sys.path.insert(0, '.')

from tennis_edge_bot import CLOBClient, MarketScanner, TennisEdgeBot

print("Testing tennis bot scanner...")

clob = CLOBClient()
scanner = MarketScanner(clob)

print("Scanning for tennis markets...")
markets = scanner.scan(max_pages=1)

print(f"\nFound {len(markets)} tennis markets")

if markets:
    for i, m in enumerate(markets):
        print(f"\n{i+1}. {m.question}")
        print(f"   Players: {m.player_a} vs {m.player_b}")
        print(f"   YES: {m.yes_price:.4f}, NO: {m.no_price:.4f}")
        print(f"   Volume: ${m.volume:.0f}")

    # Try to process the first market
    print("
Trying to process first market...")
    bot = TennisEdgeBot()
    try:
        bot.process_market(markets[0])
    except Exception as e:
        print(f"Error processing market: {e}")
else:
    print("No markets found")