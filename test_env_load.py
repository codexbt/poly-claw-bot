#!/usr/bin/env python3
"""
Test script to verify that .env is being loaded correctly
and that DRY_RUN is set to true (NOT live mode)
"""

import os
import sys
from pathlib import Path

# Add workspace to path
sys.path.insert(0, str(Path(__file__).parent))

# Import dotenv
from dotenv import load_dotenv

print("\n" + "="*80)
print("ENV LOADING TEST")
print("="*80 + "\n")

# Get the current directory
current_dir = Path(__file__).parent
env_file = current_dir / ".env"

print(f"[1] Current directory: {current_dir}")
print(f"[2] Looking for .env at: {env_file}")
print(f"[3] .env exists: {env_file.exists()}")

if env_file.exists():
    print(f"[4] .env file size: {env_file.stat().st_size} bytes\n")
    
    # Read raw content
    with open(env_file, 'r') as f:
        content = f.read()
        if "DRY_RUN=true" in content:
            print("[OK] DRY_RUN=true found in .env file")
        elif "DRY_RUN=false" in content:
            print("[WARNING] DRY_RUN=false found in .env file - THIS IS LIVE MODE!")
            sys.exit(1)
        else:
            print("[WARNING] DRY_RUN not found in .env file")
    
    # Load using dotenv
    load_dotenv(env_file, override=True)
    
    # Check what os.getenv returns
    dry_run_str = os.getenv("DRY_RUN", "NOT_FOUND")
    print(f"\n[5] os.getenv('DRY_RUN') returns: {dry_run_str}")
    
    # Parse to boolean
    dry_run_bool = dry_run_str.lower() == "true"
    print(f"[6] Parsed to boolean: {dry_run_bool}")
    
    # Verify
    print("\n" + "="*80)
    if dry_run_bool:
        print("[SUCCESS] DRY_RUN is TRUE - Testing mode (no real USDC spent)")
    else:
        print("[ERROR] DRY_RUN is FALSE - LIVE TRADING mode (REAL USDC will be spent!)")
        sys.exit(1)
    print("="*80 + "\n")
    
else:
    print("[ERROR] .env file not found!")
    sys.exit(1)
