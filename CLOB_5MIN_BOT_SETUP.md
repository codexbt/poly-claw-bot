# 🤖 CLOB 5-MIN BOT — QUICK START GUIDE

## Installation & Setup

### 1️⃣ Install Dependencies
```bash
pip install py-clob-client python-dotenv apscheduler pandas numpy
```

### 2️⃣ Setup Environment
```bash
cp .env.clob_5min_example .env
# Edit .env with your actual keys and market IDs
```

### 3️⃣ Run the Bot
```bash
python clob_5min_bot.py
```

---

## 📋 Configuration (.env)

**Required Fields:**
- `POLYGON_PRIVATE_KEY` - Your Polygon L1 private key
- `MARKETS` - Comma-separated market condition IDs to scan
- `PAPER_TRADING` - Set to `false` for live trading (⚠️ **risk real money**)

**Optional:**
- `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` - For trade alerts
- `STARTING_BALANCE` - Bankroll (default: $100)
- `MAX_BET_USD` - Max single trade size (default: $10)
- `KELLY_FRACTION` - Position sizing fraction (default: 0.25 = quarter-kelly)
- `MIN_EV_THRESHOLD` - Min expected value to trade (default: 0.08)
- `SCAN_INTERVAL_SECONDS` - How often to scan markets (default: 300 = 5 min)

---

## 🎯 Features

✅ **Colored Logging** - All trades show in bright colors (BUY=🟢 SELL=🔴)  
✅ **5-Min Scanning** - APScheduler runs market scan every 5 minutes  
✅ **Balance Tracking** - SQLite DB logs balance + PnL after each scan  
✅ **Kelly Sizing** - Automatic position sizing based on win probability  
✅ **Self-Learning** - Auto-tunes Kelly fraction and EV threshold  
✅ **Paper Mode** - Test strategies safely without real money  
✅ **EV Engine** - Volume imbalance + mid-price + last trade signals  

---

## 📊 Dashboard Output

Each scan shows:
```
════════════════════════════════════════════════════════════════════════════════
⟳  SCAN  14:35:22 UTC  |  Kelly=0.250  EV≥0.0800
════════════════════════════════════════════════════════════════════════════════
  [PAPER] [BUY]    Bitcoin will reach $100k by EOY       Price=0.6500 Size=10.00$ EV=+0.0850
  [PAPER] [SELL]   Trump wins 2028 election               Price=0.3200 Size=5.50$ EV=+0.0620

📊 Balance:    100.00$  |  Trades:   2  W/L: 1/0  PnL: +2.50$
```

---

## 💾 Database Files

- `clob_trade_history.db` - All trades logged
- Tables:
  - `trades` - Individual executed trades with EV, PnL, outcome
  - `balance_history` - Balance snapshots after each scan
  - `learning_state` - Kelly fraction & EV threshold tuning

---

## ⚠️ Safety Notes

1. **PAPER_TRADING=true** by default — activate only when tested!
2. **Start with small MAX_BET_USD** — test with $1-5 first
3. **Never share your POLYGON_PRIVATE_KEY** — keep in .env, never commit
4. **Monitor early trades** — manually verify bot logic before going large
5. **Set hard loss limits** — e.g., stop if daily PnL < -$50

---

## 🔧 Customization

### Add External Signals
Edit the `EXTERNAL_EDGE_SIGNALS` dict to plug in your own probability estimates:
```python
EXTERNAL_EDGE_SIGNALS = {
    "0x12345abc...": +0.05,  # Override true_prob for specific market
    "0x67890def...": -0.03,
}
```

### Adjust EV Threshold Dynamically
Modify `get_best_side()` to use different thresholds per market type.

### Custom Kelly Fraction
Set `KELLY_FRACTION=0.1` for ultra-conservative (1/10 Kelly)  
Set `KELLY_FRACTION=0.5` for more aggressive (half Kelly)

---

## 🆘 Troubleshooting

**"CLOB connection failed"**
→ Check `POLYGON_PRIVATE_KEY` in .env is valid hex

**"No MARKETS configured"**
→ Add market condition IDs to `MARKETS=` in .env (comma-separated)

**"Insufficient balance"**
→ Live mode need enough USDC on-chain. Check L2 account.

**No colored output?**
→ Terminal may not support ANSI. Try: `python clob_5min_bot.py > output.log`

---

## 📈 Expected Performance

- **Win Rate**: 50-65% typical (depends on EV threshold & markets)
- **PnL**: Depends on Kelly, market efficiency, your edge signals
- **Trades/Day**: 10-50 typical (varies by SCAN_INTERVAL)

---

## 📞 Support

Check logs: `sqlite3 clob_trade_history.db "SELECT * FROM trades ORDER BY id DESC LIMIT 10;"`

Good luck! 🚀
