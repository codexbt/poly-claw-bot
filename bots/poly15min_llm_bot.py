#!/usr/bin/env python3
"""
Polymarket BTC & ETH 15-Minute Sniper Bot - v5 (24/7 + Auto-Sell)

KEY CHANGES FROM v4:
- Runs 24/7 — IST hours restriction completely removed
- Positions auto-sell when token price reaches 0.99
- No on-chain redemption is used
- On DRY_RUN: auto-sell is simulated without a real chain call
- Balance + trade summary shown at every 15-min window close (colorful)
- All terminal logs are colorful and eye-catching
- Every 10s tick logs current status to terminal

EXIT FLOW:
  1. Trade executed → condition_id stored
  2. Every tick: _monitor_positions_for_auto_sell() checks open positions
  3. If a position reaches 0.99, it is auto-sold and closed
  4. If a 15-min window closes without auto-sell, the trade is recorded as LOSS

GAMMA API: NOT required — no redemption or resolution polling is required.
"""

import asyncio
import json
import time
import csv
import os
import sys
import re
import logging
import threading
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from typing import Optional
from pathlib import Path
import requests

try:
    from dotenv import load_dotenv
    script_path = Path(__file__).resolve()
    candidate = script_path.parents[1] / "config" / ".env"
    dotenv_path = candidate if candidate.exists() else script_path.parents[1] / ".env"
    load_dotenv(dotenv_path, override=True)
except Exception:
    print("⚠️  python-dotenv not installed — .env will not be loaded. pip install python-dotenv")

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import (
    MarketOrderArgs, OrderType, BalanceAllowanceParams, AssetType
)
from py_clob_client.order_builder.constants import BUY, SELL

# ─────────────────────────────────────────────
# ANSI COLOR CODES
# ─────────────────────────────────────────────
class C:
    RESET    = "\033[0m"
    BOLD     = "\033[1m"
    DIM      = "\033[2m"
    BLACK    = "\033[30m"
    RED      = "\033[31m"
    GREEN    = "\033[32m"
    YELLOW   = "\033[33m"
    BLUE     = "\033[34m"
    MAGENTA  = "\033[35m"
    CYAN     = "\033[36m"
    WHITE    = "\033[37m"
    BRED     = "\033[91m"
    BGREEN   = "\033[92m"
    BYELLOW  = "\033[93m"
    BBLUE    = "\033[94m"
    BMAGENTA = "\033[95m"
    BCYAN    = "\033[96m"
    BWHITE   = "\033[97m"
    BG_BLACK  = "\033[40m"
    BG_RED    = "\033[41m"
    BG_GREEN  = "\033[42m"
    BG_YELLOW = "\033[43m"
    BG_BLUE   = "\033[44m"
    BG_CYAN   = "\033[46m"

def cprint(msg: str):
    print(msg + C.RESET)

def tag(label: str, color: str) -> str:
    return f"{color}{C.BOLD}[{label}]{C.RESET}"

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
OPENROUTER_API_KEY = (
    os.getenv("OPENROUTER_API_KEY")
    or os.getenv("OPENROUTER_KEY")
    or os.getenv("OR_API_KEY")
)
OPENROUTER_API_KEY_SOURCE = next(
    (name for name in ("OPENROUTER_API_KEY", "OPENROUTER_KEY", "OR_API_KEY") if os.getenv(name)),
    None,
)
OPENROUTER_MODEL = "qwen/qwen3-14b"
FALLBACK_MODEL   = "deepseek/deepseek-r1"

PRIVATE_KEY               = os.getenv("PRIVATE_KEY", "") or os.getenv("FUNDING_PRIVATE_KEY", "")
CHAIN_ID                  = int(os.getenv("CHAIN_ID", "137"))
SIGNATURE_TYPE            = int(os.getenv("SIGNATURE_TYPE", "0"))
POLYMARKET_FUNDER_ADDRESS = os.getenv("POLYMARKET_FUNDER_ADDRESS", "")
RELAYER_URL               = os.getenv("RELAYER_URL", "https://relayer.polymarket.com")
RELAYER_API_KEY           = os.getenv("RELAYER_API_KEY", "")
RELAYER_API_KEY_ADDRESS   = os.getenv("RELAYER_API_KEY_ADDRESS", "")

BINANCE_BASE   = "https://api.binance.com"
BINANCE_BASES  = [
    "https://www.binance.com",
    "https://api.binance.us",
    "https://api.binance.com",
    "https://api1.binance.com",
    "https://api2.binance.com",
    "https://api3.binance.com",
]
BINANCE_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
    "Referer": "https://www.binance.com/",
}
DRY_RUN        = os.getenv("DRY_RUN", "false").lower() in ("true", "1", "yes")
INITIAL_BALANCE = 100.0
LOOP_INTERVAL  = 10      # seconds between ticks
LLM_TIMEOUT    = 25      # seconds

# Auto-sell threshold
AUTO_SELL_PRICE = 0.99

# Layer-1 filter thresholds
L1_WINDOW_DELTA_THRESH = 0.00025
L1_MOMENTUM_30S_THRESH = 0.00035
L1_VOL_SURGE_THRESH    = 2.0

MIN_TRADE_SIZE = 7
MAX_TRADE_SIZE = 7

# Redemption: how long after window close to attempt resolve check
REDEEM_GRACE_SECONDS = 30    # wait 30s after market closes before checking
REDEEM_MAX_ATTEMPTS  = 20    # max times to try resolving before giving up

LOG_FILE        = "trades_log.csv"
LIVE_LOG        = "live_log.txt"
DASHBOARD_FILE  = "dashboard_data.json"
MAX_LIVE_EVENTS = 80

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("bot")


# ─────────────────────────────────────────────
# DATA STRUCTURES
# ─────────────────────────────────────────────
@dataclass
class TradeRecord:
    timestamp:        str
    symbol:           str
    direction:        str
    entry_price:      float
    trade_size_usd:   float
    entry_shares:     float
    confidence:       int
    reasoning:        str
    market_ts:        str
    condition_id:     str  = ""
    outcome:          str  = "OPEN"
    pnl:              float = 0.0
    auto_sell_done:   bool  = False
    tracking_stopped: bool  = False
    redeem_attempted: bool  = False   # True once redeem was tried
    redeem_done:      bool  = False   # True once successfully redeemed
    resolve_attempts: int   = 0       # how many times we tried to check resolution

    @property
    def roi_pct(self) -> float:
        if self.trade_size_usd:
            return round((self.pnl / self.trade_size_usd) * 100, 2)
        return 0.0


@dataclass
class PendingPrediction:
    symbol:     str
    market_ts:  int
    decision:   dict
    created_at: float


@dataclass
class BotStats:
    initial_balance: float = INITIAL_BALANCE
    current_balance: float = INITIAL_BALANCE
    total_trades:    int   = 0
    wins:            int   = 0
    losses:          int   = 0
    daily_spent:     float = 0.0
    daily_pnl:       float = 0.0
    llm_calls:       int   = 0
    redeemed_count:  int   = 0        # total successful redemptions
    llm_history:     list  = field(default_factory=list)
    balance_history: list  = field(default_factory=list)

    @property
    def win_rate(self):
        closed = self.wins + self.losses
        return (self.wins / closed * 100) if closed else 0.0


