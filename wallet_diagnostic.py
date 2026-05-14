#!/usr/bin/env python3
"""
Wallet Balance Diagnostic Tool
===============================
Checks wallet balance across multiple Polymarket APIs and blockchains.
Helps identify where USDC might be located.
"""

import sys
import os
import json
import requests

sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv()

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import BalanceAllowanceParams, AssetType

from bot import CFG, log

# APIs
CLOB_API = "https://clob.polymarket.com"
DATA_API = "https://data-api.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"


def check_clob_balance():
    """Check balance via CLOB API (Polygon/USDC)."""
    print("\n" + "-"*70)
    print("1. CLOB API Balance Check (Polygon USDC)")
    print("-"*70)
    
    try:
        client = ClobClient(
            CLOB_API,
            key=CFG["PRIVATE_KEY"],
            chain_id=CFG["CHAIN_ID"],
            signature_type=0
        )
        
        # Derive credentials
        try:
            creds = client.create_or_derive_api_creds()
            client.set_api_creds(creds)
        except:
            pass
        
        # Get balance
        balance_response = client.get_balance_allowance(
            BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        )
        
        usdc_balance = int(balance_response.get('balance', 0)) / 1e6
        allowance = int(balance_response.get('allowance', 0)) / 1e6
        
        print(f"✓ USDC Balance: ${usdc_balance:,.2f}")
        print(f"✓ Allowance: ${allowance:,.2f}")
        
        return usdc_balance
        
    except Exception as e:
        print(f"✗ Error: {e}")
        return 0


def check_data_api_positions():
    """Check positions via Data API."""
    print("\n" + "-"*70)
    print("2. Data API - User Positions")
    print("-"*70)
    
    try:
        wallet = CFG['RELAYER_API_KEY_ADDRESS']
        url = f"{DATA_API}/user/{wallet}"
        
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        
        if data:
            print(f"✓ User data found")
            print(json.dumps(data, indent=2)[:500])
        else:
            print("✗ No user data found")
            
    except Exception as e:
        print(f"✗ Error: {e}")


def check_wallet_address():
    """Verify wallet configuration."""
    print("\n" + "-"*70)
    print("3. Wallet Configuration")
    print("-"*70)
    
    wallet = CFG['RELAYER_API_KEY_ADDRESS']
    private_key = CFG['PRIVATE_KEY']
    
    print(f"Wallet Address: {wallet}")
    print(f"Private Key: {private_key[:20]}...{private_key[-10:]}")
    print(f"Chain ID: {CFG['CHAIN_ID']} (Polygon)")
    
    # Verify private key format
    if private_key.startswith('0x') and len(private_key) == 66:
        print("✓ Private key format valid (0x + 64 hex characters)")
    else:
        print("✗ WARNING: Private key format may be invalid")


def check_transaction_history():
    """Check recent transactions for the wallet."""
    print("\n" + "-"*70)
    print("4. Recent Orders/Trades")
    print("-"*70)
    
    try:
        wallet = CFG['RELAYER_API_KEY_ADDRESS']
        url = f"{DATA_API}/orders"
        
        params = {
            "user": wallet,
            "limit": 10
        }
        
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        
        orders = response.json()
        
        if isinstance(orders, list) and orders:
            print(f"✓ Found {len(orders)} recent orders")
            for order in orders[:3]:
                print(f"\n  Order: {order.get('id', 'unknown')[:20]}...")
                print(f"    Side: {order.get('side', 'N/A')}")
                print(f"    Amount: ${order.get('amount', 0)}")
                print(f"    Status: {order.get('status', 'N/A')}")
        else:
            print("ℹ No recent orders found")
            
    except Exception as e:
        print(f"ℹ Could not fetch order history: {e}")


def check_what_to_do():
    """Provide actionable next steps."""
    print("\n" + "="*70)
    print("WHAT TO DO NEXT")
    print("="*70)
    
    balance = check_clob_balance()
    
    if balance >= 1.0:
        print(f"\n✓ Your wallet has ${balance:.2f} USDC")
        print("  You can now execute live trades!")
        print("\n  Run: python check_balance_and_trade.py")
        
    elif balance > 0:
        print(f"\n⚠️ Your wallet has ${balance:.2f} USDC (need $1.00+ per trade)")
        print("\n  To fund wallet:")
        print(f"    1. Send USDC to: {CFG['RELAYER_API_KEY_ADDRESS']}")
        print("    2. Network: Polygon (MATIC)")
        print("    3. Amount: $10+ recommended")
        print("    4. Wait for confirmation")
        print("    5. Run this script again to verify")
        
    else:
        print("\n✗ Your wallet has $0 USDC balance")
        print("\n  To start trading:")
        print(f"    1. Transfer USDC to Polygon network")
        print(f"    2. Send to address: {CFG['RELAYER_API_KEY_ADDRESS']}")
        print(f"    3. Recommended amount: $10-100 USD")
        print("\n  Transfer via:")
        print("    • Bridge.polymarket.com")
        print("    • Polygon Bridge (polygon.technology/bridge)")
        print("    • CEX withdrawal to Polygon (Kraken, Coinbase, etc)")
        print("\n  After funding, run this script again")
        
    print("\n" + "="*70 + "\n")


def main():
    """Run all diagnostics."""
    
    print("\n" + "🔍 "*25)
    print("POLYMARKET WALLET DIAGNOSTIC")
    print("🔍 "*25)
    
    check_wallet_address()
    check_clob_balance()
    check_data_api_positions()
    check_transaction_history()
    check_what_to_do()


if __name__ == "__main__":
    main()
