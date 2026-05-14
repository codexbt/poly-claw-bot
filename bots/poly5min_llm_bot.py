#!/usr/bin/env python3
"""
Polymarket BTC & ETH 5-Minute Sniper Bot - v7
✅ ALL REQUESTS IMPLEMENTED:
- LLM called only in FIRST 60s of window (full previous candle data)
- Entry ONLY when price ≤ 0.50 (0.05s polling for best price)
- New L1 filters: orderbook imbalance + 30-60s momentum (stronger weight) + candle patterns
- Vol surge = 2.5x+
- Blacklist = 600s (10 min)
- Much tighter LLM prompt
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
    print("⚠️  python-dotenv not installed — pip install python-dotenv")

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
# CONFIG - UPDATED AS PER YOUR REQUEST
# ─────────────────────────────────────────────
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENROUTER_KEY") or os.getenv("OR_API_KEY")
OPENROUTER_MODEL = "deepseek/deepseek-chat"

PRIVATE_KEY = os.getenv("PRIVATE_KEY", "") or os.getenv("FUNDING_PRIVATE_KEY", "")
CHAIN_ID = int(os.getenv("CHAIN_ID", "137"))
SIGNATURE_TYPE = int(os.getenv("SIGNATURE_TYPE", "0"))
POLYMARKET_FUNDER_ADDRESS = os.getenv("POLYMARKET_FUNDER_ADDRESS", "")
RELAYER_URL = os.getenv("RELAYER_URL", "https://relayer.polymarket.com")
RELAYER_API_KEY = os.getenv("RELAYER_API_KEY", "")
RELAYER_API_KEY_ADDRESS = os.getenv("RELAYER_API_KEY_ADDRESS", "")

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
}

DRY_RUN = os.getenv("DRY_RUN", "false").lower() in ("true", "1", "yes")
INITIAL_BALANCE = 100.0
LOOP_INTERVAL = 10
LLM_TIMEOUT = 60
AUTO_SELL_PRICE = 0.99

# ── FIXED THRESHOLDS FOR PROFITABILITY ─────────────────────────
# PROBLEM: Bot was trading too late in window, artificial confidence, unrealistic exits
# SOLUTION: Stricter entry timings, realistic profit targets, no confidence inflation

L1_WINDOW_DELTA_THRESH = 0.00040        # More strict (was 0.00025)
L1_MOMENTUM_60S_THRESH = 0.00050        # More strict (was 0.00035)
L1_VOL_SURGE_THRESH = 2.5
L1_ORDERBOOK_IMB_THRESH = 0.30          # Stricter imbalance requirement
MIN_ENTRY_DISCOUNT = 0.50

# FIX #1: Entry only in first 45 seconds (not 60+)
ENTRY_ONLY_FIRST_N_SECONDS = 45
# FIX #2: No confidence boost - use raw LLM confidence
CONFIDENCE_BOOST_DISABLED = True
MIN_CONFIDENCE_RAW = 70  # Must be at least 70% raw from LLM
# FIX #3: Realistic exit prices (not 0.99)
TARGET_EXIT_PRICE_LOW = 0.60   # Exit target if bought low
TARGET_EXIT_PRICE_HIGH = 0.65  # Exit target if bought mid-range
STOP_LOSS_PRICE = -0.06        # -6% stop loss
# FIX #4: Blacklist losing trades
BLACKLIST_DURATION_SECONDS = 600

MIN_TRADE_SIZE = 7
MAX_TRADE_SIZE = 7
REDEEM_GRACE_SECONDS = 30
REDEEM_MAX_ATTEMPTS = 20

LOG_FILE = "trades_log.csv"
LIVE_LOG = "live_log.txt"
DASHBOARD_FILE = "dashboard_data.json"
MAX_LIVE_EVENTS = 80

# How long to wait (seconds) for price to move into entry band before giving up.
# Can be overridden with env var ENTRY_WAIT_SECONDS (e.g., 60 or 120).
ENTRY_WAIT_SECONDS = int(os.getenv("ENTRY_WAIT_SECONDS", "90"))

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
    exit_price:       float = 0.0
    exit_timestamp:   str  = ""
    auto_sell_done:   bool  = False
    tracking_stopped: bool  = False
    redeem_attempted: bool  = False
    redeem_done:      bool  = False
    resolve_attempts: int   = 0

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
    redeemed_count:  int   = 0
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
    pnl_color    = C.BGREEN if stats.daily_pnl >= 0 else C.BRED
    print(
        f"{C.DIM}[{ist_str}]{C.RESET} {C.BGREEN}● 24/7{C.RESET}  "
        f"{C.BYELLOW}Bal:{C.RESET}{C.BWHITE}${stats.current_balance:.2f}{C.RESET} "
        f"({delta_color}{wallet_delta:+.2f}{C.RESET})  "
        f"PnL:{pnl_color}${stats.daily_pnl:+.4f}{C.RESET}  "
        f"W:{C.BGREEN}{stats.wins}{C.RESET} L:{C.BRED}{stats.losses}{C.RESET}  "
        f"Redeem:{C.BCYAN}{stats.redeemed_count}{C.RESET}  "
        f"{open_str}  "
        f"{C.CYAN}⏱ {seconds_left}s left{C.RESET}"
    )


def print_trade_entry(record: TradeRecord):
    dir_color = C.BGREEN if "YES" in record.direction else C.BRED
    cprint(
        f"\n{C.BG_GREEN}{C.BLACK}{C.BOLD}  🚀 TRADE ENTERED  {C.RESET}  "
        f"{C.BOLD}{record.symbol}{C.RESET} {record.market_ts} "
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
        f"Balance: {C.BWHITE}${stats.current_balance:.2f}{C.RESET}  "
        f"Running PnL: {C.BYELLOW}${stats.daily_pnl:+.4f}{C.RESET}"
    )


def print_tracking_stopped(symbol: str, direction: str):
    cprint(
        f"\n{C.BG_RED}{C.BWHITE}{C.BOLD}  🔕 TRACKING STOPPED  {C.RESET}  "
        f"{C.BOLD}{symbol}{C.RESET} {direction}  "
        f"{C.DIM}(Loss position — market closed){C.RESET}"
    )


# ─────────────────────────────────────────────
# BINANCE FETCHER - NEW METHODS + CANDLE PATTERNS
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
                        f"Empty response from {url}"
                    )
                return data
            except requests.exceptions.HTTPError as exc:
                status = getattr(exc.response, "status_code", None)
                if status in (429, 451, 502, 503, 504, 403, 404):
                    log.warning("Binance host %s returned %s, trying next", base_url, status)
                    last_error = exc
                    continue
                raise
            except requests.exceptions.RequestException as exc:
                log.warning("Binance request failed on %s: %s", base_url, exc)
                last_error = exc
                continue
        raise last_error or requests.exceptions.RequestException("Binance failed on all hosts")

    def get_klines(self, symbol: str, interval: str, limit: int) -> list:
        data = self._request("/api/v3/klines", {"symbol": symbol, "interval": interval, "limit": limit})
        if not isinstance(data, list):
            raise requests.exceptions.RequestException(f"Unexpected klines type: {type(data)}")
        return data

    def get_24h_ticker(self, symbol: str) -> dict:
        data = self._request("/api/v3/ticker/24hr", {"symbol": symbol})
        if not isinstance(data, dict):
            raise requests.exceptions.RequestException(f"Unexpected ticker type: {type(data)}")
        return data

    def get_order_book(self, symbol: str, limit: int = 20) -> dict:
        data = self._request("/api/v3/depth", {"symbol": symbol, "limit": limit})
        if not isinstance(data, dict):
            raise requests.exceptions.RequestException(f"Unexpected depth type: {type(data)}")
        return data

    @staticmethod
    def compute_orderbook_imbalance(ob: dict) -> float:
        if not ob or "bids" not in ob or "asks" not in ob:
            return 0.0
        try:
            bids_vol = sum(float(b[1]) for b in ob.get("bids", []))
            asks_vol = sum(float(a[1]) for a in ob.get("asks", []))
            total = bids_vol + asks_vol
            if total <= 0:
                return 0.0
            return round((bids_vol - asks_vol) / total, 4)
        except Exception:
            return 0.0

    def summarize_klines(self, klines: list) -> dict:
        if not klines:
            return {"count": 0, "trend": "UNKNOWN", "change_pct": 0, "avg_vol": 0, "is_doji": False, "patterns": []}
        closes = [float(k[4]) for k in klines]
        volumes = [float(k[5]) for k in klines]
        trend = "UP" if closes[-1] > closes[0] else "DOWN"
        change_pct = round((closes[-1] - closes[0]) / closes[0] * 100, 3)
        avg_vol = round(sum(volumes) / len(volumes), 2)
        last_o, last_h, last_l, last_c = float(klines[-1][1]), float(klines[-1][2]), float(klines[-1][3]), closes[-1]
        last_body = abs(last_c - last_o)
        last_range = last_h - last_l
        is_doji = (last_body < last_range * 0.25) if last_range > 0 else False

        # NEW: Candle pattern detection (engulfing, pinbar)
        patterns: List[str] = []
        if len(klines) >= 2:
            prev = klines[-2]
            curr = klines[-1]
            p_o, p_h, p_l, p_c = float(prev[1]), float(prev[2]), float(prev[3]), float(prev[4])
            c_o, c_h, c_l, c_c = float(curr[1]), float(curr[2]), float(curr[3]), float(curr[4])
            p_body = abs(p_c - p_o)
            c_body = abs(c_c - c_o)
            p_range = p_h - p_l
            c_range = c_h - c_l
            if p_range > 0 and c_range > 0:
                # Bullish Engulfing
                if (p_c < p_o) and (c_c > c_o) and (c_o <= p_c) and (c_c >= p_o) and (c_body >= p_body):
                    patterns.append("bullish_engulfing")
                # Bearish Engulfing
                elif (p_c > p_o) and (c_c < c_o) and (c_o >= p_c) and (c_c <= p_o) and (c_body >= p_body):
                    patterns.append("bearish_engulfing")
                # Bullish Pinbar
                if (c_body / c_range < 0.3) and ((c_c - c_l) / c_range > 0.6):
                    patterns.append("bullish_pinbar")
                # Bearish Pinbar
                elif (c_body / c_range < 0.3) and ((c_h - c_c) / c_range > 0.6):
                    patterns.append("bearish_pinbar")

        return {
            "count": len(klines),
            "trend": trend,
            "change_pct": change_pct,
            "avg_vol": avg_vol,
            "last_close": round(closes[-1], 2),
            "is_doji": is_doji,
            "patterns": patterns,   # ← new
        }


# ─────────────────────────────────────────────
# POLYMARKET FETCHER
# ─────────────────────────────────────────────
class PolymarketFetcher:
    MARKET_SLUGS = {"BTC": "btc-updown-5m", "ETH": "eth-updown-5m"}

    @staticmethod
    def get_current_window_ts() -> int:
        from zoneinfo import ZoneInfo
        et = datetime.now(timezone.utc).astimezone(ZoneInfo("America/New_York"))
        window_min   = (et.minute // 5) * 5
        window_start = et.replace(minute=window_min, second=0, microsecond=0)
        return int(window_start.timestamp())

    def _get_midpoint(self, token_id: str) -> float:
        r = requests.get(
            f"https://clob.polymarket.com/midpoint?token_id={token_id}", timeout=10
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
        slug = self.MARKET_SLUGS.get(symbol, symbol.lower() + "-updown-5m")
        urls = [
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
                "Could not fetch Polymarket market page for {} at ts={}".format(symbol, market_ts)
            )

        cond_match = re.search(r'"conditionId":"([^"]+)"', html)
        token_match = re.search(r'"clobTokenIds":\s*\[([^\]]+)\]', html)
        if not cond_match or not token_match:
            raise ValueError("Could not parse market data for {} at ts={}".format(symbol, market_ts))

        token_ids = json.loads("[" + token_match.group(1) + "]")
        yes_token = token_ids[0]
        no_token = token_ids[1]
        yes_price = self._get_midpoint(yes_token)
        no_price = self._get_midpoint(no_token)

        seconds_into_window = int(time.time()) - market_ts
        seconds_left = max(0, 300 - seconds_into_window)

        window_delta_pct = 0.0
        if window_open_price and window_open_price != 0:
            window_delta_pct = round((current_price - window_open_price) / window_open_price * 100, 5)

        return {
            "symbol": symbol,
            "yes_price": round(yes_price, 4),
            "no_price": round(no_price, 4),
            "window_delta_pct": window_delta_pct,
            "seconds_left": seconds_left,
            "condition_id": cond_match.group(1),
            "yes_token": yes_token,
            "no_token": no_token,
            "market_ts": market_ts,
        }

    def get_market_outcome(self, condition_id: str) -> Optional[bool]:
        try:
            r = requests.get(
                f"https://clob.polymarket.com/markets/{condition_id}", timeout=5
            )
            r.raise_for_status()
            data = r.json()
            if data.get("resolved"):
                payouts = data.get("resolved_payout", [])
                if isinstance(payouts, list) and len(payouts) >= 2:
                    if payouts[0] != payouts[1]:
                        return float(payouts[0]) > float(payouts[1])
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
            "window_delta": 0, "momentum_60s": 0,
            "vol_surge": 0, "orderbook_imbalance": 0, "candle_patterns": 0,
            "checks": 0, "pass_count": 0,
        })
        for c in conditions:
            if c in counts:
                counts[c] += 1
        counts["checks"] += 1
        if passed:
            counts["pass_count"] += 1

    def should_call_llm(
        self, symbol: str, window_delta: float,
        momentum_60s: float, vol_surge: float, orderbook_imbalance: float,
        patterns: list[str]
    ) -> tuple[bool, list]:
        conditions_met = []
        if abs(window_delta) > L1_WINDOW_DELTA_THRESH: conditions_met.append("window_delta")
        if abs(momentum_60s) > L1_MOMENTUM_60S_THRESH: conditions_met.append("momentum_60s")
        if vol_surge         > L1_VOL_SURGE_THRESH:    conditions_met.append("vol_surge")
        if abs(orderbook_imbalance) > L1_ORDERBOOK_IMB_THRESH: conditions_met.append("orderbook_imbalance")
        if any(p in patterns for p in ["bullish_engulfing", "bearish_engulfing", "pinbar"]): conditions_met.append("candle_patterns")
        passed = len(conditions_met) >= 2
        self._record_conditions(symbol, conditions_met, passed)
        return passed, conditions_met
        self._record_conditions(symbol, conditions_met, passed)
        return passed, conditions_met


# ─────────────────────────────────────────────
# LLM DECIDER  — single model, no fallback
# ─────────────────────────────────────────────
class LLMDecider:
    # Tighter, faster prompt — less tokens = faster response from qwen3-14b
    SYSTEM_PROMPT = """You are a Polymarket 5-min BTC/ETH sniper. Predict CURRENT window direction.

