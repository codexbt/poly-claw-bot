#!/usr/bin/env python3
"""
Execute $200 BUY_YES Trade on GUT vs MUM Cricket Market
Based on poly5min_llm_bot.py trading logic
======================================================

Market: https://polymarket.com/sports/cricipl/cricipl-guj-mum-2026-04-20
"""

import os
import sys
import json
import time
import re
import logging
import requests
from datetime import datetime, timezone
from dotenv import load_dotenv

# Load environment
load_dotenv()

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import MarketOrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY, SELL

# ============================================================================
# CONFIG - FROM .env
# ============================================================================
CLOB_API = "https://clob.polymarket.com"
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
CHAIN_ID = int(os.getenv("CHAIN_ID", "137"))
RELAYER_API_KEY = os.getenv("RELAYER_API_KEY")
RELAYER_API_KEY_ADDRESS = os.getenv("RELAYER_API_KEY_ADDRESS")

TRADE_SIZE_USD = 200.0
ACTION = "BUY_YES"  # Gujarat Titans (YES)
MARKET_URL = "https://polymarket.com/sports/cricipl/cricipl-guj-mum-2026-04-20"

# Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("gut_trade")

# ============================================================================
# MARKET FETCHER (from poly5min_llm_bot approach)
# ============================================================================
def get_market_from_html(html: str) -> dict:
    """
    Parse market data from Polymarket HTML page
    Extract: conditionId, clobTokenIds, prices
    """
    log.info("📄 Parsing market data from HTML...")
    
    # Extract condition ID
    cond_match = re.search(r'"conditionId":"([^"]+)"', html)
    if not cond_match:
        raise ValueError("Could not find conditionId in HTML")
    
    # Extract token IDs
    token_match = re.search(r'"clobTokenIds":\s*\[([^\]]+)\]', html)
    if not token_match:
        raise ValueError("Could not find clobTokenIds in HTML")
    
    try:
        token_ids = json.loads("[" + token_match.group(1) + "]")
    except json.JSONDecodeError as e:
        raise ValueError(f"Could not parse token IDs: {e}")
    
    if len(token_ids) < 2:
        raise ValueError(f"Expected 2 tokens, got {len(token_ids)}")
    
    condition_id = cond_match.group(1)
    yes_token = token_ids[0]
    no_token = token_ids[1]
    
    log.info(f"✓ Condition ID: {condition_id[:40]}...")
    log.info(f"✓ YES Token: {str(yes_token)[:40]}...")
    log.info(f"✓ NO Token: {str(no_token)[:40]}...")
    
    return {
        "condition_id": condition_id,
        "yes_token": yes_token,
        "no_token": no_token,
    }

def get_token_midpoint(token_id: str) -> float:
    """Get midpoint price for a token from CLOB API"""
    try:
        r = requests.get(
            f"https://clob.polymarket.com/midpoint?token_id={token_id}",
            timeout=10
        )
        r.raise_for_status()
        return float(r.json().get("mid", 0.5))
    except Exception as e:
        log.warning(f"Failed to get midpoint for {str(token_id)[:20]}...: {e}")
        return 0.5

def fetch_market_html(url: str) -> str:
    """Fetch market HTML from Polymarket"""
    log.info(f"🔍 Fetching market page: {url}")
    
    import requests
    for attempt in range(1, 4):
        try:
            r = requests.get(
                url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
                timeout=12
            )
            r.raise_for_status()
            log.info(f"✓ Market page fetched (attempt {attempt})")
            return r.text
        except Exception as e:
            log.warning(f"Attempt {attempt} failed: {e}")
            if attempt < 3:
                time.sleep(1)
    
    raise ConnectionError(f"Could not fetch market page after 3 attempts")

