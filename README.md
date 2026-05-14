# Poly Claw Bot

A polished, production-ready trading repository built for `codexbt`.
This project combines multiple automated trading strategies, live market execution, and optional AI-powered signal validation.

## What’s included

- `bot.py` — the core Polymarket BTC/Polygon momentum trading engine
- `sports_bot.py` — sports market scanning and execution automation
- `poly5min_llm_bot.py` — LLM-enhanced trading strategy with OpenRouter / Claude reasoning
- `poly5min_all.py` / `polyarbi.py` — broader multi-asset and arbitrage support
- Environment-driven configuration for secure private key, API, and relayer usage
- Built-in win/loss tracking, live performance reporting, and trade logging

## Why this repository is professional

- Clean root layout with only essential README files
- Focused bot scripts for each strategy, not dozens of scattered docs
- Explicit environment configuration and secure `.env` handling
- AI integration is used as a signal filter, not a blind decision engine
- Trade metrics and win rate are tracked in every active bot loop

## Core bot roles

| Script | Purpose |
|---|---|
| `bot.py` | Main BTC/Polygon momentum bot with gasless relayer support. Ideal for fast 5-minute scans and live order execution. |
| `sports_bot.py` | Sports market bot for value trading, market scanning, and live sports edges. |
| `poly5min_llm_bot.py` | LLM-driven strategy using OpenRouter. Adds an AI reasoning layer to entry and exit decisions. |
| `poly5min_all.py` | Multi-market coverage and broader crypto scanning. |
| `polyarbi.py` | Arbitrage-style market scanning and execution support. |

## Running bots

Each strategy is a standalone script. Run the one you want directly:

```bash
pip install -r requirements.txt
```

```bash
python bot.py
python sports_bot.py
python poly5min_llm_bot.py
```

If you want multiple bots running at the same time, start each one in its own terminal session:

```bash
start powershell -NoExit python bot.py
start powershell -NoExit python sports_bot.py
start powershell -NoExit python poly5min_llm_bot.py
```

## Shared architecture

- Each bot uses environment variables for configuration and key management
- The same risk controls apply across strategies: trade size, daily limits, stop rules
- Performance is logged continuously with win rate, PnL, and trade counts
- This makes it easy to add another strategy without changing the repo layout

## AI / agent usage

The LLM bot uses `OPENROUTER_API_KEY` and a model such as `deepseek/deepseek-chat` or Claude.
The AI layer is used for:

- interpreting short-term market momentum and orderbook context
- validating entry decisions before trades are placed
- limiting AI calls to the most relevant windows

AI is combined with market filters, volume surge checks, price bands, and risk rules.

## Performance tracking

Each bot keeps live statistics:

- total trades executed
- wins and losses
- win rate percentage
- daily spent and current PnL

Logs and console summaries help tune thresholds and confidence over time.

## Setup

1. Copy `.env.example` to `.env`
2. Add your private wallet key and API credentials
3. Set `PAPER_MODE=true` for testing before switching to live mode

Example environment variables:

```ini
PRIVATE_KEY=0xYOUR_PRIVATE_KEY
OPENROUTER_API_KEY=sk-or-YOUR_KEY
API_KEY=YOUR_API_KEY
API_SECRET=YOUR_API_SECRET
RELAYER_API_KEY=YOUR_RELAYER_API_KEY
RELAYER_API_KEY_ADDRESS=0xYOUR_SIGNER_ADDRESS
```

## Notes

- This repository now contains only the core README files for a clean professional layout.
- Extra root markdown docs were removed so the project is easy to navigate.
- The repo is designed for `codexbt` and ready for GitHub publication.
