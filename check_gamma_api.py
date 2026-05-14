#!/usr/bin/env python3
"""Check the exact structure of a Polymarket Gamma API response."""

import requests
import json

slug = "highest-temperature-in-nyc-on-april-23-2026"
resp = requests.get(f"https://gamma-api.polymarket.com/events?slug={slug}", timeout=10)
data = resp.json()
if data and isinstance(data, list) and len(data) > 0:
    event = data[0]
    print("Event title:", event.get("title"))
    print("Event keys:", list(event.keys()))
    print("\nMarkets (first):")
    if event.get("markets"):
        market = event["markets"][0]
        print("  Market keys:", list(market.keys()))
        print("  Market question:", market.get("question"))
        print("  Tokens:", market.get("tokens"))
        if market.get("tokens"):
            token = market["tokens"][0]
            print(
                "  Token keys:",
                list(token.keys()) if isinstance(token, dict) else "not a dict",
            )
            print(
                "  Token ID:",
                token.get("token_id") if isinstance(token, dict) else token,
            )
    else:
        print("  No markets")
else:
    print("No event data")
