#!/usr/bin/env python3
"""Quick test: run scan_and_trade directly on 1 city."""

import sys, os, json, time

sys.path.insert(0, r"D:\btcupdownclaudebot")
from dotenv import load_dotenv

load_dotenv()
from weather_bot import CLOBClient, LOCATIONS, scan_and_trade, load_state, print_status
from datetime import datetime, timezone, timedelta

# Reduce scan scope to only NYC
original_loc = LOCATIONS.copy()
LOCATIONS.clear()
LOCATIONS["nyc"] = original_loc["nyc"]

# Override environment for quick test
os.environ["SCAN_INTERVAL_SECONDS"] = "3600"
os.environ["MIN_HOURS"] = "2.0"
os.environ["MAX_HOURS"] = "72.0"

PRIVATE_KEY = os.getenv("PRIVATE_KEY")
CHAIN_ID = int(os.getenv("CHAIN_ID", "137"))
SIGNATURE_TYPE = int(os.getenv("SIGNATURE_TYPE", "1"))
FUNDER_ADDRESS = os.getenv("POLYMARKET_FUNDER_ADDRESS") or os.getenv("WALLET_ADDRESS")
CLOB_HOST = os.getenv("POLYMARKET_HOST", "https://clob.polymarket.com")

print("Initializing CLOB L2...")
clob = CLOBClient(CLOB_HOST, PRIVATE_KEY, CHAIN_ID, SIGNATURE_TYPE, FUNDER_ADDRESS)

state = load_state()
print(f"Starting scan (balance: ${state['balance']:.2f})...")
new_trades, closed = scan_and_trade(clob, state)
print(f"\nResult: new trades={new_trades}, closed={closed}")
print(f"Final balance: ${load_state()['balance']:.2f}")
print("\n--- Status ---")
print_status(clob)
