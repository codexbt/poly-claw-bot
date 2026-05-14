#!/usr/bin/env python3
"""Debug order book from CLOB client."""

import sys

sys.path.insert(0, r"D:\btcupdownclaudebot")
from dotenv import load_dotenv

load_dotenv()
from weather_bot import CLOBClient, LOCATIONS, get_polymarket_event, MONTHS
from datetime import datetime
import os
import json

PRIVATE_KEY = os.getenv("PRIVATE_KEY")
CHAIN_ID = int(os.getenv("CHAIN_ID", "137"))
SIGNATURE_TYPE = int(os.getenv("SIGNATURE_TYPE", "1"))
FUNDER_ADDRESS = os.getenv("POLYMARKET_FUNDER_ADDRESS") or os.getenv("WALLET_ADDRESS")
CLOB_HOST = os.getenv("POLYMARKET_HOST", "https://clob.polymarket.com")

print("Init...")
clob = CLOBClient(CLOB_HOST, PRIVATE_KEY, CHAIN_ID, SIGNATURE_TYPE, FUNDER_ADDRESS)

city_slug = "nyc"
dt = datetime.strptime("2026-04-23", "%Y-%m-%d")
event = get_polymarket_event(city_slug, MONTHS[dt.month - 1], dt.day, dt.year)
if event:
    market = event["markets"][0]
    raw_ids = market.get("clobTokenIds", "[]")
    token_ids = json.loads(raw_ids) if isinstance(raw_ids, str) else raw_ids
    print(f"Token IDs raw: {token_ids} (type: {type(token_ids)})")
    token_id = token_ids[0] if token_ids else None
    print(f"Token ID: {token_id}")
    book = clob.get_order_book(token_id)
    print(f"Book raw: {book}")
    bids = book.get("bids", [])
    asks = book.get("asks", [])
    print(f"Bids: {bids[:3]}")
    print(f"Asks: {asks[:3]}")
