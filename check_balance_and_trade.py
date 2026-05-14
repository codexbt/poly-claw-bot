#!/usr/bin/env python3
"""
Check Wallet Balance & Execute Live Trade
==========================================
Checks USDC balance on Polygon and executes a real $1 trade on 
the current 5-minute BTC UP/DOWN market.

This script follows Polymarket API best practices from:
https://docs.polymarket.com/
"""

import sys
import os
from datetime import datetime, timezone
import json
from pprint import pprint

# Setup
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv()

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import (
    MarketOrderArgs, BalanceAllowanceParams, AssetType, OrderType
)
from py_clob_client.order_builder.constants import BUY, SELL

from bot import (
    find_btc_5min_market, market_prices, CFG, log
)

# API endpoints
CLOB_API = "https://clob.polymarket.com"


def check_balance():
    """
    Check USDC balance on Polygon wallet.
    Uses py-clob-client to query balance via CLOB API.
    """
    print("\n" + "="*70)
    print("1. CHECKING WALLET BALANCE")
    print("="*70 + "\n")
    
    try:
        # Initialize authenticated client
        auth_client = ClobClient(
            CLOB_API,
            key=CFG["PRIVATE_KEY"],
            chain_id=CFG["CHAIN_ID"],
            signature_type=0
        )
        
        # Derive API credentials from private key
        try:
            creds = auth_client.create_or_derive_api_creds()
            auth_client.set_api_creds(creds)
        except Exception as e:
            log.warning(f"Could not derive API creds: {e}")
        
        # Get balance
        balance_response = auth_client.get_balance_allowance(
            BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        )
        
        # Balance is in microUSDC (1e-6)
        usdc_balance = int(balance_response.get('balance', 0)) / 1e6
        usdc_allowance = int(balance_response.get('allowance', 0)) / 1e6
        
        print(f"Wallet Address: {CFG['RELAYER_API_KEY_ADDRESS']}")
        print(f"USDC Balance: ${usdc_balance:,.2f}")
        print(f"USDC Allowance: ${usdc_allowance:,.2f}\n")
        
        return usdc_balance, auth_client
        
    except Exception as e:
        print(f"✗ FAILED to check balance: {e}\n")
        return 0, None


def execute_live_trade(auth_client, balance):
    """
    Execute a real $1 trade on the current 5-minute BTC market.
    
    Args:
        auth_client: Authenticated ClobClient
        balance: Current USDC balance
    
    Returns:
        dict: Order response or None if failed
    """
    
    print("="*70)
    print("2. FINDING MARKET")
    print("="*70 + "\n")
    
    # Check balance is sufficient
    if balance < 1.0:
        print(f"✗ INSUFFICIENT BALANCE: ${balance:.2f} < $1.00 required")
        print("  Please deposit $10+ USDC to your wallet\n")
        return None
    
    print(f"✓ Balance check passed: ${balance:,.2f} >= $1.00\n")
    
    # Find market
    market = find_btc_5min_market()
    if not market:
        print("✗ Could not find 5-minute market")
        print("  (Market may not be active yet)\n")
        return None
    
    cond_id = market.get('conditionId', 'N/A')[:20]
    market_url = market.get('direct_url', 'N/A')
    
    print(f"✓ Market Found!")
    print(f"  Condition ID: {cond_id}...")
    print(f"  URL: {market_url}\n")
    
    # Get market prices
    print("="*70)
    print("3. GETTING MARKET PRICES")
    print("="*70 + "\n")
    
    try:
        up_px, dn_px, up_id, dn_id = market_prices(market)
        
        if up_px is None:
            print("✗ Could not fetch prices from order book")
            print("  Using fallback prices...\n")
            up_px = 0.50
            dn_px = 0.50
            up_id = market['clobTokenIds'][0]
            dn_id = market['clobTokenIds'][1]
        
        print(f"✓ Market Prices:")
        print(f"  UP binary (YES): ${up_px:.4f}")
        print(f"  DOWN binary (NO): ${dn_px:.4f}")
        print(f"  UP Token ID: {str(up_id)[:40]}...")
        print(f"  DOWN Token ID: {str(dn_id)[:40]}...\n")
        
    except Exception as e:
        print(f"✗ Failed to get prices: {e}\n")
        return None
    
    # Execute trade
    print("="*70)
    print("4. EXECUTING LIVE TRADE")
    print("="*70 + "\n")
    
    try:
        trade_amount = 1.0  # $1 USDC
        trade_side = BUY  # Buy UP shares
        
        print(f"Creating market order:")
        print(f"  Direction: UP (BUY)")
        print(f"  Amount: ${trade_amount} USDC")
        print(f"  Token: {str(up_id)[:30]}...")
        print(f"  Order Type: FOK (Fill-or-Kill)\n")
        
        # Create market order
        market_order = MarketOrderArgs(
            token_id=str(up_id),
            amount=trade_amount,
            side=trade_side
        )
        
        # Sign order
        print("Signing order with private key...")
        signed_order = auth_client.create_market_order(market_order)
        print("✓ Order signed\n")
        
        # Submit order
        print("Submitting to CLOB API...")
        response = auth_client.post_order(signed_order, OrderType.FOK)
        print("✓ Order submitted\n")
        
        # Check response
        if response:
            order_id = response.get('orderID', response.get('id', 'UNKNOWN'))
            status = response.get('status', 'PENDING')
            
            print("="*70)
            print("🎉 LIVE TRADE EXECUTED SUCCESSFULLY 🎉")
            print("="*70)
            print(f"\nOrder ID: {order_id}")
            print(f"Status: {status}")
            print(f"Amount: ${trade_amount}")
            print(f"Market: {market_url}")
            print(f"Timestamp: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
            print("\n" + "="*70)
            print("Full Response:")
            print("="*70)
            pprint(response)
            print()
            
            return response
        else:
            print("✗ No response from API\n")
            return None
            
    except Exception as e:
        print(f"✗ TRADE EXECUTION ERROR:")
        print(f"  {type(e).__name__}: {e}\n")
        return None


def main():
    """Main execution flow."""
    
    print("\n" + "🚀 "*20)
    print("POLYMARKET BTC 5-MIN LIVE TRADE")
    print("CHECK BALANCE & EXECUTE TRADE")
    print("🚀 "*20 + "\n")
    
    # Check balance
    balance, auth_client = check_balance()
    
    if not auth_client:
        print("Cannot proceed without authenticated client\n")
        return
    
    # Execute trade if balance sufficient
    if balance >= 1.0:
        print("="*70)
        print("Balance sufficient for $1 trade - proceeding...")
        print("="*70 + "\n")
        
        result = execute_live_trade(auth_client, balance)
        
        if result:
            print("✓ Trade execution completed - check Polymarket to monitor position")
        else:
            print("✗ Trade execution failed - see errors above")
    else:
        print("\n" + "!"*70)
        print("INSUFFICIENT BALANCE FOR TRADING")
        print("!"*70)
        print(f"\nCurrent Balance: ${balance:.2f}")
        print("Required: $1.00+ USDC")
        print("\nTo fund your wallet:")
        print(f"  1. Network: Polygon (MATIC)")
        print(f"  2. Token: USDC")
        print(f"  3. Address: {CFG['RELAYER_API_KEY_ADDRESS']}")
        print(f"  4. Amount: $10+ recommended")
        print("\nOnce funded, run this script again to execute trades\n")
    
    print("="*70)
    print("Session complete\n")


if __name__ == "__main__":
    main()