# ─────────────────────────────────────────────
# COLORFUL TERMINAL DISPLAY
# ─────────────────────────────────────────────
def print_window_summary(stats: BotStats, open_trades: list, window_ts: int):
    bar  = "━" * 62
    half = "─" * 62
    wallet_delta = stats.current_balance - stats.initial_balance
    win_rate     = stats.win_rate
    delta_color  = C.BGREEN if wallet_delta >= 0 else C.BRED
    pnl_color    = C.BGREEN if stats.daily_pnl >= 0 else C.BRED
    from zoneinfo import ZoneInfo
    IST = ZoneInfo("Asia/Kolkata")
    window_dt = datetime.fromtimestamp(window_ts, tz=IST).strftime("%d %b %Y  %I:%M %p IST")

    print(f"\n{C.BCYAN}{C.BOLD}{bar}{C.RESET}")
    print(f"  {C.BG_BLUE}{C.BWHITE}{C.BOLD}  📊  5-MIN WINDOW CLOSED: {window_dt}  {C.RESET}")
    print(f"{C.BCYAN}{half}{C.RESET}")
    print(f"  {C.BYELLOW}💰 Balance{C.RESET}   Start: {C.WHITE}${stats.initial_balance:.2f}{C.RESET}   "
          f"Now: {C.BWHITE}${stats.current_balance:.2f}{C.RESET}   "
          f"Δ: {delta_color}${wallet_delta:+.2f}{C.RESET}")
    print(f"  {C.BYELLOW}📈 Realized PnL:{C.RESET} {pnl_color}${stats.daily_pnl:+.4f}{C.RESET}   "
          f"{C.DIM}(Committed: ${stats.daily_spent:.2f}){C.RESET}")
    wr_color = C.BGREEN if win_rate >= 50 else C.BRED
    print(f"  {C.BYELLOW}🎯 Trades:{C.RESET} {C.BWHITE}{stats.total_trades}{C.RESET} total   "
          f"{C.BGREEN}W:{stats.wins}{C.RESET}  {C.BRED}L:{stats.losses}{C.RESET}  "
          f"WR: {wr_color}{win_rate:.1f}%{C.RESET}   "
          f"{C.CYAN}LLM: {stats.llm_calls}  Redeemed: {stats.redeemed_count}{C.RESET}")
    if open_trades:
        print(f"  {C.BYELLOW}⏳ Open Positions ({len(open_trades)}):{C.RESET}")
        for t in open_trades:
            flags = ""
            if t.tracking_stopped: flags += f" {C.BRED}[TRACKING STOPPED]{C.RESET}"
            if t.auto_sell_done:   flags += f" {C.BGREEN}[AUTO-SOLD]{C.RESET}"
            if t.redeem_done:      flags += f" {C.BCYAN}[REDEEMED]{C.RESET}"
            elif t.redeem_attempted: flags += f" {C.BYELLOW}[REDEEM PENDING]{C.RESET}"
            print(f"    {C.BMAGENTA}▸{C.RESET} {C.BOLD}{t.symbol}{C.RESET} "
                  f"{C.BCYAN}{t.direction}{C.RESET} "
                  f"@ {C.WHITE}{t.entry_price:.4f}{C.RESET}  "
                  f"${t.trade_size_usd:.2f}  conf={t.confidence}%{flags}")
    else:
        print(f"  {C.DIM}No open positions.{C.RESET}")
    print(f"{C.BCYAN}{bar}{C.RESET}\n")


def print_tick_status(stats: BotStats, open_trades: list, seconds_left: int, ist_str: str):
    wallet_delta = stats.current_balance - stats.initial_balance
    delta_color  = C.BGREEN if wallet_delta >= 0 else C.BRED
    open_str     = f"{C.BMAGENTA}{len(open_trades)} open{C.RESET}" if open_trades else f"{C.DIM}no open{C.RESET}"
    print(
        f"{C.DIM}[{ist_str}]{C.RESET} {C.BGREEN}● 24/7{C.RESET}  "
        f"{C.BYELLOW}Bal:{C.RESET}{C.BWHITE}${stats.current_balance:.2f}{C.RESET} "
        f"({delta_color}{wallet_delta:+.2f}{C.RESET})  "
        f"W:{C.BGREEN}{stats.wins}{C.RESET} L:{C.BRED}{stats.losses}{C.RESET}  "
        f"Redeem:{C.BCYAN}{stats.redeemed_count}{C.RESET}  "
        f"{open_str}  "
        f"{C.CYAN}⏱ {seconds_left}s left{C.RESET}"
    )


def print_trade_entry(record: TradeRecord):
    dir_color = C.BGREEN if "YES" in record.direction else C.BRED
    cprint(
        f"\n{C.BG_GREEN}{C.BLACK}{C.BOLD}  🚀 TRADE ENTERED  {C.RESET}  "
        f"{C.BOLD}{record.symbol}{C.RESET} "
        f"{dir_color}{record.direction}{C.RESET}  "
        f"@ {C.BWHITE}{record.entry_price:.4f}{C.RESET}  "
        f"${record.trade_size_usd:.2f}  "
        f"Shares: {C.BYELLOW}{record.entry_shares:.4f}{C.RESET}  "
        f"Conf: {C.BCYAN}{record.confidence}%{C.RESET}"
    )


def print_auto_sell(symbol: str, direction: str, price: float, shares: float, pnl: float):
    cprint(
        f"\n{C.BG_YELLOW}{C.BLACK}{C.BOLD}  💸 AUTO-SELL @ 0.99  {C.RESET}  "
        f"{C.BOLD}{symbol}{C.RESET} {direction}  "
        f"Price: {C.BGREEN}{price:.4f}{C.RESET}  "
        f"Shares: {C.BYELLOW}{shares:.4f}{C.RESET}  "
        f"PnL: {C.BGREEN}${pnl:+.4f}{C.RESET}"
    )


def print_settlement(record: TradeRecord, stats: BotStats):
    if record.outcome == "WIN":
        bg = C.BG_GREEN; label = "✅ WIN"
        pnl_col = C.BGREEN
    else:
        bg = C.BG_RED; label = "❌ LOSS"
        pnl_col = C.BRED
    cprint(
        f"\n{bg}{C.BLACK}{C.BOLD}  {label}  {C.RESET}  "
        f"{C.BOLD}{record.symbol}{C.RESET} {record.direction}  "
        f"PnL: {pnl_col}${record.pnl:+.4f} ({record.roi_pct:+.2f}%){C.RESET}  "
        f"Running PnL: {C.BYELLOW}${stats.daily_pnl:+.4f}{C.RESET}"
    )


def print_redeem_success(symbol: str, direction: str, condition_id: str, payout: float):
    cprint(
        f"\n{C.BG_CYAN}{C.BLACK}{C.BOLD}  💎 REDEEMED ON-CHAIN  {C.RESET}  "
        f"{C.BOLD}{symbol}{C.RESET} {direction}  "
        f"Payout: {C.BGREEN}${payout:.4f}{C.RESET}  "
        f"conditionId: {C.DIM}{condition_id[:16]}...{C.RESET}"
    )


def print_redeem_fail(symbol: str, direction: str, reason: str):
    cprint(
        f"\n{C.BG_RED}{C.BWHITE}{C.BOLD}  ⚠️  REDEEM FAILED  {C.RESET}  "
        f"{C.BOLD}{symbol}{C.RESET} {direction}  {C.DIM}{reason}{C.RESET}"
    )


def print_tracking_stopped(symbol: str, direction: str):
    cprint(
        f"\n{C.BG_RED}{C.BWHITE}{C.BOLD}  🔕 TRACKING STOPPED  {C.RESET}  "
        f"{C.BOLD}{symbol}{C.RESET} {direction}  "
        f"{C.DIM}(Loss position — market closed){C.RESET}"
    )


# ─────────────────────────────────────────────
# BINANCE DATA FETCHER
# ─────────────────────────────────────────────
class BinanceFetcher:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(BINANCE_HEADERS)
        self.session.trust_env = False

    def _request(self, path: str, params: dict) -> dict:
        last_error = None
        for base_url in BINANCE_BASES:
            url = f"{base_url}{path}"
            try:
                r = self.session.get(url, params=params, timeout=7)
                r.raise_for_status()
                data = r.json()
                if not data:
                    raise requests.exceptions.RequestException(
                        f"Empty response from Binance endpoint {url}"
                    )
                return data
            except requests.exceptions.HTTPError as exc:
                status = getattr(exc.response, "status_code", None)
                if status in (429, 451, 502, 503, 504, 403, 404):
                    log.warning(
                        "  Binance host %s returned %s (%s), trying next host",
                        base_url, status, exc
                    )
                    last_error = exc
                    continue
                raise
            except requests.exceptions.RequestException as exc:
                log.warning(
                    "  Binance request failed on %s: %s",
                    base_url, exc,
                )
                last_error = exc
                continue
        raise last_error or requests.exceptions.RequestException("Binance request failed on all hosts")

    def get_klines(self, symbol: str, interval: str, limit: int) -> list:
        data = self._request(
            "/api/v3/klines",
            {"symbol": symbol, "interval": interval, "limit": limit},
        )
        if not isinstance(data, list):
            raise requests.exceptions.RequestException(
                f"Unexpected Binance klines response type: {type(data)}"
            )
        return data

    def get_24h_ticker(self, symbol: str) -> dict:
        data = self._request(
            "/api/v3/ticker/24hr",
            {"symbol": symbol},
        )
        if not isinstance(data, dict):
            raise requests.exceptions.RequestException(
                f"Unexpected Binance ticker response type: {type(data)}"
            )
        return data

    def summarize_klines(self, klines: list) -> dict:
        if not klines:
            return {"count": 0, "trend": "UNKNOWN", "change_pct": 0, "avg_vol": 0, "is_doji": False}
        closes  = [float(k[4]) for k in klines]
        volumes = [float(k[5]) for k in klines]
        trend      = "UP" if closes[-1] > closes[0] else "DOWN"
        change_pct = round((closes[-1] - closes[0]) / closes[0] * 100, 3)
        avg_vol    = round(sum(volumes) / len(volumes), 2)
        last_o, last_h, last_l, last_c = (
            float(klines[-1][1]), float(klines[-1][2]), float(klines[-1][3]), closes[-1]
        )
        last_body  = abs(last_c - last_o)
        last_range = last_h - last_l
        is_doji    = (last_body < last_range * 0.25) if last_range > 0 else False
        return {
            "count": len(klines),
            "trend": trend,
            "change_pct": change_pct,
            "avg_vol": avg_vol,
            "last_close": round(closes[-1], 2),
            "is_doji": is_doji,
        }


