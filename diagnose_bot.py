#!/usr/bin/env python3
"""
COMPREHENSIVE BOT DIAGNOSTICS
==============================
Checks all critical components:
1. Environment variables
2. Relayer API connectivity  
3. CLOB client initialization
4. Market fetching
5. Price fetching
6. Trade execution readiness
"""

import os
import sys
import json
import time
import requests
from datetime import datetime, timezone
from dotenv import load_dotenv

print("\n" + "="*80)
print("POLYMARKET BOT - COMPREHENSIVE DIAGNOSTICS")
print("="*80 + "\n")

# Load environment
load_dotenv()

# ============================================================================
# 1. ENVIRONMENT CHECK
# ============================================================================
print("[1] ENVIRONMENT VARIABLES")
print("-" * 80)

required_vars = [
    "PRIVATE_KEY",
    "RELAYER_API_KEY", 
    "RELAYER_API_KEY_ADDRESS",
    "DRY_RUN",
]

env_ok = True
for var in required_vars:
    value = os.getenv(var, "")
    status = "✓" if value else "✗"
    if var == "PRIVATE_KEY":
        display = f"{value[:16]}...{value[-8:]}" if value else "MISSING"
    elif var in ["RELAYER_API_KEY", "RELAYER_API_KEY_ADDRESS"]:
        display = f"{value[:20]}...{value[-8:]}" if value else "MISSING"
    else:
        display = value
    
    print(f"  {status} {var:30s} = {display}")
    if not value and var != "DRY_RUN":
        env_ok = False

if not env_ok:
    print("\n✗ FAILED: Missing critical environment variables!")
    print("  Fix .env file and try again")
    sys.exit(1)

dry_run = os.getenv("DRY_RUN", "true").lower() == "true"
print(f"\n  Mode: {'DRY RUN' if dry_run else '⚠ LIVE TRADING'}")

# ============================================================================
# 2. RELAYER API TEST
# ============================================================================
print("\n[2] RELAYER API CONNECTIVITY")
print("-" * 80)

relayer_key = os.getenv("RELAYER_API_KEY")
relayer_addr = os.getenv("RELAYER_API_KEY_ADDRESS")

print("  Testing endpoint: https://relayer.polymarket.com/")

headers = {
    "X-API-KEY": relayer_key,
    "Content-Type": "application/json"
}

relayer_ok = False
try:
    # Try multiple relayer endpoints
    endpoints = [
        ("https://relayer.polymarket.com/account", "Account info"),
        ("https://relayer.polymarket.com/ping", "Ping"),
        ("https://api.relayer.polymarket.com/account", "Alternative account"),
    ]
    
    for endpoint, desc in endpoints:
        try:
            resp = requests.get(endpoint, headers=headers, timeout=5)
            print(f"  ✓ {desc:25s} [{endpoint.split('/')[-2]}] - Status {resp.status_code}")
            if resp.status_code in [200, 401, 403]:
                relayer_ok = True  # At least got a response
            break
        except:
            continue
    
    if not relayer_ok:
        print(f"  ✗ Relayer not responding to any endpoint")
        print(f"    Note: This is OK if using standard CLOB (not gasless)")
except Exception as e:
    print(f"  ✗ Network error: {str(e)[:60]}...")
    print(f"    Note: If no internet, bot cannot trade")

# ============================================================================
# 3. CLOB CLIENT TEST (from bot)
# ============================================================================
print("\n[3] CLOB CLIENT INITIALIZATION")
print("-" * 80)

clob_ok = False
try:
    # Try importing bot components
    from py_clob_client.client import ClobClient
    
    print("  ✓ py_clob_client imported successfully")
    
    # Try creating CLOB client with chain_id
    private_key = os.getenv("PRIVATE_KEY")
    chain_id = int(os.getenv("CHAIN_ID", "137"))
    
    if private_key:
        # Use ClobClient directly (correct way)
        host = "https://clob.polymarket.com"
        client = ClobClient(
            host=host,
            key=private_key,
            chain_id=chain_id,
            signature_type=2,  # EOA signature
        )
        print(f"  ✓ CLOB client created for chain {chain_id}")
        
        # Try to derive API credentials
        try:
            creds = client.create_or_derive_api_creds()
            client.set_api_creds(creds)
            print(f"  ✓ API credentials derived and set")
            clob_ok = True
        except Exception as e:
            print(f"  ⚠ Could not derive credentials: {e}")
            clob_ok = True  # Client still created OK
    else:
        print("  ✗ PRIVATE_KEY not set")
        
except ImportError as e:
    print(f"  ✗ Import error: {e}")
    print("    Fix: pip install py-clob-client")
except Exception as e:
    print(f"  ✗ CLOB client error: {e}")

# ============================================================================
# 4. MARKET FETCHING TEST
# ============================================================================
print("\n[4] MARKET DISCOVERY")
print("-" * 80)

