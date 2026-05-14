"""
Test Relayer API Key for Gasless Transactions
Verifies the relayer key works without needing MATIC for gas
"""

import os
import requests
from dotenv import load_dotenv

load_dotenv()

# Your Relayer API credentials
RELAYER_API_KEY = os.getenv("RELAYER_API_KEY", "")
RELAYER_API_KEY_ADDRESS = os.getenv("RELAYER_API_KEY_ADDRESS", "")
RELAYER_HOST = "https://relayer.polymarket.com"

print("="*80)
print("POLYMARKET RELAYER API TEST - GASLESS TRANSACTIONS")
print("="*80)

# Check if credentials are loaded
print("\n[1] Checking Credentials:")
print(f"    RELAYER_API_KEY: {'✓ SET' if RELAYER_API_KEY else '✗ MISSING'}")
if RELAYER_API_KEY:
    print(f"                 {RELAYER_API_KEY[:16]}...{RELAYER_API_KEY[-8:]}")
print(f"    RELAYER_ADDRESS: {'✓ SET' if RELAYER_API_KEY_ADDRESS else '✗ MISSING'}")  
if RELAYER_API_KEY_ADDRESS:
    print(f"                 {RELAYER_API_KEY_ADDRESS}")

if not RELAYER_API_KEY or not RELAYER_API_KEY_ADDRESS:
    print("\n[ERROR] Relayer credentials missing in .env!")
    exit(1)

# Test 1: Check relayer connectivity
print("\n[2] Testing Relayer Connectivity:")
response = None
test_status = False
try:
    headers = {
        "X-API-KEY": RELAYER_API_KEY,
        "Content-Type": "application/json"
    }
    
    # Try to get account info from relayer
    response = requests.get(
        f"{RELAYER_HOST}/account",
        headers=headers,
        timeout=10
    )
    
    print(f"    Status Code: {response.status_code}")
    if response.status_code == 200:
        print(f"    ✓ Relayer API is REACHABLE")
        test_status = True
        try:
            data = response.json()
            print(f"    Account Info: {data}")
        except:
            print(f"    Response received but JSON parse error")
    elif response.status_code == 401 or response.status_code == 403:
        print(f"    ✗ Authentication FAILED - Check API key")
        print(f"    Response: {response.text[:200]}")
    else:
        print(f"    ⚠ Unexpected response: {response.text[:200]}")
        
except requests.exceptions.Timeout:
    print(f"    ✗ Request TIMEOUT - Relayer may be down")
    test_status = False
except Exception as e:
    print(f"    ✗ Error: {e}")
    test_status = False

# Test 2: Check if key is valid
print("\n[3] Validating API Key Format:")
if len(RELAYER_API_KEY) == 36 and RELAYER_API_KEY.count("-") == 4:
    print(f"    ✓ API Key format is VALID (UUID format)")
else:
    print(f"    ✗ API Key format looks INVALID")
    print(f"    Expected: UUID format (xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx)")
    print(f"    Length: {len(RELAYER_API_KEY)} chars")

# Test 3: Check address format
print("\n[4] Validating Relayer Address:")
if RELAYER_API_KEY_ADDRESS.startswith("0x") and len(RELAYER_API_KEY_ADDRESS) == 42:
    print(f"    ✓ Address format is VALID (Ethereum address)")
else:
    print(f"    ✗ Address format looks INVALID")
    print(f"    Expected: 0x + 40 hex chars")
    print(f"    Got: {RELAYER_API_KEY_ADDRESS}")

print("\n" + "="*80)
print("TEST SUMMARY:")
print("="*80)

if test_status and response and response.status_code == 200:
    print("✓ Relayer API Key is VALID and WORKING!")
    print("✓ Gasless transactions are READY to use")
    print("\nNext steps:")
    print("  1. Update poly5min_all.py to use relayer for orders")
    print("  2. No MATIC gas needed for transactions!")
else:
    print("✗ Relayer API Key test FAILED")
    print("  Check your API key and address in .env")
    if response:
        print(f"  Response code: {response.status_code}")

print("="*80 + "\n")
