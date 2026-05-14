# Polyarbi Bot — Quick Start Reference

## ✅ Status: Ready to Run

All systems verified:
- ✅ Python 3.10.11
- ✅ All dependencies installed (aiohttp, websockets, web3, eth_account, python-dateutil, python-dotenv)
- ✅ .env configured (PRIVATE_KEY, WALLET_ADDRESS, API credentials, INITIAL_BANKROLL=$50)
- ✅ Connectivity working (Binance, Polymarket CLOB, Gamma API)

---

## Quick Run Commands

### Test Dry-Run (Safe — No Real Money)
```bash
python polyarbi.py
```
This mode:
- Reads real data from Binance (BTC/ETH/SOL)
- Reads real markets from Polymarket
- Simulates trades with $50 bankroll
- Logs everything to `logs/polyarbi.log`
- Does NOT spend real USDC

### Switch to Live (Real Trading)
```bash
DRY_RUN=0 python polyarbi.py
```
⚠️ **WARNING**: This WILL place real orders with real USDC!

---

## What Polyarbi Does

**Input:** Binance 5-Min Price Updates + Polymarket Order Books

**Output:** Automatic trades on 3 strategies:

1. **Arbitrage (ARB)** — Buy YES+NO cheap, sell for $1.00 profit
2. **Directional** — Use Bayesian probability + Kelly sizing for directional bets
3. **Market Making** — Post bid/ask quotes (Stoikov model)

**Data Sources:**
- 🔴 Binance WebSocket → Real BTC/ETH/SOL prices
- 💎 Polymarket Gamma API → 300+ markets every 30s
- 📊 Polymarket CLOB WebSocket → Real order book updates

---

## Monitoring

### Live Logs
```bash
tail -f logs/polyarbi.log
```

### Example Log Output
```
[2025-04-12 14:23:45] [STATUS] Bankroll=$50.00 | TotalPnL=$0.50 | Trades=2 | WinRate=100.0%
[2025-04-12 14:23:50] [Trade] ✅ DIRECTIONAL YES | BTC | $5.00 @ 0.5230 | edge=+0.052 | order=DRY_000001
[2025-04-12 14:24:10] [Trade] Closed YES BTC | pnl=+0.24 | exit=0.5430
```

### State File
```bash
cat state/polyarbi_state.json
```
Shows: bankroll, trades, positions, P&L

---

## Configuration Options

Edit `polyarbi.py` (top of file):

```python
# Strategy thresholds
arb_min_gap = 0.008           # Min gap needed (0.8%)
arb_min_profit_usdc = 0.50    # Min profit per trade ($0.50)
kelly_fraction = 0.25          # Size multiplier (25% of Kelly)
max_bet_usdc = 200.0           # Max per trade

# Risk limits
max_daily_loss_pct = 0.05      # Stop if lost 5% today
max_drawdown_pct = 0.12        # Stop if down 12% lifetime
max_open_positions = 8         # Max concurrent trades

# Timing
scan_interval_seconds = 6.0    # Scan markets every 6 seconds
momentum_window_seconds = 60   # Use 60s of price history
```

---

## Dry-Run → Live Migration

### Step 1: Test in Dry-Run (Recommended: 30 min ~ 1 hour)
```bash
# Terminal 1
python polyarbi.py

# Terminal 2 (watch logs)
tail -f logs/polyarbi.log
```
✓ Check that trades are appearing
✓ Verify bankroll tracking
✓ Confirm no errors

### Step 2: Enable Live Mode
```bash
# Edit .env
nano .env
# Change: DRY_RUN=1  →  DRY_RUN=0
```

### Step 3: Run Live (with confirmation)
```bash
python polyarbi.py
```
Bot will ask:
```
WARNING: LIVE MODE — Real orders will be placed!
Bankroll: $50.00 USDC
Type 'LIVE' to confirm, or anything else to abort:
```
Type `LIVE` and press Enter to start.

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Bot not starting | `python verify_polyarbi.py` to debug |
| No trades after 5 min | Check `logs/polyarbi.log` for "No edges found" |
| "Bot halted: Daily loss limit" | Daily limit hit (resets after 24h) |
| Needs more logs | `LOG_LEVEL=DEBUG` in .env, but logs are already verbose |
| Wants to test specific market | Edit `STRATEGY.target_assets` in code |

---

## Files Overview

```
polyarbi.py                    ← Main bot (single file, ~2000 lines)
POLYARBI_SETUP.md             ← Full setup guide
POLYARBI_QUICKSTART.md        ← This file
verify_polyarbi.py            ← Verification script (already passed ✅)

logs/
  └─ polyarbi.log             ← Real-time trade logs
state/
  └─ polyarbi_state.json      ← Session state & P&L
```

---

## Next Steps

1. **Run dry-run now**: `python polyarbi.py`
2. **Watch for 5-10 min** to see if trades appear
3. **Check logs**: `tail -f logs/polyarbi.log`
4. **When confident**, enable live mode (change DRY_RUN=0)

---

**Created:** April 12, 2026
**Bot Name:** polyarbi (Polymarket Arbitrage)
**Status:** ✅ Ready to trade

Happy arbitraging! 🚀
