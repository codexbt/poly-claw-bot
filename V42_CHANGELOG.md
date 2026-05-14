# Bot v4.2 - Enhanced Accuracy & Smart Validation

## What Changed?

Your trading bot has been upgraded with **4 critical validation layers** and **API-aware logging** to eliminate false trades and show exactly what decisions are being made in real-time.

---

## 🎯 Major Features Added

### 1. **API Mode Detection & Terminal Logging** `[RELAYER] vs [CLOB]`
- Bot now **detects which API is active** (Relayer for gasless or CLOB for standard)
- **Every trade displays** which mode was used
- 30-second summaries show `API: RELAYER (Gasless - No MATIC)` or `API: CLOB (Standard)`
- Terminal shows: `[{api_mode}] TRADE EXECUTING`

**Terminal Output Example:**
```
[RELAYER] TRADE EXECUTING: BTC UP | Size=$2.45 | Score=0.782
  Breakdown: Momentum(40%)=0.88 + Candles(35%)=0.71 + Market(25%)=0.62
  Market: TrDaJi5hC... | TX Mode: RELAYER (Gasless - No MATIC)
  [LIVE] [GASLESS] Order ID: 0x234f...
```

---

### 2. **Time Window Validation** `Last 60-Second Gate`
- **Rejects trades** if not in the last 60 seconds of 5-min candle
- **Why?** Market closing time is critical → prices move most in final minute
- Shows seconds remaining in window: `Time: 45s left in window`
- **Automatic Skip** if trade would execute too early

**Decision Logic:**
```
IF time_remaining > 60 seconds:
    SKIP → "Too early (65s left, need <60s)"
ELSE:
    PROCEED
```

---

### 3. **Volatility Gate** `Reject Flat Markets`
- **Minimum 0.3% price movement required** (configurable)
- **Why?** Flat markets = false signals → money wasted
- Shows: `Volatility: 0.234%` - automatically rejected if too low
- Helps avoid choppy, rangey conditions

**Decision Logic:**
```
IF price_movement < 0.3%:
    SKIP → "Low volatility: 0.23% (need 0.5%)"
ELSE:
    PROCEED
```

---

### 4. **86% Threshold Crossing Validation** `Confirm NOT Just Above`
- **Verifies threshold was CROSSED** (from different side), not just sitting above
- **Why?** Prevents whipsaw trades on fake breakouts
- Shows: `Crossing: YES price crossed from 0.81→0.87, Signal confirmed`
- Checks **direction confirmation** (did it actually move there?)

**Decision Logic:**
```
IF signal == "UP":
    IF yes_price > 0.86 AND came_from_below:
        PROCEED → "Threshold crossed from 0.81→0.87"
    ELSE:
        SKIP → "Not crossed, already above"
ELSE:
    (similar for DOWN signal)
```

---

### 5. **Dynamic Trade Sizing** `$1–$3 Based on Signal Quality`
- **Score ≤ 0.50**: $1.00 (weak signal, dip toes in)
- **Score 0.50–0.75**: $1.00–$2.00 (interpolated)
- **Score 0.75–1.00**: $2.00–$3.00 (strong signal, commit)

**Formula:**
```
IF 0.50 ≤ score < 0.75:
    size = 1.0 + (score - 0.50) / 0.25 * 1.0
ELSE IF score ≥ 0.75:
    size = 2.0 + (score - 0.75) / 0.25 * 1.0
```

**Terminal Shows:**
```
Signal Score: 0.782 | Trade Size: $2.45
Breakdown: Momentum(40%)=0.88 + Candles(35%)=0.71 + Market(25%)=0.62
```

---

### 6. **Enhanced Terminal Logging** `See Every Decision`
- **Unified logging format** for all decisions
- **Rejection reasons** are now clear:
  - ❌ "Outside time window" → shows seconds elapsed
  - ❌ "Low volatility: 0.23%" → shows actual %
  - ❌ "Threshold not crossed" → shows prices
  - ❌ "Score too low" → shows actual score
  
- **Accepted trades** show full reasoning:
  - Price values (YES/NO)
  - Signal confidence breakdown
  - API mode being used
  - Order ID confirmation

---

## ✅ What You Get Now

### Before v4.2 (False Trades):
```
[BTC] NEUTRAL — no momentum
[BTC] Already traded this window
Order submission FAILED
```
❌ Not clear WHY trades failed or were skipped
❌ Flat markets → wasted money
❌ Didn't show which API was used

### After v4.2 (Smart Gateway):
```
[BTC] Outside time window (already passed) → SKIP
[BTC] Low volatility: 0.23% (need 0.3%) → SKIP
[BTC] Threshold not crossed: YES=0.81 (need >0.86) → SKIP

[RELAYER] TRADE EXECUTING: BTC UP | Size=$2.45 | Score=0.782
  API: RELAYER | Time: 45s left in window
  Volatility: 0.456%
  Market: TrDaJi5hC... | TX Mode: RELAYER (Gasless)
  [LIVE] [GASLESS] Order ID: 0x234f...
```