# ─────────────────────────────────────────────
# POLYMARKET FETCHER
# ─────────────────────────────────────────────
class PolymarketFetcher:
    MARKET_SLUGS = {"BTC": "btc-updown-15m", "ETH": "eth-updown-15m"}

    @staticmethod
    def get_current_window_ts() -> int:
        from zoneinfo import ZoneInfo
        et = datetime.now(timezone.utc).astimezone(ZoneInfo("America/New_York"))
        window_min   = (et.minute // 15) * 15
        window_start = et.replace(minute=window_min, second=0, microsecond=0)
        return int(window_start.timestamp())

    def _get_midpoint(self, token_id: str) -> float:
        r = requests.get(
            f"https://clob.polymarket.com/midpoint?token_id={token_id}", timeout=5
        )
        r.raise_for_status()
        return float(r.json().get("mid", 0.5))

    def get_token_price(self, token_id: str) -> float:
        try:
            return self._get_midpoint(token_id)
        except Exception as e:
            log.warning("get_token_price error %s: %s", token_id, e)
            return 0.0

    def get_current_market(self, symbol: str, window_open_price: float, current_price: float) -> dict:
        market_ts = self.get_current_window_ts()
        slug      = self.MARKET_SLUGS.get(symbol, symbol.lower() + "-updown-5m")
        urls      = [
            "https://polymarket.com/event/{}-{}".format(slug, market_ts),
            "https://www.polymarket.com/event/{}-{}".format(slug, market_ts),
        ]

        html = None
        for attempt in range(1, 4):
            for url in urls:
                try:
                    log.info("  Fetching market page %s (attempt %d)", url, attempt)
                except Exception:
                    log.info("  Fetching market page (attempt %d)", attempt)
                try:
                    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=12)
                    r.raise_for_status()
                    html = r.text
                    break
                except requests.exceptions.RequestException as exc:
                    log.warning("  Market page fetch failed: %s — %s", url, exc)
            if html is not None:
                break
            time.sleep(1.5)

        if html is None:
            raise ConnectionError(
                f"Could not fetch Polymarket market page for {symbol} at ts={market_ts} after retries"
            )

        cond_match  = re.search(r'"conditionId":"([^"]+)"', html)
        token_match = re.search(r'"clobTokenIds":\s*\[([^\]]+)\]', html)
        if not cond_match or not token_match:
            raise ValueError(f"Could not parse market data for {symbol} at ts={market_ts}")

        token_ids = json.loads("[" + token_match.group(1) + "]")
        yes_token = token_ids[0]
        no_token  = token_ids[1]
        yes_price = self._get_midpoint(yes_token)
        no_price  = self._get_midpoint(no_token)

        seconds_into_window = int(time.time()) - market_ts
        seconds_left        = max(0, 900 - seconds_into_window)

        window_delta_pct = 0.0
        if window_open_price and window_open_price != 0:
            window_delta_pct = round((current_price - window_open_price) / window_open_price * 100, 5)

        return {
            "symbol":           symbol,
            "yes_price":        round(yes_price, 4),
            "no_price":         round(no_price, 4),
            "window_delta_pct": window_delta_pct,
            "seconds_left":     seconds_left,
            "condition_id":     cond_match.group(1),
            "yes_token":        yes_token,
            "no_token":         no_token,
            "market_ts":        market_ts,
        }

    def get_market_outcome(self, condition_id: str) -> Optional[bool]:
        """
        Check resolution via CLOB API only (no Gamma needed).
        Returns: True = YES won, False = NO won, None = not resolved yet.
        """
        try:
            r = requests.get(
                f"https://clob.polymarket.com/markets/{condition_id}", timeout=5
            )
            r.raise_for_status()
            data = r.json()

            # Method 1: resolved flag + payout array
            if data.get("resolved"):
                payouts = data.get("resolved_payout", [])
                if isinstance(payouts, list) and len(payouts) >= 2:
                    if payouts[0] != payouts[1]:
                        return float(payouts[0]) > float(payouts[1])

            # Method 2: token winner flags
            yes_winner = False
            no_winner  = False
            for token in data.get("tokens", []):
                outcome = token.get("outcome", "").upper()
                if token.get("winner"):
                    if outcome == "YES":
                        yes_winner = True
                    elif outcome == "NO":
                        no_winner = True

            if yes_winner and not no_winner:
                return True
            if no_winner and not yes_winner:
                return False
            return None

        except Exception as e:
            log.warning("get_market_outcome error for %s: %s", condition_id, e)
            return None


