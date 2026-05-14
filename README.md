<div align="center">
  <img src="state/logo.svg" alt="Poly Claw Bot logo" width="420" />
  
  [![GitHub stars](https://img.shields.io/github/stars/codexbt/poly-claw-bot.svg?style=flat-square)](https://github.com/codexbt/poly-claw-bot/stargazers)
  [![GitHub forks](https://img.shields.io/github/forks/codexbt/poly-claw-bot.svg?style=flat-square)](https://github.com/codexbt/poly-claw-bot/network)
  [![GitHub issues](https://img.shields.io/github/issues/codexbt/poly-claw-bot.svg?style=flat-square)](https://github.com/codexbt/poly-claw-bot/issues)
  [![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg?style=flat-square)](https://www.python.org/)
  [![License](https://img.shields.io/badge/License-MIT-green.svg?style=flat-square)](LICENSE)
  [![Status](https://img.shields.io/badge/Status-Production%20Ready-brightgreen.svg?style=flat-square)](https://github.com/codexbt/poly-claw-bot)
</div>

> 🤖 AI-powered live trading suite for Polymarket and sports markets. Built for `codexbt` with modular strategies, secure configs, and LLM validation.

---

## ✨ Features

- 🚀 **Professional Architecture**: Clean layout with modular, focused scripts
- 🤖 **Multi-Strategy Bots**: Polymarket momentum, sports scanning, arbitrage, LLM-enhanced decisions
- ⚙️ **Config-Driven**: Environment variables in secure `.env` files
- 🔄 **Live Trading Ready**: Relayer integration, paper trading mode, comprehensive tracking
- 🧠 **AI Validation**: LLM-powered signal confidence for smarter trades
- 📊 **Advanced Metrics**: Win/loss ratios, PnL tracking, real-time logging
- 🔒 **Security First**: Encrypted secrets, no hardcoded credentials
- 🎯 **Risk Management**: Trade limits, position sizing, automated stops

---

## 📁 Project Structure

| File | Description |
|------|-------------|
| `poly5min_llm_bot.py` | 🤖 LLM-enhanced Polymarket 5-min momentum bot |
| `sports_bot.py` | ⚽ Sports market scanner and automated trader |
| `polyarbi.py` | 📈 Arbitrage and spread trading engine |
| `swissbot.py` | 🏔️ Swiss market trading bot |
| `sniperbot.py` | 🎯 Precision sniper bot for opportunities |
| `poly5min_all.py` | 🔍 Multi-market scanning and analysis tool |
| `requirements.txt` | 📦 Python dependencies |
| `.env` (example) | 🔐 Secure configuration template |

---

## 🚀 Quick Start

### Prerequisites
- Python 3.10+
- Git

### Installation

1. **Clone the repo**
   ```bash
   git clone https://github.com/codexbt/poly-claw-bot.git
   cd poly-claw-bot
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure environment**
   ```bash
   cp .env.example .env  # Edit with your keys
   ```

### Run a Bot

**Polymarket LLM Bot:**
```bash
python poly5min_llm_bot.py
```

**Sports Bot:**
```bash
python sports_bot.py
```

**Arbitrage Bot:**
```bash
python polyarbi.py
```

**Windows PowerShell (parallel):**
```powershell
start powershell -NoExit python poly5min_llm_bot.py
start powershell -NoExit python sports_bot.py
start powershell -NoExit python polyarbi.py
```

---

## 🔧 Configuration

Create a `.env` file in the root directory with your secure credentials:

```env
# Polymarket/Clob Credentials
PRIVATE_KEY=0xYOUR_PRIVATE_KEY
RELAYER_API_KEY=YOUR_RELAYER_API_KEY
RELAYER_API_KEY_ADDRESS=0xYOUR_SIGNER_ADDRESS

# AI/LLM Integration
OPENROUTER_API_KEY=sk-or-YOUR_OPENROUTER_KEY

# Trading Mode
PAPER_MODE=true  # Set to false for live trading

# Optional: Additional API keys for sports data
SPORTS_API_KEY=YOUR_SPORTS_API_KEY
```

> ⚠️ **Security Note**: Never commit `.env` files. Use `.gitignore` to exclude them.

---

## 🧠 Capabilities

- 🎯 **Advanced Trading Engines**: Momentum-based Polymarket trading with volume triggers and risk controls
- 🤖 **AI-Powered Validation**: OpenRouter LLM integration for trade signal confidence scoring
- 📊 **Comprehensive Metrics**: Real-time win/loss tracking, PnL calculations, and detailed logging
- 🔧 **Modular Architecture**: Easily extensible strategies and clean codebase
- 🔒 **Enterprise Security**: Secure `.env` handling, encrypted communications, no secrets in code
- ⚡ **High Performance**: Optimized for low-latency execution and concurrent operations

---

## 📌 Why this repo is professional

- Clean presentation with badges, clear structure, and concise messaging.
- Minimal root clutter and a sharp developer-first layout.
- Practical quick start and configuration guidance.
- Focus on safe trading patterns, not unbounded automation.

---

## 💡 Best Practices

- 🧪 **Test First**: Always use `PAPER_MODE=true` for testing strategies
- 🔐 **Secure Secrets**: Store all credentials in `.env`, never in code
- 📈 **Monitor Performance**: Track logs and metrics when adjusting parameters
- 🚦 **Risk Management**: Start with small positions and scale gradually
- 🔄 **Regular Updates**: Keep dependencies updated and review code periodically

## 📖 Examples

### Running Multiple Bots

```bash
# Start all bots in background
python poly5min_llm_bot.py &
python sports_bot.py &
python polyarbi.py &
```

### Custom Configuration

```python
# Example: Modify risk parameters
MAX_POSITION_SIZE = 0.1  # 10% of portfolio
STOP_LOSS_PERCENT = 0.05  # 5% stop loss
```

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit changes (`git commit -m 'Add amazing feature'`)
4. Push to branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## 🐛 Issues & Support

- 📋 [Report Issues](https://github.com/codexbt/poly-claw-bot/issues)
- 📧 Contact: Built for `codexbt`

## ⭐ Support the Project

If Poly Claw Bot helps your trading, please:
- ⭐ Star the repository
- 🍴 Fork and contribute
- 💝 Share with fellow traders

---

## 📄 License

Licensed under MIT License - see [LICENSE](LICENSE) for details.

---

<!-- Documentation updates - 2026-03-25 -->

*Built with ❤️ for `codexbt` - Professional trading automation made simple.*

> Updated 2025-11-16: Improve config hints and notes
> Updated 2025-11-23: Refactor bot startup logging
> Updated 2025-11-25: Update LLM validation note
> Updated 2025-11-27: Refactor bot startup logging
> Updated 2025-12-01: Update LLM validation note
> Updated 2026-04-10: Polish performance logging text
> Updated 2026-04-14: Improve config hints and notes
> Updated 2026-04-29: Clarify thresholds in comments
> Updated 2026-05-08: Improve config hints and notes
> Updated 2026-05-14: Refactor bot startup logging