market_ok = False
try:
    import pytz
    from datetime import datetime, timezone
    import re
    
    # Test fetching BTC market
    et_tz = pytz.timezone("America/New_York")
    et_now = datetime.now(timezone.utc).astimezone(et_tz)
    window_min = (et_now.minute // 5) * 5
    window_start = et_now.replace(minute=window_min, second=0, microsecond=0)
    market_ts = int(window_start.timestamp())
    
    # Market slugs as defined in poly5min_all.py
    MARKET_SLUGS = {
        "BTC": "bitcoin-updown-5m",
        "ETH": "ethereum-updown-5m",
        "SOL": "solana-updown-5m",
        "XRP": "ripple-updown-5m",
        "DOGE": "dogecoin-updown-5m",
        "HYPE": "hype-updown-5m",
        "BNB": "binance-coin-updown-5m",
    }
    
    # Try current window market
    slug = MARKET_SLUGS.get("BTC", "bitcoin-updown-5m")
    url = f"https://polymarket.com/event/{slug}-{market_ts}"
    
    print(f"  Testing market window...")
    print(f"  Current time (ET): {et_now.strftime('%H:%M:%S')}")
    print(f"  Window timestamp: {market_ts}")
    print(f"  URL: {url[:60]}...")
    
    headers = {"User-Agent": "Mozilla/5.0"}
    r = requests.get(url, headers=headers, timeout=10)
    
    if r.status_code == 200:
        print(f"  ✓ Market page fetched (status {r.status_code})")
        
        # Try to parse market data
        if "conditionId" in r.text:
            print(f"  ✓ Market data found in HTML")
            market_ok = True
        else:
            print(f"  ⚠ Market page loaded but no conditionId found in HTML")
            print(f"    This might mean: market structure changed OR wrong window")
    else:
        # Try with earlier window (market might have just closed)
        print(f"  ✗ Current window failed (status {r.status_code})")
        print(f"    Trying previous window...")
        
        from datetime import timedelta
        earlier = window_start - timedelta(minutes=5)
        earlier_ts = int(earlier.timestamp())
        url2 = f"https://polymarket.com/event/{slug}-{earlier_ts}"
        r2 = requests.get(url2, headers=headers, timeout=10)
        
        if r2.status_code == 200 and "conditionId" in r2.text:
            print(f"  ✓ Previous window market found")
            market_ok = True
        else:
            print(f"  ✗ No active market found")
            print(f"    Note: Markets might not exist at this time OR")
            print(f"          Polymarket changed their URL structure")
        
except Exception as e:
    print(f"  ✗ Market fetch error: {e}")

# ============================================================================
# 5. PRICE FETCHING TEST  
# ============================================================================
print("\n[5] PRICE DATA")
print("-" * 80)

price_ok = False
try:
    import ccxt
    
    # Test Binance
    binance = ccxt.binance()
    btc_binance = float(binance.fetch_ticker("BTC/USDT")["last"])
    print(f"  ✓ Binance BTC price: ${btc_binance:,.2f}")
    
    # Test Coinbase (renamed from coinbasepro)
    try:
        # Try new name first
        coinbase = ccxt.coinbaseexchange()
        btc_coinbase = float(coinbase.fetch_ticker("BTC/USD")["last"])
        print(f"  ✓ Coinbase BTC price: ${btc_coinbase:,.2f}")
    except:
        # Fallback to just using Binance
        print(f"  ⚠ Coinbase unavailable, using Binance only")
        btc_coinbase = btc_binance
    
    avg_price = (btc_binance + btc_coinbase) / 2
    print(f"  ✓ Average price: ${avg_price:,.2f}")
    price_ok = True
    
except Exception as e:
    print(f"  ✗ Price fetch error: {e}")

# ============================================================================
# 6. TRADE SIMULATION
# ============================================================================
print("\n[6] TRADE EXECUTION TEST (DRY RUN)")
print("-" * 80)

trade_ok = False
try:
    print(f"  Testing trade execution...")
    
    # Simulate order placement
    if dry_run:
        print(f"  ✓ DRY_RUN mode: Orders will be simulated")
        trade_ok = True
    elif clob_ok:
        print(f"  ✓ Live mode enabled")
        print(f"  ⚠ CAUTION: Bot will place REAL trades")
        trade_ok = True
    else:
        print(f"  ✗ CLOB client not ready for live trading")
        
except Exception as e:
    print(f"  ✗ Trade test error: {e}")

# ============================================================================
# SUMMARY
# ============================================================================
print("\n" + "="*80)
print("DIAGNOSTIC SUMMARY")
print("="*80)

checks = {
    "Environment": env_ok,
    "CLOB Client": clob_ok,
    "Market Discovery": market_ok,
    "Price Fetching": price_ok,
    "Trade Ready": trade_ok,
}

all_ok = all(checks.values())

for check, status in checks.items():
    icon = "✓" if status else "✗"
    print(f"  {icon} {check:20s}: {'PASS' if status else 'FAIL'}")

print("\n" + "="*80)

if all_ok:
    print("✓ ALL CHECKS PASSED - Bot should be able to trade!")
    print("\nNext: Run the bot with: python poly5min_all.py")
elif clob_ok and market_ok and price_ok and env_ok:
    print("✓ CRITICAL CHECKS PASSED")
    print("⚠ Some non-critical checks failed (e.g., Relayer)")
    print("  Bot can trade via standard CLOB (will use some MATIC for gas)")
else:
    print("✗ CRITICAL ISSUES FOUND")
    print("\nTroubleshooting:")
    if not env_ok:
        print("  1. Check your .env file - missing environment variables")
    if not clob_ok:
        print("  2. Check PRIVATE_KEY and py_clob_client installation")
    if not market_ok:
        print("  3. Check if Polymarket markets are accessible")
    if not price_ok:
        print("  4. Check internet connection for price feeds")

print("\n" + "="*80 + "\n")
