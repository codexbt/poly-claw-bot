#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
debug_api_response.py
Polymarket API response को debug करने के लिए
"""

import requests
import json
from datetime import datetime

# Configuration
POLYMARKET_USER_WALLET = "0x82ff01408b945af138d3c4619dcf876387d52b09"
DATA_API = "https://data-api.polymarket.com"

def fetch_and_print_response(endpoint, params=""):
    """API response को fetch और print करो"""
    url = f"{DATA_API}{endpoint}"
    if params:
        url += params
    
    print(f"\n{'='*80}")
    print(f"Fetching: {url}")
    print(f"Time: {datetime.now()}")
    print(f"{'='*80}\n")
    
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        print(json.dumps(data, indent=2, default=str))
        
        # Print analysis
        if isinstance(data, list):
            print(f"\n>>> Total items: {len(data)}")
            if data:
                print(f">>> Sample item (first one):")
                print(json.dumps(data[0], indent=2, default=str))
                print(f"\n>>> Available keys in first item:")
                print(list(data[0].keys()) if isinstance(data[0], dict) else "Not a dict")
    except Exception as e:
        print(f"ERROR: {str(e)}")

# Test different endpoints
print("\n")
print("█" * 80)
print("POLYMARKET API RESPONSE DEBUG")
print("█" * 80)
print(f"User Wallet: {POLYMARKET_USER_WALLET}\n")

# 1. Running Positions
print("\n1. RUNNING POSITIONS")
fetch_and_print_response(f"/positions?user={POLYMARKET_USER_WALLET}&limit=10")

# 2. Closed Positions  
print("\n2. CLOSED POSITIONS")
fetch_and_print_response(f"/closed-positions?user={POLYMARKET_USER_WALLET}&limit=10")

# 3. Trade History
print("\n3. TRADE HISTORY")
fetch_and_print_response(f"/trades?user={POLYMARKET_USER_WALLET}&limit=10")

# 4. Public Profile
print("\n4. PUBLIC PROFILE")
fetch_and_print_response(f"https://gamma-api.polymarket.com/public-profile?address={POLYMARKET_USER_WALLET}")

print("\n" + "="*80)
print("DEBUG COMPLETE")
print("="*80)
