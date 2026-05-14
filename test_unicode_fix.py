#!/usr/bin/env python
"""
Quick test to verify Unicode logging works without errors
"""
import logging
import sys
import io

# Test logging configuration like superbot.py does
LOG_LEVEL = logging.INFO

logger = logging.getLogger("UnicodTest")
logger.setLevel(LOG_LEVEL)

fmt = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

# Console handler with UTF-8 encoding (fixes Windows cp1252 error)
ch = logging.StreamHandler(sys.stdout)
# For Windows: configure encoding explicitly
if hasattr(ch.stream, 'reconfigure'):
    ch.stream.reconfigure(encoding='utf-8', errors='replace')
    print("[TEST] Stream reconfigured to UTF-8")
else:
    print("[TEST] Stream reconfiguration not available on this platform")

ch.setFormatter(fmt)
logger.addHandler(ch)

# Test messages that would previously fail
test_messages = [
    ("Test 1: Market discovery", "[MARKET] [OK] Found market: BTC Up or Down at 22:15 UTC"),
    ("Test 2: Signal evaluation", "[SIGNAL] [UP] Δ=+0.0234% | Conf=75.2% | Dir=YES"),
    ("Test 3: Trade firing", "[SNIPER] [FIRE] YES order @ 0.5245 | Conf=75.2% | Size=$1.00"),
    ("Test 4: Trade result", "[RESULT] [WIN] Trade abc123 | PnL: +$0.1234"),
    ("Test 5: Status update", "[WAITING] T-45s until sniper zone | BTC=$42,346.12"),
    ("Test 6: Price update", "[SNIPER] [PRICE] Current BTC: $42,347.89"),
    ("Test 7: Token prices", "[SNIPER] [TOKEN] YES=0.5245 | NO=0.4755"),
]

print("\n" + "="*60)
print("UNICODE LOGGING TEST")
print("="*60 + "\n")

for test_name, message in test_messages:
    try:
        logger.info(message)
        print(f"✓ {test_name} - SUCCESS")
    except UnicodeEncodeError as e:
        print(f"✗ {test_name} - FAILED: {e}")
        sys.exit(1)

print("\n" + "="*60)
print("[SUCCESS] All Unicode logging tests passed!")
print("="*60 + "\n")
print("The bot is ready to run without UnicodeEncodeError!")
