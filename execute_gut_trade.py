#!/usr/bin/env python3
"""
Execute $200 BUY_YES Trade on GUT vs MUM Cricket Market
========================================================
Polymarket IPL: Gujarat Titans (YES) - Live Execution
"""

import os
import sys
import json
import time
from datetime import datetime, timezone
from dotenv import load_dotenv

# Load environment
load_dotenv()

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import (
    MarketOrderArgs, BalanceAllowanceParams, AssetType, OrderType
)
from py_clob_client.order_builder.constants import BUY, SELL

# Config
CLOB_API = "https://clob.polymarket.com"
TRADE_SIZE_USDC = 200.0
TRADE_DIRECTION = BUY  # YES / Gujarat
CHAIN_ID = int(os.getenv("CHAIN_ID", "137"))
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
RELAYER_API_KEY = os.getenv("RELAYER_API_KEY")
RELAYER_API_KEY_ADDRESS = os.getenv("RELAYER_API_KEY_ADDRESS")

print("\n" + "="*80)
print("  🏏 EXECUTING $200 BUY_YES TRADE ON GUT (GUJARAT TITANS)")
print("="*80)
print(f"  Date: {datetime.now(timezone.utc).isoformat()}")
print(f"  Amount: ${TRADE_SIZE_USDC}")
print(f"  Direction: BUY (YES / Gujarat Titans)")
print(f"  Wallet: {RELAYER_API_KEY_ADDRESS}")
print("="*80 + "\n")

try:
    # 1. Initialize authenticated client
    print("� Initializing Polymarket client...")
    auth_client = ClobClient(
        CLOB_API,
        key=PRIVATE_KEY,
        chain_id=CHAIN_ID,
        signature_type=0
    )
    
    # 2. Create API credentials
    print("📝 Deriving API credentials...")
    creds = auth_client.create_or_derive_api_creds()
    auth_client.set_api_creds(creds)
    print(f"✓ API credentials set\n")
    
    # 3. Check balance
    print("� Checking USDC balance...")
    balance_response = auth_client.get_balance_allowance(
        BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
    )
    usdc_balance = int(balance_response.get('balance', 0)) / 1e6
    print(f"✓ Balance: ${usdc_balance:.2f}\n")
    
    if usdc_balance < TRADE_SIZE_USDC:
        print(f"❌ INSUFFICIENT BALANCE: ${usdc_balance:.2f} < ${TRADE_SIZE_USDC:.2f}")
        sys.exit(1)
    
    # 4. Search for GUT vs MUM market
    print("🔍 Searching for GUT vs MUM cricket market...")
    markets = auth_client.get_markets(search_term="GUT MUM")
    
    if not markets:
        print("❌ No market found. Trying alternative search...")
        markets = auth_client.get_markets(search_term="Gujarat")
    
    if not markets:
        print("❌ No Gujarat market found. Listing active markets...")
        markets = auth_client.get_markets(limit=50)
        print(f"Found {len(markets)} markets. Searching for cricket/IPL...")
        
        # Filter for cricket/IPL markets
        cricket_markets = [m for m in markets if any(
            keyword in m.get('question', '').upper() 
            for keyword in ['GUJARA', 'GUT', 'MUMBAI', 'MUM', 'IPL', 'CRICKET']
        )]
        
        if not cricket_markets:
            print("\n❌ Could not find GUT vs MUM market")
            print("\nAvailable markets (showing first 5):")
            for m in markets[:5]:
                print(f"  - {m.get('question', 'N/A')[:60]}")
            sys.exit(1)
        
        markets = cricket_markets
    
    # Use first matching market
    market = markets[0]
    market_id = market.get('id')
    market_question = market.get('question', 'Unknown')
    
    print(f"✓ Market found: {market_question[:70]}")
    print(f"  Market ID: {market_id}\n")
    
    # 5. Get market details and YES token
    print("📊 Getting market order book...")
    market_data = auth_client.get_market(market_id)
    
    # YES token is typically at index 0
    yes_token_id = market_data.get('clobTokenIds', [None])[0]
    
    if not yes_token_id:
        print("❌ Could not find YES token in market")
        sys.exit(1)
    
    print(f"✓ YES Token ID: {yes_token_id[:40]}...\n")
    
    # 6. Get current prices
    print("� Checking market prices...")
    order_book = auth_client.get_order_book(market_id)
    
    if order_book and 'bids' in order_book and order_book['bids']:
        best_ask_price = float(order_book['asks'][0]['price']) if order_book.get('asks') else 0.50
        print(f"✓ Best ask price for YES: ${best_ask_price:.4f}\n")
    else:
        best_ask_price = 0.50
        print(f"⚠️  Using fallback price: ${best_ask_price:.4f}\n")
    
    # 7. Execute market order
    print("🚀 EXECUTING MARKET ORDER...")
    print(f"   Buying ${TRADE_SIZE_USDC:.2f} of YES @ market price")
    
    # Calculate size (shares)
    size = TRADE_SIZE_USDC / best_ask_price
    
    order_args = MarketOrderArgs(
        token_id=yes_token_id,
        price=best_ask_price,
        size=size,
        direction=BUY,
        relayer_api_key=RELAYER_API_KEY,
        relayer_api_key_address=RELAYER_API_KEY_ADDRESS,
    )
    
    print(f"   Size: {size:.6f} shares")
    print(f"   Price: ${best_ask_price:.4f}/share\n")
    
    # Submit order
    order_response = auth_client.create_market_order(order_args)
    
    if order_response and 'orderId' in order_response:
        order_id = order_response.get('orderId')
        print(f"✅ TRADE EXECUTED!")
        print(f"   Order ID: {order_id}")
        print(f"   Status: {order_response.get('status', 'Unknown')}\n")
        
        # Save trade record
        trade_record = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'market': market_question,
            'market_id': market_id,
            'direction': 'BUY_YES',
            'amount_usd': TRADE_SIZE_USDC,
            'shares': size,
            'price': best_ask_price,
            'order_id': order_id,
            'status': order_response.get('status', 'Unknown')
        }
        
        with open('gut_trade_record.json', 'w') as f:
            json.dump(trade_record, f, indent=2)
        
        print("📝 Trade record saved to: gut_trade_record.json\n")
        print("="*80)
        print(f"✅ SUCCESS: $200 BUY_YES order placed on {market_question[:50]}")
        print("="*80 + "\n")
        
    else:
        error_msg = order_response.get('error', 'Unknown error') if order_response else 'No response'
        print(f"❌ ORDER FAILED: {error_msg}\n")
        sys.exit(1)

except Exception as e:
    print(f"\n❌ ERROR: {str(e)}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
