#!/usr/bin/env python3
"""
Polymarket BTC & ETH 5-Minute Sniper Bot - v8 (VOLUME REVERSAL EDITION)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✅ NO LLM — Pure volume + candle reversal logic
✅ 1-second polling in last 2 minutes of each window
✅ Volume surge + candle body reversal + orderbook confirmation
✅ Instant BUY YES / BUY NO on confirmed signal
✅ Blacklist losing symbols for 10 minutes
✅ Auto-sell at 0.99
# Sniper bot volume reversal - 2026-03-05
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
from collections import deque
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from typing import Optional, List, Tuple
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
    RESET    = "\033[0m";  BOLD     = "\033[1m";  DIM      = "\033[2m"
    RED      = "\033[31m"; GREEN    = "\033[32m"; YELLOW   = "\033[33m"
    BLUE     = "\033[34m"; MAGENTA  = "\033[35m"; CYAN     = "\033[36m"
    BRED     = "\033[91m"; BGREEN   = "\033[92m"; BYELLOW  = "\033[93m"
    BBLUE    = "\033[94m"; BMAGENTA = "\033[95m"; BCYAN    = "\033[96m"
    BWHITE   = "\033[97m"; WHITE    = "\033[37m"
    BG_BLACK = "\033[40m"; BG_RED   = "\033[41m"; BG_GREEN = "\033[42m"
    BG_YELLOW= "\033[43m"; BG_BLUE  = "\033[44m"; BG_CYAN  = "\033[46m"

def cprint(msg: str):
    print(msg + C.RESET)

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
PRIVATE_KEY              = os.getenv("PRIVATE_KEY", "") or os.getenv("FUNDING_PRIVATE_KEY", "")
CHAIN_ID                 = int(os.getenv("CHAIN_ID", "137"))
SIGNATURE_TYPE           = int(os.getenv("SIGNATURE_TYPE", "0"))
POLYMARKET_FUNDER_ADDRESS= os.getenv("POLYMARKET_FUNDER_ADDRESS", "")
RELAYER_URL              = os.getenv("RELAYER_URL", "https://relayer.polymarket.com")
RELAYER_API_KEY          = os.getenv("RELAYER_API_KEY", "")
RELAYER_API_KEY_ADDRESS  = os.getenv("RELAYER_API_KEY_ADDRESS", "")

DRY_RUN          = os.getenv("DRY_RUN", "false").lower() in ("true", "1", "yes")
INITIAL_BALANCE  = 100.0
AUTO_SELL_PRICE  = 0.99
MIN_ENTRY_DISCOUNT = 0.50   # Entry only if price <= 0.50
MIN_TRADE_SIZE   = 7.0
MAX_TRADE_SIZE   = 7.0
LOG_FILE         = "trades_log_v8.csv"
LIVE_LOG         = "live_log_v8.txt"
DASHBOARD_FILE   = "dashboard_data_v8.json"
MAX_LIVE_EVENTS  = 80
BLACKLIST_SECONDS= 600      # 10 min blacklist after loss
REDEEM_GRACE_SECONDS = 30

BINANCE_BASES = [
    "https://api.binance.com",
    "https://api1.binance.com",
    "https://api2.binance.com",
    "https://api3.binance.com",
    "https://api.binance.us",
]
BINANCE_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0.0.0",
    "Accept-Language": "en-US,en;q=0.9",
}

# ── REVERSAL DETECTION PARAMETERS ──────────────────────────────────
# These are the core signals. Tune as needed.

# 1. Volume surge: last candle volume must be >= this multiple of recent average
VOL_SURGE_MIN = 1.8          # 1.8x average = significant spike

# 2. Candle body reversal: current candle must be opposite to previous trend
#    and body must be >= this fraction of its range (strong candle, not doji)
CANDLE_BODY_MIN_PCT = 0.40   # 40% body = meaningful reversal candle

# 3. Minimum price move in reversal direction (% of current price)
REVERSAL_MOVE_MIN_PCT = 0.05 # 0.05% minimum move in new direction

# 4. Orderbook must confirm direction (imbalance > this for YES, < -this for NO)
OB_CONFIRM_THRESHOLD = 0.10  # 10% net bias in orderbook

# 5. Momentum confirmation: last 30s price momentum must align
MOM_ALIGN_MIN = 0.0001       # tiny positive/negative is enough to confirm

# 6. Previous trend: how many candles should be in opposite direction before reversal
PREV_TREND_CANDLES = 2       # need 2 prior candles in same direction before reversal counts

# 7. Last N minutes to watch for reversal signals
REVERSAL_WATCH_WINDOW_SECONDS = 120  # Last 2 minutes of each 5-min window

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
    redeem_done:      bool  = False

    @property
    def roi_pct(self) -> float:
        if self.trade_size_usd:
            return round((self.pnl / self.trade_size_usd) * 100, 2)
        return 0.0


@dataclass
class BotStats:
    initial_balance: float = INITIAL_BALANCE
    current_balance: float = INITIAL_BALANCE
    total_trades:    int   = 0
    wins:            int   = 0
    losses:          int   = 0
    daily_spent:     float = 0.0
    daily_pnl:       float = 0.0
    redeemed_count:  int   = 0
    balance_history: list  = field(default_factory=list)
    signal_history:  list  = field(default_factory=list)   # for stats

    @property
    def win_rate(self):
        closed = self.wins + self.losses
        return (self.wins / closed * 100) if closed else 0.0


# ─────────────────────────────────────────────
# REVERSAL SIGNAL RESULT
# ─────────────────────────────────────────────
@dataclass
class ReversalSignal:
    direction:   str    # "BUY_YES" or "BUY_NO"
    confidence:  int    # 0-100 rule-based score
    reasoning:   str
    vol_surge:   float
    ob_imbalance:float
    candle_body: float


# ─────────────────────────────────────────────
# COLORFUL DISPLAY FUNCTIONS
# ─────────────────────────────────────────────
def print_tick_status(stats: BotStats, open_trades: list, seconds_left: int, ist_str: str, mode: str):
    wallet_delta = stats.current_balance - stats.initial_balance
    delta_color  = C.BGREEN if wallet_delta >= 0 else C.BRED
    open_str     = f"{C.BMAGENTA}{len(open_trades)} open{C.RESET}" if open_trades else f"{C.DIM}no open{C.RESET}"
    pnl_color    = C.BGREEN if stats.daily_pnl >= 0 else C.BRED
    watch_color  = C.BRED if seconds_left <= 120 else C.DIM
    watch_label  = f"{C.BRED}🔴 WATCH MODE{C.RESET}" if seconds_left <= 120 else f"{C.DIM}⏸ waiting{C.RESET}"
    print(
        f"{C.DIM}[{ist_str}]{C.RESET} {C.BGREEN}●{C.RESET} {watch_label}  "
        f"{C.BYELLOW}Bal:{C.RESET}{C.BWHITE}${stats.current_balance:.2f}{C.RESET} "
        f"({delta_color}{wallet_delta:+.2f}{C.RESET})  "
        f"PnL:{pnl_color}${stats.daily_pnl:+.4f}{C.RESET}  "
        f"W:{C.BGREEN}{stats.wins}{C.RESET} L:{C.BRED}{stats.losses}{C.RESET}  "
        f"{open_str}  "
        f"{watch_color}⏱ {seconds_left}s left{C.RESET}"
    )


def print_signal(signal: ReversalSignal, symbol: str, seconds_left: int):
    dir_color = C.BGREEN if "YES" in signal.direction else C.BRED
    cprint(
        f"\n{C.BG_CYAN}{C.BLACK}{C.BOLD}  📡 REVERSAL SIGNAL  {C.RESET}  "
        f"{C.BOLD}{symbol}{C.RESET}  {dir_color}{signal.direction}{C.RESET}  "
        f"Conf:{C.BYELLOW}{signal.confidence}%{C.RESET}  "
        f"Vol:{C.BCYAN}{signal.vol_surge:.2f}x{C.RESET}  "
        f"OB:{C.BMAGENTA}{signal.ob_imbalance:+.4f}{C.RESET}  "
        f"Body:{C.BWHITE}{signal.candle_body:.1f}%{C.RESET}  "
        f"{C.DIM}{signal.reasoning}{C.RESET}  "
        f"⏱{C.BRED}{seconds_left}s{C.RESET}"
    )


def print_trade_entry(record: TradeRecord):
    dir_color = C.BGREEN if "YES" in record.direction else C.BRED
    cprint(
        f"\n{C.BG_GREEN}{C.BLACK}{C.BOLD}  🚀 TRADE ENTERED  {C.RESET}  "
        f"{C.BOLD}{record.symbol}{C.RESET} {record.market_ts} "
        f"{dir_color}{record.direction}{C.RESET}  "
        f"@ {C.BWHITE}{record.entry_price:.4f}{C.RESET}  "
        f"${record.trade_size_usd:.2f}  "
        f"Shares:{C.BYELLOW}{record.entry_shares:.4f}{C.RESET}  "
        f"Conf:{C.BCYAN}{record.confidence}%{C.RESET}"
    )


def print_settlement(record: TradeRecord, stats: BotStats):
    bg    = C.BG_GREEN if record.outcome == "WIN" else C.BG_RED
    label = "✅ WIN" if record.outcome == "WIN" else "❌ LOSS"
    pnl_c = C.BGREEN if record.pnl >= 0 else C.BRED
    cprint(
        f"\n{bg}{C.BLACK}{C.BOLD}  {label}  {C.RESET}  "
        f"{C.BOLD}{record.symbol}{C.RESET} {record.direction}  "
        f"PnL:{pnl_c}${record.pnl:+.4f} ({record.roi_pct:+.2f}%){C.RESET}  "
        f"Balance:{C.BWHITE}${stats.current_balance:.2f}{C.RESET}  "
        f"Running:{C.BYELLOW}${stats.daily_pnl:+.4f}{C.RESET}"
    )


def print_window_summary(stats: BotStats, open_trades: list, window_ts: int):
    bar = "━" * 62
    try:
        from zoneinfo import ZoneInfo
        IST = ZoneInfo("Asia/Kolkata")
        window_dt = datetime.fromtimestamp(window_ts, tz=IST).strftime("%d %b %Y  %I:%M %p IST")
    except Exception:
        window_dt = str(window_ts)
    wallet_delta = stats.current_balance - stats.initial_balance
    delta_color  = C.BGREEN if wallet_delta >= 0 else C.BRED
    pnl_color    = C.BGREEN if stats.daily_pnl >= 0 else C.BRED
    wr_color     = C.BGREEN if stats.win_rate >= 50 else C.BRED

    print(f"\n{C.BCYAN}{C.BOLD}{bar}{C.RESET}")
    print(f"  {C.BG_BLUE}{C.BWHITE}{C.BOLD}  📊  5-MIN WINDOW: {window_dt}  {C.RESET}")
    print(f"  {C.BYELLOW}💰 Balance{C.RESET}  "
          f"Start:{C.WHITE}${stats.initial_balance:.2f}{C.RESET}  "
          f"Now:{C.BWHITE}${stats.current_balance:.2f}{C.RESET}  "
          f"Δ:{delta_color}${wallet_delta:+.2f}{C.RESET}")
    print(f"  {C.BYELLOW}📈 PnL:{C.RESET}{pnl_color}${stats.daily_pnl:+.4f}{C.RESET}  "
          f"{C.DIM}Spent:${stats.daily_spent:.2f}{C.RESET}")
    print(f"  {C.BYELLOW}🎯 Trades:{C.RESET}{C.BWHITE}{stats.total_trades}{C.RESET}  "
          f"{C.BGREEN}W:{stats.wins}{C.RESET}  {C.BRED}L:{stats.losses}{C.RESET}  "
          f"WR:{wr_color}{stats.win_rate:.1f}%{C.RESET}")
    print(f"{C.BCYAN}{bar}{C.RESET}\n")


# ─────────────────────────────────────────────
# BINANCE FETCHER
# ─────────────────────────────────────────────
class BinanceFetcher:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(BINANCE_HEADERS)
        self.session.trust_env = False

    def _request(self, path: str, params: dict) -> any:
        last_error = None
        for base in BINANCE_BASES:
            url = f"{base}{path}"
            try:
                r = self.session.get(url, params=params, timeout=5)
                r.raise_for_status()
                data = r.json()
                if not data:
                    raise requests.exceptions.RequestException("Empty response")
                return data
            except requests.exceptions.HTTPError as exc:
                status = getattr(exc.response, "status_code", None)
                if status in (429, 451, 502, 503, 504, 403, 404):
                    last_error = exc
                    continue
                raise
            except requests.exceptions.RequestException as exc:
                last_error = exc
                continue
        raise last_error or requests.exceptions.RequestException("All Binance hosts failed")

    def get_klines(self, symbol: str, interval: str, limit: int) -> list:
        data = self._request("/api/v3/klines", {"symbol": symbol, "interval": interval, "limit": limit})
        if not isinstance(data, list):
            raise ValueError(f"Unexpected klines type: {type(data)}")
        return data

    def get_order_book(self, symbol: str, limit: int = 20) -> dict:
        data = self._request("/api/v3/depth", {"symbol": symbol, "limit": limit})
        if not isinstance(data, dict):
            raise ValueError(f"Unexpected depth type: {type(data)}")
        return data

    def get_ticker_24h(self, symbol: str) -> dict:
        data = self._request("/api/v3/ticker/24hr", {"symbol": symbol})
        if not isinstance(data, dict):
            raise ValueError(f"Unexpected ticker type: {type(data)}")
        return data

    @staticmethod
    def compute_ob_imbalance(ob: dict) -> float:
        try:
            bids_vol = sum(float(b[1]) for b in ob.get("bids", []))
            asks_vol = sum(float(a[1]) for a in ob.get("asks", []))
            total = bids_vol + asks_vol
            if total <= 0:
                return 0.0
            return round((bids_vol - asks_vol) / total, 4)
        except Exception:
            return 0.0


# ─────────────────────────────────────────────
# VOLUME REVERSAL DETECTOR
# ─────────────────────────────────────────────
class VolumeReversalDetector:
    """
    Core logic — no LLM, pure price/volume analysis.

    Detects genuine candle reversals backed by volume surge, then
    cross-checks with live orderbook.

    Decision tree:
      1. Get last N 1-minute candles (up to 6)
      2. Check if previous 2+ candles are in the SAME direction (trend established)
      3. Check if latest candle is OPPOSITE direction with strong body (reversal candle)
      4. Check if latest candle volume >= VOL_SURGE_MIN × average of previous candles
      5. Check if orderbook imbalance confirms the reversal direction
      6. If ALL pass → emit ReversalSignal
    """

    def __init__(self):
        # Track price history per symbol for momentum
        self._price_hist: dict[str, deque] = {}

    def update_price(self, symbol: str, price: float):
        if symbol not in self._price_hist:
            self._price_hist[symbol] = deque(maxlen=120)  # 2 min of 1s samples
        self._price_hist[symbol].append((time.time(), price))

    def momentum_30s(self, symbol: str) -> float:
        hist = self._price_hist.get(symbol)
        if not hist:
            return 0.0
        now    = time.time()
        cutoff = now - 30
        past   = [p for t, p in hist if t <= cutoff]
        if not past or not hist:
            return 0.0
        return (hist[-1][1] - past[-1]) / past[-1]

    @staticmethod
    def _candle_direction(open_: float, close_: float) -> int:
        """1 = bullish, -1 = bearish, 0 = doji"""
        if close_ > open_ * 1.00005:
            return 1
        elif close_ < open_ * 0.99995:
            return -1
        return 0

    @staticmethod
    def _body_pct(open_: float, close_: float, high_: float, low_: float) -> float:
        rng = high_ - low_
        if rng <= 0:
            return 0.0
        return round(abs(close_ - open_) / rng * 100, 1)

    def detect(
        self,
        klines_1m: list,
        ob: dict,
        symbol: str,
        ticker_24h: dict,
    ) -> Optional[ReversalSignal]:
        """
        Main detection function. Returns ReversalSignal or None.

        klines_1m: list of Binance 1m kline arrays, most recent last
        ob: order book dict
        symbol: e.g. "BTC"
        """
        if len(klines_1m) < PREV_TREND_CANDLES + 1:
            return None

        # Parse candles (last N+1)
        candles = klines_1m[-(PREV_TREND_CANDLES + 2):]

        def parse(k):
            o = float(k[1]); h = float(k[2])
            l = float(k[3]); c = float(k[4]); v = float(k[5])
            return o, h, l, c, v

        parsed = [parse(k) for k in candles]

        current   = parsed[-1]            # latest (possibly incomplete) candle
        prev_ones = parsed[:-1]           # previous candles

        c_o, c_h, c_l, c_c, c_v = current
        curr_dir  = self._candle_direction(c_o, c_c)
        curr_body = self._body_pct(c_o, c_c, c_h, c_l)

        if curr_dir == 0:
            log.debug("  [REV] Current candle is doji — skip")
            return None

        # ── 1. Check previous trend ──────────────────────────────────
        prev_dirs = [self._candle_direction(p[0], p[3]) for p in prev_ones]
        # Need at least PREV_TREND_CANDLES going the OPPOSITE direction to current
        prev_trend_dir = -curr_dir  # if current is bullish, trend was bearish
        trend_count = sum(1 for d in prev_dirs if d == prev_trend_dir)
        if trend_count < PREV_TREND_CANDLES:
            log.debug(
                "  [REV] %s: need %d prior %s candles, got %d",
                symbol, PREV_TREND_CANDLES,
                "bearish" if prev_trend_dir == -1 else "bullish",
                trend_count
            )
            return None

        # ── 2. Check current candle body strength ────────────────────
        if curr_body < CANDLE_BODY_MIN_PCT * 100:
            log.debug("  [REV] %s: body %.1f%% < %.1f%% threshold", symbol, curr_body, CANDLE_BODY_MIN_PCT * 100)
            return None

        # ── 3. Check price move magnitude ────────────────────────────
        move_pct = abs(c_c - c_o) / c_o * 100
        if move_pct < REVERSAL_MOVE_MIN_PCT:
            log.debug("  [REV] %s: move %.4f%% too small", symbol, move_pct)
            return None

        # ── 4. Volume surge ──────────────────────────────────────────
        prev_vols = [p[4] for p in prev_ones]
        avg_vol   = sum(prev_vols) / len(prev_vols) if prev_vols else 1.0
        vol_surge = c_v / avg_vol if avg_vol > 0 else 1.0

        if vol_surge < VOL_SURGE_MIN:
            log.debug("  [REV] %s: vol surge %.2fx < %.2fx threshold", symbol, vol_surge, VOL_SURGE_MIN)
            return None

        # ── 5. Orderbook confirmation ────────────────────────────────
        ob_imbalance = BinanceFetcher.compute_ob_imbalance(ob)
        expected_ob  = 1 if curr_dir == 1 else -1   # bullish needs bids > asks

        if expected_ob == 1 and ob_imbalance < OB_CONFIRM_THRESHOLD:
            log.debug("  [REV] %s: OB imbalance %.4f < %.4f (need bullish book)", symbol, ob_imbalance, OB_CONFIRM_THRESHOLD)
            return None
        if expected_ob == -1 and ob_imbalance > -OB_CONFIRM_THRESHOLD:
            log.debug("  [REV] %s: OB imbalance %.4f > %.4f (need bearish book)", symbol, ob_imbalance, -OB_CONFIRM_THRESHOLD)
            return None

        # ── 6. Momentum alignment (30s price momentum) ───────────────
        mom = self.momentum_30s(symbol)
        if curr_dir == 1 and mom < -MOM_ALIGN_MIN:
            log.debug("  [REV] %s: momentum %.6f contradicts bullish signal", symbol, mom)
            return None
        if curr_dir == -1 and mom > MOM_ALIGN_MIN:
            log.debug("  [REV] %s: momentum %.6f contradicts bearish signal", symbol, mom)
            return None

        # ── ALL CHECKS PASSED — build signal ─────────────────────────
        direction = "BUY_YES" if curr_dir == 1 else "BUY_NO"

        # Confidence score: 60 base + bonuses
        conf = 60
        conf += min(15, int((vol_surge - VOL_SURGE_MIN) * 10))       # vol bonus
        conf += min(10, int(abs(ob_imbalance) * 40))                  # ob bonus
        conf += min(10, int((curr_body - CANDLE_BODY_MIN_PCT * 100) / 5))  # body bonus
        conf += min(5, int(trend_count - PREV_TREND_CANDLES) * 5)    # extra trend bonus
        conf = max(60, min(95, conf))

        reason = (
            f"{trend_count} {'bearish' if curr_dir==1 else 'bullish'} candles → "
            f"{'bullish' if curr_dir==1 else 'bearish'} reversal | "
            f"vol={vol_surge:.2f}x | body={curr_body:.1f}% | ob={ob_imbalance:+.4f} | "
            f"move={move_pct:.3f}%"
        )

        return ReversalSignal(
            direction=direction,
            confidence=conf,
            reasoning=reason,
            vol_surge=round(vol_surge, 3),
            ob_imbalance=round(ob_imbalance, 4),
            candle_body=round(curr_body, 1),
        )


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
            f"https://clob.polymarket.com/midpoint?token_id={token_id}",
            timeout=8
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
            f"https://polymarket.com/event/{slug}-{market_ts}",
            f"https://www.polymarket.com/event/{slug}-{market_ts}",
        ]

        html = None
        for attempt in range(1, 4):
            for url in urls:
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
            raise ConnectionError(f"Could not fetch Polymarket market for {symbol}")

        cond_match  = re.search(r'"conditionId":"([^"]+)"', html)
        token_match = re.search(r'"clobTokenIds":\s*\[([^\]]+)\]', html)
        if not cond_match or not token_match:
            raise ValueError(f"Could not parse market data for {symbol}")

        token_ids = json.loads("[" + token_match.group(1) + "]")
        yes_token = token_ids[0]
        no_token  = token_ids[1]
        yes_price = self._get_midpoint(yes_token)
        no_price  = self._get_midpoint(no_token)

        seconds_into_window = int(time.time()) - market_ts
        seconds_left = max(0, 300 - seconds_into_window)

        return {
            "symbol":       symbol,
            "yes_price":    round(yes_price, 4),
            "no_price":     round(no_price, 4),
            "seconds_left": seconds_left,
            "condition_id": cond_match.group(1),
            "yes_token":    yes_token,
            "no_token":     no_token,
            "market_ts":    market_ts,
        }

    def get_market_outcome(self, condition_id: str) -> Optional[bool]:
        try:
            r = requests.get(
                f"https://clob.polymarket.com/markets/{condition_id}", timeout=5
            )
            r.raise_for_status()
            data = r.json()
            yes_winner = False; no_winner = False
            for token in data.get("tokens", []):
                outcome = token.get("outcome", "").upper()
                if token.get("winner"):
                    if outcome == "YES": yes_winner = True
                    elif outcome == "NO": no_winner  = True
            if yes_winner and not no_winner: return True
            if no_winner and not yes_winner: return False
            return None
        except Exception as e:
            log.warning("get_market_outcome error %s: %s", condition_id, e)
            return None


# ─────────────────────────────────────────────
# TRADE EXECUTOR
# ─────────────────────────────────────────────
class TradeExecutor:
    _dry_run_balance: float = INITIAL_BALANCE

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
                key=PRIVATE_KEY, chain_id=CHAIN_ID,
                signature_type=SIGNATURE_TYPE, funder=POLYMARKET_FUNDER_ADDRESS,
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

    def _submit_relayer(self, signed_payload: dict) -> Optional[dict]:
        payload = signed_payload
        if hasattr(signed_payload, "dict"):
            try: payload = signed_payload.dict()
            except: payload = signed_payload
        try:
            r = requests.post(
                f"{RELAYER_URL}/order",
                headers={
                    "Content-Type": "application/json",
                    "RELAYER_API_KEY": RELAYER_API_KEY,
                    "RELAYER_API_KEY_ADDRESS": RELAYER_API_KEY_ADDRESS,
                },
                json=payload, timeout=15,
            )
            r.raise_for_status()
            data = r.json()
            log.info("  ✅ Relayer order: %s", data.get("orderID") or data.get("id", "?"))
            return data
        except Exception as e:
            log.warning("  Relayer submit failed: %s", e)
            return None

    def _place_order(self, token_id: str, action: str, amount: float, _retry: int = 0) -> Optional[dict]:
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
            if _retry < 2:
                time.sleep(0.5 + _retry * 0.5)
                return self._place_order(token_id, action, amount, _retry + 1)
            return None
        except Exception as e:
            log.error("  ❌ Order error: %s", e)
            if _retry < 2:
                time.sleep(0.5 + _retry * 0.5)
                return self._place_order(token_id, action, amount, _retry + 1)
            return None

    def _place_sell_order(self, token_id: str, shares: float, _retry: int = 0) -> Optional[dict]:
        if self.client is None or DRY_RUN:
            log.info("  [DRY-RUN] SELL %.4f shares", shares)
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
            if _retry < 2:
                time.sleep(0.5)
                return self._place_sell_order(token_id, shares, _retry + 1)
            return None
        except Exception as e:
            log.error("  ❌ SELL error: %s", e)
            if _retry < 2:
                time.sleep(0.5)
                return self._place_sell_order(token_id, shares, _retry + 1)
            return None

    def _extract_fill_price(self, resp: dict) -> Optional[float]:
        if not isinstance(resp, dict):
            return None
        for key in ("avgFillPrice", "avg_fill_price", "avg_price", "price", "filled_price"):
            if resp.get(key) is not None:
                try: return float(resp[key])
                except: pass
        return None

    def execute(
        self,
        signal: ReversalSignal,
        market: dict,
        stats: BotStats,
        polymarket_fetcher: "PolymarketFetcher",
    ) -> Optional[TradeRecord]:

        action      = signal.direction
        size        = float(MAX_TRADE_SIZE)
        symbol      = market["symbol"]
        entry_price = market["yes_price"] if action == "BUY_YES" else market["no_price"]
        token_id    = market["yes_token"] if action == "BUY_YES" else market["no_token"]

        if entry_price <= 0:
            log.warning("Invalid entry price %.4f", entry_price)
            return None

        # Wait for price <= 0.50 (poll every 0.5s, max 30s wait)
        if entry_price > MIN_ENTRY_DISCOUNT:
            cprint(
                f"  {C.BYELLOW}⏳ Price={entry_price:.4f} > {MIN_ENTRY_DISCOUNT:.2f} — "
                f"waiting up to 30s for better price...{C.RESET}"
            )
            waited = 0
            while waited < 30:
                try:
                    p = polymarket_fetcher.get_token_price(token_id)
                except Exception:
                    p = entry_price
                if p <= MIN_ENTRY_DISCOUNT:
                    entry_price = p
                    cprint(f"  {C.BGREEN}✅ Price hit {entry_price:.4f} — entering{C.RESET}")
                    break
                time.sleep(0.5)
                waited += 0.5
            else:
                log.warning("Price never reached %.2f in 30s — skipping", MIN_ENTRY_DISCOUNT)
                return None

        entry_shares = round(size / entry_price, 4)

        # DRY_RUN path
        if DRY_RUN or self.client is None:
            TradeExecutor._dry_run_balance = round(TradeExecutor._dry_run_balance - size, 4)
            stats.current_balance = TradeExecutor._dry_run_balance
            stats.daily_spent    += size
            stats.total_trades   += 1
            return TradeRecord(
                timestamp=datetime.now().isoformat(timespec="seconds"),
                symbol=symbol, direction=action,
                entry_price=entry_price, trade_size_usd=size,
                entry_shares=entry_shares, confidence=signal.confidence,
                reasoning=signal.reasoning,
                market_ts=str(market.get("market_ts", "")),
                condition_id=market.get("condition_id", ""),
            )

        # Live path
        resp = self._place_order(token_id, action, size)
        if not resp:
            log.error("Order failed for %s %s", symbol, action)
            return None

        actual_price = self._extract_fill_price(resp) or entry_price
        actual_shares = round(size / actual_price, 4)
        stats.current_balance = self.get_balance_usd()
        stats.daily_spent    += size
        stats.total_trades   += 1
        return TradeRecord(
            timestamp=datetime.now().isoformat(timespec="seconds"),
            symbol=symbol, direction=action,
            entry_price=actual_price, trade_size_usd=size,
            entry_shares=actual_shares, confidence=signal.confidence,
            reasoning=signal.reasoning,
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
        if record.outcome == "OPEN":
            line = (
                f"[TRADE] {record.timestamp} | {record.symbol} {record.direction}"
                f" @ {record.entry_price:.4f} | ${record.trade_size_usd:.2f}"
                f" | conf={record.confidence}%"
            )
        else:
            result = "WIN" if record.pnl >= 0 else "LOSS"
            line = (
                f"[{result}] {record.timestamp} | {record.symbol} {record.direction}"
                f" | pnl=${record.pnl:+.4f} ({record.roi_pct:+.2f}%)"
                f" | bal=${stats.current_balance:.2f}"
            )
        with open(LIVE_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        log.info(line)


# ─────────────────────────────────────────────
# DASHBOARD
# ─────────────────────────────────────────────
class DashboardWriter:
    def __init__(self):
        with open(DASHBOARD_FILE, "w") as f:
            json.dump({"updated_at": None, "mode": "v8-volume-reversal"}, f)

    def write(self, stats: BotStats, live_events: list, open_trades: list, symbol_summary: dict):
        payload = {
            "updated_at":  datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "mode":        "v8-volume-reversal",
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
                "redeemed_count":  stats.redeemed_count,
            },
            "live_events":    live_events[-MAX_LIVE_EVENTS:],
            "open_trades":    [asdict(r) for r in open_trades],
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
        self.detector   = VolumeReversalDetector()
        self.executor   = TradeExecutor()
        self.logger     = TradingLogger()
        self.dashboard  = DashboardWriter()

        self.stats = BotStats()
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

        self.open_trades:   list[TradeRecord] = []
        self.live_events:   list[dict]        = []
        self.symbol_summary: dict             = {}

        # Per-symbol per-window: has trade been placed already?
        self._traded_this_window: dict[str, int] = {}   # symbol -> window_ts

        # Per-symbol per-window: is 1s polling active?
        self._polling_active: dict[str, bool] = {}

        # Trade token registry
        self._trade_tokens: dict[str, tuple[str, str]] = {}

        # Blacklist
        self.blacklisted:   dict[str, float] = {}

        self._lock         = threading.Lock()
        self._prev_window: Optional[int] = None

    # ── Blacklist helpers ────────────────────────────────────────────
    def _is_blacklisted(self, sym: str) -> bool:
        expiry = self.blacklisted.get(sym, 0)
        if expiry > time.time():
            return True
        if sym in self.blacklisted:
            del self.blacklisted[sym]
        return False

    def _blacklist(self, sym: str):
        self.blacklisted[sym] = time.time() + BLACKLIST_SECONDS
        cprint(f"  {C.BRED}🚫 {sym} blacklisted for {BLACKLIST_SECONDS}s{C.RESET}")

    # ── Token registry ───────────────────────────────────────────────
    def _register_tokens(self, cid: str, yes_tok: str, no_tok: str):
        with self._lock:
            self._trade_tokens[cid] = (yes_tok, no_tok)

    def _get_token(self, trade: TradeRecord) -> Optional[str]:
        tokens = self._trade_tokens.get(trade.condition_id)
        if not tokens:
            return None
        return tokens[0] if trade.direction == "BUY_YES" else tokens[1]

    # ── Auto-sell monitor ────────────────────────────────────────────
    async def _monitor_auto_sell(self):
        with self._lock:
            snapshot = list(self.open_trades)

        for trade in snapshot:
            if trade.auto_sell_done or trade.tracking_stopped:
                continue
            token_id = self._get_token(trade)
            if not token_id:
                continue
            try:
                price = await asyncio.to_thread(self.polymarket.get_token_price, token_id)
            except Exception:
                continue

            if price >= AUTO_SELL_PRICE:
                cprint(
                    f"\n{C.BG_YELLOW}{C.BLACK}{C.BOLD}  💸 AUTO-SELL @ {price:.4f}  {C.RESET}  "
                    f"{C.BOLD}{trade.symbol}{C.RESET} {trade.direction}"
                )
                await asyncio.to_thread(self.executor._place_sell_order, token_id, trade.entry_shares)
                proceeds = round(price * trade.entry_shares, 4)
                pnl      = round(proceeds - trade.trade_size_usd, 4)

                with self._lock:
                    trade.auto_sell_done = True
                    trade.outcome        = "WIN" if pnl >= 0 else "LOSS"
                    trade.pnl            = pnl
                    trade.exit_price     = price
                    trade.exit_timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
                    if DRY_RUN:
                        TradeExecutor._dry_run_balance = round(TradeExecutor._dry_run_balance + proceeds, 4)
                        self.stats.current_balance = TradeExecutor._dry_run_balance
                    else:
                        self.stats.current_balance = self.executor.get_balance_usd()
                    self.stats.daily_pnl += pnl
                    if pnl >= 0:
                        self.stats.wins += 1
                    else:
                        self.stats.losses += 1
                        self._blacklist(trade.symbol)
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
                        f"AUTO-SELL {trade.symbol} {trade.direction} @ {price:.4f} | pnl=${pnl:+.4f}"
                    )

    # ── Settle expired trades ────────────────────────────────────────
    def _settle_expired(self):
        now = time.time()
        to_settle = [
            t for t in self.open_trades
            if not t.auto_sell_done
            and not t.tracking_stopped
            and now > int(t.market_ts) + 300 + REDEEM_GRACE_SECONDS
        ]
        for trade in to_settle:
            pnl = -trade.trade_size_usd
            trade.outcome        = "LOSS"
            trade.pnl            = pnl
            trade.tracking_stopped = True
            trade.exit_price     = 0.0
            trade.exit_timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
            with self._lock:
                self.stats.losses    += 1
                self.stats.daily_pnl += pnl
                if not DRY_RUN and self.executor.client is not None:
                    self.stats.current_balance = self.executor.get_balance_usd()
                self._blacklist(trade.symbol)
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
                    f"LOSS(EXPIRED) {trade.symbol} {trade.direction} | pnl=${pnl:+.4f}"
                )

    # ── 1-second polling loop per symbol (last 2 min) ────────────────
    async def _poll_symbol_1s(self, sym_bin: str, sym_label: str, window_ts: int):
        """
        Polls every 1 second.
        On each tick:
          - Fetch fresh 1m klines (last 6) + orderbook
          - Run VolumeReversalDetector
          - If signal found and no trade yet for this window → execute immediately
        Runs until: window ends OR trade placed OR blacklisted
        """
        cprint(
            f"\n{C.BBLUE}{C.BOLD}  🔴 ACTIVATING 1s POLLING for {sym_label} "
            f"(window={window_ts}){C.RESET}"
        )
        first_market_fetch = True
        market_cache       = None
        consecutive_errors = 0

        while True:
            now = time.time()
            seconds_left = int(window_ts + 300 - now)

            # Stop if window over
            if seconds_left <= 0:
                log.info("  [1s] %s window ended — stopping poll", sym_label)
                break

            # Stop if blacklisted
            if self._is_blacklisted(sym_label):
                log.info("  [1s] %s blacklisted — stopping poll", sym_label)
                break

            # Stop if already traded this window
            with self._lock:
                already_traded = self._traded_this_window.get(sym_label) == window_ts
            if already_traded:
                log.info("  [1s] %s already traded in window %d — stopping poll", sym_label, window_ts)
                break

            try:
                # ── Fetch data ───────────────────────────────────────
                klines_1m = await asyncio.to_thread(
                    self.binance.get_klines, sym_bin, "1m", 8
                )
                ob = await asyncio.to_thread(
                    self.binance.get_order_book, sym_bin, 20
                )

                cur_price = float(klines_1m[-1][4])
                self.detector.update_price(sym_label, cur_price)

                # ── Run reversal detector ─────────────────────────────
                ticker = await asyncio.to_thread(self.binance.get_ticker_24h, sym_bin)
                signal = await asyncio.to_thread(
                    self.detector.detect, klines_1m, ob, sym_label, ticker
                )
                consecutive_errors = 0

                ob_imb = self.binance.compute_ob_imbalance(ob)
                log.info(
                    "  [1s] %s | price=%.4f | ob_imb=%+.4f | signal=%s | %ds left",
                    sym_label, cur_price, ob_imb,
                    signal.direction if signal else "None",
                    seconds_left,
                )

                if signal:
                    print_signal(signal, sym_label, seconds_left)

                    # Fetch market once (or reuse cache)
                    if first_market_fetch or market_cache is None:
                        try:
                            open_5m = float(klines_1m[-5][1]) if len(klines_1m) >= 5 else cur_price
                            market_cache = await asyncio.to_thread(
                                self.polymarket.get_current_market,
                                sym_label, open_5m, cur_price
                            )
                            self._register_tokens(
                                market_cache["condition_id"],
                                market_cache["yes_token"],
                                market_cache["no_token"],
                            )
                            first_market_fetch = False
                        except Exception as e:
                            log.error("  [1s] Polymarket fetch failed: %s", e)
                            await asyncio.sleep(1)
                            continue

                    # Entry price check
                    entry_price = (
                        market_cache["yes_price"]
                        if signal.direction == "BUY_YES"
                        else market_cache["no_price"]
                    )

                    if entry_price > MIN_ENTRY_DISCOUNT:
                        log.info(
                            "  [1s] Price %.4f > %.2f discount — skipping entry (waiting next tick)",
                            entry_price, MIN_ENTRY_DISCOUNT
                        )
                        await asyncio.sleep(1)
                        continue

                    # ── EXECUTE ──────────────────────────────────────
                    cprint(
                        f"  {C.BG_GREEN}{C.BLACK}{C.BOLD}  ⚡ EXECUTING {sym_label} "
                        f"{signal.direction} IMMEDIATELY  {C.RESET}"
                    )
                    record = await asyncio.to_thread(
                        self.executor.execute,
                        signal, market_cache, self.stats, self.polymarket
                    )
                    if record:
                        with self._lock:
                            self.open_trades.append(record)
                            self._traded_this_window[sym_label] = window_ts
                            self.logger.log_trade(record, self.stats, self.open_trades)
                            DashboardWriter.add_event(
                                self.live_events,
                                f"TRADE {record.symbol} {record.direction} @ {record.entry_price:.4f}"
                                f" | ${record.trade_size_usd:.2f} | conf={record.confidence}%"
                            )
                        print_trade_entry(record)
                        break  # Done for this window
                    else:
                        log.warning("  [1s] execute() returned None for %s", sym_label)

            except Exception as e:
                consecutive_errors += 1
                log.error("  [1s] Error polling %s: %s", sym_label, e)
                if consecutive_errors >= 5:
                    log.error("  [1s] Too many errors — stopping poll for %s", sym_label)
                    break
                await asyncio.sleep(1)
                continue

            await asyncio.sleep(1)

        with self._lock:
            self._polling_active[sym_label] = False
        log.info("  [1s] Poll ended for %s", sym_label)

    # ── Main tick (every 10s) ────────────────────────────────────────
    async def _tick(self):
        window_ts    = self.polymarket.get_current_window_ts()
        now          = time.time()
        seconds_left = max(0, int(window_ts + 300 - now))

        try:
            from zoneinfo import ZoneInfo
            ist_str = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M:%S IST")
        except Exception:
            ist_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        print_tick_status(self.stats, self.open_trades, seconds_left, ist_str, "v8")

        # Window changed — print summary and reset polling flags
        if self._prev_window is not None and window_ts != self._prev_window:
            print_window_summary(self.stats, self.open_trades, self._prev_window)
            with self._lock:
                # Clean up traded_this_window entries from old windows
                old_keys = [sym for sym, wts in self._traded_this_window.items() if wts < window_ts]
                for k in old_keys:
                    del self._traded_this_window[k]
                for sym_bin, sym_label in self.SYMBOLS:
                    self._polling_active[sym_label] = False

        self._prev_window = window_ts

        # Monitor open positions
        await self._monitor_auto_sell()
        self._settle_expired()

        # ── Activate 1s polling in last 2 minutes ────────────────────
        if seconds_left <= REVERSAL_WATCH_WINDOW_SECONDS:
            for sym_bin, sym_label in self.SYMBOLS:
                with self._lock:
                    already_traded  = self._traded_this_window.get(sym_label) == window_ts
                    already_polling = self._polling_active.get(sym_label, False)
                    is_blacklisted  = self._is_blacklisted(sym_label)

                if already_traded or already_polling or is_blacklisted:
                    continue

                with self._lock:
                    self._polling_active[sym_label] = True

                # Launch polling loop as background task
                asyncio.create_task(
                    self._poll_symbol_1s(sym_bin, sym_label, window_ts)
                )
                cprint(
                    f"  {C.BBLUE}🟢 Started 1s polling for {sym_label} "
                    f"({seconds_left}s left in window){C.RESET}"
                )
        else:
            log.info(
                "  Waiting for last 2 min. window | %ds left "
                "| polling activates at %ds",
                seconds_left, REVERSAL_WATCH_WINDOW_SECONDS,
            )

        # Update dashboard
        with self._lock:
            self.dashboard.write(self.stats, self.live_events, self.open_trades, self.symbol_summary)

    # ── Run loop ─────────────────────────────────────────────────────
    async def run(self):
        cprint(
            f"\n{C.BG_BLUE}{C.BWHITE}{C.BOLD}"
            f"  🚀 Sniper Bot v8 — VOLUME REVERSAL EDITION  "
            f"| dry_run={DRY_RUN}  {C.RESET}"
        )
        cprint(
            f"  {C.BYELLOW}⚙️  Strategy: Volume Surge + Candle Reversal + Orderbook Confirm{C.RESET}"
        )
        cprint(
            f"  {C.BCYAN}📋 Params:{C.RESET}\n"
            f"  {C.DIM}  Vol surge min     : {VOL_SURGE_MIN}x{C.RESET}\n"
            f"  {C.DIM}  Candle body min   : {CANDLE_BODY_MIN_PCT*100:.0f}%{C.RESET}\n"
            f"  {C.DIM}  OB confirm thresh : {OB_CONFIRM_THRESHOLD:.2f}{C.RESET}\n"
            f"  {C.DIM}  Watch window      : last {REVERSAL_WATCH_WINDOW_SECONDS}s of each window{C.RESET}\n"
            f"  {C.DIM}  Entry discount    : <= {MIN_ENTRY_DISCOUNT}{C.RESET}\n"
            f"  {C.DIM}  Auto-sell         : {AUTO_SELL_PRICE}{C.RESET}\n"
            f"  {C.DIM}  Blacklist         : {BLACKLIST_SECONDS}s after loss{C.RESET}\n"
        )

        while True:
            try:
                await asyncio.wait_for(self._tick(), timeout=120)
            except KeyboardInterrupt:
                cprint(f"\n{C.BRED}Bot stopped by user.{C.RESET}")
                break
            except asyncio.TimeoutError:
                log.error("Tick timed out, continuing")
            except Exception as e:
                log.exception("Tick error: %s", e)
            await asyncio.sleep(10)


# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────
if __name__ == "__main__":
    if not DRY_RUN and not PRIVATE_KEY:
        cprint(f"{C.BRED}⚠️  Set PRIVATE_KEY or FUNDING_PRIVATE_KEY for live trading.{C.RESET}")
        cprint(f"{C.BYELLOW}    Or set DRY_RUN=true for testing without real money.{C.RESET}")
        sys.exit(1)

    cprint(f"  {C.BWHITE}Live trading : {C.BGREEN if not DRY_RUN else C.BRED}{not DRY_RUN}{C.RESET}")
    cprint(f"  {C.BWHITE}Trade size   : {C.BCYAN}${MIN_TRADE_SIZE:.0f}{C.RESET}")
    cprint(f"  {C.BWHITE}Watch window : {C.BCYAN}last {REVERSAL_WATCH_WINDOW_SECONDS}s{C.RESET}")

    while True:
        try:
            asyncio.run(SniperBot().run())
            break
        except KeyboardInterrupt:
            cprint(f"\n{C.BRED}Bot stopped by user.{C.RESET}")
            break
        except Exception as e:
            log.exception("Bot crashed: %s", e)
            cprint(f"\n{C.BRED}Restarting in 5s...{C.RESET}")
            time.sleep(5)
# Updated 2026-01-19: Refactor bot startup logging
# Updated 2026-01-21: Polish performance logging text
# Updated 2026-01-23: Tighten strategy commentary
# Updated 2026-01-25: Improve config hints and notes
# Updated 2026-01-28: Adjust comments for strategy clarity
# Updated 2026-01-30: Tighten strategy commentary
# Updated 2026-02-03: Refactor bot startup logging
# Updated 2026-02-09: Strengthen orderbook imbalance comment
# Updated 2026-02-11: Refine documentation details
# Updated 2026-02-18: Improve config hints and notes
# Updated 2026-02-20: Refine documentation details
# Updated 2026-02-22: Strengthen orderbook imbalance comment
# Updated 2026-02-24: Update LLM validation note
# Updated 2026-04-04: Add inline guidance for dry-run mode
# Updated 2026-04-16: Polish performance logging text
# Updated 2026-04-23: Tighten strategy commentary