# ─────────────────────────────────────────────
# LAYER-1 FILTER
# ─────────────────────────────────────────────
class Layer1Filter:
    def __init__(self):
        self._price_history    = {}
        self._condition_counts = {}

    @staticmethod
    def _get_bucket(ts: float) -> int:
        return int(ts // 300) * 300

    def update_price(self, symbol: str, price: float):
        ts   = time.time()
        hist = self._price_history.setdefault(symbol, [])
        hist.append((ts, price))
        cutoff = ts - 600
        self._price_history[symbol] = [(t, p) for t, p in hist if t > cutoff]

    def momentum(self, symbol: str, seconds: int) -> float:
        hist = self._price_history.get(symbol, [])
        if not hist:
            return 0.0
        now    = time.time()
        cutoff = now - seconds
        past   = [p for t, p in hist if t <= cutoff]
        if not past:
            return 0.0
        return (hist[-1][1] - past[-1]) / past[-1]

    @staticmethod
    def volume_surge(avg_vol_per_min: float, last_min_vol: float) -> float:
        return (last_min_vol / avg_vol_per_min) if avg_vol_per_min > 0 else 1.0

    def _record_conditions(self, symbol: str, conditions: list, passed: bool):
        ts     = time.time()
        bucket = self._get_bucket(ts)
        sym_buckets = self._condition_counts.setdefault(symbol, {})
        counts = sym_buckets.setdefault(bucket, {
            "window_delta": 0, "momentum_30s": 0,
            "vol_surge": 0, "checks": 0, "pass_count": 0,
        })
        for c in conditions:
            if c in counts:
                counts[c] += 1
        counts["checks"] += 1
        if passed:
            counts["pass_count"] += 1

    def current_window_summary(self, symbol: str) -> dict:
        ts     = time.time()
        bucket = self._get_bucket(ts)
        return self._condition_counts.get(symbol, {}).get(bucket, {
            "window_delta": 0, "momentum_30s": 0,
            "vol_surge": 0, "checks": 0, "pass_count": 0,
        })

    def should_call_llm(
        self, symbol: str, window_delta: float,
        momentum_30s: float, vol_surge: float, is_doji: bool
    ) -> tuple[bool, list]:
        if is_doji:
            return False, ["doji_detected"]
        conditions_met = []
        if abs(window_delta) > L1_WINDOW_DELTA_THRESH: conditions_met.append("window_delta")
        if abs(momentum_30s) > L1_MOMENTUM_30S_THRESH: conditions_met.append("momentum_30s")
        if vol_surge         > L1_VOL_SURGE_THRESH:    conditions_met.append("vol_surge")
        passed = len(conditions_met) >= 2
        self._record_conditions(symbol, conditions_met, passed)
        return passed, conditions_met


# ─────────────────────────────────────────────
# LLM DECIDER
# ─────────────────────────────────────────────
class LLMDecider:
    SYSTEM_PROMPT = """
You are a fast, strict Polymarket 15-min window sniper.
You receive completed candle data from window N. Predict direction for NEXT window N+1.

Analysis checklist:
1. Is momentum strong and consistent across 5m candles?
2. Does 15m candle confirm direction?
3. Is volume supporting the move?
4. Yes/No prices are reference only — do not over-weight them.

Rules:
- BUY_YES or BUY_NO only if confidence >= 60.
- YES price >= 0.94 with weak momentum -> NO_TRADE.
- Doji or conflicting signals -> NO_TRADE.
- Confidence < 60 -> force NO_TRADE.
- Use the previous completed 15-min candle's change %, body strength, and volume to judge momentum continuation into the next window.
- Data is sourced from Binance 5m candles (20 bars) and 15m candles (15 bars).

Output ONLY valid JSON, nothing else:
{"action":"BUY_YES"|"BUY_NO"|"NO_TRADE",
"confidence":0-100,
"trade_size_usd":1.0-2.0,
"reasoning":"one short sentence",
"suggested_entry_seconds_left":5-60}
"""

    @staticmethod
    def _extract_json(text: str) -> str:
        text = text.strip()
        if "```" in text:
            parts = text.split("```")
            for part in parts:
                part = part.strip()
                if part.startswith("json"):
                    part = part[4:].strip()
                if part.startswith("{"):
                    text = part
                    break
        if text.startswith("{") and text.endswith("}"):
            return text
        start = text.find("{")
        if start == -1:
            raise ValueError("No JSON found in LLM response")
        depth = 0
        for i, ch in enumerate(text[start:], start):
            if ch == "{":   depth += 1
            elif ch == "}": depth -= 1
            if depth == 0:
                return text[start : i + 1]
        raise ValueError("Unbalanced JSON braces in LLM response")

    def call(self, snapshot: dict, timeout: int = LLM_TIMEOUT, symbol: str = "") -> Optional[dict]:
        user_msg = f"Completed window data:\n{json.dumps(snapshot, separators=(',', ':'))}"
        for model in [OPENROUTER_MODEL, FALLBACK_MODEL]:
            try:
                t0 = time.time()
                resp = requests.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                        "Content-Type":  "application/json",
                    },
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": self.SYSTEM_PROMPT},
                            {"role": "user",   "content": user_msg},
                        ],
                        "max_tokens":  200,
                        "temperature": 0.02,
                    },
                    timeout=timeout,
                )
                elapsed = time.time() - t0
                log.info("  %sLLM response %.2fs (model=%s)", symbol + " " if symbol else "", elapsed, model)

                if resp.status_code != 200:
                    log.warning("LLM HTTP %d: %s", resp.status_code, resp.text[:200])
                    continue

                body    = resp.json()
                choices = body.get("choices") if isinstance(body, dict) else None
                if not choices or not isinstance(choices, list):
                    continue

                message = choices[0].get("message") if isinstance(choices[0], dict) else None
                if not message or not isinstance(message, dict):
                    continue

                raw = message.get("content")
                if not isinstance(raw, str):
                    continue

                parsed   = json.loads(self._extract_json(raw))
                required = {"action", "confidence", "trade_size_usd", "reasoning", "suggested_entry_seconds_left"}
                if not required.issubset(parsed.keys()):
                    log.warning("LLM missing fields: %s", parsed.keys())
                    continue

                parsed["confidence"] = int(parsed["confidence"])
                if parsed["confidence"] < 60:
                    parsed["action"] = "NO_TRADE"

                if parsed["action"] not in {"BUY_YES", "BUY_NO", "NO_TRADE"}:
                    log.warning("LLM invalid action: %s", parsed["action"])
                    continue

                parsed["trade_size_usd"] = max(MIN_TRADE_SIZE, min(MAX_TRADE_SIZE, float(parsed["trade_size_usd"])))
                parsed["reasoning"] = str(parsed["reasoning"]).strip()[:200]
                parsed["suggested_entry_seconds_left"] = max(5, min(60, int(parsed["suggested_entry_seconds_left"])))
                return parsed

            except requests.exceptions.Timeout:
                log.warning("LLM timeout (%ds) model=%s", timeout, model)
            except Exception as e:
                log.warning("LLM error model=%s: %s", model, e)

        log.error("All LLM models failed")
        return None


