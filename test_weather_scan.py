#!/usr/bin/env python3
"""Quick test: scan one city and show detailed output."""

import sys

sys.path.insert(0, r"D:\btcupdownclaudebot")

from dotenv import load_dotenv

load_dotenv()

from weather_bot import (
    CLOBClient,
    LOCATIONS,
    take_forecast_snapshot,
    get_polymarket_event,
    MONTHS,
)
from datetime import datetime, timezone, timedelta
import os

PRIVATE_KEY = os.getenv("PRIVATE_KEY")
CHAIN_ID = int(os.getenv("CHAIN_ID", "137"))
SIGNATURE_TYPE = int(os.getenv("SIGNATURE_TYPE", "1"))
FUNDER_ADDRESS = os.getenv("POLYMARKET_FUNDER_ADDRESS") or os.getenv("WALLET_ADDRESS")
CLOB_HOST = os.getenv("POLYMARKET_HOST", "https://clob.polymarket.com")

print("Initializing CLOB L2 client...")
clob = CLOBClient(CLOB_HOST, PRIVATE_KEY, CHAIN_ID, SIGNATURE_TYPE, FUNDER_ADDRESS)

city_slug = "nyc"
loc = LOCATIONS[city_slug]
now = datetime.now(timezone.utc)
dates = [(now + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(4)]

print(f"\nWeather forecast for {loc['name']}:")
snapshots = take_forecast_snapshot(city_slug, dates)
for date_str, snap in snapshots.items():
    best = snap.get("best")
    source = snap.get("best_source", "N/A")
    print(f"  {date_str}: {best}°{loc['unit']} (source={source})")

print(f"\nPolymarket markets:")
for date_str in dates:
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    event = get_polymarket_event(city_slug, MONTHS[dt.month - 1], dt.day, dt.year)
    if event:
        print(f"\n  {date_str}:")
        for market in event.get("markets", []):
            question = market.get("question", "")
            try:
                token_id = market.get("clobTokenIds", [None])[0]
                if not token_id:
                    raise KeyError("No clobTokenIds")
                book = clob.get_order_book(token_id)
                best_ask = (
                    float(book.get("asks", [{}])[0].get("price", 0))
                    if book.get("asks")
                    else 0
                )
                best_bid = (
                    float(book.get("bids", [{}])[0].get("price", 0))
                    if book.get("bids")
                    else 0
                )
                print(f"    {question}")
                print(f"      Bid: ${best_bid:.3f}  Ask: ${best_ask:.3f}")
            except Exception as e:
                print(f"    {question} [ERROR: {e}]")
    else:
        print(f"\n  {date_str}: No market")

print("\nTest complete.")
