# Polyarbi Bot Setup Guide

**Polyarbi** — Polymarket Arbitrage Bot with Binance 5-Min BTC Up/Down Detection

---

## Quick Start

### 1. **Install Dependencies**

```bash
pip install aiohttp websockets python-dateutil python-dotenv web3 eth-account
```

### 2. **Verify .env Configuration**

Ensure these are set in `.env`:

```env
PRIVATE_KEY=0x...your_polygon_private_key
WALLET_ADDRESS=0x...your_wallet_address
POLY_API_KEY=your_api_key
POLY_API_SECRET=your_api_secret
POLY_PASSPHRASE=your_passphrase
INITIAL_BANKROLL=50.0
DRY_RUN=1
```

### 3. **Run Dry-Run Mode (Safe)**

```bash
python polyarbi.py
```

Output will go to:
- **Console** — Real-time status logs
- **logs/polyarbi.log** — Detailed trading history
- **state/polyarbi_state.json** — Bot state & trades

### 4. **Switch to Live Mode**

After testing successfully in dry-run:

```bash
DRY_RUN=0 python polyarbi.py
```

⚠️ **WARNING**: Will place REAL orders with REAL USDC!

---

## Bot Architecture

### Data Feeds

1. **Binance WebSocket** → Real-time BTC/ETH/SOL prices (24h ticker)
2. **Polymarket Gamma API** → All active markets (300+ results)
3. **Polymarket CLOB WebSocket** → Real-time order books

### Strategies

The bot automatically detects and executes:

#### **1. Arbitrage (ARB)**
- Buys YES + NO tokens that sum to < $1.00
- Profit = $1.00 - (YES_price + NO_price)
- Min profit threshold: $0.50
- Highest priority (score = 100+)

#### **2. Directional Trading**
- Detects momentum from Binance price data
- Bayesian posterior probability estimation
- Uses Kelly criterion for position sizing
- Requires edge > 0.02 & confidence > 0.55

#### **3. Market Making (MM)**
- Stoikov-optimal quote generation
- Adapts spread based on time-to-expiry
- Inventory skew management
- Fallback when no arb/directional edge

---

## Configuration

Edit `polyarbi.py` top section:

```python
@dataclass
class StrategyConfig:
    min_liquidity_usdc: float = 500.0      # Min 24h volume
    max_time_to_expiry_hours: float = 2.0  # Only near-term markets
    arb_min_gap: float = 0.008             # Min arb profit threshold
    kelly_fraction: float = 0.25           # Size multiplier (0-1)
    max_bet_usdc: float = 200.0            # Max single trade size
```

```python
@dataclass
class RiskConfig:
    initial_bankroll: float = 50.0         # From .env INITIAL_BANKROLL
    max_daily_loss_pct: float = 0.05       # Stop if lost 5% daily
    max_drawdown_pct: float = 0.12         # Stop if down 12% from peak
    max_single_trade_pct: float = 0.03     # Max 3% per trade
    max_open_positions: int = 8            # Limit concurrent trades
```

---

## Status Log Output

Every 30 seconds:

```
[STATUS] Bankroll=$50.00 | DailyPnL=$0.00 | TotalPnL=$0.00 |
         Positions=0 | WinRate=0.0% | Trades=0 | Drawdown=0.0% | Halted=False
```

---

## Files Generated

```
logs/
  └─ polyarbi.log                 # All logs (timestamp, event, trade details)

state/
  └─ polyarbi_state.json          # Session state (bankroll, trades, positions)

trades/
  └─ polyarbi_trades.csv          # (Optional) Trade export
```

---

## Key Features

✅ **No Dashboard Required** — All output in logs
✅ **Fully Async** — 1000s of markets scanned per cycle
✅ **Dry-Run Mode** — Test without spending USDC
✅ **Auto Risk Management** — Stops on daily loss / drawdown limits
✅ **Real Data Sources** — Binance + Polymarket CLOB
✅ **Multi-Strategy** — ARB + Directional + MM in one bot

---

## Troubleshooting

### Bot Not Finding Markets?
- Check Binance feed: `[BinanceFeed] Connected ✓`
- Check Polymarket API: `[Scanner] Found N qualifying markets`
- Ensure `min_liquidity_usdc` is not too high

### No Orders Being Placed?
- Check risk manager: Are you halted?
- Check position limits: Max 8 open positions
- Check edge calculation: Need positive edge after fees

### "Bot halted: Daily loss limit hit"
- Reset daily stats (automatic after 24h)
- Or manually adjust `max_daily_loss_pct` in config

---

## Support

Generated configs per your requirements:
- ✅ Single file (no separate modules)
- ✅ Dry-run with $50 bankroll
- ✅ Real Binance + CLOB data
- ✅ Execution via .env credentials
- ✅ Polymarket ARB + directional trading

Good luck! 🚀
