# CemeterysunReplicant — Polymarket Sports Trading Bot

## Overview
An advanced sports value betting bot that finds statistical edges between ESPN data + LLM analysis vs Polymarket odds. Focuses on MLB, NBA, and Soccer markets.

## Features
- **Multi-Source Data**: ESPN stats, pitcher data, home/away analysis
- **LLM Analysis**: Deep reasoning with deepseek-r1 (primary) + Claude fallback
- **CLOB Integration**: Live trading with py-clob-client
- **Risk Management**: Kelly sizing, daily limits, position tracking
- **Market Discovery**: Gamma API primary + CLOB fallback

## Setup

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Environment Variables (.env)
```bash
# Required
OPENROUTER_API_KEY=sk-or-...

# Optional (for live trading)
PRIVATE_KEY=0x...
POLYMARKET_FUNDER_ADDRESS=0x...
CHAIN_ID=137
SIGNATURE_TYPE=0

# Trading parameters
PAPER_MODE=true
MIN_EDGE=0.03
MAX_BET_USD=2000
DAILY_LOSS_LIMIT=200
KELLY_FRACTION=0.25
```

## Usage

### Paper Trading (Recommended First)
```bash
# One-time scan
python sports_bot.py

# Continuous scanning
python sports_bot.py --daemon

# Test specific market
python sports_bot.py --test-market "CIN vs MIN"
```

### Live Trading
```bash
# Set PAPER_MODE=false in .env first
python sports_bot.py --live --daemon
```

### Other Commands
```bash
# Show open positions + PnL
python sports_bot.py --positions

# Reset simulation state
python sports_bot.py --reset
```

## Market Discovery
- **Primary**: Gamma API `/markets?active=true&closed=false&limit=100`
- **Filtering**: Tags, categories, title keywords for MLB/NBA/Soccer
- **Fallback**: CLOB client `get_simplified_markets()` or `get_markets()`

## Risk Management
- Minimum edge: 3% (configurable)
- Kelly fraction: 25% of optimal
- Max bet: $2000 per trade
- Daily loss limit: $200
- Position sizing based on balance + edge

## Debugging
- Check `sports_bot.log` for detailed logs
- Use `--test-market` to debug specific markets
- Paper mode for safe testing
- LLM calls include retry logic with exponential backoff

## Data Sources
- **Polymarket**: Gamma API for discovery, CLOB for prices/orders
- **ESPN**: Team records, pitcher stats, game info
- **LLM**: Probability calibration with chain-of-thought reasoning

## Safety
- Paper mode by default
- Extensive logging of all decisions
- Balance validation before trades
- Automatic position settlement</content>
<parameter name="filePath">d:\btcupdownclaudebot\README_sports.md