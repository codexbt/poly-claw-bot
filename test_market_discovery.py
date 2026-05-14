#!/usr/bin/env python
"""
Quick test of market discovery using requests (like poly5min_all.py)
"""
import re
import json
import pytz
from datetime import datetime, timezone

print("[TEST] Testing market discovery (requests-based)...\n")

try:
    import requests
    print("[OK] requests module available")
except ImportError:
    print("[ERROR] requests not found!")
    exit(1)

# Calculate ET timestamp (exactly like our updated code)
et_tz = pytz.timezone("America/New_York")
et_now = datetime.now(timezone.utc).astimezone(et_tz)
window_min = (et_now.minute // 5) * 5
window_start_et = et_now.replace(minute=window_min, second=0, microsecond=0)
market_ts = int(window_start_et.timestamp())

slug = "btc-updown-5m"
url = f"https://polymarket.com/event/{slug}-{market_ts}"

print(f"[TEST] URL: {url}\n")
print(f"[TEST] Fetching HTML...")

try:
    headers = {"User-Agent": "Mozilla/5.0"}
    r = requests.get(url, headers=headers, timeout=10)
    r.raise_for_status()
    html = r.text
    print(f"[OK] Got HTML response ({len(html)} bytes)\n")
    
    # Parse HTML
    print("[TEST] Parsing HTML for market data...")
    cond_match = re.search(r'"conditionId":"([^"]+)"', html)
    tok_match = re.search(r'"clobTokenIds":\s*\[([^\]]+)\]', html)
    
    if cond_match and tok_match:
        condition_id = cond_match.group(1)
        token_ids_str = tok_match.group(1)
        token_ids = json.loads("[" + token_ids_str + "]")
        
        print(f"[OK] Condition ID: {condition_id[:16]}...")
        print(f"[OK] Token IDs found: {len(token_ids)}")
        
        if len(token_ids) >= 2:
            print(f"[OK] YES Token: {token_ids[0][:16]}...")
            print(f"[OK] NO Token:  {token_ids[1][:16]}...")
            print("\n[SUCCESS] Market discovery working!")
        else:
            print("[ERROR] Not enough tokens")
    else:
        print("[ERROR] Could not parse HTML")
        print(f"  - conditionId found: {bool(cond_match)}")
        print(f"  - clobTokenIds found: {bool(tok_match)}")
        
except requests.exceptions.RequestException as e:
    print(f"[ERROR] Network error: {e}")
    print("\nThis might be a DNS or firewall issue on your network.")
    print("Try: ping polymarket.com")
except Exception as e:
    print(f"[ERROR] {type(e).__name__}: {e}")
