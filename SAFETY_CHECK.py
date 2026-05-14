#!/usr/bin/env python3
"""
SAFETY CHECK BEFORE RUNNING BOT
Ensures you're running in DRY_RUN mode (testing) NOT LIVE mode
"""

import os
import sys
from datetime import datetime
from dotenv import load_dotenv

def check_dry_run_safety():
    """Verify DRY_RUN is true before starting bot"""
    
    # Load .env with force override
    load_dotenv(override=True)
    
    # Get DRY_RUN value
    dry_run_str = os.getenv("DRY_RUN", "false").lower()
    dry_run_bool = dry_run_str == "true"
    
    # Get other critical values
    private_key = os.getenv("PRIVATE_KEY", "")
    funder_addr = os.getenv("POLYMARKET_FUNDER_ADDRESS", "")
    
    print("\n" + "="*90)
    print("  POLYMARKET 5-MIN BOT - SAFETY CHECK")
    print("="*90)
    
    # Check 1: DRY_RUN
    print(f"\n[CHECK 1] DRY_RUN Mode")
    print(f"  Value from .env: '{dry_run_str}'")
    if dry_run_bool:
        print(f"  Status: [SAFE] ✓ DRY RUN MODE (No real USDC will be spent)")
    else:
        print(f"  Status: [WARNING] ⚠️ LIVE MODE (REAL USDC WILL BE USED!)")
        print(f"  Action: Change .env to 'DRY_RUN=true' to enable testing mode")
    
    # Check 2: Private Key
    print(f"\n[CHECK 2] PRIVATE_KEY")
    if private_key and len(private_key) > 10:
        key_preview = private_key[:10] + "..." + private_key[-6:]
        print(f"  Status: [OK] ✓ Set ({key_preview})")
    else:
        print(f"  Status: [ERROR] Not set or invalid")
        return False
    
    # Check 3: Funder Address
    print(f"\n[CHECK 3] POLYMARKET_FUNDER_ADDRESS")
    if funder_addr and len(funder_addr) > 10:
        addr_preview = funder_addr[:8] + "..." + funder_addr[-6:]
        print(f"  Status: [OK] ✓ Set ({addr_preview})")
    else:
        print(f"  Status: [ERROR] Not set or invalid")
        return False
    
    # Final decision
    print("\n" + "="*90)
    if dry_run_bool:
        print("  [APPROVED] Safe to start bot in DRY RUN mode")
        print("  All signals and candles will be analyzed")
        print("  All trades will be shown with [DRY] prefix")
        print("  NO REAL USDC WILL BE SPENT")
    else:
        print("  [BLOCKED] Cannot start bot in LIVE mode")
        print("  To proceed with live trading:")
        print("    1. Ensure PRIVATE_KEY is correct")
        print("    2. Ensure POLYMARKET_FUNDER_ADDRESS has USDC loaded")
        print("    3. Change 'DRY_RUN=true' to 'DRY_RUN=false' ONLY IF INTENTIONAL")
        print("    4. Bot will wait 5 seconds before trading")
        return False
    
    print("="*90 + "\n")
    return dry_run_bool

if __name__ == "__main__":
    # Run safety check
    is_safe = check_dry_run_safety()
    
    if not is_safe:
        print("[ERROR] Safety check failed. Fix .env and try again.")
        sys.exit(1)
    
    print("[SUCCESS] All safety checks passed!")
    print("Ready to run: python poly5min_all.py\n")
    sys.exit(0)
