#!/usr/bin/env python3
"""
Find Active Polymarket 5-Min Markets
====================================
Searches for currently available 5-min prediction markets
"""

import requests
import json
from datetime import datetime, timezone, timedelta
import pytz
import re

print("\n" + "="*80)
print("POLYMARKET 5-MIN ACTIVE MARKETS FINDER")
print("="*80 + "\n")

# Get current time
utc_now = datetime.now(timezone.utc)
et_tz = pytz.timezone("America/New_York")
et_now = utc_now.astimezone(et_tz)

print(f"Current Time (UTC): {utc_now.strftime('%Y-%m-%d %H:%M:%S')}")
print(f"Current Time (ET):  {et_now.strftime('%Y-%m-%d %H:%M:%S')}\n")

# API endpoints to try
endpoints = [
    "https://gamma-api.polymarket.com/markets?limit=100",  # New API
    "https://polymarket.com/api/markets?limit=100",  # Old API
    "https://api.polymarket.com/markets?limit=100",  # Alternative
]

print("Searching for 5-min markets via API...\n")

found_markets = []

for endpoint in endpoints:
    try:
        print(f"Trying: {endpoint[:50]}...")
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(endpoint, headers=headers, timeout=10)
        
        if r.status_code == 200:
            data = r.json()
            print(f"✓ API accessible! Found {len(data)} markets\n")
            
            # Show first 10 markets to see format
            print("Sample markets returned:")
            for i, market in enumerate(data[:10]):
                if isinstance(market, dict):
                    title = market.get("title", market.get("question", "N/A"))
                    state = market.get("status", market.get("state", "N/A"))
                    print(f"  {i+1}. {title[:70]}")
                    print(f"     State: {state}")
            
            print(f"\nSearching for 5-min markets in all {len(data)}...\n")
            
            # Search for 5-min markets
            for market in data:
                if isinstance(market, dict):
                    title = market.get("title", market.get("question", ""))
                    if "5m" in title.lower() or "5-min" in title.lower() or "5 min" in title.lower():
                        market_info = {
                            "title": title,
                            "id": market.get("conditionId", market.get("id")),
                            "tokens": market.get("tokens", market.get("clobTokenIds", [])),
                            "status": market.get("status", market.get("state", "")),
                        }
                        found_markets.append(market_info)
                        print(f"  ✓ Found: {title[:60]}")
            
            if found_markets:
                break
            else:
                print("✗ No 5-min markets found in API response")
                print("\nNote: Polymarket may not have 5-min markets running at this time (2:38 AM ET)")
                break
                
    except requests.exceptions.Timeout:
        print(f"  Timeout\n")
    except Exception as e:
        print(f"  Error: {str(e)[:50]}\n")
        continue

# If API didn't work, try searching the web
if not found_markets:
    print("\nAPI search failed. Searching Polymarket website...\n")
    
    try:
        # Try searching for current 5-min markets on website
        url = "https://polymarket.com"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        
        if r.status_code == 200:
            # Look for market links in the page
            matches = re.findall(r'href="/markets/([^"]+)-(\d{10})"', r.text)
            
            print(f"Found {len(matches)} potential markets on website\n")
            
            for slug, timestamp in matches[:10]:
                if "updown" in slug.lower() or "5m" in slug.lower():
                    print(f"  Market: {slug}")
                    print(f"  Timestamp: {timestamp}\n")
                    found_markets.append({"slug": slug, "ts": timestamp})
                    
    except Exception as e:
        print(f"Web search failed: {e}\n")

# Summary
print("="*80)
if found_markets:
    print(f"✓ Found {len(found_markets)} active 5-min markets!\n")
    for m in found_markets[:5]:
        if "title" in m:
            print(f"  - {m['title'][:70]}")
            if m.get("id"):
                print(f"    ID: {m['id'][:30]}...")
        else:
            print(f"  - {m.get('slug', 'Unknown')}")
else:
    print("✗ No 5-min markets found via API or web")
    print("\nPossible reasons:")
    print("  1. No 5-min markets currently open")
    print("  2. Polymarket API structure changed")
    print("  3. Markets are open but with different naming")
    print("\nAction: Check https://polymarket.com/browse to see current markets")

print("\n" + "="*80 + "\n")
