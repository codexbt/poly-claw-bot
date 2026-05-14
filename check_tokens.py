#!/usr/bin/env python3
"""Check the token structure for Polymarket Gamma events."""

import requests
import json

slug = "highest-temperature-in-nyc-on-april-23-2026"
resp = requests.get(f"https://gamma-api.polymarket.com/events?slug={slug}", timeout=10)
data = resp.json()
if data and isinstance(data, list) and len(data) > 0:
    event = data[0]
    if event.get("markets"):
        market = event["markets"][0]
        print("clobTokenIds:", market.get("clobTokenIds"))
        print("outcomes:", market.get("outcomes"))
        print("outcomePrices:", market.get("outcomePrices"))
        print("question:", market.get("question"))
        # Try to find token ID for YES outcome
        clob_token_ids = market.get("clobTokenIds")
        if (
            clob_token_ids
            and isinstance(clob_token_ids, list)
            and len(clob_token_ids) > 0
        ):
            print("First token ID:", clob_token_ids[0])
        # Also check the full response
        print("\nFull market sample (truncated):")
        print(json.dumps(market, indent=2)[:2000])
