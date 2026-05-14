# Polymarket BTC Momentum Bot — Setup Guide

## Files
```
polymarket_bot/
├── bot.py            ← Main bot (all logic here)
├── .env.example      ← Copy to .env and fill in
├── requirements.txt  ← Python dependencies
└── logs/             ← Auto-created, daily log files
```

---

## Setup on Termux (Android)

```bash
# 1. Install system packages
pkg update && pkg upgrade -y
pkg install python git -y

# 2. Clone or copy bot files into a folder
mkdir ~/polymarket_bot && cd ~/polymarket_bot
# (copy bot.py, .env.example, requirements.txt here)

# 3. Install Python dependencies
pip install -r requirements.txt

# 4. Create your .env file
cp .env.example .env
nano .env          # fill in your credentials

# 5. Test in DRY_RUN mode first (default is true)
python bot.py
```

---

## Setup on Linux VPS (Ubuntu/Debian)

```bash
# 1. Update and install Python 3.11+
sudo apt update && sudo apt install python3 python3-pip python3-venv -y

# 2. Create virtual environment
python3 -m venv venv
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure
cp .env.example .env
nano .env    # fill in PRIVATE_KEY, API credentials, etc.

# 5. Test dry run
python bot.py
```

---

## Run 24/7 in Background with tmux

```bash
# Install tmux
pkg install tmux -y          # Termux
sudo apt install tmux -y     # Ubuntu

# Create a named session
tmux new-session -s polybot

# Inside the tmux session, start the bot
cd ~/polymarket_bot
python bot.py

# Detach from tmux (bot keeps running)
# Press: Ctrl+B, then D

# Re-attach later to check logs
tmux attach -t polybot

# Kill the bot
tmux kill-session -t polybot
```

---

## Getting Polymarket API Credentials

1. Go to https://polymarket.com
2. Connect your MetaMask wallet
3. Click Profile → API Management
4. Create new API key — save KEY, SECRET, PASSPHRASE
5. Deposit USDC to your Polygon address

---

## Recommended First-Run Order

1. Set DRY_RUN=true in .env
2. Run bot.py and watch logs for 30-60 minutes
3. Verify it finds markets, calculates momentum, and prints trade signals
4. Check that trade sizes are within expected range ($5–$30)
5. If everything looks right, set DRY_RUN=false
6. Start with BASE_TRADE_SIZE=5 and DAILY_LIMIT=50 for first live day

---

## Safety Warnings

⚠️  PRIVATE KEY SECURITY
  - Never share your private key with anyone
  - Never paste it in a chat, Discord, Telegram, etc.
  - Use a dedicated wallet with only trading capital
  - Keep main funds in a separate wallet

⚠️  SLIPPAGE
  - Default 20% slippage is needed for thin 5-min markets
  - Lower = more rejections. Higher = worse fill price
  - Watch fill prices in logs vs expected price

⚠️  DAILY LIMITS
  - Start with DAILY_LIMIT=50 for the first week
  - Increase only after confirming the bot behaves correctly

⚠️  PREDICTION MARKET RISK
  - Even a 77% win rate means 23% losing trades
  - Variance is high on short-duration binary markets
  - This bot has NO guarantee of profit

---

## Improving Signal Accuracy (Advanced)

These upgrades can increase win rate from ~65% to potentially 75%+:

1. **RSI Filter**
   Add short-term RSI (period=5, 1-min data) from ccxt.
   Only trade "Up" when RSI < 65 (not overbought).
   Only trade "Down" when RSI > 35 (not oversold).

2. **Volume Spike Detection**
   Check if Binance BTC/USDT volume in last 30s is 2× the average.
   High volume + momentum = higher conviction signal.

3. **Order Book Imbalance**
   Use ccxt fetch_order_book() to check bid vs ask depth.
   If bid depth >> ask depth → more buyers → supports "Up" bet.

4. **Multi-Timeframe Confirmation**
   Check 1-min and 5-min candle direction both agree.
   Reduces false signals from 30-second spikes.

5. **Better Window Timing**
   Track which % of window gives best historical win rate.
   Might find 50%-75% is optimal vs current 38%-85%.

6. **Polymarket Order Book**
   Before placing, check if the CLOB has enough liquidity.
   If ask size < your trade size, skip (bad fill risk).