✅ **Clear decision path**: See exactly why each trade accepted/rejected
✅ **Zero false trades**: All validations must pass
✅ **API transparency**: Know which API is submitting orders
✅ **Volatility safety**: Flat markets automatically filtered

---

## 🔧 Configuration

All new validations are **built-in**, but you can customize:

### Time Window Gate
- **Currently**: Last 60 seconds of 5-min candle
- **Reason**: Market volatility peaks near window close
- **To change**: Edit `check_time_window_valid()` function

### Volatility Gate  
- **Currently**: 0.3% minimum price movement
- **Reason**: Filters out choppy, ranging markets
- **To adjust**: Change `min_pct=0.3` in `tick()` function

### Dynamic Sizing
- **Currently**: $1–$3 range (configurable via .env)
- **MIN_TRADE_SIZE**: $1.00
- **BASE_TRADE_SIZE**: $2.00
- **MAX_TRADE_SIZE**: $3.00

---

## 📊 Validation Order (Critical)

Trades MUST pass ALL checks in this sequence:

1. ✅ **Time Window** - Is it in last 60-sec?
2. ✅ **Volatility** - Is there >0.3% movement?
3. ✅ **86% Threshold** - Did price actually cross?
4. ✅ **Signal Quality** - Is score high enough?
5. ✅ **API Available** - Do we have API access?
6. ✅ **Daily Limit** - Haven't spent too much?

If ANY check fails → **SKIP** (logged in terminal)

---

## 🚀 Terminal Output Example

```
[11:05:32] 30-SEC SUMMARY | API: RELAYER (Gasless - No MATIC)
  BTC=$68,850  |  ETH=$2,650  |  SOL=$210  |  XRP=$2.35  |  DOGE=$0.42  |  HYPE=$15.20  |  BNB=$620
  Daily: $4.67/$300.00 | Trades: 3

[BTC] SIGNAL DETECTED!
  API: RELAYER | Time: 47s left in window
  Momentum: +2.3456% UP | Volatility: 0.456%
  Market ID: TrDaJi5hC... | TS: 1704067532
  Prices: YES=$0.87 | NO=$0.13
  Crossing: YES price crossed from 0.81→0.87, Signal confirmed

  Signal Score: 0.782 | Trade Size: $2.45
  Breakdown: Momentum(40%)=0.88 + Candles(35%)=0.71 + Market(25%)=0.62

  ────────────────────────────────────────────────────
  [RELAYER] TRADE EXECUTING: BTC UP | Size=$2.45 | Score=0.782
    Breakdown: Momentum(40%)=0.88 + Candles(35%)=0.71 + Market(25%)=0.62
    Market: TrDaJi5hC... | TX Mode: RELAYER (Gasless - No MATIC)
    Token: 0x8A4C63...
    [GASLESS] Submitting via Relayer API (no MATIC fee)...
    [LIVE] [GASLESS] Order ID: 0x234f8c9a1b2d3e4f...
    Portfolio Update: Spent=$7.12/$300.00 | Total Trades=4
  ────────────────────────────────────────────────────
```

---

## ⚠️ Important Notes

### Rejections Are GOOD
- **False positives eliminated** = More profitable
- Each rejection logged shows **why** it was rejected
- Review logs to understand market conditions better

### Gasless Priority
- **RELAYER API is tried first** (if key configured)
- Falls back to CLOB automatically if Relayer fails
- Shows in every trade: `[RELAYER]` vs `[CLOB]`

### Daily Limit Protection
- **Default**: $300/day spend limit
- Prevents runaway losses
- Resets at 00:00 UTC
- Edit in .env: `DAILY_LIMIT=300.0`

---

## 🎓 What to Watch For

1. **Terminal Comments** - Read the rejection reasons
2. **API Mode** - Verify `[RELAYER]` is showing (gasless)
3. **Time Windows** - Watch for trades in last 60-sec only
4. **Volatility %** - Confirm you're seeing 0.3%+ movements
5. **Order IDs** - Each trade should show confirmation

---

## 🆘 Troubleshooting

### No Trades Showing?
- Check time window (need to be in last 60-sec of 5-min candle)
- Check volatility (need >0.3% movement)
- Check threshold (need actual crossing, not just above)
- Check daily limit (might have hit $300 today)

### Seeing "[CLOB]" Instead of "[RELAYER]"?
- Relayer API didn't respond → fell back to standard CLOB
- Check: Is `RELAYER_API_KEY` set in .env?
- This is normal & automatic - still executes trades

### Too Many Rejections?
- Market is choppy/ranging (good! Bot is protecting you)
- Wait for clear trend
- That's the bot working as intended

---

## 📝 Version History

- **v4.0**: Core bot with 7 markets
- **v4.1**: Added validation functions (architecture)
- **v4.2**: **[YOU ARE HERE]** Integrated validations into trading loop + Enhanced logging

---

## Questions?

All 4 validation functions are in `poly5min_all.py`:
1. `get_api_mode_status()` - Line ~495
2. `check_time_window_valid()` - Line ~510
3. `check_volatility_sufficient()` - Line ~530  
4. `check_86_threshold_crossing()` - Line ~550

They're called in `tick()` function starting around line 810.

Good luck with live trading! जय हिन्द! 🚀
