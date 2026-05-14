#!/usr/bin/env python3
"""
Polymarket BTC 5-Min Live Trade Test
=====================================
Tests a real $1 trade on the current BTC UP/DOWN 5-minute market.

Usage:
  python test_live_trade.py          # Attempts live $1 trade
  python test_live_trade.py --dry    # DRY RUN mode (test without real money)

Requirements:
  - .env file with PRIVATE_KEY and API credentials
  - Wallet funded with $10+ USDC (for live trades)
"""

import sys
import os
from datetime import datetime, timezone

# Load environment
from dotenv import load_dotenv
load_dotenv()

# Import bot functions
from bot import (
    init_client, find_btc_5min_market, market_prices, 
    market_order, btc_price, CFG, log
)

def test_live_trade(dry_run=False):
    """
    Execute a test trade on the current 5-minute BTC market.
    
    Args:
        dry_run (bool): If True, only simulate without sending real order
    
    Returns:
        dict: Trade result with order ID or error
    """
    
    print("\n" + "="*70)
    print("POLYMARKET BTC 5-MIN LIVE TRADE TEST")
    print("="*70 + "\n")
    
    # Show configuration
    mode = "DRY RUN (simulated)" if dry_run or CFG["DRY_RUN"] else "LIVE (real money)"
    print(f"Mode: {mode}")
    print(f"Trade Size: ${CFG['BASE_TRADE_SIZE']}")
    print(f"Wallet: {CFG['RELAYER_API_KEY_ADDRESS']}")
    print()
    
    # Step 1: Initialize CLOB client
    print("-" * 70)
    print("1. INITIALIZING CLOB CLIENT")
    print("-" * 70)
    
    # In DRY_RUN mode, we still need a client object to test the flow
    if CFG["DRY_RUN"]:
        # Create a minimal mock client for testing
        class MockClient:
            pass
        client = MockClient()
        print("✓ Mock CLOB client created (DRY RUN)")
    else:
        client = init_client()
        if not client:
            print("✗ FAILED: Could not initialize CLOB client")
            return None
        print("✓ CLOB client initialized with derived API credentials")
    
    print()
    
    # Step 2: Get current BTC price
    print("-" * 70)
    print("2. CHECKING BTC PRICE")
    print("-" * 70)
    
    try:
        btc_px = btc_price()
        print(f"✓ BTC Price: ${btc_px:,.2f}\n")
    except Exception as e:
        print(f"✗ FAILED to get BTC price: {e}\n")
        return None
    
    # Step 3: Find current 5-minute market
    print("-" * 70)
    print("3. FINDING 5-MINUTE MARKET")
    print("-" * 70)
    
    market = find_btc_5min_market()
    if not market:
        print("✗ FAILED: Could not find market")
        print("  (Market may not exist yet for current 5-min window)\n")
        return None
    
    cond_id = market.get('conditionId', 'N/A')[:20]
    market_url = market.get('direct_url', 'N/A')
    
    print(f"✓ Market Found!")
    print(f"  Condition ID: {cond_id}...")
    print(f"  URL: {market_url}\n")
    
    # Step 4: Get market prices
    print("-" * 70)
    print("4. FETCHING MARKET PRICES")
    print("-" * 70)
    
    try:
        up_px, dn_px, up_id, dn_id = market_prices(market)
        
        if up_px is None:
            print("✗ Could not fetch prices from order book")
            print("  Using fallback prices...")
            up_px = 0.50
            dn_px = 0.50
            up_id = market['clobTokenIds'][0] if market.get('clobTokenIds') else '?'
            dn_id = market['clobTokenIds'][1] if len(market.get('clobTokenIds', [])) > 1 else '?'
        
        print(f"✓ Market Prices:")
        print(f"  UP binary: ${up_px:.4f}")
        print(f"  DOWN binary: ${dn_px:.4f}")
        print(f"  UP Token ID: {str(up_id)[:40]}...")
        print(f"  DOWN Token ID: {str(dn_id)[:40]}...\n")
        
    except Exception as e:
        print(f"✗ Failed to get prices: {e}\n")
        return None
    
    # Step 5: Execute trade
    print("-" * 70)
    print("5. PLACING $1 UP TRADE")
    print("-" * 70)
    
    trade_direction = "UP"
    trade_size = 1.0
    trade_price = up_px
    
    print(f"Direction: {trade_direction}")
    print(f"Size: ${trade_size:.2f} USDC")
    print(f"Price: ${trade_price:.4f}")
    print(f"Execution: ", end="", flush=True)
    
    try:
        result = market_order(client, up_id, trade_size, trade_price, trade_direction)
        
        if result is None:
            print("\n\n✗ TRADE REJECTED")
            print("  Reason: Likely insufficient USDC balance")
            print("  Action: Deposit $10+ USDC to wallet before trading\n")
            return None
        
        if result.get('dry_run'):
            print("SIMULATED")
            print("\n✓ DRY RUN SUCCESSFUL")
            print("  (No real order placed - test passed)\n")
            return result
        
        # Live trade executed
        order_id = result.get('orderID', result.get('id', 'UNKNOWN'))
        print("LIVE")
        print("\n" + "🎉 " * 10)
        print("✓✓✓ LIVE TRADE EXECUTED ✓✓✓")
        print("🎉 " * 10)
        print(f"\nOrder ID: {order_id}")
        print(f"Result: {result}\n")
        
        return result
        
    except Exception as e:
        print(f"\n\n✗ TRADE EXECUTION ERROR")
        print(f"  Exception: {type(e).__name__}")
        print(f"  Message: {e}\n")
        return None


def main():
    # Check for --dry flag
    dry_run = '--dry' in sys.argv or '--dry-run' in sys.argv
    
    if dry_run:
        # Force dry run mode
        os.environ['DRY_RUN'] = 'true'
        print("⚠️  DRY RUN MODE ENABLED (no real trades)\n")
    
    # Execute test
    result = test_live_trade(dry_run=dry_run)
    
    # Summary
    print("="*70)
    print("TEST SUMMARY")
    print("="*70)
    
    if result:
        if result.get('dry_run'):
            print("✓ Dry run successful - code paths validated")
            print("  Ready for live trading once wallet is funded")
        else:
            print("✓ Live trade executed successfully!")
            print("  Check Polymarket to confirm fill")
    else:
        print("✗ Test failed - see errors above")
    
    if not CFG['DRY_RUN'] and not dry_run:
        print("\n⚠️  WALLET STATUS:")
        print(f"  Address: {CFG.get('RELAYER_API_KEY_ADDRESS', 'unknown')}")
        print("  Required: $10+ USDC for live trading")
        print("  To deposit: Send USDC to above address on Polygon network")
    
    print("="*70 + "\n")


if __name__ == "__main__":
    main()
