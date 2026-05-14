# 🚀 CLOB 5-MIN BOT — QUICK START SETUP

## Step 1: Create `.env` File

Copy the example and add your keys:

```bash
cp .env.clob_5min_example .env
```

## Step 2: Edit `.env` with Your Configuration

Open `.env` and fill in these required fields:

```ini
# ⚠️ CRITICAL - Get your private key from MetaMask
POLYGON_PRIVATE_KEY=0xYOUR_PRIVATE_KEY_HEX
POLYMARKET_HOST=https://clob.polymarket.com
CHAIN_ID=137

# Trading Settings
STARTING_BALANCE=100.0
MAX_BET_USD=10.0
KELLY_FRACTION=0.25
MIN_EV_THRESHOLD=0.08
SCAN_INTERVAL_SECONDS=300
PAPER_TRADING=true

# Add Polymarket condition IDs (find on polymarket.com URLs)
# Example: MARKETS=0x12345...abc,0x67890...def
MARKETS=
```

## Step 3: Find Market Condition IDs

1. Go to **polymarket.com**
2. Click any market (e.g., "Bitcoin over $100k")
3. Copy the ID from URL: `polymarket.com/market/**0x123abc...**`
4. Paste into `.env` as: `MARKETS=0x123abc...,0x456def...`

## Step 4: Test in Paper Mode First

```bash
# .env should have PAPER_TRADING=true
python clob_5min_bot.py
```

**Expected output:**
```
════════════════════════════════════════════════════════════════════════════════
  ╔═══════════════════════════════════════════════════════════════════╗
  ║   POLYMARKET CLOB 5-MIN SCAN BOT v1.0                            ║
  ║   Mode: PAPER 📄                                                  ║
  ║   Starting Balance: $100.00                                       ║
  ╚═══════════════════════════════════════════════════════════════════╝
════════════════════════════════════════════════════════════════════════════════

✅ Database ready
  🔌 Connecting to Polymarket CLOB …
  ✅ CLOB L2 connected
```

## Step 5: Monitor Initial Scans

Watch for colored BUY/SELL outputs:
- 🟢 **BUY** trades (green)
- 🔴 **SELL** trades (red)
- 📊 Balance updates every 5 minutes

## Step 6: Go LIVE (When Confident)

Edit `.env`:
```ini
PAPER_TRADING=false
```

⚠️ **This will use REAL MONEY** — start with `MAX_BET_USD=1`

---

## 🆘 Troubleshooting

| Error | Fix |
|-------|-----|
| `[FATAL] POLYGON_PRIVATE_KEY not set` | Add your key to `.env` |
| `No MARKETS configured` | Add condition IDs to `MARKETS=` in `.env` |
| `cannot import name 'Side'` | Already fixed! Run bot again. |
| No trades appearing | Check `MIN_EV_THRESHOLD` isn't too high (try 0.05) |
| Colored output missing | Windows Terminal should show colors — try `python clob_5min_bot.py > log.txt` |

---

## 📊 What to Expect

### Per Scan (5 minutes):
- Fetches orderbooks from selected markets
- Calculates expected value
- Executes trades if EV > threshold
- Updates balance
- Logs colorized output

### Daily Performance:
- ~288 scans (once every 5 min)
- 10-50 trades typical
- Win rate target: 55-65%

---

## ✅ Success Checklist

- [ ] `POLYGON_PRIVATE_KEY` added to `.env`
- [ ] At least 1 market ID in `MARKETS=`
- [ ] `PAPER_TRADING=true` for initial test
- [ ] Bot starts without errors
- [ ] See colored BUY/SELL logs
- [ ] Balance updates appear every 5 min
- [ ] Trades logged to `clob_trade_history.db`

---

**Ready to scan? Run:** `python clob_5min_bot.py`
