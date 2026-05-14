#!/usr/bin/env python3
import sys
sys.path.insert(0, '.')

try:
    from tennis_edge_bot import CLOBClient, MarketScanner
    print("Imports successful")

    clob = CLOBClient()
    scanner = MarketScanner(clob)
    print("Objects created")

    markets = scanner.scan(max_pages=1)
    print(f"Found {len(markets)} markets")

except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()