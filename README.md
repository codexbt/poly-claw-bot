# Poly Claw Bot

[![GitHub Repo](https://img.shields.io/badge/GitHub-Repository-black.svg)](https://github.com/codexbt/poly-claw-bot)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Status](https://img.shields.io/badge/Status-Production%20Ready-brightgreen.svg)](https://github.com/codexbt/poly-claw-bot)

AI-powered live trading suite for Polymarket and sports markets.
A clean, professional repository built for `codexbt` with modular bot strategies, secure configuration, and optional LLM signal validation.

---

## 🚀 What this project delivers

- **Professional bot architecture** with a polished folder layout and focused core scripts.
- **Multi-strategy execution**: Polymarket momentum, sports scanning, LLM validation, and arbitrage support.
- **Config-driven operation** using environment variables and a centralized `config/` directory.
- **Live execution ready** with relayer support, dry-run mode, and robust trade tracking.
- **AI signal validation** for smarter entries, not blind automation.

---

## 📁 Repository structure

| Folder / File | Purpose |
|---|---|
| `bots/poly-claw.py` | Main Polymarket momentum trading engine |
| `bots/sports_bot.py` | Sports market scanner and execution bot |
| `bots/poly5min_llm_bot.py` | LLM-enhanced decision engine for Polymarket |
| `poly5min_all.py` | Broad multi-market scanning tool |
| `polyarbi.py` | Arbitrage and spread trading support |
| `config/env.example` | Secure environment template and secret handling |

---

## ⚡ Recommended quick start

```bash
pip install -r requirements.txt
```

```bash
cp config/env.example config/.env
```

```bash
python bots/poly-claw.py
```

For Windows PowerShell:

```powershell
start powershell -NoExit python bots/poly-claw.py
start powershell -NoExit python bots/sports_bot.py
start powershell -NoExit python bots/poly5min_llm_bot.py
```

---

## 🔧 Configuration

Create a `config/.env` file and add your credentials securely.

Example variables:

```ini
PRIVATE_KEY=0xYOUR_PRIVATE_KEY
OPENROUTER_API_KEY=sk-or-YOUR_KEY
API_KEY=YOUR_API_KEY
API_SECRET=YOUR_API_SECRET
RELAYER_API_KEY=YOUR_RELAYER_API_KEY
RELAYER_API_KEY_ADDRESS=0xYOUR_SIGNER_ADDRESS
PAPER_MODE=true
```

---

## 🧠 Core capabilities

- **Polymarket trading engine** with momentum filters, volume triggers, and trade risk controls.
- **LLM validation layer** using OpenRouter for signal confidence checks.
- **Trade metrics** including win/loss tracking, realized PnL, and live logging.
- **Modular design** for easier strategy extension and cleaner GitHub presentation.
- **Secure `.env` handling** from the `config/` folder, avoiding secrets in source control.

---

## 📌 Why this repo is professional

- Clean presentation with badges, clear structure, and concise messaging.
- Minimal root clutter and a sharp developer-first layout.
- Practical quick start and configuration guidance.
- Focus on safe trading patterns, not unbounded automation.

---

## 💡 Best practices

- Use `PAPER_MODE=true` for testing before running live.
- Keep secrets inside `config/.env` and never commit them.
- Monitor logs and trade performance when tuning thresholds.

---

## ⭐ Support

If this project improves your workflow, please star the repo and consider supporting continued development.

---

## 📄 License

MIT License

---

## Contact

Built for `codexbt` with production-ready structure and a professional GitHub presentation.


Built for `codexbt` with production-ready structure and a professional GitHub presentation.