# ─────────────────────────────────────────────
# TRADE EXECUTOR  (with on-chain redemption)
# ─────────────────────────────────────────────
class TradeExecutor:
    def __init__(self):
        self.client          = None
        self.relayer_enabled = False

        if DRY_RUN:
            log.info("🔵 DRY_RUN enabled — CLOB disabled, redemption simulated")
            return
        if not PRIVATE_KEY:
            log.warning("No PRIVATE_KEY — running in simulation mode")
            return

        try:
            self.client = ClobClient(
                host="https://clob.polymarket.com",
                key=PRIVATE_KEY,
                chain_id=CHAIN_ID,
                signature_type=SIGNATURE_TYPE,
                funder=POLYMARKET_FUNDER_ADDRESS,
            )
            creds = self.client.create_or_derive_api_creds()
            self.client.set_api_creds(creds)
            self.relayer_enabled = bool(RELAYER_API_KEY and RELAYER_API_KEY_ADDRESS)
            log.info("✅ CLOB initialized (relayer=%s)", "yes" if self.relayer_enabled else "no")
        except Exception as e:
            log.warning("ClobClient init failed: %s — simulation mode", e)
            self.client = None

    # ── Balance ──────────────────────────────────────────────────────
    def get_balance_usd(self) -> float:
        if self.client is None:
            return INITIAL_BALANCE
        try:
            resp = self.client.get_balance_allowance(
                BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            )
            return round(int(resp.get("balance", 0)) / 1e6, 2)
        except Exception as e:
            log.error("get_balance_usd error: %s", e)
            return INITIAL_BALANCE

    # ── Helpers ──────────────────────────────────────────────────────
    def _submit_relayer(self, signed_payload: dict) -> Optional[dict]:
        payload = signed_payload
        if hasattr(signed_payload, "dict"):
            try:
                payload = signed_payload.dict()
            except Exception as exc:
                log.warning("  Failed to serialize signed payload: %s", exc)
                payload = signed_payload

        try:
            r = requests.post(
                f"{RELAYER_URL}/order",
                headers={
                    "Content-Type":            "application/json",
                    "RELAYER_API_KEY":          RELAYER_API_KEY,
                    "RELAYER_API_KEY_ADDRESS":  RELAYER_API_KEY_ADDRESS,
                },
                json=payload,
                timeout=15,
            )
            r.raise_for_status()
            data = r.json()
            log.info("  ✅ Relayer order: %s", data.get("orderID") or data.get("id", "?"))
            return data
        except Exception as e:
            log.warning("  Relayer submit failed: %s", e)
            return None

    def _place_order(self, token_id: str, action: str, amount: float, _retry: int = 0) -> Optional[dict]:
        MAX_RETRIES = 2
        if self.client is None:
            return None
        label = f" [RETRY {_retry}]" if _retry else ""
        try:
            log.info("  [CLOB%s] %s $%.2f", label, action, amount)
            order  = MarketOrderArgs(token_id=token_id, amount=amount, side=BUY, order_type=OrderType.FOK)
            signed = self.client.create_market_order(order)
            resp   = None
            if self.relayer_enabled:
                resp = self._submit_relayer(signed)
            if resp is None:
                resp = self.client.post_order(signed, OrderType.FOK)
            if resp and (resp.get("orderID") or resp.get("id")):
                return resp
            log.warning("  ❌ Order failed: %s", resp)
            if _retry < MAX_RETRIES:
                time.sleep(1 + _retry)
                return self._place_order(token_id, action, amount, _retry + 1)
            return None
        except Exception as e:
            log.error("  ❌ Order error: %s", e)
            if _retry < MAX_RETRIES:
                time.sleep(1 + _retry)
                return self._place_order(token_id, action, amount, _retry + 1)
            return None

    def _place_sell_order(self, token_id: str, shares: float, _retry: int = 0) -> Optional[dict]:
        MAX_RETRIES = 2
        if self.client is None:
            log.info("  [DRY-RUN] SELL %.4f shares token=%s", shares, token_id)
            return {"dry_run_sell": True}
        label = f" [RETRY {_retry}]" if _retry else ""
        try:
            log.info("  [CLOB%s] SELL %.4f shares", label, shares)
            order  = MarketOrderArgs(token_id=token_id, amount=shares, side=SELL, order_type=OrderType.FOK)
            signed = self.client.create_market_order(order)
            resp   = None
            if self.relayer_enabled:
                resp = self._submit_relayer(signed)
            if resp is None:
                resp = self.client.post_order(signed, OrderType.FOK)
            if resp and (resp.get("orderID") or resp.get("id")):
                return resp
            log.warning("  ❌ SELL failed: %s", resp)
            if _retry < MAX_RETRIES:
                time.sleep(1 + _retry)
                return self._place_sell_order(token_id, shares, _retry + 1)
            return None
        except Exception as e:
            log.error("  ❌ SELL error: %s", e)
            if _retry < MAX_RETRIES:
                time.sleep(1 + _retry)
                return self._place_sell_order(token_id, shares, _retry + 1)
            return None

    def _extract_fill_price(self, resp: dict) -> Optional[float]:
        if not isinstance(resp, dict):
            return None
        for key in ("avgFillPrice", "avg_fill_price", "avg_price", "price", "filled_price"):
            if resp.get(key) is not None:
                try:
                    return float(resp[key])
                except (TypeError, ValueError):
                    pass
        if isinstance(resp.get("order"), dict):
            return self._extract_fill_price(resp["order"])
        fills = resp.get("fills") or resp.get("fill") or resp.get("executions")
        if isinstance(fills, list) and fills:
            first = fills[0]
            if isinstance(first, dict):
                for key in ("price", "fill_price", "avg_price", "avgFillPrice"):
                    if key in first:
                        try:
                            return float(first[key])
                        except (TypeError, ValueError):
                            pass
        return None

    def _fetch_fill_price_from_order_id(self, order_id: str) -> Optional[float]:
        if not order_id or self.client is None:
            return None
        try:
            order_data = self.client.get_order(order_id)
            return self._extract_fill_price(order_data)
        except Exception as e:
            log.warning("  Could not fetch order details for fill price: %s", e)
            return None

    # ── REDEMPTION ────────────────────────────────────────────────────
    def redeem_position(self, condition_id: str, shares: float) -> tuple[bool, float]:
        """
        Redeem a winning position on-chain via CLOB L2.

        Uses py_clob_client.redeem_positions() which handles:
          - ConditionalTokens.redeemPositions() on Polygon
          - Merges winning CTF shares back to USDC collateral

        Returns: (success: bool, payout_usd: float)

        DRY_RUN: simulates payout as shares * 1.0 (full payout).
        Live:    calls redeem_positions then refreshes balance.
        """
        if DRY_RUN:
            simulated_payout = round(shares * 1.0, 4)
            log.info(
                "  [DRY-RUN] REDEEM condition=%s shares=%.4f simulated_payout=$%.4f",
                condition_id[:16], shares, simulated_payout
            )
            return True, simulated_payout

        if self.client is None:
            log.warning("  No CLOB client — cannot redeem")
            return False, 0.0

        try:
            # py_clob_client redeem call
            # The method signature varies slightly by version:
            #   client.redeem_positions(condition_id)   OR
            #   client.redeem(condition_id=condition_id)
            # We try both to be safe.
            balance_before = self.get_balance_usd()
            redeemed = False

            try:
                result = self.client.redeem_positions(condition_id)
                log.info("  [REDEEM] redeem_positions result: %s", result)
                redeemed = True
            except AttributeError:
                pass

            if not redeemed:
                try:
                    result = self.client.redeem(condition_id=condition_id)
                    log.info("  [REDEEM] redeem result: %s", result)
                    redeemed = True
                except AttributeError:
                    pass

            if not redeemed:
                log.error("  [REDEEM] py_clob_client has no redeem method — check version")
                return False, 0.0

            # Wait briefly for on-chain confirmation then re-fetch balance
            time.sleep(3)
            balance_after = self.get_balance_usd()
            payout = round(balance_after - balance_before, 4)
            if payout < 0:
                payout = round(shares * 1.0, 4)   # fallback estimate
            return True, payout

        except Exception as e:
            log.error("  [REDEEM] Error redeeming condition=%s: %s", condition_id[:16], e)
            return False, 0.0

    # ── Execute entry ─────────────────────────────────────────────────
    def execute(self, decision: dict, market: dict, stats: BotStats) -> Optional[TradeRecord]:
        action = decision.get("action", "NO_TRADE")
        if action == "NO_TRADE":
            return None

        confidence = int(decision.get("confidence", 0))
        size       = float(decision.get("trade_size_usd", MIN_TRADE_SIZE))
        size       = max(MIN_TRADE_SIZE, min(MAX_TRADE_SIZE, size))

        symbol      = market["symbol"]
        entry_price = market["yes_price"] if action == "BUY_YES" else market["no_price"]
        if entry_price <= 0:
            log.warning("Invalid entry price %.4f, skipping", entry_price)
            return None

        entry_shares = round(size / entry_price, 4) if entry_price > 0 else 0.0

        # Simulation / DRY_RUN path
        if self.client is None:
            stats.current_balance = max(0, stats.current_balance - size)
            stats.daily_spent    += size
            stats.total_trades   += 1
            return TradeRecord(
                timestamp=datetime.now().isoformat(timespec="seconds"),
                symbol=symbol, direction=action,
                entry_price=entry_price, trade_size_usd=size,
                entry_shares=entry_shares, confidence=confidence,
                reasoning=decision.get("reasoning", ""),
                market_ts=str(market.get("market_ts", "")),
                condition_id=market.get("condition_id", ""),
            )

        token_id = market["yes_token"] if action == "BUY_YES" else market["no_token"]
        if not token_id:
            log.error("Missing token_id for %s", action)
            return None

        resp = self._place_order(token_id, action, size)
        if not resp:
            log.error("Order placement failed for %s %s", symbol, action)
            return None

        actual_price = self._extract_fill_price(resp)
        order_id     = resp.get("orderID") or resp.get("id")
        if actual_price is None and order_id:
            actual_price = self._fetch_fill_price_from_order_id(order_id)
        if actual_price is None:
            actual_price = entry_price

        actual_shares = round(size / actual_price, 4) if actual_price > 0 else entry_shares

        stats.current_balance = self.get_balance_usd()
        stats.daily_spent    += size
        stats.total_trades   += 1
        return TradeRecord(
            timestamp=datetime.now().isoformat(timespec="seconds"),
            symbol=symbol, direction=action,
            entry_price=actual_price, trade_size_usd=size,
            entry_shares=actual_shares, confidence=confidence,
            reasoning=decision.get("reasoning", ""),
            market_ts=str(market.get("market_ts", "")),
            condition_id=market.get("condition_id", ""),
        )