# ============================================================================
# TRADE EXECUTOR (simplified from poly5min_llm_bot)
# ============================================================================
def execute_trade(client, market: dict, action: str, size_usd: float):
    """
    Execute market order on Polymarket
    
    Args:
        client: ClobClient authenticated
        market: Dict with condition_id, yes_token, no_token
        action: "BUY_YES" or "BUY_NO"
        size_usd: USD amount to trade
    """
    log.info(f"\n{'='*80}")
    log.info(f"🚀 EXECUTING {action} FOR ${size_usd:.2f}")
    log.info(f"{'='*80}\n")
    
    # Select token
    token_id = market["yes_token"] if action == "BUY_YES" else market["no_token"]
    log.info(f"📍 Using token: {str(token_id)[:40]}...")
    
    # Get current price
    log.info("💹 Getting market price...")
    price = get_token_midpoint(token_id)
    log.info(f"✓ Price: ${price:.4f}")
    
    if price <= 0:
        raise ValueError(f"Invalid price: ${price}")
    
    # Calculate shares
    shares = round(size_usd / price, 6)
    log.info(f"✓ Shares: {shares:.6f} @ ${price:.4f} = ${size_usd:.2f}\n")
    
    # Create market order
    log.info("📮 Creating market order...")
    order = MarketOrderArgs(
        token_id=token_id,
        amount=size_usd,  # USD amount to spend
        side=BUY,
        order_type=OrderType.FOK
    )
    
    # Sign order
    log.info("🔏 Signing order...")
    signed_order = client.create_market_order(order)
    
    # Submit order (with relayer if available, else direct)
    log.info("⏳ Submitting to Polymarket...")
    try:
        # Try relayer first
        response = None
        if RELAYER_API_KEY and RELAYER_API_KEY_ADDRESS:
            try:
                payload = {
                    "orderSignature": signed_order.dict(),
                }
                r = requests.post(
                    "https://relayer.polymarket.com/submit-order",
                    headers={
                        "Content-Type": "application/json",
                        "RELAYER_API_KEY": RELAYER_API_KEY,
                        "RELAYER_API_KEY_ADDRESS": RELAYER_API_KEY_ADDRESS,
                    },
                    json=payload,
                    timeout=15,
                )
                r.raise_for_status()
                response = r.json()
                log.info("✓ Relayer submission successful")
            except Exception as e:
                log.warning(f"Relayer submission failed: {e}, falling back to CLOB API")
        
        # Fallback to CLOB API
        if response is None:
            response = client.post_order(signed_order, OrderType.FOK)
        
        if response and ("orderId" in response or "id" in response):
            order_id = response.get("orderId") or response.get("id")
            status = response.get("status", "SUBMITTED")
            
            log.info(f"\n✅ ORDER EXECUTED!")
            log.info(f"   Order ID: {order_id}")
            log.info(f"   Status: {status}")
            log.info(f"   Amount: ${size_usd:.2f}")
            log.info(f"   Price: ${price:.4f}")
            log.info(f"   Shares: {shares:.6f}\n")
            
            # Save record
            record = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "market": "GUT vs MUM Cricket",
                "action": action,
                "amount_usd": size_usd,
                "price": price,
                "shares": shares,
                "order_id": str(order_id),
                "status": status,
                "condition_id": market["condition_id"],
            }
            
            with open("gut_trade_executed.json", "w") as f:
                json.dump(record, f, indent=2)
            
            log.info("📝 Trade record saved to: gut_trade_executed.json")
            return True
        else:
            error = response.get("error", "Unknown error") if response else "No response"
            log.error(f"\n❌ ORDER FAILED: {error}")
            return False
            
    except Exception as e:
        log.error(f"\n❌ EXECUTION ERROR: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

# ============================================================================
# MAIN
# ============================================================================
def main():
    log.info("\n" + "="*80)
    log.info("  🏏 GUT vs MUM CRICKET TRADE EXECUTION")
    log.info("="*80)
    log.info(f"  Amount: ${TRADE_SIZE_USD}")
    log.info(f"  Action: {ACTION}")
    log.info(f"  Market: {MARKET_URL}")
    log.info(f"  Wallet: {RELAYER_API_KEY_ADDRESS}")
    log.info("="*80 + "\n")
    
    try:
        # 1. Initialize CLOB client
        log.info("🔑 Initializing Polymarket client...")
        client = ClobClient(CLOB_API, key=PRIVATE_KEY, chain_id=CHAIN_ID)
        creds = client.create_or_derive_api_creds()
        client.set_api_creds(creds)
        log.info("✓ Client connected\n")
        
        # 2. Fetch market HTML
        html = fetch_market_html(MARKET_URL)
        
        # 3. Parse market data
        market = get_market_from_html(html)
        log.info("")
        
        # 4. Execute trade
        success = execute_trade(client, market, ACTION, TRADE_SIZE_USD)
        
        if success:
            log.info("="*80)
            log.info("✅ SUCCESS - Trade placed on GUT (YES)")
            log.info("="*80)
            return 0
        else:
            log.error("\n" + "="*80)
            log.error("❌ FAILED - Trade could not be executed")
            log.error("="*80)
            return 1
            
    except Exception as e:
        log.error(f"\n❌ FATAL ERROR: {str(e)}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    sys.exit(main())