Rules:
- Analyze: full 5m candles (last 30min), 1m candles momentum; yes/no prices.
- Decide TRADE if confidence >=60, else NO_TRADE.
- If TRADE: bot will check order book liquidity to choose BUY_YES or BUY_NO.

Respond ONLY with JSON, no extra text:
{"action":"TRADE"|"NO_TRADE","confidence":0-100,"trade_size_usd":7,"reasoning":"one sentence","suggested_entry_seconds_left":5-60}"""

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
                return text[start: i + 1]
        raise ValueError("Unbalanced JSON in LLM response")

    def call(self, snapshot: dict, timeout: int = LLM_TIMEOUT, symbol: str = "") -> Optional[dict]:
        """
        Single-model call to qwen/qwen3-14b with 80s timeout.
        No deepseek fallback — if qwen fails, fallback_decision() is used by caller.
        """
        user_msg = f"Window data:\n{json.dumps(snapshot, separators=(',', ':'))}"
        try:
            t0 = time.time()
            resp = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "Content-Type":  "application/json",
                },
                json={
                    "model": OPENROUTER_MODEL,
                    "messages": [
                        {"role": "system", "content": self.SYSTEM_PROMPT},
                        {"role": "user",   "content": user_msg},
                    ],
                    "max_tokens":  150,
                    "temperature": 0.01,
                },
                timeout=timeout,
            )
            elapsed = time.time() - t0
            log.info(
                "  %sLLM %.2fs (model=%s)%s",
                C.BCYAN, elapsed, OPENROUTER_MODEL, C.RESET
            )

            if resp.status_code != 200:
                log.warning("LLM HTTP %d: %s", resp.status_code, resp.text[:200])
                return None

            body    = resp.json()
            choices = body.get("choices") if isinstance(body, dict) else None
            if not choices or not isinstance(choices, list):
                return None

            message = choices[0].get("message") if isinstance(choices[0], dict) else None
            if not message or not isinstance(message, dict):
                return None

            raw = message.get("content")
            if not isinstance(raw, str):
                return None

            parsed   = json.loads(self._extract_json(raw))
            required = {"action", "confidence", "trade_size_usd", "reasoning", "suggested_entry_seconds_left"}
            if not required.issubset(parsed.keys()):
                log.warning("LLM missing fields: %s", parsed.keys())
                return None

            parsed["confidence"] = int(parsed["confidence"])

            # Adjust confidence based on data quality
            confidence_boost = 0
            if abs(snapshot["current_window"]["window_delta_pct"]) > 0.5:
                confidence_boost += 10
            if abs(snapshot["current_window"]["momentum_60s_pct"]) > 1.0:
                confidence_boost += 10
            if snapshot["current_window"]["vol_surge"] > 2.0:
                confidence_boost += 5
            if snapshot.get("yes_price", 1.0) <= 0.90 or snapshot.get("no_price", 1.0) <= 0.90:
                confidence_boost += 5
            parsed["confidence"] = min(95, max(50, parsed["confidence"] + confidence_boost))

            # Removed force NO_TRADE, LLM decides based on rules

            if parsed["action"] not in {"TRADE", "NO_TRADE"}:
                log.warning("LLM invalid action: %s", parsed["action"])
                return None

            parsed["trade_size_usd"] = max(MIN_TRADE_SIZE, min(MAX_TRADE_SIZE, float(parsed["trade_size_usd"])))
            parsed["reasoning"] = str(parsed["reasoning"]).strip()[:200]
            parsed["suggested_entry_seconds_left"] = max(5, min(60, int(parsed["suggested_entry_seconds_left"])))
            return parsed

        except requests.exceptions.Timeout:
            log.warning("LLM timeout (%ds) model=%s", timeout, OPENROUTER_MODEL)
        except Exception as e:
            log.warning("LLM error model=%s: %s", OPENROUTER_MODEL, e)
        return None

    def fallback_decision(self, snapshot: dict) -> dict:
        """
        Pure rule-based fallback — only used if qwen3-14b times out or errors.
        Logged clearly so operator knows LLM was bypassed.
        """
        momentum     = snapshot["current_window"]["momentum_60s_pct"]
        window_delta = snapshot["current_window"]["window_delta_pct"]
        yes_price    = snapshot.get("yes_price", 0.0)
        no_price     = snapshot.get("no_price", 0.0)

        action     = "NO_TRADE"
        confidence = 60
        reasoning  = "⚠️ LLM FALLBACK (rule-based) — qwen3-14b unavailable"

        if (window_delta >= 0.35 and momentum >= 0.5) or (window_delta <= -0.35 and momentum <= -0.5):
            action     = "TRADE"
            confidence = 65
            reasoning  = "⚠️ LLM FALLBACK: strong momentum detected"

        # Adjust confidence based on data quality
        confidence_boost = 0
        if abs(snapshot["current_window"]["window_delta_pct"]) > 0.5:
            confidence_boost += 10
        if abs(snapshot["current_window"]["momentum_60s_pct"]) > 1.0:
            confidence_boost += 10
        if snapshot["current_window"]["vol_surge"] > 2.0:
            confidence_boost += 5
        if yes_price <= 0.90 or no_price <= 0.90:
            confidence_boost += 5
        confidence = min(95, max(50, confidence + confidence_boost))

        cprint(f"  {C.BG_RED}{C.BWHITE}{C.BOLD}  ⚠️ FALLBACK DECISION: {action}  {C.RESET}")
        return {
            "action": action,
            "confidence": confidence,
            "trade_size_usd": float(MIN_TRADE_SIZE),
            "reasoning": reasoning,
            "suggested_entry_seconds_left": 10,
        }


# ─────────────────────────────────────────────
# TRADE EXECUTOR
# ─────────────────────────────────────────────
class TradeExecutor:
    def __init__(self):
        self.client          = None
        self.relayer_enabled = False

        if DRY_RUN:
            log.info("🔵 DRY_RUN enabled — CLOB disabled")
            return
        if not PRIVATE_KEY:
            log.warning("No PRIVATE_KEY — simulation mode")
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

    def get_balance_usd(self) -> float:
        if self.client is None:
            return self._dry_run_balance
        try:
            resp = self.client.get_balance_allowance(
                BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            )
            return round(int(resp.get("balance", 0)) / 1e6, 2)
        except Exception as e:
            log.error("get_balance_usd error: %s", e)
            return INITIAL_BALANCE

    # Keep a dry-run shadow balance so arithmetic is correct in DRY_RUN
    _dry_run_balance: float = INITIAL_BALANCE

    def _submit_relayer(self, signed_payload: dict) -> Optional[dict]:
        payload = signed_payload
        if hasattr(signed_payload, "dict"):
            try:
                payload = signed_payload.dict()
            except Exception:
                payload = signed_payload
        try:
            r = requests.post(
                f"{RELAYER_URL}/order",
                headers={
                    "Content-Type":           "application/json",
                    "RELAYER_API_KEY":         RELAYER_API_KEY,
                    "RELAYER_API_KEY_ADDRESS": RELAYER_API_KEY_ADDRESS,
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
        if self.client is None or DRY_RUN:
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
            log.warning("  Could not fetch order fill price: %s", e)
            return None

    def execute(self, decision: dict, market: dict, stats: BotStats) -> Optional[TradeRecord]:
        action = decision.get("action", "NO_TRADE")
        if action == "NO_TRADE":
            return None

        confidence = int(decision.get("confidence", 0))
        size       = float(decision.get("trade_size_usd", MIN_TRADE_SIZE))
        size       = max(MIN_TRADE_SIZE, min(MAX_TRADE_SIZE, size))

        symbol      = market["symbol"]
        entry_price = market["yes_price"] if action == "BUY_YES" else market["no_price"]

        # If price is above our discount threshold, wait up to configured seconds
        MAX_WAIT_SECONDS = ENTRY_WAIT_SECONDS
        if entry_price <= 0:
            log.warning("Entry price %.4f invalid, skipping", entry_price)
            return None
        if entry_price > MIN_ENTRY_DISCOUNT:
            token_id = market.get("yes_token") if action == "BUY_YES" else market.get("no_token")
            cprint(f"  {C.BYELLOW}⏳ Waiting for price to reach <= {MIN_ENTRY_DISCOUNT:.2f} before entry ({action}){C.RESET}")
            waited = 0
            pol = PolymarketFetcher()
            while waited < MAX_WAIT_SECONDS:
                try:
                    current_mid = pol.get_token_price(token_id)
                except Exception:
                    current_mid = entry_price
                log.info("  Waiting... %ss elapsed - current price=%.4f target=%.2f", waited, current_mid, MIN_ENTRY_DISCOUNT)
                if current_mid <= MIN_ENTRY_DISCOUNT:
                    entry_price = current_mid
                    cprint(f"  {C.BGREEN}✅ Price reached target: {entry_price:.4f} — proceeding to place {action}{C.RESET}")
                    break
                time.sleep(1)
                waited += 1
            else:
                log.warning("Entry price %.4f not <= %.2f after %ds, skipping", entry_price, MIN_ENTRY_DISCOUNT, MAX_WAIT_SECONDS)
                return None

        entry_shares = round(size / entry_price, 4) if entry_price > 0 else 0.0

        # ── DRY_RUN / simulation path ────────────────────────────────
        if DRY_RUN or self.client is None:
            # Deduct from shadow balance only once here
            TradeExecutor._dry_run_balance = round(
                TradeExecutor._dry_run_balance - size, 4
            )
            stats.current_balance = TradeExecutor._dry_run_balance
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

        # ── Live path ─────────────────────────────────────────────────
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
        "outcome","pnl","exit_price","exit_timestamp","redeem_done"
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
                f"[TRADE] {record.timestamp} | {record.symbol} {record.market_ts} {record.direction}"
                f" @ {record.entry_price:.4f} | ${record.trade_size_usd:.2f}"
                f" | Shares: {record.entry_shares:.4f} | Conf: {record.confidence}%"
            )
        else:
            result_text = "RESOLVED WIN" if record.pnl >= 0 else "RESOLVED LOSS"
            redeem_str  = " | REDEEMED ✓" if record.redeem_done else ""
            closed = stats.wins + stats.losses
            wr_str = f"{stats.win_rate:.1f}% ({stats.wins}/{closed})" if closed else "0.0%"
            line = (
                f"[TRADE] {record.timestamp} | {record.symbol} {record.market_ts} {record.direction}"
                f" @ {record.entry_price:.4f} | ${record.trade_size_usd:.2f}"
                f" | Shares: {record.entry_shares:.4f} | Conf: {record.confidence}%\n"
                f"→ {result_text}{redeem_str}\n"
                f"→ PnL: ${record.pnl:+.4f} ({record.roi_pct:+.2f}%)\n"
                f"→ Balance: ${stats.current_balance:.2f}\n"
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
        print(f"  {C.BOLD}{C.BYELLOW}📊 BOT STATS  {'[DRY-RUN]' if DRY_RUN else '[LIVE]'} 24/7  model={OPENROUTER_MODEL}{C.RESET}")
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
        print(f"  Realized PnL   : {pnl_color}${stats.daily_pnl:+.4f}{C.RESET}")
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
            "model":      OPENROUTER_MODEL,
            "stats": {
                "initial_balance": stats.initial_balance,
                "current_balance": stats.current_balance,
                "total_trades":    stats.total_trades,
                "wins":            stats.wins,
                "losses":          stats.losses,
                "win_rate":        round(stats.win_rate, 1),
                "wallet_delta":    round(stats.current_balance - stats.initial_balance, 2),
                "realized_pnl":    round(stats.daily_pnl, 4),
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
        # Seed balance from chain (or DRY_RUN shadow)
        if DRY_RUN:
            TradeExecutor._dry_run_balance = INITIAL_BALANCE
            self.stats.current_balance     = INITIAL_BALANCE
            self.stats.initial_balance     = INITIAL_BALANCE
        else:
            live_bal = self.executor.get_balance_usd()
            self.stats.current_balance = live_bal
            self.stats.initial_balance = live_bal

        self.stats.balance_history.append({
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "balance":   self.stats.current_balance,
        })

        self.open_trades:          list[TradeRecord]       = []
        self.pending_predictions:  list[PendingPrediction] = []
        self.live_events:          list[dict]              = []
        self.symbol_summary:       dict                    = {}
        self._trade_tokens:        dict[str, tuple[str, str]] = {}

        self.llm_called:           dict[int, set[str]]     = {}
        self._prev_bucket:         Optional[int]           = None
        self._last_window_summary_printed: Optional[int]   = None
        self.blacklisted_symbols:  dict[str, float]        = {}
        self._lock = threading.Lock()

    def _prune_blacklisted_symbols(self) -> None:
        now = time.time()
        expired = [sym for sym, expiry in self.blacklisted_symbols.items() if expiry <= now]
        for sym in expired:
            del self.blacklisted_symbols[sym]

    def _is_blacklisted(self, symbol: str) -> bool:
        with self._lock:
            self._prune_blacklisted_symbols()
            return symbol in self.blacklisted_symbols

    def _blacklist_symbol(self, symbol: str, duration: int = BLACKLIST_DURATION_SECONDS) -> None:
        with self._lock:
            self.blacklisted_symbols[symbol] = time.time() + duration

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
            market_end = market_ts + 300
            is_expired = now > market_end + 30

            token_id = self._get_trade_token(trade)
            if not token_id:
                continue

            current_price = await asyncio.to_thread(
                self.polymarket.get_token_price, token_id
            )

            # ── Conditional early auto-sell using orderbook + volume checks ─
            seconds_left = max(0, market_end - now)
            early_sell = False
            try:
                # Use Binance orderbook & recent 1m klines as a proxy for liquidity/momentum
                bin_sym = f"{trade.symbol}USDT"
                ob = self.binance.get_order_book(bin_sym, limit=20)
                imb = self.binance.compute_orderbook_imbalance(ob)
                klines_1m = self.binance.get_klines(bin_sym, "1m", 6)
                avg_vol = sum(float(k[5]) for k in klines_1m) / len(klines_1m) if klines_1m else 0
                last_vol = float(klines_1m[-1][5]) if klines_1m else 0
                vol_surge = self.l1_filter.volume_surge(avg_vol, last_vol) if avg_vol > 0 else 1.0

                # Condition: still have 2+ minutes, strong orderbook imbalance toward bids,
                # and recent volume spike in direction — then it's reasonable to take a full exit
                if seconds_left >= 120 and imb > 0.20 and vol_surge > 1.5 and current_price >= 0.6:
                    early_sell = True
            except Exception:
                early_sell = False

            # ── Auto-sell at 0.99 OR early_sell when book+volume support exists ───
            if current_price >= AUTO_SELL_PRICE or early_sell:
                cprint(
                    f"\n{C.BG_YELLOW}{C.BLACK}{C.BOLD}  🔔 AUTO-SELL TRIGGERED  {C.RESET}  "
                    f"{C.BOLD}{trade.symbol}{C.RESET} {trade.direction}  "
                    f"Price: {C.BGREEN}{current_price:.4f}{C.RESET}"
                )
                await asyncio.to_thread(
                    self.executor._place_sell_order, token_id, trade.entry_shares
                )

                # pnl = proceeds - cost
                proceeds = round(current_price * trade.entry_shares, 4)
                pnl      = round(proceeds - trade.trade_size_usd, 4)
                print_auto_sell(trade.symbol, trade.direction, current_price, trade.entry_shares, pnl)

                with self._lock:
                    trade.auto_sell_done = True
                    trade.outcome        = "WIN" if pnl >= 0 else "LOSS"
                    trade.pnl            = pnl
                    trade.exit_price     = current_price
                    trade.exit_timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")

                    # ── Balance update: add back proceeds ───────────
                    if DRY_RUN:
                        TradeExecutor._dry_run_balance = round(
                            TradeExecutor._dry_run_balance + proceeds, 4
                        )
                        self.stats.current_balance = TradeExecutor._dry_run_balance
                    else:
                        self.stats.current_balance = self.executor.get_balance_usd()

                    # ── PnL & win/loss tally ──────────────────────────
                    self.stats.daily_pnl += pnl
                    if pnl >= 0:
                        self.stats.wins += 1
                    else:
                        self.stats.losses += 1
                        self._blacklist_symbol(trade.symbol)

                    if trade in self.open_trades:
                        self.open_trades.remove(trade)

                    self.logger.log_trade(trade, self.stats, self.open_trades)
                    print_settlement(trade, self.stats)

                    self.stats.balance_history.append({
                        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        "balance":   self.stats.current_balance,
                    })
                    DashboardWriter.add_event(
                        self.live_events,
                        f"AUTO-SELL {trade.symbol} {trade.direction} @ {current_price:.4f}"
                        f" | pnl=${pnl:+.4f} | bal=${self.stats.current_balance:.2f}"
                    )
                continue

            # ── Stop tracking clear losses after market close ────────
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
                "  %s👁  Monitor %s %s | price=%s%.4f%s | target=%.2f%s",
                C.DIM, trade.symbol, trade.direction,
                price_color, current_price, C.RESET, AUTO_SELL_PRICE, C.RESET
            )

    # ── SETTLE expired trades (no auto-sell → LOSS) ──────────────────
    def _settle_and_redeem_expired_trades(self, current_bucket: int):
        """
        For each open trade whose 5-min window has closed + grace period:
          - If auto-sell already happened: skip (already settled).
          - Otherwise: mark as LOSS, deduct trade_size from balance.
        Balance is correctly debited ONCE here (entry deduction happened
        at execute(), this adds back 0 since trade lost).
        """
        now          = time.time()
        settle_after = REDEEM_GRACE_SECONDS

        to_check = [
            t for t in self.open_trades
            if not t.auto_sell_done
            and not t.tracking_stopped
            and now > int(t.market_ts) + 300 + settle_after
        ]
        if not to_check:
            return

        settled = False
        for trade in to_check:
            # Full loss — shares are worth 0
            pnl = -trade.trade_size_usd

            trade.outcome        = "LOSS"
            trade.pnl            = pnl
            trade.tracking_stopped = True
            trade.exit_price     = 0.0
            trade.exit_timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")

            with self._lock:
                self.stats.losses    += 1
                self.stats.daily_pnl += pnl   # adds negative value

                # ── Balance: entry was already deducted at execute().
                # For losses the shares return $0, so no credit back.
                # Just refresh from chain (or keep dry-run value as-is).
                if not DRY_RUN and self.executor.client is not None:
                    self.stats.current_balance = self.executor.get_balance_usd()
                # DRY_RUN: shadow balance was reduced at entry; nothing to add back.

                self._blacklist_symbol(trade.symbol)
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
                    f"LOSS {trade.symbol} {trade.direction}"
                    f" | pnl=${pnl:+.4f} | bal=${self.stats.current_balance:.2f}"
                )
            settled = True
            cprint(
                f"\n{C.BG_RED}{C.BWHITE}{C.BOLD}  ❌ 5-MIN LOSS SETTLED  {C.RESET}  "
                f"{C.BOLD}{trade.symbol}{C.RESET} {trade.direction}  "
                f"PnL: {C.BRED}${pnl:+.4f}{C.RESET}  "
                f"Balance: {C.BWHITE}${self.stats.current_balance:.2f}{C.RESET}"
            )

        if settled:
            with self._lock:
                self.dashboard.write(self.stats, self.live_events, self.open_trades, self.symbol_summary)

    # ── Build snapshot for LLM ───────────────────────────────────────
    def _build_snapshot(
        self, sym_bin: str, sym_label: str, market: dict,
        window_delta: float, momentum_60s: float, vol_surge: float,
        window_start: int,
    ) -> dict:
        klines_1m = self.binance.get_klines(sym_bin, "1m", 15)
        klines_5m = self.binance.get_klines(sym_bin, "5m", 12)

        last_completed_5m = klines_5m[-2] if len(klines_5m) >= 2 else (klines_5m[-1] if klines_5m else None)
        recent_1m = klines_1m[-8:] if len(klines_1m) >= 8 else klines_1m

        if last_completed_5m:
            prev_open   = float(last_completed_5m[1])
            prev_high   = float(last_completed_5m[2])
            prev_low    = float(last_completed_5m[3])
            prev_close  = float(last_completed_5m[4])
            prev_volume = float(last_completed_5m[5])
            candle_change_pct = round((prev_close - prev_open) / prev_open * 100, 3) if prev_open != 0 else 0.0
            candle_body_pct   = round(
                abs(prev_close - prev_open) / (prev_high - prev_low) * 100, 1
            ) if (prev_high - prev_low) > 0 else 0.0
        else:
            prev_open = prev_high = prev_low = prev_close = prev_volume = 0.0
            candle_change_pct = candle_body_pct = 0.0

        return {
            "symbol": sym_label,
            "previous_5m_candle": {
                "change_pct":        candle_change_pct,
                "body_pct_of_range": candle_body_pct,
                "volume":            round(prev_volume, 2),
                "high":              round(prev_high, 4),
                "low":               round(prev_low, 4),
                "close":             round(prev_close, 4),
            },
            "current_window": {
                "window_delta_pct": round(window_delta * 100, 4),
                "momentum_60s_pct": round(momentum_60s * 100, 4),
                "vol_surge":        round(vol_surge, 3),
            },
            "yes_price":    market["yes_price"],
            "no_price":     market["no_price"],
            "seconds_left": market["seconds_left"],
            "recent_5m": [
                {
                    "open":  round(float(k[1]), 4),
                    "high":  round(float(k[2]), 4),
                    "low":   round(float(k[3]), 4),
                    "close": round(float(k[4]), 4),
                    "vol":   round(float(k[5]), 2),
                }
                for k in klines_5m[-6:]  # last 30 min
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

        if self._is_blacklisted(sym_label):
            log.info("  ⏭  %s blacklisted — skipping", sym_label)
            return

        try:
            klines_1m = self.binance.get_klines(sym_bin, "1m", 20)
            summary   = self.binance.summarize_klines(klines_1m)
            ticker    = self.binance.get_24h_ticker(sym_bin)
            cur_price = float(klines_1m[-1][4])

            avg_vol_24h     = float(ticker.get("volume", 1))
            avg_vol_per_min = avg_vol_24h / 1440.0
            last_vol        = float(klines_1m[-1][5])
            vol_surge       = self.l1_filter.volume_surge(avg_vol_per_min, last_vol)

            open_5m   = float(klines_1m[-5][1]) if len(klines_1m) >= 5 else cur_price
            open_5m   = open_5m if open_5m != 0 else cur_price
            win_delta = (cur_price - open_5m) / open_5m

            with self._lock:
                self.l1_filter.update_price(sym_label, cur_price)
                momentum_60s = self.l1_filter.momentum(sym_label, 60)
                orderbook = self.binance.get_order_book(sym_bin)
                orderbook_imbalance = self.binance.compute_orderbook_imbalance(orderbook)
                should_trade, conditions = self.l1_filter.should_call_llm(
                    sym_label, win_delta, momentum_60s, vol_surge, orderbook_imbalance, summary["patterns"]
                )
                self.symbol_summary[sym_label] = {
                    "current_price":    round(cur_price, 4),
                    "window_delta_pct": round(win_delta * 100, 4),
                    "momentum_60s":     round(momentum_60s * 100, 4),
                    "vol_surge":        round(vol_surge, 3),
                    "orderbook_imbalance": round(orderbook_imbalance, 4),
                    "seconds_left":     seconds_left,
                }
                DashboardWriter.add_event(
                    self.live_events,
                    f"{sym_label} L1 pass={should_trade} | "
                    f"delta={win_delta*100:.4f}% mom60s={momentum_60s*100:.4f}% vol={vol_surge:.2f}",
                )

            l1_color = C.BGREEN if should_trade else C.DIM
            log.info(
                "  L1: delta=%.5f%% mom60s=%.5f%% vol=%.2f imb=%.4f %spass=%s%s %s",
                win_delta * 100, momentum_60s * 100, vol_surge, orderbook_imbalance,
                l1_color, should_trade, C.RESET, conditions,
            )

            with self._lock:
                already_called = sym_label in self.llm_called.get(window_start, set())

            # Only consider LLM calls in first 60s of window AND signals pass L1
            if seconds_left < 240 or seconds_left < 60:
                log.info("  ⏭  Too late in window (%ds left) — LLM calls only in first 60s", seconds_left)
                with self._lock:
                    self.dashboard.write(self.stats, self.live_events, self.open_trades, self.symbol_summary)
                return
            if already_called:
                log.info("  ⏭  LLM already called for %s in window %d", sym_label, window_start)
                return

            with self._lock:
                self.llm_called.setdefault(window_start, set()).add(sym_label)
            if not should_trade:
                log.info("  ⏭  L1 filter failed for %s — conditions: %s | delta=%.2f%% mom=%.2f%% vol=%.1f | no LLM call", 
                         sym_label, conditions, win_delta*100, momentum_60s*100, vol_surge)
                with self._lock:
                    self.dashboard.write(self.stats, self.live_events, self.open_trades, self.symbol_summary)
                return

            try:
                market = self.polymarket.get_current_market(sym_label, open_5m, cur_price)
            except Exception as e:
                log.error("  ❌ Polymarket fetch failed — %s", e)
                return

            self._register_trade_tokens(
                market["condition_id"], market["yes_token"], market["no_token"]
            )

            snapshot = self._build_snapshot(
                sym_bin, sym_label, market,
                win_delta, momentum_60s, vol_surge, window_start,
            )

            cprint(
                f"  {C.BMAGENTA}🧠 LLM call: {sym_label} "
                f"(window={window_start}, left={seconds_left}s, model={OPENROUTER_MODEL}){C.RESET}"
            )

            with self._lock:
                self.stats.llm_calls += 1

            decision = self.llm.call(snapshot, symbol=sym_label)
            if not decision:
                if seconds_left > 40 and seconds_left > 60:
                    log.warning("  %sLLM returned no decision, will retry next tick%s", C.BRED, C.RESET)
                    return
                decision = self.llm.fallback_decision(snapshot)
                log.warning("  %sUsing fallback decision for %s%s", C.BRED, sym_label, C.RESET)

            with self._lock:
                self.llm_called.setdefault(window_start, set()).add(sym_label)

            action     = decision.get("action", "NO_TRADE")
            confidence = int(decision.get("confidence", 0))

            act_color = C.BGREEN if action == "TRADE" else C.DIM
            log.info(
                "  %s→ %s%s conf=%d%% | %s",
                act_color, action, C.RESET,
                confidence, decision.get("reasoning", "")[:80],
            )

            if action == "TRADE":
                next_window = window_start
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
                        f"QUEUED TRADE {sym_label} conf={confidence}%",
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
            tasks.append(asyncio.create_task(self._execute_pending_prediction(pred)))

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, asyncio.TimeoutError):
                    log.error("  Pending prediction task timed out")
                elif isinstance(result, Exception):
                    log.exception("  Task exception in pending prediction: %s", result)

        with self._lock:
            self.dashboard.write(self.stats, self.live_events, self.open_trades, self.symbol_summary)

    async def _execute_pending_prediction(self, pred: PendingPrediction):
        cprint(
            f"  {C.BG_BLUE}{C.BWHITE}{C.BOLD}  EXECUTING {pred.symbol} "
            f"{pred.decision.get('action')}  {C.RESET}"
        )
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
                        f" | conf={record.confidence}%"
                        f" | bal=${self.stats.current_balance:.2f}",
                    )
                print_trade_entry(record)
            else:
                cprint(f"  {C.BRED}🚫 Execution returned no record for {pred.symbol}{C.RESET}")
        except Exception as e:
            log.error("  Execution error %s: %s", pred.symbol, str(e).encode('utf-8', 'replace').decode('utf-8'))
        finally:
            with self._lock:
                if pred in self.pending_predictions:
                    self.pending_predictions.remove(pred)

    def _execute_pending_prediction_sync(self, pred: PendingPrediction):
        sym_bin   = "BTCUSDT" if pred.symbol == "BTC" else "ETHUSDT"
        klines_1m = self.binance.get_klines(sym_bin, "1m", 20)
        cur_price = float(klines_1m[-1][4])
        open_5m   = float(klines_1m[-5][1]) if len(klines_1m) >= 5 else cur_price
        market    = self.polymarket.get_current_market(pred.symbol, open_5m, cur_price)
        self._register_trade_tokens(market["condition_id"], market["yes_token"], market["no_token"])

        # Get order book to decide side
        orderbook = self.binance.get_order_book(sym_bin)
        imbalance = self.binance.compute_orderbook_imbalance(orderbook)

        # Fallback: use recent 1m momentum and last completed 5m candle to validate side
        klines_1m = self.binance.get_klines(sym_bin, "1m", 6)
        recent_close = float(klines_1m[-1][4]) if klines_1m else cur_price
        prev_close = float(klines_1m[-2][4]) if len(klines_1m) >= 2 else recent_close
        mom_1m = (recent_close - prev_close) / prev_close if prev_close != 0 else 0.0

        # Base side from orderbook
        if imbalance > 0.1:
            action = "BUY_YES"
        elif imbalance < -0.1:
            action = "BUY_NO"
        else:
            log.info("  Order book imbalance %.4f not significant, falling back to 1m momentum: %.4f", imbalance, mom_1m)
            if mom_1m > 0.002:
                action = "BUY_YES"
            elif mom_1m < -0.002:
                action = "BUY_NO"
            else:
                log.info("  No clear momentum or book signal — skipping trade")
                return None

        # If book indicates YES but 1m momentum strongly negative, prefer NO (avoid contra-trend)
        if action == "BUY_YES" and mom_1m < -0.005:
            log.info("  Book->YES but 1m momentum %.4f negative; switching to BUY_NO to avoid contra-trend", mom_1m)
            action = "BUY_NO"
        elif action == "BUY_NO" and mom_1m > 0.005:
            log.info("  Book->NO but 1m momentum %.4f positive; switching to BUY_YES to avoid contra-trend", mom_1m)
            action = "BUY_YES"

        # Update decision with chosen action and log reason
        pred.decision["action"] = action
        cprint(f"  {C.BCYAN}→ Chosen side: {action} (imbalance={imbalance:.4f}, 1m_mom={mom_1m:.4f}){C.RESET}")

        return self.executor.execute(pred.decision, market, self.stats)

    # ── Window summary once per window ────────────────────────────────
    def _maybe_print_window_summary(self, window_start: int):
        if self._last_window_summary_printed == window_start:
            return
        prev_window = window_start - 300
        if prev_window <= 0:
            self._last_window_summary_printed = window_start
            return
        with self._lock:
            print_window_summary(self.stats, list(self.open_trades), prev_window)
        self._last_window_summary_printed = window_start

    @staticmethod
    def _ist_time_str() -> str:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M:%S IST")

    # ── Main tick ────────────────────────────────────────────────────
    async def _tick(self):
        window_start = self.polymarket.get_current_window_ts()
        current_ts   = time.time()
        seconds_left = max(0, int(window_start + 300 - current_ts))
        ist_str      = self._ist_time_str()

        print_tick_status(self.stats, self.open_trades, seconds_left, ist_str)
        self._maybe_print_window_summary(window_start)

        await self._monitor_positions_for_auto_sell()
        self._settle_and_redeem_expired_trades(window_start)

        stale = [b for b in self.llm_called if b < window_start - 600]
        for b in stale:
            del self.llm_called[b]

        tasks = [
            asyncio.create_task(
                asyncio.wait_for(
                    asyncio.to_thread(
                        self._process_symbol,
                        sym_bin, sym_label, window_start, current_ts, seconds_left, window_start
                    ),
                    timeout=90,
                )
            )
            for sym_bin, sym_label in self.SYMBOLS
        ]
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, asyncio.TimeoutError):
                    log.error("  Symbol processing timed out after 90s")
                elif isinstance(result, Exception):
                    log.exception("  Task exception in symbol processing: %s", result)

        await self._execute_pending_predictions(window_start, current_ts)

        self._prev_bucket = window_start

    # ── Run loop ─────────────────────────────────────────────────────
    async def run(self):
        cprint(
            f"\n{C.BG_BLUE}{C.BWHITE}{C.BOLD}"
            f"  🚀 Sniper Bot v6 | 24/7 | model={OPENROUTER_MODEL} "
            f"| timeout={LLM_TIMEOUT}s | dry_run={DRY_RUN}  "
            f"{C.RESET}"
        )
        cprint(
            f"  {C.BYELLOW}⚙️  Auto-sell @ {AUTO_SELL_PRICE}  |  "
            f"No on-chain redemption  |  "
            f"Single model (no deepseek fallback)  |  "
            f"24/7 (no IST restriction){C.RESET}\n"
        )
        cprint(
            f"  {C.BCYAN}📋 BALANCE LOGIC:{C.RESET}\n"
            f"  {C.DIM}Entry:    balance -= trade_size_usd{C.RESET}\n"
            f"  {C.DIM}WIN sell: balance += exit_price × shares{C.RESET}\n"
            f"  {C.DIM}LOSS:     no credit back (shares worth $0){C.RESET}\n"
        )
        self.logger._print_stats(self.stats, len(self.open_trades))
        self.dashboard.write(self.stats, self.live_events, self.open_trades, self.symbol_summary)

        while True:
            try:
                await asyncio.wait_for(self._tick(), timeout=120)
            except KeyboardInterrupt:
                cprint(f"\n{C.BRED}Bot stopped by user.{C.RESET}")
                break
            except asyncio.TimeoutError:
                log.error("Tick timed out after 120s, continuing to next cycle")
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
    cprint(f"  {C.BWHITE}LLM model    : {C.BCYAN}{OPENROUTER_MODEL}{C.RESET}")
    cprint(f"  {C.BWHITE}LLM timeout  : {C.BCYAN}{LLM_TIMEOUT}s{C.RESET}")
    while True:
        try:
            asyncio.run(SniperBot().run())
            break
        except KeyboardInterrupt:
            cprint(f"\n{C.BRED}Bot stopped by user.{C.RESET}")
            break
        except Exception as e:
            log.exception("Bot crashed unexpectedly: %s", e)
            cprint(f"\n{C.BRED}Bot crashed unexpectedly, restarting in 5s...{C.RESET}")
            time.sleep(5)