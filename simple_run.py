#!/usr/bin/env python3
"""
Simple wrapper to run the tennis bot scanner without any Unicode output issues.
"""

import sys
import os

# Ensure we're in the right directory
os.chdir('d:/btcupdownclaudebot')
sys.path.insert(0, 'd:/btcupdownclaudebot')

print("Starting tennis bot scanner...")
print("-" * 60)

try:
    from tennis_edge_bot import CLOBClient, MarketScanner, TennisEdgeBot, CHAMP_PRICE_MIN, CHAMP_PRICE_MAX, INITIAL_ENTRY
    
    print("Initializing bot components...")
    clob = CLOBClient()
    scanner = MarketScanner(clob)
    bot = TennisEdgeBot()
    
    print("Scanning markets (up to 5 pages) for tennis champions...")
    markets = scanner.scan(max_pages=5)
    print("Total tennis markets discovered: " + str(len(markets)))
    print("-" * 60)
    
    executed = False
    for idx, m in enumerate(markets[:20]):
        print("\n[Market " + str(idx+1) + "] " + m.player_a + " vs " + m.player_b)
        print("  YES price: " + str(round(m.yes_price, 3)) + " | NO price: " + str(round(m.no_price, 3)) + " | Volume: $" + str(round(m.volume, 0)))
        
        # check if yes_price matches champion range
        if CHAMP_PRICE_MIN <= m.yes_price <= CHAMP_PRICE_MAX:
            print("  --> YES PRICE MATCHES RANGE [" + str(CHAMP_PRICE_MIN) + " - " + str(CHAMP_PRICE_MAX) + "]")
            print("  --> Executing DRY RUN BUY for " + m.player_a + " (" + str(INITIAL_ENTRY) + " USD)...")
            
            decision = {
                'action': 'BUY_CHAMPION',
                'player_name': m.player_a,
                'size_dollars': float(INITIAL_ENTRY),
                'confidence': 90,
            }
            
            try:
                ok = bot.execute(decision, m)
                if ok:
                    print("  --> SUCCESS! Trade executed.")
                    executed = True
                    break
                else:
                    print("  --> Execute returned False")
            except Exception as e:
                print("  --> Execute error: " + str(e))
        
        # Also check NO price
        elif CHAMP_PRICE_MIN <= m.no_price <= CHAMP_PRICE_MAX:
            print("  --> NO PRICE MATCHES RANGE [" + str(CHAMP_PRICE_MIN) + " - " + str(CHAMP_PRICE_MAX) + "]")
            print("  --> Executing DRY RUN BUY for " + m.player_b + " (underdog) (" + str(INITIAL_ENTRY) + " USD)...")
            
            decision = {
                'action': 'BUY_CHAMPION',
                'player_name': m.player_b,
                'size_dollars': float(INITIAL_ENTRY),
                'confidence': 90,
            }
            
            try:
                ok = bot.execute(decision, m)
                if ok:
                    print("  --> SUCCESS! Trade executed.")
                    executed = True
                    break
                else:
                    print("  --> Execute returned False")
            except Exception as e:
                print("  --> Execute error: " + str(e))

    print("\n" + "=" * 60)
    if executed:
        print("RESULT: Trade executed successfully!")
        print("Check tennisedge_state.json and match_logs/ for details.")
    else:
        print("RESULT: No champion-priced markets found in this scan.")
    print("=" * 60)

except Exception as e:
    print("\nFATAL ERROR: " + str(e))
    import traceback
    traceback.print_exc()
    sys.exit(1)
