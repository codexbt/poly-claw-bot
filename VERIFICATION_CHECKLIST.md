# Verification Checklist - Market Discovery Fix

Use this checklist to verify all fixes are working correctly.

## Pre-Run Checks

- [ ] Python syntax valid: `python -m py_compile superbot.py`
  - Expected: `[SUCCESS] superbot.py syntax is valid`

- [ ] Required packages installed:
  ```bash
  python -c "import pytz, requests, re, aiohttp; print('[OK] All deps available')"
  ```
  - Expected: `[OK] All deps available`

- [ ] .env file exists and has required variables:
  ```bash
  cat .env | grep -E "PRIVATE_KEY|DRY_RUN|TRADE_SIZE"
  ```
  - Should show your config

---

## Running the Bot

### Start in DRY RUN mode (no real money):

```bash
# Set DRY_RUN=True in .env first!
python superbot.py
```

### What to expect in the first 30 seconds:

```
[INFO] ============================================================
[INFO] [SNIPER] POLYMARKET BTC 5-MIN SNIPER BOT
[INFO]    Mode:        SAFE
[INFO]    Dry Run:     True
[INFO] ============================================================

[INFO] [FEED] Loaded 25 candles. Current BTC: $70,XXX.XX
[INFO] [FEED] WebSocket connected [OK]
[INFO] [BOT] All systems ready. Entering main loop...

[INFO] [WINDOW] New 5-min window | Start: 22:15:00 UTC | Closes in: XXXs
[INFO] [MARKET] Searching for BTC 5-min market...
[DEBUG] [MARKET] Querying: https://polymarket.com/event/btc-updown-5m-1712583780
[INFO] [MARKET] [OK] Found market: BTC Up/Down | ID=0x1234567890ab...
[DEBUG] [MARKET]      YES=0.5234 | NO=0.4766
[INFO] [MARKET] Fetching token prices...
[INFO] [MARKET] Token prices: YES=0.5234 | NO=0.4766
```

✅ **If you see these logs**: Market discovery is working!

---

## Specific Log Checks

### Check 1: Market is discovered

🔍 **Look for**: `[INFO] [MARKET] [OK] Found market:`

- ✅ Good: Market found and ID shown
- ❌ Bad: Still seeing "Cannot connect to gamma-api"

### Check 2: Token prices are fetched

🔍 **Look for**: `[INFO] [MARKET] Token prices: YES=...`

- ✅ Good: Token prices shown (e.g., YES=0.5234)
- ❌ Bad: Missing token prices or errors

### Check 3: Signal evaluation works

🔍 **Look for**: `[INFO] [SIGNAL] [UP/DOWN]`

- ✅ Good: Signal evaluated with confidence %
- ❌ Bad: Never reaches signal evaluation

### Check 4: No more API errors

🔍 **Search for**: `[ERROR]` in the output

- ✅ Good: No [ERROR] lines about GAMMA API or DNS
- ❌ Bad: Still seeing connection errors

---

## Common Issues & Fixes

### Issue 1: "Cannot resolve host gamma-api.polymarket.com"
**Status**: ✅ FIXED (we're not using GAMMA API anymore)

### Issue 2: "ClobClient has no attribute 'approve_usdc'"
**Status**: ✅ FIXED (added hasattr check)

### Issue 3: "ClobClient has no attribute 'get_balance'"
**Status**: ✅ FIXED (added fallback to environment)

### Issue 4: UnicodeEncodeError with emoji
**Status**: ✅ FIXED (all emoji replaced, UTF-8 logging)

---

## If Something Still Fails

### Step 1: Check internet connectivity
```bash
# Can you reach polymarket.com?
curl -I https://polymarket.com/event/btc-updown-5m-1712583780
```

Expected: `HTTP/1.1 200 OK` (or 404 if market doesn't exist yet)

### Step 2: Check Binance connection
```bash
# Can you reach Binance?
python -c "import ccxt; b = ccxt.binance(); print(b.fetch_ticker('BTC/USDT'))"
```

### Step 3: Check if market exists in this time window
```bash
# Try a different timestamp (±300 seconds)
curl -I "https://polymarket.com/event/btc-updown-5m-1712583480"
curl -I "https://polymarket.com/event/btc-updown-5m-1712584080"
```

### Step 4: Enable debug logging
```bash
# Edit .env and set:
LOG_LEVEL=DEBUG

# Then run again and look for more verbose output:
python superbot.py 2>&1 | grep -i market
```

---

## Success Indicators 🎯

### Minimal (bot is alive):
- [x] No syntax errors
- [x] Feed loads candles
- [x] Bot enters main loop
- [x] Finds at least one market per window

### Good (bot is working):
- [x] Markets discovered every 5 minutes
- [x] Token prices fetched successfully  
- [x] Signal evaluation runs
- [x] Logs show status updates every 5-10 seconds

### Excellent (ready for trading):
- [x] Confident signals detected (>40% confidence)
- [x] Bot enters sniper zone (T-10s to T-5s)
- [x] Orders placed (even in DRY RUN mode)
- [x] Trades tracked and logged

---

## Log File Location

Logs are saved to: `logs/bot.log`

```bash
# View latest logs
tail -50 logs/bot.log

# Watch logs in real-time
tail -f logs/bot.log

# Search for markets discovered
grep "\[MARKET\] \[OK\]" logs/bot.log

# Count discovered markets per hour
grep "\[MARKET\] \[OK\]" logs/bot.log | wc -l
```

---

## Rollback (if needed)

If the changes break something, the original versions are still available:

```bash
# View recent changes
git diff superbot.py

# Revert to last version
git checkout superbot.py

# Or restore from backup
cp superbot.py.backup superbot.py
```

---

## Questions to Answer

Before reporting an issue, answer these:

1. **What error message do you see?**
   - Include the full error log

2. **Does it happen at startup or after running for a while?**
   - Immediately / After 5 minutes / After 30 minutes

3. **Is DRY_RUN=True or False?**
   - Testing mode / Live trading

4. **Can you reach Polymarket in your browser?**
   - Yes / No / Slow connection

5. **What's your .env configuration?**
   - (Don't share PRIVATE_KEY, just confirm it's set)

---

## Next Phase

Once you confirm markets are being discovered:

1. **Test order placement** in DRY RUN mode
   - Place fake orders and see them logged

2. **Monitor signal accuracy** for 1-2 days
   - Collect data on confidence and win/loss ratio

3. **Start live trading** with smallest position
   - $1 test trade to verify funds flow

4. **Scale up gradually** after 10+ successful trades
   - Increase position size based on results

---

**Last Updated**: 2026-04-08  
**Version**: Market Discovery Fix v1.0  
**Status**: Ready for testing ✅
