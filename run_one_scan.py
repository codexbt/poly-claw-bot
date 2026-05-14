#!/usr/bin/env python3
"""Run one scan cycle of weather_bot and exit."""

import sys

sys.path.insert(0, r"D:\btcupdownclaudebot")

from dotenv import load_dotenv

load_dotenv()

from weather_bot import (
    CLOBClient,
    LOCATIONS,
    take_forecast_snapshot,
    get_polymarket_event,
)
from weather_bot import scan_and_trade, check_and_close_positions, load_state
from datetime import datetime, timezone, timedelta
import os

# Override config for quick test
os.environ["SCAN_INTERVAL_SECONDS"] = "3600"
os.environ["MONITOR_INTERVAL_SECONDS"] = "600"
os.environ["MIN_HOURS"] = "2.0"
os.environ["MAX_HOURS"] = "72.0"

PRIVATE_KEY = os.getenv("PRIVATE_KEY")
CHAIN_ID = int(os.getenv("CHAIN_ID", "137"))
SIGNATURE_TYPE = int(os.getenv("SIGNATURE_TYPE", "1"))
FUNDER_ADDRESS = os.getenv("POLYMARKET_FUNDER_ADDRESS") or os.getenv("WALLET_ADDRESS")
CLOB_HOST = os.getenv("POLYMARKET_HOST", "https://clob.polymarket.com")

print("Initializing CLOB L2 client...")
clob = CLOBClient(CLOB_HOST, PRIVATE_KEY, CHAIN_ID, SIGNATURE_TYPE, FUNDER_ADDRESS)

state = load_state()
print(f"\nStarting scan (balance: ${state['balance']:.2f})...")
new_trades, closed = scan_and_trade(clob, state)
print(f"\n--- Scan complete ---")
print(f"New trades: {new_trades}")
print(f"Positions closed: {closed}")
print(f"Balance: ${load_state()['balance']:.2f}")