# ─────────────────────────────────────────────
# LOGGER
# ─────────────────────────────────────────────
class TradingLogger:
    FIELDS = [
        "timestamp","symbol","direction","entry_price","trade_size_usd",
        "entry_shares","confidence","reasoning","market_ts","condition_id",
        "outcome","pnl","redeem_done"
    ]

    def __init__(self):
        if not os.path.exists(LOG_FILE):
            with open(LOG_FILE, "w", newline="") as f:
                csv.DictWriter(f, fieldnames=self.FIELDS).writeheader()

    def log_trade(self, record: TradeRecord, stats: BotStats, open_trades: list):
        row = {k: getattr(record, k, "") for k in self.FIELDS}
        with open(LOG_FILE, "a", newline="") as f:
            csv.DictWriter(f, fieldnames=self.FIELDS).writerow(row)
        open_exposure = sum(t.trade_size_usd for t in open_trades) if open_trades else 0.0
        if record.outcome == "OPEN":
            line = (
                f"[TRADE] {record.timestamp} | {record.symbol} {record.direction}"
                f" @ {record.entry_price:.4f} | ${record.trade_size_usd:.2f}"
                f" | Shares: {record.entry_shares:.4f} | Conf: {record.confidence}%"
            )
        else:
            result_text = "RESOLVED WIN" if record.pnl >= 0 else "RESOLVED LOSS"
            redeem_str  = " | REDEEMED ✓" if record.redeem_done else ""
            closed = stats.wins + stats.losses
            wr_str = f"{stats.win_rate:.1f}% ({stats.wins}/{closed})" if closed else "0.0%"
            line = (
                f"[TRADE] {record.timestamp} | {record.symbol} {record.direction}"
                f" @ {record.entry_price:.4f} | ${record.trade_size_usd:.2f}"
                f" | Shares: {record.entry_shares:.4f} | Conf: {record.confidence}%\n"
                f"→ {result_text}{redeem_str}\n"
                f"→ PnL: ${record.pnl:+.4f} ({record.roi_pct:+.2f}%)\n"
                f"→ Running PnL: ${stats.daily_pnl:+.4f} | WR: {wr_str}"
            )
        with open(LIVE_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        for part in line.split("\n"):
            log.info(part)
        self._print_stats(stats, len(open_trades) if open_trades else 0, open_exposure)

    @staticmethod
    def _print_stats(stats: BotStats, open_count: int = 0, open_exposure: float = 0.0):
        closed       = stats.wins + stats.losses
        wallet_delta = stats.current_balance - stats.initial_balance
        delta_color  = C.BGREEN if wallet_delta >= 0 else C.BRED
        bar = "─" * 58
        print(f"\n{C.CYAN}{bar}{C.RESET}")
        print(f"  {C.BOLD}{C.BYELLOW}📊 BOT STATS  {'[DRY-RUN]' if DRY_RUN else '[LIVE REAL]'} 24/7{C.RESET}")
        print(f"  Start balance  : {C.WHITE}${stats.initial_balance:.2f}{C.RESET}")
        print(f"  Current balance: {C.BWHITE}${stats.current_balance:.2f}{C.RESET}")
        print(f"  Wallet Δ       : {delta_color}${wallet_delta:+.2f}{C.RESET}")
        print(f"  Open exposure  : {C.BYELLOW}${open_exposure:.2f}{C.RESET}")
        wr_color = C.BGREEN if stats.win_rate >= 50 else C.BRED
        print(f"  Trades         : {C.BWHITE}{stats.total_trades}{C.RESET}  "
              f"closed={closed}  open={open_count}  "
              f"{C.BGREEN}W:{stats.wins}{C.RESET}/{C.BRED}L:{stats.losses}{C.RESET}  "
              f"WR:{wr_color}{stats.win_rate:.1f}%{C.RESET}")
        pnl_color = C.BGREEN if stats.daily_pnl >= 0 else C.BRED
        print(f"  Realized PnL   : {pnl_color}${stats.daily_pnl:+.2f}{C.RESET}")
        print(f"  Redeemed       : {C.BCYAN}{stats.redeemed_count}{C.RESET} positions")
        print(f"{C.CYAN}{bar}{C.RESET}\n")


# ─────────────────────────────────────────────
# DASHBOARD WRITER
# ─────────────────────────────────────────────
class DashboardWriter:
    def __init__(self):
        if not os.path.exists(DASHBOARD_FILE):
            with open(DASHBOARD_FILE, "w") as f:
                json.dump({"updated_at": None}, f)

    def write(self, stats: BotStats, live_events: list, open_trades: list, symbol_summary: dict):
        payload = {
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "mode":       "24/7",
            "stats": {
                "initial_balance": stats.initial_balance,
                "current_balance": stats.current_balance,
                "total_trades":    stats.total_trades,
                "wins":            stats.wins,
                "losses":          stats.losses,
                "win_rate":        round(stats.win_rate, 1),
                "wallet_delta":    round(stats.current_balance - stats.initial_balance, 2),
                "realized_pnl":    round(stats.daily_pnl, 2),
                "daily_spent":     round(stats.daily_spent, 2),
                "llm_calls":       stats.llm_calls,
                "redeemed_count":  stats.redeemed_count,
            },
            "live_events":    live_events[-MAX_LIVE_EVENTS:],
            "trade_history":  [asdict(r) for r in open_trades],
            "symbol_summary": symbol_summary,
            "balance_history": stats.balance_history[-50:],
        }
        with open(DASHBOARD_FILE, "w") as f:
            json.dump(payload, f, indent=2)

    @staticmethod
    def add_event(live_events: list, message: str):
        live_events.append({
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "message":   message,
        })
        if len(live_events) > MAX_LIVE_EVENTS:
            live_events.pop(0)


# ─────────────────────────────────────────────
# MAIN BOT
# ─────────────────────────────────────────────
class SniperBot:
    SYMBOLS = [("BTCUSDT", "BTC"), ("ETHUSDT", "ETH")]

    def __init__(self):
        self.binance    = BinanceFetcher()
        self.polymarket = PolymarketFetcher()
        self.l1_filter  = Layer1Filter()
        self.llm        = LLMDecider()
        self.executor   = TradeExecutor()
        self.logger     = TradingLogger()
        self.dashboard  = DashboardWriter()

        self.stats      = BotStats()
        self.stats.current_balance = self.executor.get_balance_usd()
        self.stats.initial_balance = self.stats.current_balance
        self.stats.balance_history.append({
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "balance":   self.stats.current_balance,
        })

        self.open_trades:          list[TradeRecord]       = []
        self.pending_predictions:  list[PendingPrediction] = []
        self.live_events:          list[dict]              = []
        self.symbol_summary:       dict                    = {}

        # token_id store for auto-sell: condition_id -> (yes_token, no_token)
        self._trade_tokens: dict[str, tuple[str, str]] = {}

        self.llm_called:            dict[int, set[str]]        = {}
        self.llm_signal_candidates: dict[str, dict[int, bool]] = {}
        self._prev_bucket:          Optional[int]              = None
        self._last_window_summary_printed: Optional[int]       = None
        self._lock = threading.Lock()

    # ── Token registry ──────────────────────────────────────────────
    def _register_trade_tokens(self, condition_id: str, yes_token: str, no_token: str):
        with self._lock:
            self._trade_tokens[condition_id] = (yes_token, no_token)

    def _get_trade_token(self, trade: TradeRecord) -> Optional[str]:
        tokens = self._trade_tokens.get(trade.condition_id)
        if not tokens:
            return None
        yes_tok, no_tok = tokens
        return yes_tok if trade.direction == "BUY_YES" else no_tok

    # ── Auto-sell monitor (price >= 0.99) ────────────────────────────
    async def _monitor_positions_for_auto_sell(self):
        now = time.time()
        with self._lock:
            trades_snapshot = list(self.open_trades)

        for trade in trades_snapshot:
            if trade.auto_sell_done or trade.tracking_stopped:
                continue

            market_ts  = int(trade.market_ts)
            market_end = market_ts + 900
            is_expired = now > market_end + 30

            token_id = self._get_trade_token(trade)
            if not token_id:
                continue

            current_price = await asyncio.to_thread(
                self.polymarket.get_token_price, token_id
            )

            # Auto-sell at 0.99
            if current_price >= AUTO_SELL_PRICE:
                cprint(
                    f"\n{C.BG_YELLOW}{C.BLACK}{C.BOLD}  🔔 AUTO-SELL TRIGGERED  {C.RESET}  "
                    f"{C.BOLD}{trade.symbol}{C.RESET} {trade.direction}  "
                    f"Price: {C.BGREEN}{current_price:.4f}{C.RESET}"
                )
                sell_resp = await asyncio.to_thread(
                    self.executor._place_sell_order, token_id, trade.entry_shares
                )
                pnl = round((current_price * trade.entry_shares) - trade.trade_size_usd, 4)
                print_auto_sell(trade.symbol, trade.direction, current_price, trade.entry_shares, pnl)

                with self._lock:
                    trade.auto_sell_done = True
                    trade.outcome        = "WIN" if pnl >= 0 else "LOSS"
                    trade.pnl            = pnl
                    if pnl >= 0:
                        self.stats.wins      += 1
                        self.stats.daily_pnl += pnl
                        self.stats.current_balance += current_price * trade.entry_shares
                    else:
                        self.stats.losses    += 1
                        self.stats.daily_pnl += pnl
                    self.stats.current_balance = self.executor.get_balance_usd()
                    if trade in self.open_trades:
                        self.open_trades.remove(trade)
                    self.logger.log_trade(trade, self.stats, self.open_trades)
                    DashboardWriter.add_event(
                        self.live_events,
                        f"AUTO-SELL {trade.symbol} {trade.direction} @ {current_price:.4f} | pnl=${pnl:+.4f}"
                    )
                    self.stats.balance_history.append({
                        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        "balance":   self.stats.current_balance,
                    })
                continue

            # Stop tracking clear losses after market close
            if is_expired and current_price < 0.5:
                with self._lock:
                    if not trade.tracking_stopped:
                        trade.tracking_stopped = True
                        print_tracking_stopped(trade.symbol, trade.direction)
                        DashboardWriter.add_event(
                            self.live_events,
                            f"TRACKING STOPPED {trade.symbol} {trade.direction} (loss, market closed)"
                        )
                continue

            price_color = C.BGREEN if current_price >= 0.8 else (C.BYELLOW if current_price >= 0.5 else C.BRED)
            log.info(
                "  %s👁  Monitor %s %s | price=%s%.4f%s | target=%.2f",
                C.DIM, trade.symbol, trade.direction,
                price_color, current_price, C.RESET, AUTO_SELL_PRICE
            )

    # ── SETTLE + REDEEM expired trades ───────────────────────────────
    def _settle_and_redeem_expired_trades(self, current_bucket: int):
        """
        For each trade whose 15-min window has closed:
          1. If the trade has already auto-sold, skip.
          2. If the trade expired without auto-sell, mark it as LOSS.
          3. No on-chain redemption is performed.
        """
        now          = time.time()
        settle_after = REDEEM_GRACE_SECONDS

        # Trades that are past their window close + grace period
        to_check = [
            t for t in self.open_trades
            if not t.auto_sell_done
            and not t.tracking_stopped
            and now > int(t.market_ts) + 900 + settle_after
        ]
        if not to_check:
            return

        settled = False
        for trade in to_check:
            trade.outcome = "LOSS"
            trade.pnl     = -trade.trade_size_usd
            trade.tracking_stopped = True
            with self._lock:
                self.stats.losses    += 1
                self.stats.daily_pnl -= trade.trade_size_usd
                if self.executor.client is not None:
                    self.stats.current_balance = self.executor.get_balance_usd()
                print_settlement(trade, self.stats)
                if trade in self.open_trades:
                    self.open_trades.remove(trade)
                self.logger.log_trade(trade, self.stats, self.open_trades)
                self.stats.balance_history.append({
                    "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "balance":   self.stats.current_balance,
                })
                DashboardWriter.add_event(
                    self.live_events,
                    f"LOSS {trade.symbol} {trade.direction} | pnl=${trade.pnl:+.4f}"
                )
            settled = True

        if settled:
            with self._lock:
                if not self.open_trades:
                    self.stats.daily_pnl = round(
                        self.stats.current_balance - self.stats.initial_balance, 4
                    )
                self.dashboard.write(self.stats, self.live_events, self.open_trades, self.symbol_summary)

    # ── Build snapshot for LLM ───────────────────────────────────────
    def _build_snapshot(
        self, sym_bin: str, sym_label: str, market: dict,
        window_delta: float, momentum_30s: float, vol_surge: float,
        prev_bucket: int, llm_call_number: int = 1,
    ) -> dict:
        klines_5m  = self.binance.get_klines(sym_bin, "5m", 20)
        klines_15m = self.binance.get_klines(sym_bin, "15m", 15)

        last_completed_15m = klines_15m[-2] if len(klines_15m) >= 2 else (klines_15m[-1] if klines_15m else None)
        recent_5m = klines_5m[-8:] if len(klines_5m) >= 8 else klines_5m

        if last_completed_15m:
            prev_open   = float(last_completed_15m[1])
            prev_high   = float(last_completed_15m[2])
            prev_low    = float(last_completed_15m[3])
            prev_close  = float(last_completed_15m[4])
            prev_volume = float(last_completed_15m[5])
            candle_change_pct = round((prev_close - prev_open) / prev_open * 100, 3) if prev_open != 0 else 0.0
            candle_body_pct   = round(
                abs(prev_close - prev_open) / (prev_high - prev_low) * 100, 1
            ) if (prev_high - prev_low) > 0 else 0.0
        else:
            prev_open = prev_high = prev_low = prev_close = prev_volume = 0.0
            candle_change_pct = candle_body_pct = 0.0

        return {
            "note": "Latest 15-min candle (current window). Predict NEXT 15-min window.",
            "symbol": sym_label,
            "previous_15m_candle": {
                "change_pct":       candle_change_pct,
                "body_pct_of_range": candle_body_pct,
                "volume":           round(prev_volume, 2),
                "high":             round(prev_high, 4),
                "low":              round(prev_low, 4),
                "close":            round(prev_close, 4),
            },
            "current_window": {
                "window_delta_pct": round(window_delta * 100, 4),
                "momentum_30s_pct": round(momentum_30s * 100, 4),
                "vol_surge":        round(vol_surge, 3),
            },
            "yes_price":   market["yes_price"],
            "no_price":    market["no_price"],
            "seconds_left": market["seconds_left"],
            "llm_call_number": llm_call_number,
            "recent_5m_summary": [
                {
                    "close": round(float(k[4]), 4),
                    "high":  round(float(k[2]), 4),
                    "low":   round(float(k[3]), 4),
                }
                for k in recent_5m
            ],
        }

    # ── Process one symbol per tick ──────────────────────────────────
    def _process_symbol(
        self, sym_bin: str, sym_label: str,
        current_bucket: int, current_ts: float, seconds_left: int, window_start: int
    ):
        log.info(
            "%s── %s  (window=%d, seconds_left=%d) ──%s",
            C.BBLUE, sym_label, window_start, seconds_left, C.RESET
        )
        try:
            klines_5m  = self.binance.get_klines(sym_bin, "5m", 20)
            klines_15m = self.binance.get_klines(sym_bin, "15m", 15)
            summary    = self.binance.summarize_klines(klines_5m)
            ticker     = self.binance.get_24h_ticker(sym_bin)
            cur_price  = float(klines_5m[-1][4])

            avg_vol_24h     = float(ticker.get("volume", 1))
            avg_vol_per_5min = avg_vol_24h / 288.0
            last_vol         = float(klines_5m[-1][5])
            vol_surge        = self.l1_filter.volume_surge(avg_vol_per_5min, last_vol)

            open_15m   = float(klines_15m[-1][1]) if len(klines_15m) >= 1 else cur_price
            open_15m   = open_15m if open_15m != 0 else cur_price
            win_delta = (cur_price - open_15m) / open_15m

            with self._lock:
                self.l1_filter.update_price(sym_label, cur_price)
                momentum_30s = self.l1_filter.momentum(sym_label, 30)
                should_trade, conditions = self.l1_filter.should_call_llm(
                    sym_label, win_delta, momentum_30s, vol_surge, summary["is_doji"]
                )
                self.symbol_summary[sym_label] = {
                    "current_price":    round(cur_price, 4),
                    "window_delta_pct": round(win_delta * 100, 4),
                    "momentum_30s":     round(momentum_30s * 100, 4),
                    "vol_surge":        round(vol_surge, 3),
                    "seconds_left":     seconds_left,
                }
                DashboardWriter.add_event(
                    self.live_events,
                    f"{sym_label} L1 pass={should_trade} | "
                    f"delta={win_delta*100:.4f}% mom30s={momentum_30s*100:.4f}% vol={vol_surge:.2f}",
                )

            l1_color = C.BGREEN if should_trade else C.DIM
            log.info(
                "  L1: delta=%.5f%% mom30s=%.5f%% vol=%.2f %spass=%s%s %s",
                win_delta * 100, momentum_30s * 100, vol_surge,
                l1_color, should_trade, C.RESET, conditions,
            )

            with self._lock:
                already_called = sym_label in self.llm_called.get(window_start, set())
            if seconds_left > 180:
                with self._lock:
                    self.dashboard.write(self.stats, self.live_events, self.open_trades, self.symbol_summary)
                return
            if already_called:
                log.info("  ⏭  LLM already called for %s in window %d", sym_label, window_start)
                return

            try:
                market = self.polymarket.get_current_market(sym_label, open_15m, cur_price)
            except Exception as e:
                log.error("  ❌ Polymarket fetch failed — %s", e)
                return

            self._register_trade_tokens(
                market["condition_id"], market["yes_token"], market["no_token"]
            )

            snapshot = self._build_snapshot(
                sym_bin, sym_label, market,
                win_delta, momentum_30s, vol_surge, window_start,
            )

            cprint(f"  {C.BMAGENTA}🧠 LLM call for {sym_label} (window={window_start}, left={seconds_left}s){C.RESET}")

            with self._lock:
                self.stats.llm_calls += 1

            decision = self.llm.call(snapshot, symbol=sym_label)
            if not decision:
                log.warning("  %sLLM returned no decision%s", C.BRED, C.RESET)
                return

            with self._lock:
                self.llm_called.setdefault(window_start, set()).add(sym_label)

            action     = decision.get("action", "NO_TRADE")
            confidence = int(decision.get("confidence", 0))
            entry_secs = int(decision.get("suggested_entry_seconds_left", 30))

            act_color = C.BGREEN if action == "BUY_YES" else (C.BRED if action == "BUY_NO" else C.DIM)
            log.info(
                "  %s→ %s%s conf=%d%% entry_in=%ds | %s",
                act_color, action, C.RESET,
                confidence, entry_secs, decision.get("reasoning", "")[:60],
            )

            if action != "NO_TRADE":
                next_window = window_start + 900
                pred = PendingPrediction(
                    symbol=sym_label, market_ts=next_window,
                    decision=decision, created_at=current_ts,
                )
                with self._lock:
                    self.pending_predictions = [
                        p for p in self.pending_predictions
                        if not (p.symbol == sym_label and p.market_ts == next_window)
                    ]
                    self.pending_predictions.append(pred)
                    DashboardWriter.add_event(
                        self.live_events,
                        f"QUEUED {sym_label} {action} conf={confidence}% entry_in={entry_secs}s",
                    )
            else:
                with self._lock:
                    DashboardWriter.add_event(
                        self.live_events, f"NO_TRADE {sym_label} conf={confidence}%",
                    )

            with self._lock:
                self.dashboard.write(self.stats, self.live_events, self.open_trades, self.symbol_summary)

        except requests.exceptions.RequestException as e:
            log.error("  Network error %s: %s", sym_label, e)
        except Exception as e:
            log.exception("  Error processing %s: %s", sym_label, e)

    # ── Execute pending predictions ──────────────────────────────────
    async def _execute_pending_predictions(self, current_bucket: int, current_ts: float):
        with self._lock:
            self.pending_predictions = [
                p for p in self.pending_predictions if p.market_ts >= current_bucket
            ]
            to_execute = [
                p for p in self.pending_predictions if p.market_ts == current_bucket
            ]

        if not to_execute:
            return

        seconds_into_window = int(current_ts - current_bucket)
        log.info("  Checking %d pending prediction(s) | %ds into window", len(to_execute), seconds_into_window)

        tasks = []
        for pred in to_execute:
            suggested_secs = int(pred.decision.get("suggested_entry_seconds_left", 30))
            if seconds_into_window < suggested_secs:
                log.info("  ⏳ %s waiting for %ds into window (now %ds)", pred.symbol, suggested_secs, seconds_into_window)
                continue
            tasks.append(asyncio.create_task(self._execute_pending_prediction(pred)))

        if tasks:
            await asyncio.gather(*tasks)

        with self._lock:
            self.dashboard.write(self.stats, self.live_events, self.open_trades, self.symbol_summary)

    async def _execute_pending_prediction(self, pred: PendingPrediction):
        cprint(f"  {C.BG_BLUE}{C.BWHITE}{C.BOLD}  🚀 Executing {pred.symbol} {pred.decision.get('action')}  {C.RESET}")
        try:
            record = await asyncio.to_thread(self._execute_pending_prediction_sync, pred)
            if record:
                with self._lock:
                    self.open_trades.append(record)
                    self.logger.log_trade(record, self.stats, self.open_trades)
                    DashboardWriter.add_event(
                        self.live_events,
                        f"EXECUTED {record.symbol} {record.direction} @ {record.entry_price:.4f}"
                        f" | ${record.trade_size_usd:.2f} | shares={record.entry_shares:.4f}"
                        f" | conf={record.confidence}%",
                    )
                print_trade_entry(record)
            else:
                cprint(f"  {C.BRED}🚫 Execution returned no record for {pred.symbol}{C.RESET}")
        except Exception as e:
            log.error("  Execution error %s: %s", pred.symbol, e)
        finally:
            with self._lock:
                if pred in self.pending_predictions:
                    self.pending_predictions.remove(pred)

    def _execute_pending_prediction_sync(self, pred: PendingPrediction):
        sym_bin   = "BTCUSDT" if pred.symbol == "BTC" else "ETHUSDT"
        klines_5m  = self.binance.get_klines(sym_bin, "5m", 20)
        klines_15m = self.binance.get_klines(sym_bin, "15m", 15)
        cur_price  = float(klines_5m[-1][4])
        open_15m   = float(klines_15m[-1][1]) if len(klines_15m) >= 1 else cur_price
        market     = self.polymarket.get_current_market(pred.symbol, open_15m, cur_price)
        self._register_trade_tokens(market["condition_id"], market["yes_token"], market["no_token"])
        return self.executor.execute(pred.decision, market, self.stats)

    # ── Window summary once per window change ────────────────────────
    def _maybe_print_window_summary(self, window_start: int):
        if self._last_window_summary_printed == window_start:
            return
        prev_window = window_start - 900
        if prev_window <= 0:
            self._last_window_summary_printed = window_start
            return
        with self._lock:
            print_window_summary(self.stats, list(self.open_trades), prev_window)
        self._last_window_summary_printed = window_start

    # ── IST time string ──────────────────────────────────────────────
    @staticmethod
    def _ist_time_str() -> str:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M:%S IST")

    # ── Main tick ────────────────────────────────────────────────────
    async def _tick(self):
        window_start = self.polymarket.get_current_window_ts()
        current_ts   = time.time()
        seconds_left = max(0, int(window_start + 900 - current_ts))
        ist_str      = self._ist_time_str()

        print_tick_status(self.stats, self.open_trades, seconds_left, ist_str)
        self._maybe_print_window_summary(window_start)

        await self._monitor_positions_for_auto_sell()
        self._settle_and_redeem_expired_trades(window_start)

        # Clean up stale records
        stale = [b for b in self.llm_called if b < window_start - 600]
        for b in stale:
            del self.llm_called[b]

        # Process both symbols in parallel
        tasks = [
            asyncio.create_task(
                asyncio.to_thread(
                    self._process_symbol,
                    sym_bin, sym_label, window_start, current_ts, seconds_left, window_start
                )
            )
            for sym_bin, sym_label in self.SYMBOLS
        ]
        await asyncio.gather(*tasks)

        await self._execute_pending_predictions(window_start, current_ts)

        self._prev_bucket = window_start

    # ── Run loop ─────────────────────────────────────────────────────
    async def run(self):
        cprint(
            f"\n{C.BG_BLUE}{C.BWHITE}{C.BOLD}"
            f"  🚀 Sniper Bot v5 | 24/7 | model={OPENROUTER_MODEL} "
            f"dry_run={DRY_RUN} | Auto-Redeem ON  "
            f"{C.RESET}"
        )
        cprint(
            f"  {C.BYELLOW}⚙️  Auto-sell: enabled  |  "
            f"Redeem: disabled  |  "
            f"No time restrictions{C.RESET}\n"
        )
        cprint(
            f"  {C.BCYAN}📋 TRADE EXIT INFO:{C.RESET}\n"
            f"  {C.DIM}If the open position reaches 0.99, bot auto-sells the shares.{C.RESET}\n"
            f"  {C.DIM}No on-chain redemption is performed.{C.RESET}\n"
        )
        self.logger._print_stats(self.stats, len(self.open_trades))
        self.dashboard.write(self.stats, self.live_events, self.open_trades, self.symbol_summary)

        while True:
            try:
                await self._tick()
            except KeyboardInterrupt:
                cprint(f"\n{C.BRED}Bot stopped by user.{C.RESET}")
                break
            except Exception as e:
                log.exception("Tick error: %s", e)
            await asyncio.sleep(LOOP_INTERVAL)


# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────
if __name__ == "__main__":
    if not OPENROUTER_API_KEY:
        cprint(f"{C.BRED}⚠️  Set OPENROUTER_API_KEY in your .env before running.{C.RESET}")
        sys.exit(1)
    if not OPENROUTER_API_KEY.startswith("sk-or-"):
        cprint(f"{C.BYELLOW}⚠️  OpenRouter key should start with sk-or-{C.RESET}")
        sys.exit(1)
    if not DRY_RUN and not PRIVATE_KEY:
        cprint(f"{C.BRED}⚠️  Set PRIVATE_KEY or FUNDING_PRIVATE_KEY for live trading.{C.RESET}")
        sys.exit(1)

    cprint(f"  {C.BWHITE}Live trading: {C.BGREEN if not DRY_RUN else C.BRED}{not DRY_RUN}{C.RESET}")
    asyncio.run(SniperBot().run())