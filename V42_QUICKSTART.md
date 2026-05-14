# 🚀 BOT v4.2 — DEPLOYMENT CHECKLIST

## ✅ What's Updated

Your bot has been enhanced with **4 smart validation layers** that eliminate false trades:

| Feature | What It Does | Terminal Shows |
|---------|-------------|-----------------|
| **Time Window Gate** | Rejects trades outside last 60-sec | `Too early (65s left, need <60s)` |
| **Volatility Gate** | Rejects flat markets <0.3% movement | `Low volatility: 0.23%` |
| **86% Crossing Gate** | Confirms threshold crossed, not just above | `YES crossed 0.81→0.87 ✓` |
| **Signal Quality** | Dynamic $1-$3 sizing (0.50–1.00 score) | `Signal Score: 0.782 | Size: $2.45` |
| **API Detection** | Shows [RELAYER] vs [CLOB] | `[RELAYER] TRADE EXECUTING` |

---

## 🎯 Start Bot in 3 Steps

### Step 1: Verify Configuration
```bash
# Check if .env is set up correctly
cat .env | grep -E "RELAYER_API_KEY|DRY_RUN|DAILY_LIMIT"
```

Should show:
```
RELAYER_API_KEY=YOUR_RELAYER_API_KEY
DRY_RUN=false
DAILY_LIMIT=300.0
LOOP_SEC=1
PRICE_THRESHOLD=0.86
```

### Step 2: Start the Bot
```bash
python poly5min_all.py
```

### Step 3: Monitor Terminal
Watch for:
- ✅ **Signal detected**: `[BTC] SIGNAL DETECTED!`
- ✅ **Trade executed**: `[RELAYER] TRADE EXECUTING`
- ✅ **Portfolio update**: `Spent=$7.12/$300.00 | Total Trades=4`

---

## 📊 Expected Terminal Output

```
[11:05:32] 30-SEC SUMMARY | API: RELAYER (Gasless - No MATIC)
  BTC=$68,850  |  ETH=$2,650  |  SOL=$210
  Daily: $4.67/$300.00 | Trades: 3

[BTC] SIGNAL DETECTED!
  API: RELAYER | Time: 47s left in window
  Momentum: +2.3456% UP | Volatility: 0.456%
  Market ID: TrDaJi5hC...
  Prices: YES=$0.87 | NO=$0.13
  Crossing: YES crossed from 0.81→0.87 ✓

  Signal Score: 0.782 | Trade Size: $2.45
  Breakdown: Momentum=0.88 | Candles=0.71 | Market=0.62

  [RELAYER] TRADE EXECUTING: BTC UP | Size=$2.45
    [LIVE] [GASLESS] Order ID: 0x234f8c9a...
    Portfolio: Spent=$7.12/$300.00 | Trades=4
```

---

## ⚠️ Common Issues

### "Too early" messages?
- **Normal** → Waiting for last 60-second window
- **Not an error** → Bot protecting against false signals

### "Low volatility" messages?
- **Good!** → Market is choppy, bot avoiding losses
- **Expected** → During flat/ranging market conditions

### Seeing "[CLOB]" instead of "[RELAYER]"?
- **OK** → Relayer API not responding → fallback to CLOB
- **Check**: Is Relayer API key correct in .env?

### No trades at all?
- Check: Are you in last 60-sec of 5-min window?
- Check: Is price moving >0.3%?
- Check: Is daily limit not yet hit ($300)?
- Check: Are markets actually available?

---

## 🛡️ Safety Features Active

✅ **Daily Limit**: Max $300/day spend (auto-resets at 00:00 UTC)
✅ **Time Window**: Only trades last 60-sec of 5-min candle
✅ **Volatility Check**: Rejects <0.3% movement
✅ **Threshold Validation**: Confirms actual crossing
✅ **Cooldown**: Won't retrade same market window
✅ **Reversal Exit**: Automatically exits on 2-candle reversal

---

## 📈 Performance Tracking

**Bot runs 1 trade cycle per second**, displays summary every 30 seconds.

### What Good Performance Looks Like:
- ✅ Some messages say "Too early" or "Low volatility" (bot is selective!)
- ✅ When trades execute, they have score ≥0.78
- ✅ Trades execute with [RELAYER] (gasless)
- ✅ Daily limit not hit by noon
- ✅ Reversal exits triggering on losing trades

### What Bad Performance Looks Like:
- ❌ Getting only "[CLOB]" trades (relayer key issue?)
- ❌ Executing every signal (no filtering = false trades)
- ❌ Hitting daily limit too early
- ❌ No reversals triggered (positions not exiting)

---

## 🧪 Testing Mode vs Live Mode

### Test Without Spending Money:
```bash
# Edit .env
DRY_RUN=true

# Run
python poly5min_all.py
```
- Orders won't execute
- Portfolio values freeze at 0
- Good for verifying signals are working

### Live Money Mode:
```bash
# Edit .env
DRY_RUN=false

# Run
python poly5min_all.py
```
- **REAL MONEY** will be spent
- Relayer API used (no MATIC fees)
- Orders execute on Polymarket

---

## 📞 Support

### Files to Reference:
- **V42_CHANGELOG.md** - Detailed feature explanations
- **GASLESS_SETUP_GUIDE.txt** - Relayer API setup
- **QUICK_START.txt** - Basic configuration
- **bot.py** - Original bot if you want to compare

### Functions Modified:
- `tick()` - Main loop now integrates all 4 validations
- `get_api_mode_status()` - API detection
- `check_time_window_valid()` - Time window validation
- `check_volatility_sufficient()` - Volatility checking
- `check_86_threshold_crossing()` - Threshold crossing check

All in: `poly5min_all.py` (lines 810-960 for main loop, ~500-550 for validations)

---

## ✨ Pro Tips

1. **Read rejection messages** - They tell you why market is unsafe
2. **Watch volatility %** - Helps understand market conditions
3. **Check 30-sec summary** - See all market prices at once
4. **Monitor Order IDs** - Know your trades are in system
5. **Let reversals exit** - Don't manual close positions

---

## 🚀 You're Ready!

```bash
# Final verification
python -m py_compile poly5min_all.py && echo "✅ Ready to trade!"

# Start trading
python poly5min_all.py
```

**Good luck! जय हिन्द! 🎯**

*Questions? Errors? Check the terminal output - it tells you exactly what's happening.*
