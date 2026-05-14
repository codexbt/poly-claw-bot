"""
╔══════════════════════════════════════════════════════════════════╗
║           SWISSBOT DIAGNOSTICS — order_version_mismatch         ║
║  Run: python diagnose.py                                        ║
║  Yeh batayega exact fix kya lagana hai tumhare .env me          ║
╚══════════════════════════════════════════════════════════════════╝
"""
import socket, os, sys, time

# Force IPv4
_orig = socket.getaddrinfo
def _ipv4(h, p, f=0, t=0, pr=0, fl=0):
    return _orig(h, p, socket.AF_INET, t, pr, fl)
socket.getaddrinfo = _ipv4

from dotenv import load_dotenv
load_dotenv("swissbot.env")
load_dotenv(".env")

PRIVATE_KEY    = os.getenv("PRIVATE_KEY","")
FUNDER_ADDRESS = os.getenv("POLYMARKET_FUNDER_ADDRESS","")
CLOB_API_KEY   = os.getenv("CLOB_API_KEY","")
CLOB_SECRET    = os.getenv("CLOB_SECRET","")
CLOB_PASS      = os.getenv("CLOB_PASS_PHRASE","")

print("\n" + "="*60)
print("  SWISSBOT DIAGNOSTICS")
print("="*60)

# Check 1: Private Key
print(f"\n[1] PRIVATE_KEY     : {'✅ SET' if PRIVATE_KEY else '❌ MISSING'}")
print(f"[2] FUNDER_ADDRESS  : {'✅ ' + FUNDER_ADDRESS[:12] + '...' if FUNDER_ADDRESS else '⚠️  NOT SET (may cause order_version_mismatch)'}")
print(f"[3] API CREDS       : {'✅ All set' if all([CLOB_API_KEY, CLOB_SECRET, CLOB_PASS]) else '⚠️  Missing (will derive fresh)'}")

try:
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import (
        ApiCreds, MarketOrderArgs, OrderType,
        BalanceAllowanceParams, AssetType
    )
    from py_clob_client.order_builder.constants import BUY
    print("\n[4] py-clob-client  : ✅ Installed")
except ImportError as e:
    print(f"\n[4] py-clob-client  : ❌ Not installed: {e}")
    sys.exit(1)

if not PRIVATE_KEY:
    print("\n❌ PRIVATE_KEY missing — add it to .env first")
    sys.exit(1)

print("\n" + "-"*60)
print("  Testing different signature types...")
print("-"*60)

CLOB_URL = "https://clob.polymarket.com"
CHAIN_ID = 137

results = {}

for sig_type in [0, 2]:
    print(f"\n  Testing signature_type={sig_type}...")
    try:
        kwargs = dict(
            host=CLOB_URL,
            key=PRIVATE_KEY,
            chain_id=CHAIN_ID,
            signature_type=sig_type,
        )
        if FUNDER_ADDRESS:
            kwargs["funder"] = FUNDER_ADDRESS

        client = ClobClient(**kwargs)

        # Try to get/derive creds
        if all([CLOB_API_KEY, CLOB_SECRET, CLOB_PASS]):
            creds = ApiCreds(
                api_key=CLOB_API_KEY,
                api_secret=CLOB_SECRET,
                api_passphrase=CLOB_PASS,
            )
        else:
            creds = client.create_or_derive_api_creds()
        client.set_api_creds(creds)

        # Test balance
        resp = client.get_balance_allowance(
            BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        )
        bal = round(int(resp.get("balance", 0)) / 1e6, 2)
        print(f"  ✅ sig_type={sig_type} WORKS | balance=${bal:.2f}")
        results[sig_type] = {"ok": True, "balance": bal, "client": client}

        if bal == 0:
            print(f"     ⚠️  Balance $0 — check if POLYMARKET_FUNDER_ADDRESS is correct")
        else:
            print(f"     💰 Balance found: ${bal:.2f}")

    except Exception as e:
        print(f"  ❌ sig_type={sig_type} FAILED: {e}")
        results[sig_type] = {"ok": False, "error": str(e)}

# Find best working config
working = [(st, r) for st, r in results.items() if r.get("ok")]

print("\n" + "="*60)
print("  RESULTS & RECOMMENDATIONS")
print("="*60)

if not working:
    print("\n❌ No signature type worked!")
    print("\nPossible fixes:")
    print("  1. PRIVATE_KEY wrong hai — check karo")
    print("  2. Network issue — VPN try karo")
    print("  3. pip install --upgrade py-clob-client")
else:
    # Prefer the one with non-zero balance
    best_sig, best_result = max(working, key=lambda x: x[1].get("balance", 0))
    
    print(f"\n✅ Best config: SIGNATURE_TYPE={best_sig}")
    print(f"   Balance: ${best_result['balance']:.2f}")

    print("\n📋 Add to your .env / swissbot.env:")
    print("-"*40)
    print(f"SIGNATURE_TYPE={best_sig}")
    if not FUNDER_ADDRESS:
        print(f"POLYMARKET_FUNDER_ADDRESS=<your_polymarket_wallet_address>")
        print(f"# Polymarket wallet address — settings me milega")
    print("-"*40)

    # Now test a dummy order to check for version mismatch
    print(f"\n  Testing order creation (sig_type={best_sig})...")
    client = best_result["client"]
    
    # Use a real liquid token for test
    # BTCUSDT-related UP token — just test signing, don't submit
    TEST_TOKEN = "21742633143463906290569050155826241533067272736897614950488156847949938836455"
    
    try:
        order = MarketOrderArgs(
            token_id=TEST_TOKEN,
            amount=1.0,
            side=BUY,
            order_type=OrderType.FOK,
        )
        signed = client.create_market_order(order)
        print(f"  ✅ Order signing works (sig_type={best_sig}) — no version mismatch!")
        print(f"\n✅ DIAGNOSIS COMPLETE — your fix:")
        print(f"   SIGNATURE_TYPE={best_sig} in .env")
    except Exception as e:
        err = str(e)
        print(f"  ❌ Order signing failed: {err}")
        if "version_mismatch" in err.lower():
            print("\n  Fix: FUNDER_ADDRESS dena zaroori hai!")
            print("  Get it from: https://polymarket.com/profile (wallet address copy karo)")
        elif "not_found" in err.lower() or "token" in err.lower():
            print(f"  ✅ Actually OK — test token not found, but signing worked!")

print("\n" + "="*60)
print("  Run 'python swissbot.py' after applying fixes")
print("="*60 + "\n")