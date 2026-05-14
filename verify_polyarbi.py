#!/usr/bin/env python3
"""
Polyarbi Bot — Pre-Flight Verification Script
Checks all dependencies, .env config, and basic connectivity
"""

import sys
import os

print("=" * 70)
print("POLYARBI BOT — PRE-FLIGHT VERIFICATION")
print("=" * 70)

# 1. Check Python version
print("\n[1/5] Checking Python version...")
if sys.version_info < (3, 8):
    print("  ❌ Python 3.8+ required")
    sys.exit(1)
print(f"  ✅ Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")

# 2. Check dependencies
print("\n[2/5] Checking dependencies...")
deps = ["aiohttp", "websockets", "dateutil", "dotenv", "web3", "eth_account"]
missing = []
for dep in deps:
    try:
        __import__(dep)
        print(f"  ✅ {dep}")
    except ImportError:
        print(f"  ❌ {dep} (missing)")
        missing.append(dep)

if missing:
    print(f"\n  Install: pip install {' '.join(missing)}")
    sys.exit(1)

# 3. Check .env file
print("\n[3/5] Checking .env file...")
if not os.path.exists(".env"):
    print("  ❌ .env file not found")
    sys.exit(1)

from dotenv import load_dotenv
load_dotenv()

required_vars = [
    "PRIVATE_KEY",
    "WALLET_ADDRESS",
    "POLY_API_KEY",
    "POLY_API_SECRET",
    "POLY_PASSPHRASE",
    "INITIAL_BANKROLL",
]

missing_vars = []
for var in required_vars:
    val = os.getenv(var)
    if not val:
        print(f"  ❌ {var} (not set)")
        missing_vars.append(var)
    else:
        masked = val[:10] + "..." if len(str(val)) > 10 else val
        status = "✅" if var != "INITIAL_BANKROLL" else "✅"
        print(f"  {status} {var} = {masked}")

if missing_vars:
    print(f"\n  Add to .env: {', '.join(missing_vars)}")
    sys.exit(1)

# 4. Check directories
print("\n[4/5] Creating required directories...")
for d in ["logs", "state"]:
    os.makedirs(d, exist_ok=True)
    print(f"  ✅ {d}/")

# 5. Test async + networking
print("\n[5/5] Testing async connectivity...")
import asyncio
import aiohttp

async def test_connectivity():
    # Test Binance
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://stream.binance.com:9443", timeout=aiohttp.ClientTimeout(total=3)) as resp:
                print("  ✅ Binance reachable")
    except:
        print("  ⚠️  Binance unreachable (connection test failed)")
    
    # Test Polymarket CLOB
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://clob.polymarket.com/health", timeout=aiohttp.ClientTimeout(total=3)) as resp:
                print("  ✅ Polymarket CLOB reachable")
    except:
        print("  ⚠️  Polymarket CLOB unreachable (connection test failed)")
    
    # Test Polymarket Gamma API
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://gamma-api.polymarket.com/markets?limit=1", timeout=aiohttp.ClientTimeout(total=3)) as resp:
                if resp.status == 200:
                    print("  ✅ Polymarket Gamma API reachable")
    except:
        print("  ⚠️  Polymarket Gamma API unreachable (connection test failed)")

try:
    asyncio.run(test_connectivity())
except Exception as e:
    print(f"  ⚠️  Network test failed: {e}")

# Final status
print("\n" + "=" * 70)
print("✅ PRE-FLIGHT CHECK COMPLETE")
print("=" * 70)
print("""
You can now run polyarbi:

  DRY-RUN MODE (safe, no real $):
    python polyarbi.py

  LIVE MODE (real trading — requires DRY_RUN=0 in .env):
    DRY_RUN=0 python polyarbi.py

  View logs:
    tail -f logs/polyarbi.log

Good luck! 🚀
""")
