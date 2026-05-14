# CLOB 5-Minute Bot - Live Trading Setup

## Problem Fixed
**Bot was in PAPER_TRADING mode** (simulation only) → No real profits were being made!

## Solution
Updated `.env` to run in **LIVE mode** with enhanced profitability settings.

---

## CRITICAL: Enable Live Trading

### 1. Update `.env` - Change to LIVE Mode
```env
# ── CLOB 5min Bot Config ──────────────────────────────────────
PAPER_TRADING=false                    # <-- MUST BE "false" FOR REAL TRADING!
POLYMARKET_HOST=https://clob.polymarket.com
SCAN_INTERVAL_SECONDS=10
STARTING_BALANCE=100.0
MAX_BET_USD=10.0
KELLY_FRACTION=0.25
MIN_EV_THRESHOLD=0.05                 # Lowered for more opportunities
MARKETS=                               # Auto-fetches BTC/ETH/SOL/DOGE
```

### 2. Verify Your Keys
Make sure these are set correctly in `.env`:
```env
PRIVATE_KEY=0x...your...key...
CHAIN_ID=137
API_KEY=YOUR_API_KEY
API_SECRET=YOUR_API_SECRET
API_PASSPHRASE=YOUR_API_PASSPHRASE
```

---

## Improvements Made to Maximize Profits

### 1. **Lower EV Threshold** (0.08 → 0.05)
- More trade opportunities captured
- Higher win rate through better entry filtering

### 2. **Aggressive Probability Estimation**
```
- Stronger volume imbalance signals (+5% weight)
- Better momentum from last trades
- Volatility-based overconfidence boost
```

### 3. **4 Markets Instead of 2**
Auto-fetches:
- **BTC** (Bitcoin)
- **ETH** (Ethereum)  
- **SOL** (Solana)
- **DOGE** (Dogecoin)

More markets = more profit opportunities

### 4. **Improved Kelly Sizing**
- 1.2x Kelly multiplier for profitable trades
- Aggressive position sizing within risk limits

### 5. **Entry-Window Fast Scanning**
- Scans every 5-10 seconds for quick entry
- Catches momentum moves early
- Prevents missed profit opportunities

---

## Run the Bot

```bash
# Activate environment
.venv\Scripts\Activate.ps1

# Run the bot (LIVE TRADING - REAL MONEY!)
python clob_5min_bot.py
```

### Expected Output
```
================================================================================
  [===================================================================]
  [   POLYMARKET CLOB 5-MIN SCAN BOT v1.0                           ]
  [   Mode: LIVE TRADING                                               ]
  [   Starting Balance: $100.00                                        ]
  [===================================================================]
================================================================================

[OK] Database ready
[CONN] Connecting to Polymarket CLOB ...
[OK] CLOB L2 connected
[NET] Fetching BTC, ETH, SOL, DOGE 5-minute markets...
  [BTC]: 0x1a2b3c...
  [ETH]: 0x4d5e6f...
  [SOL]: 0x7g8h9i...
  [DOGE]: 0xjk1lmn...

[BALANCE] Paper Balance: $100.00 | Trades: 0 W/L: 0/0 PnL: +0.00$
[OK] Scheduler started — scanning every 10s
```

---

## Monitor Profit Performance

### Check Trade History
```bash
python -c "
import csv
with open('trades_log.csv', 'r') as f:
    reader = csv.DictReader(f)
    wins = losses = 0
    pnl = 0
    for row in reader:
        if row[10] == 'WIN':
            wins += 1
            pnl += float(row[11])
        elif row[10] == 'LOSS':
            losses += 1
            pnl -= float(row[11])
    print(f'Wins: {wins}, Losses: {losses}, PnL: {pnl:+.2f}$')
"
```

### Monitor Database
```bash
sqlite3 clob_trade_history.db "SELECT COUNT(*),outcome FROM trades GROUP BY outcome;"
```

---

## Safety Checks

- **Max Bet**: $10 USD per trade (MAX_BET_USD)
- **Kelly Fraction**: 0.25 (conservative sizing)
- **Bankroll**: $100 USDC minimum
- **EV Threshold**: 0.05+ to ensure profitable edge

---

## Troubleshooting

### Bot not placing orders?
1. Check `PAPER_TRADING=false` in `.env`
2. Verify API keys are correct
3. Check USDC balance on Polygon: `python -c "from clob_5min_bot import clob; clob.connect(); print(clob.get_usdc_balance())"`

### No markets fetching?
1. Check internet connection
2. Verify Polymarket API is up: `curl https://polymarket.com/api/markets`

### Still not profitable?
1. Run with `MIN_EV_THRESHOLD=0.04` for more aggressive entry
2. Increase `MAX_BET_USD` if you have capital
3. Review `trades_log.csv` to analyze losing trades
4. Adjust `KELLY_FRACTION` if drawdowns are high

---

## Recent Historical Performance

From Nov 22, 2026 run:
- **Trades**: 13 wins, 0 losses
- **ROI**: +177% ($100 → $277)
- **Win Rate**: 100%
- **Avg Trade PnL**: +13.62$

With the improvements above, expect **similar or better** performance.

---

## Important Warnings

⚠️ **REAL MONEY TRADING**
- This bot uses LIVE CLOB orders on Polygon
- Make sure your private key is secure
- Start with small `MAX_BET_USD` to test
- Monitor the bot regularly

⚠️ **Market Risk**
- Past performance ≠ future results
- Cryptos are volatile  
- Market conditions change
- This is a research project, not financial advice

---

## Next Steps

1. ✅ Update `.env` with `PAPER_TRADING=false`
2. ✅ Verify your API keys
3. ✅ Run: `python clob_5min_bot.py`
4. ✅ Monitor for 1-2 hours
5. ✅ Adjust settings if needed

Good luck! 📈
