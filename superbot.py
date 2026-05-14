"""
================================================================================
  POLYMARKET BTC 5-MIN SNIPER BOT (v5.1 - FIXED)
  Replicates the high-frequency "Up or Down" strategy with poly5min integration
  Strategy: Window Delta + Composite Signal + Sniper Timing + Smart Order Execution

  FIXES v5.1:
  [FIX 1] UnicodeEncodeError  — Windows CP1252 console cannot render delta symbol
           Solution: StreamHandler forced to UTF-8; all log messages use ASCII "D="
           instead of Greek delta character U+0394
  [FIX 2] Missing token_id    — _discover_window_market() did not correctly map
           yes_token_id / no_token_id from the market dict returned by
           discover_btc_market(). The dict uses flat keys "yes_token"/"no_token",
           not a nested "tokens" list.  Both code-paths (flat keys + list fallback)
           are now handled.
================================================================================
"""

import asyncio
import csv
import json
import logging
import math
import os
import sys
import time
import threading
import concurrent.futures
from collections import deque
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Tuple, Dict, List

import aiohttp
import ccxt
import numpy as np
import pandas as pd
import websockets
import requests
import re
import pytz
from dotenv import load_dotenv

# py-clob-client imports
try:
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import (
        OrderArgs,
        OrderType,
        MarketOrderArgs,
    )
    from py_clob_client.order_builder.constants import BUY, SELL
    from py_clob_client.constants import POLYGON
    CLOB_OK = True
except ImportError as e:
    print(f"ERROR: py-clob-client not installed. Run: pip install py-clob-client")
    CLOB_OK = False
    sys.exit(1)

# ---------------------------------------------------------------------------
# CONFIGURATION (All values loaded from .env)
# ---------------------------------------------------------------------------

load_dotenv()

# -- Wallet & Auth ----------------------------------------------------------
PRIVATE_KEY                = os.getenv("PRIVATE_KEY", "")
POLYMARKET_ADDRESS         = os.getenv("POLYMARKET_ADDRESS", "")
POLYMARKET_FUNDER_ADDRESS  = os.getenv("POLYMARKET_FUNDER_ADDRESS", "")

# -- Trading Params ---------------------------------------------------------
DRY_RUN                = os.getenv("DRY_RUN", "True").lower() == "true"
TRADE_SIZE_USD         = float(os.getenv("TRADE_SIZE_USD", "1.0"))
MIN_BALANCE_USD        = float(os.getenv("MIN_BALANCE_USD", "10.0"))
MAX_ACTIVE_TRADES      = int(os.getenv("MAX_ACTIVE_TRADES", "5"))
DAILY_LIMIT_USD        = float(os.getenv("DAILY_LIMIT_USD", "36000.0"))
BOT_MODE               = os.getenv("BOT_MODE", "safe")
CONFIDENCE_THRESHOLD   = float(os.getenv("CONFIDENCE_THRESHOLD", "0.40"))

# -- Telegram (optional) ---------------------------------------------------
TELEGRAM_TOKEN         = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID       = os.getenv("TELEGRAM_CHAT_ID", "")

# -- Polymarket API ---------------------------------------------------------
GAMMA_API_BASE         = "https://gamma-api.polymarket.com"
CLOB_HOST              = "https://clob.polymarket.com"
CHAIN_ID               = POLYGON
SIGNATURE_TYPE         = int(os.getenv("SIGNATURE_TYPE", "0"))

MARKET_SLUGS           = {"BTC": "btc-updown-5m", "ETH": "eth-updown-5m"}

# -- Relayer API (Gasless) --------------------------------------------------
RELAYER_API_KEY         = os.getenv("RELAYER_API_KEY", "")
RELAYER_API_KEY_ADDRESS = os.getenv("RELAYER_API_KEY_ADDRESS", "")

# -- Binance WebSocket ------------------------------------------------------
BINANCE_WS_URL = "wss://stream.binance.com:9443/stream?streams=btcusdt@kline_1m/btcusdt@trade"

# -- Signal Weights (improved: require more price movement) ---------
WINDOW_DELTA_WEIGHTS = [
    (0.10, 7),    # > 0.10% = strongest signal
    (0.05, 5),    # > 0.05% = very strong (increased from 0.02%)
    (0.02, 3),    # > 0.02% = strong (increased from 0.005%)
    (0.01, 1),    # > 0.01% = signal exists (increased from 0.001%)
    (0.0,  0),    # else = no signal
]

# -- Candle Store -----------------------------------------------------------
MAX_CANDLES = 30

# -- Paths ------------------------------------------------------------------
LOG_DIR    = Path("logs")
LOG_FILE   = LOG_DIR / "superbot.log"
TRADES_CSV = LOG_DIR / "trades.csv"
LOG_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# LOGGING SETUP
# FIX 1: Force UTF-8 on the StreamHandler so Windows CP1252 console never
#         chokes on Unicode characters (delta U+0394, checkmarks, etc.).
#         We also reconfigure stdout itself when possible (Python 3.7+).
# ---------------------------------------------------------------------------

LOG_LEVEL = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)

logger = logging.getLogger("SuperBot")
logger.setLevel(LOG_LEVEL)

fmt = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

# File handler — always UTF-8
fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
fh.setFormatter(fmt)
logger.addHandler(fh)

# Console handler — force UTF-8 so Windows doesn't raise UnicodeEncodeError
try:
    # Python 3.7+: reconfigure stdout to UTF-8 with replacement fallback
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass  # older Python — best-effort

ch = logging.StreamHandler(sys.stdout)
ch.setFormatter(fmt)
logger.addHandler(ch)

# ---------------------------------------------------------------------------
# DATA MODELS
# ---------------------------------------------------------------------------

@dataclass
class Candle:
    open_time:  int
    open:       float
    high:       float
    low:        float
    close:      float
    volume:     float
    is_closed:  bool = False

    @property
    def mid(self) -> float:
        return (self.high + self.low) / 2


@dataclass
class WindowState:
    window_start:    int   = 0
    window_end:      int   = 0
    open_price:      float = 0.0
    current_price:   float = 0.0
    market_id:       str   = ""
    yes_token_id:    str   = ""
    no_token_id:     str   = ""
    yes_price:       float = 0.0
    no_price:        float = 0.0


@dataclass
class PreviousMarketData:
    """Tracks the last closed market's outcome and COMPLETE technical analysis."""
    window_start:      int   = 0
    market_id:         str   = ""
    direction:         str   = ""         # "YES" or "NO" (what market resolved to)
    movement_pct:      float = 0.0        # Final price movement %
    momentum_signal:   str   = ""         # "UP", "DOWN", or "NEUTRAL"
    
    # TECHNICAL ANALYSIS SNAPSHOT (captured at close)
    rsi_at_close:      float = 0.0        # RSI value when market closed
    ema_status:        str   = ""         # "bullish" or "bearish" (EMA9 vs EMA21)
    volume_signal:     str   = ""         # "strong" or "weak"
    volatility_level:  str   = ""         # "high" or "low"
    
    timestamp:         str   = ""


@dataclass
class Signal:
    direction:        str   = ""
    window_delta_pct: float = 0.0
    window_weight:    int   = 0
    composite_score:  float = 0.0
    confidence:       float = 0.0
    implied_prob:     float = 0.0
    has_edge:         bool  = False
    edge_size:        float = 0.0
    momentum_boost:   float = 0.0    # Bonus from previous market match


@dataclass
class Trade:
    trade_id:        str   = ""
    timestamp:       str   = ""
    window_start:    int   = 0
    market_id:       str   = ""
    direction:       str   = ""
    entry_price:     float = 0.0
    size_usd:        float = 0.0
    tokens_bought:   float = 0.0
    confidence:      float = 0.0
    composite_score: float = 0.0
    status:          str   = "OPEN"
    pnl_usd:         float = 0.0
    order_id:        str   = ""
    dry_run:         bool  = True


# ---------------------------------------------------------------------------
# CSV TRADE LOGGER
# ---------------------------------------------------------------------------

TRADE_CSV_FIELDS = [
    "trade_id", "timestamp", "window_start", "market_id", "direction",
    "entry_price", "size_usd", "tokens_bought", "confidence",
    "composite_score", "status", "pnl_usd", "order_id", "dry_run"
]

def write_trade_csv(trade: Trade):
    file_exists = TRADES_CSV.exists()
    with open(TRADES_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=TRADE_CSV_FIELDS)
        if not file_exists:
            writer.writeheader()
        row = asdict(trade)
        writer.writerow({k: row.get(k, "") for k in TRADE_CSV_FIELDS})


def update_trade_csv(trade_id: str, status: str, pnl: float):
    if not TRADES_CSV.exists():
        return
    rows = []
    with open(TRADES_CSV, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row["trade_id"] == trade_id:
                row["status"]  = status
                row["pnl_usd"] = str(round(pnl, 4))
            rows.append(row)
    with open(TRADES_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=TRADE_CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


# ---------------------------------------------------------------------------
# TELEGRAM NOTIFIER
# ---------------------------------------------------------------------------

async def send_telegram(message: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url     = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status != 200:
                    logger.warning(f"[TELEGRAM] Failed: HTTP {resp.status}")
    except Exception as e:
        logger.warning(f"[TELEGRAM] Error: {e}")


# ---------------------------------------------------------------------------
# MARKET DISCOVERY
# ---------------------------------------------------------------------------

def get_current_window_timestamps() -> tuple:
    now          = int(time.time())
    window_start = (now // 300) * 300
    window_end   = window_start + 300
    return window_start, window_end


async def discover_btc_market(session: aiohttp.ClientSession, window_start: int) -> Optional[dict]:
    """
    Discover BTC 5-min market using poly5min approach.
    Returns a dict with flat keys:
        condition_id, yes_token, no_token, yes_price, no_price, timestamp
    
    CRITICAL: Try CURRENT window's close time FIRST.
    If not found, try NEXT window's close time to prevent "advance trading".
    """
    symbol = "BTC"
    slug   = MARKET_SLUGS.get(symbol, f"{symbol.lower()}-updown-5m")
    
    # Try timestamps in order: CURRENT window close, then NEXT window close
    timestamps_to_try = [
        (window_start + 300, "CURRENT window close"),
        (window_start + 600, "NEXT window close (fallback)"),
    ]
    
    for market_ts, description in timestamps_to_try:
        try:
            url = f"https://polymarket.com/event/{slug}-{market_ts}"
            logger.debug(f"[MARKET] Trying {description}: {url}")

            headers  = {"User-Agent": "Mozilla/5.0"}
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            html = response.text

            cond_match = re.search(r'"conditionId":"([^"]+)"', html)
            if not cond_match:
                logger.debug(f"[MARKET] No conditionId found for {description} (TS={market_ts})")
                continue  # Try next timestamp
            condition_id = cond_match.group(1)

            tok_match = re.search(r'"clobTokenIds":\s*\[([^\]]+)\]', html)
            if not tok_match:
                logger.debug(f"[MARKET] No clobTokenIds for {description}")
                continue  # Try next timestamp

            try:
                token_ids = json.loads("[" + tok_match.group(1) + "]")
            except json.JSONDecodeError:
                logger.debug(f"[MARKET] Failed to parse tokens for {description}")
                continue

            if len(token_ids) < 2:
                logger.debug(f"[MARKET] Expected 2 tokens, got {len(token_ids)} for {description}")
                continue
            
            # SUCCESS: Found market with valid condition and tokens
            yes_token   = token_ids[0]
            no_token    = token_ids[1]
            
            # Fetch prices
            yes_price = 0.5
            no_price  = 0.5
            price_match = re.search(r'"bid":\s*([\d.]+)', html)
            if price_match:
                try:
                    yes_price = float(price_match.group(1))
                    no_price  = 1.0 - yes_price
                except ValueError:
                    pass

            logger.info(f"[MARKET] ✓ Found market for {description} (TS={market_ts})")
            
            return {
                "condition_id": condition_id,
                "yes_token":    yes_token,
                "no_token":     no_token,
                "yes_price":    yes_price,
                "no_price":     no_price,
                "timestamp":    market_ts,
            }

        except requests.RequestException as e:
            logger.debug(f"[MARKET] Request error for {description}: {e}")
            continue
        except Exception as e:
            logger.debug(f"[MARKET] Unexpected error for {description}: {e}")
            continue
    
    # No market found for either timestamp
    logger.warning(f"[MARKET] No active BTC market found for window starting {datetime.utcfromtimestamp(window_start).strftime('%H:%M:%S')} UTC")
    return None


async def get_token_prices(session: aiohttp.ClientSession, condition_id: str) -> tuple:
    try:
        async with session.get(
            f"{CLOB_HOST}/midpoints",
            params={"token_ids": condition_id},
            timeout=aiohttp.ClientTimeout(total=5)
        ) as resp:
            if resp.status == 200:
                data  = await resp.json()
                yes_p = no_p = 0.5
                for item in (data if isinstance(data, list) else []):
                    mid = float(item.get("mid", 0.5))
                    if yes_p == 0.5:
                        yes_p = mid
                    else:
                        no_p = mid
                return yes_p, no_p
    except Exception as e:
        logger.debug(f"[CLOB] Price fetch error: {e}")
    return 0.5, 0.5


# ---------------------------------------------------------------------------
# SIGNAL ENGINE
# ---------------------------------------------------------------------------

class SignalEngine:
    MAX_SCORE = 15.0

    def __init__(self):
        self.candles: deque = deque(maxlen=MAX_CANDLES)
        self.ticks:   deque = deque(maxlen=20)

    def add_candle(self, c: Candle):
        if self.candles and self.candles[-1].open_time == c.open_time:
            self.candles[-1] = c
        else:
            self.candles.append(c)

    def add_tick(self, price: float):
        self.ticks.append(price)

    def _window_delta_weight(self, delta_pct: float) -> int:
        abs_d = abs(delta_pct)
        for threshold, weight in WINDOW_DELTA_WEIGHTS:
            if abs_d > threshold:
                return weight
        return 0

    def _micro_momentum(self) -> float:
        """Momentum from last 3-5 closed candles (more stable than 2)."""
        closed = [c for c in self.candles if c.is_closed]
        if len(closed) < 3:
            return 0.0
        score = 0.0
        # Use last 3 candles instead of 2 for better signal
        for c in list(closed)[-3:]:
            if c.open > 0:
                pct = (c.close - c.open) / c.open * 100
                # Reduced sensitivity: 0.1% = 1.0 (was 0.05%)
                score += max(-1.0, min(1.0, pct / 0.10))
        return score / 3.0  # Average of 3 candles

    def _acceleration(self) -> float:
        closed = [c for c in self.candles if c.is_closed]
        if len(closed) < 3:
            return 0.0
        c1, c2   = closed[-2], closed[-1]
        delta1   = (c1.close - c1.open) / c1.open * 100 if c1.open > 0 else 0
        delta2   = (c2.close - c2.open) / c2.open * 100 if c2.open > 0 else 0
        accel    = delta2 - delta1
        return max(-1.5, min(1.5, accel / 0.03))

    def _ema_crossover(self) -> float:
        if len(self.candles) < 22:
            return 0.0
        closes = np.array([c.close for c in self.candles])
        def ema(values, period):
            k = 2.0 / (period + 1)
            e = values[0]
            for v in values[1:]:
                e = v * k + e * (1 - k)
            return e
        ema9  = ema(closes, 9)
        ema21 = ema(closes, 21)
        if ema9 > ema21:   return  1.5
        if ema9 < ema21:   return -1.5
        return 0.0

    def _rsi_signal(self) -> float:
        """RSI-14 signal with proper overbought/oversold detection."""
        closed = [c for c in self.candles if c.is_closed]
        if len(closed) < 15:
            return 0.0
        closes  = [c.close for c in closed]
        deltas  = np.diff(closes[-15:])
        gains   = np.where(deltas > 0, deltas, 0)
        losses  = np.where(deltas < 0, -deltas, 0)
        avg_g   = np.mean(gains)   if len(gains)  > 0 else 0
        avg_l   = np.mean(losses)  if len(losses) > 0 else 1e-9
        rs      = avg_g / avg_l
        rsi     = 100 - (100 / (1 + rs))
        # Proper RSI interpretation:
        if rsi > 70:    return -0.5   # Overbought = sell signal
        if rsi > 60:    return  0.5   # Strong uptrend
        if rsi > 55:    return  0.2   # Mild uptrend
        if rsi < 30:    return  0.5   # Oversold = buy signal  
        if rsi < 40:    return -0.2   # Mild downtrend
        if rsi < 45:    return -0.5   # Strong downtrend
        return 0.0  # Neutral 50-55

    def _volume_surge(self) -> float:
        closed = [c for c in self.candles if c.is_closed]
        if len(closed) < 11:
            return 0.0
        vols     = [c.volume for c in closed]
        avg10    = np.mean(vols[-11:-1])
        curr_vol = vols[-1]
        if avg10 == 0:
            return 0.0
        ratio      = curr_vol / avg10
        last       = closed[-1]
        candle_dir = 1 if last.close > last.open else -1
        if ratio > 2.0:  return candle_dir * 1.0  # Strong spike = 1.0
        if ratio > 1.5:  return candle_dir * 0.2  # Reduced from 0.5
        return 0.0

    def _tick_trend(self) -> float:
        if len(self.ticks) < 10:
            return 0.0
        ticks = np.array(list(self.ticks)[-20:])  # Use more ticks for better trend
        x     = np.arange(len(ticks))
        slope = np.polyfit(x, ticks, 1)[0]
        scale = ticks.mean()
        if scale == 0:
            return 0.0
        norm = slope / scale * 1000
        # Reduced sensitivity: need stronger trend
        return max(-0.5, min(0.5, norm * 5))  # Was norm * 10

    def compute(self, window: WindowState) -> Signal:
        sig = Signal()
        if window.open_price <= 0 or window.current_price <= 0:
            logger.debug("[SIGNAL] Missing price data")
            return sig

        delta_pct = (window.current_price - window.open_price) / window.open_price * 100
        w_weight  = self._window_delta_weight(delta_pct)

        sig.window_delta_pct = delta_pct
        sig.window_weight    = w_weight

        raw_dir  = w_weight if delta_pct > 0 else -w_weight
        micro    = self._micro_momentum()
        accel    = self._acceleration()
        ema_c    = self._ema_crossover()
        rsi_s    = self._rsi_signal()
        vol_s    = self._volume_surge()
        tick_t   = self._tick_trend()

        total = raw_dir + micro + accel + ema_c + rsi_s + vol_s + tick_t

        sig.composite_score = round(total, 3)
        sig.confidence      = round(min(abs(total) / 7.0, 1.0), 4)
        sig.direction       = "YES" if total > 0 else "NO"

        our_prob = min(0.90, sig.confidence * 0.85 + 0.45)
        if sig.direction == "YES":
            sig.implied_prob = window.yes_price
        else:
            sig.implied_prob = window.no_price
        sig.has_edge  = our_prob > sig.implied_prob + 0.03
        sig.edge_size = round(our_prob - sig.implied_prob, 4)

        # FIX 1: use ASCII "D=" instead of Greek delta character U+0394
        logger.debug(
            f"[SIGNAL] D={delta_pct:+.4f}% w={w_weight} | "
            f"total={total:.2f} conf={sig.confidence:.3f}"
        )
        return sig


# ---------------------------------------------------------------------------
# ORDER MANAGER
# ---------------------------------------------------------------------------

class OrderManager:
    def __init__(self):
        self.client: Optional[ClobClient] = None
        self.active_trades: List[Trade]   = []
        self.balance_usd:   float         = 0.0
        self.daily_pnl:     float         = 0.0
        self.total_pnl:     float         = 0.0
        self.daily_spent:   float         = 0.0
        self.trades_today:  int           = 0
        self.day_str:       str           = datetime.now().strftime("%Y%m%d")

    def initialize(self):
        if DRY_RUN:
            logger.info("[ORDER] DRY RUN mode -- no real orders will be placed")
            self.balance_usd = 35.0
            return
        if not PRIVATE_KEY:
            raise ValueError("PRIVATE_KEY not set in .env!")
        logger.info("[ORDER] Initializing CLOB client...")
        try:
            self.client = ClobClient(
                host=CLOB_HOST,
                chain_id=CHAIN_ID,
                key=PRIVATE_KEY,
                signature_type=SIGNATURE_TYPE,
                funder=POLYMARKET_FUNDER_ADDRESS,
            )
            creds = self.client.create_or_derive_api_creds()
            self.client.set_api_creds(creds)
            wallet_type  = "Email" if SIGNATURE_TYPE == 1 else "EOA"
            gasless_mode = "GASLESS (Relayer)" if RELAYER_API_KEY else "Standard CLOB"
            logger.info(f"[CLOB-OK] Ready ({wallet_type}) - {gasless_mode}")
            self._update_balance()
        except Exception as e:
            logger.error(f"[ORDER] CLOB init failed: {e}")
            raise

    def _update_balance(self):
        if DRY_RUN or not self.client:
            return
        try:
            logger.debug("[BALANCE] Using conservative default (balance API varies by setup)")
            self.balance_usd = 10000.0
        except Exception as e:
            logger.debug(f"[BALANCE] Skipping: {e}")
            self.balance_usd = 10000.0

    def reset_daily_if_needed(self):
        today = datetime.now().strftime("%Y%m%d")
        if today != self.day_str:
            self.daily_spent  = 0.0
            self.daily_pnl    = 0.0
            self.trades_today = 0
            self.day_str      = today
            logger.info("[RESET] Daily counters reset for new day")

    def can_trade(self) -> tuple:
        self.reset_daily_if_needed()
        if DRY_RUN:
            if self.balance_usd < MIN_BALANCE_USD:
                return False, f"Simulated balance ${self.balance_usd:.2f} < ${MIN_BALANCE_USD:.2f}"
            if len(self.active_trades) >= MAX_ACTIVE_TRADES:
                return False, f"Max {MAX_ACTIVE_TRADES} active trades reached"
            if self.daily_spent + TRADE_SIZE_USD > DAILY_LIMIT_USD:
                return False, f"Daily limit ${DAILY_LIMIT_USD:.2f} would be exceeded"
            return True, "OK"
        self._update_balance()
        if self.balance_usd < MIN_BALANCE_USD:
            return False, f"SAFETY STOP: Balance ${self.balance_usd:.2f} < ${MIN_BALANCE_USD:.2f}"
        if self.balance_usd < TRADE_SIZE_USD:
            return False, f"Insufficient: ${self.balance_usd:.2f} < ${TRADE_SIZE_USD:.2f}"
        if len(self.active_trades) >= MAX_ACTIVE_TRADES:
            return False, f"Max {MAX_ACTIVE_TRADES} active trades reached"
        if self.daily_spent + TRADE_SIZE_USD > DAILY_LIMIT_USD:
            return False, f"Daily limit ${DAILY_LIMIT_USD:.2f} would be exceeded"
        return True, "OK"

    async def place_order(self, window: WindowState, signal: Signal) -> Optional[Trade]:
        can, reason = self.can_trade()
        if not can:
            logger.warning(f"[ORDER] Cannot trade: {reason}")
            return None

        if signal.direction == "YES":
            token_id    = window.yes_token_id
            token_price = window.yes_price
        else:
            token_id    = window.no_token_id
            token_price = window.no_price

        # FIX 2 guard: token_id must be a non-empty string
        if not token_id or not isinstance(token_id, str) or len(token_id) < 10:
            logger.error(
                f"[ORDER] Missing token_id for direction={signal.direction} "
                f"(yes_token_id='{window.yes_token_id}' no_token_id='{window.no_token_id}')"
            )
            return None

        if token_price <= 0.01 or token_price >= 0.99:
            logger.warning(f"[ORDER] Token price {token_price:.3f} too extreme -- skipping")
            return None

        tokens_to_buy = TRADE_SIZE_USD / token_price
        trade_id      = f"{int(time.time())}_{signal.direction[:1]}"

        logger.info(
            f"[ORDER] {'[DRY] ' if DRY_RUN else ''}Placing {signal.direction} | "
            f"Price: {token_price:.4f} | Size: ${TRADE_SIZE_USD:.2f} | "
            f"Tokens: {tokens_to_buy:.2f}"
        )

        trade = Trade(
            trade_id        = trade_id,
            timestamp       = datetime.now(timezone.utc).isoformat(),
            window_start    = window.window_start,
            market_id       = window.market_id,
            direction       = signal.direction,
            entry_price     = token_price,
            size_usd        = TRADE_SIZE_USD,
            tokens_bought   = round(tokens_to_buy, 4),
            confidence      = signal.confidence,
            composite_score = signal.composite_score,
            status          = "OPEN",
            dry_run         = DRY_RUN,
        )

        if DRY_RUN:
            trade.order_id    = f"DRY_{trade_id}"
            self.balance_usd -= TRADE_SIZE_USD
            logger.info(f"[DRY] Order simulated | Balance: ${self.balance_usd:.2f}")
        else:
            response = self._place_clob_order(token_id, signal.direction, TRADE_SIZE_USD)
            if response and response.get("orderID"):
                trade.order_id = response.get("orderID", "")
                logger.info(f"[ORDER] Confirmed: {trade.order_id[:30]}...")
            else:
                logger.error(f"[ORDER] Failed: {response}")
                return None

        self.active_trades.append(trade)
        self.trades_today += 1
        self.daily_spent  += TRADE_SIZE_USD
        write_trade_csv(trade)

        logger.info(
            f"[PORTFOLIO] Spent=${self.daily_spent:.2f}/${DAILY_LIMIT_USD:.2f} | "
            f"Trades={self.trades_today} | Balance=${self.balance_usd:.2f}"
        )

        await send_telegram(
            f"{'[DRY] ' if DRY_RUN else ''}Trade #{self.trades_today}\n"
            f"Direction: <b>{signal.direction}</b> | Price: {token_price:.4f}\n"
            f"Confidence: {signal.confidence:.1%} | Spent: ${self.daily_spent:.2f}"
        )

        return trade

    def _place_clob_order(self, token_id: str, signal: str, amount: float, retry_count: int = 0) -> Optional[dict]:
        if not self.client:
            logger.error("[CLOB] Client not initialized")
            return None
        MAX_RETRIES = 2
        try:
            label        = f" [RETRY {retry_count}]" if retry_count > 0 else ""
            attempt_size = amount if retry_count == 0 else max(amount, 1.0)
            logger.info(f"[CLOB{label}] Building order: {signal} ${attempt_size:.2f}")
            order    = MarketOrderArgs(token_id=token_id, amount=attempt_size, side=BUY, order_type=OrderType.FOK)
            signed   = self.client.create_market_order(order)
            response = self.client.post_order(signed, OrderType.FOK)
            if response and response.get("orderID"):
                logger.info(f"[CLOB-OK] Confirmed: {response['orderID'][:20]}...")
                return response
            logger.warning(f"[CLOB] No orderID: {response}")
            if retry_count < MAX_RETRIES:
                time.sleep(1)
                return self._place_clob_order(token_id, signal, amount, retry_count + 1)
            return None
        except Exception as e:
            logger.error(f"[CLOB-ERROR] {e}")
            if retry_count < MAX_RETRIES:
                time.sleep(1 + retry_count)
                return self._place_clob_order(token_id, signal, amount, retry_count + 1)
            return None

    async def check_and_redeem(self, session: aiohttp.ClientSession):
        if not self.active_trades:
            return
        settled: List[Trade] = []
        for trade in self.active_trades:
            try:
                status = await self._check_market_status(session, trade.market_id)
                if status == "resolved":
                    outcome = await self._get_resolution(session, trade.market_id)
                    if outcome == trade.direction:
                        pnl           = trade.tokens_bought - trade.size_usd
                        trade.pnl_usd = round(pnl, 4)
                        trade.status  = "WON"
                        self.daily_pnl += pnl
                        self.total_pnl += pnl
                        logger.info(f"[RESULT] WON | {trade.trade_id} | PnL: +${pnl:.4f}")
                        if not DRY_RUN:
                            await self._redeem_position(trade)
                        await send_telegram(f"WON | +${pnl:.4f}\nDir: {trade.direction}")
                    else:
                        pnl           = -trade.size_usd
                        trade.pnl_usd = round(pnl, 4)
                        trade.status  = "LOST"
                        self.daily_pnl += pnl
                        self.total_pnl += pnl
                        logger.info(f"[RESULT] LOST | {trade.trade_id} | PnL: ${pnl:.4f}")
                        await send_telegram(f"LOST | ${pnl:.4f}")
                    update_trade_csv(trade.trade_id, trade.status, trade.pnl_usd)
                    settled.append(trade)
            except Exception as e:
                logger.error(f"[REDEEM] Error: {e}")
        for t in settled:
            self.active_trades.remove(t)

    async def _check_market_status(self, session: aiohttp.ClientSession, market_id: str) -> str:
        try:
            async with session.get(
                f"{GAMMA_API_BASE}/markets/{market_id}",
                timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("closed") or data.get("resolved"):
                        return "resolved"
                    return "active"
        except Exception:
            pass
        return "unknown"

    async def _get_resolution(self, session: aiohttp.ClientSession, market_id: str) -> str:
        try:
            async with session.get(
                f"{GAMMA_API_BASE}/markets/{market_id}",
                timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                if resp.status == 200:
                    data   = await resp.json()
                    winner = data.get("winningOutcome") or data.get("winning_outcome", "")
                    if winner:
                        return winner.upper()
        except Exception as e:
            logger.debug(f"[RESOLVE] Error: {e}")
        return ""

    async def _redeem_position(self, trade: Trade):
        if not self.client:
            return
        try:
            self.client.redeem_positions(condition_ids=[trade.market_id])
            trade.status = "REDEEMED"
            logger.info(f"[REDEEM] Redeemed {trade.trade_id}")
        except Exception as e:
            logger.error(f"[REDEEM] Failed: {e}")

    def print_daily_summary(self):
        logger.info("=" * 80)
        logger.info(f"[DAILY SUMMARY] {datetime.now(timezone.utc).date()}")
        logger.info(f"   Trades:         {self.trades_today}")
        logger.info(f"   Daily Spent:    ${self.daily_spent:.2f}")
        logger.info(f"   Daily PnL:      ${self.daily_pnl:+.4f}")
        logger.info(f"   Total PnL:      ${self.total_pnl:+.4f}")
        logger.info(f"   Balance:        ${self.balance_usd:.2f}")
        logger.info(f"   Active Trades:  {len(self.active_trades)}")
        logger.info("=" * 80)


# ---------------------------------------------------------------------------
# BINANCE PRICE FEED
# ---------------------------------------------------------------------------

class BinanceFeed:
    def __init__(self, signal_engine: SignalEngine):
        self.engine        = signal_engine
        self.current_price = 0.0
        self.connected     = False
        self._ws_task: Optional[asyncio.Task] = None
        self._exchange     = ccxt.binance({"enableRateLimit": True})

    async def start(self):
        logger.info("[FEED] Starting Binance BTC/USDT feed...")
        self._ws_task = asyncio.create_task(self._ws_loop())

    async def stop(self):
        if self._ws_task:
            self._ws_task.cancel()

    async def _ws_loop(self):
        backoff = 1
        while True:
            try:
                await self._connect()
                backoff = 1
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"[FEED] WS error: {e} -- reconnecting in {backoff}s")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

    async def _connect(self):
        async with websockets.connect(BINANCE_WS_URL, ping_interval=20, ping_timeout=10) as ws:
            self.connected = True
            logger.info("[FEED] WebSocket connected [OK]")
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                    await self._handle_message(msg)
                except json.JSONDecodeError:
                    pass

    async def _handle_message(self, msg: dict):
        stream = msg.get("stream", "")
        data   = msg.get("data", {})
        if "kline" in stream:
            k = data.get("k", {})
            if k:
                candle = Candle(
                    open_time = int(k["t"]),
                    open      = float(k["o"]),
                    high      = float(k["h"]),
                    low       = float(k["l"]),
                    close     = float(k["c"]),
                    volume    = float(k["v"]),
                    is_closed = bool(k.get("x", False)),
                )
                self.engine.add_candle(candle)
                self.current_price = candle.close
        elif "trade" in stream:
            price = float(data.get("p", 0))
            if price > 0:
                self.current_price = price
                self.engine.add_tick(price)

    async def get_price_rest(self) -> float:
        try:
            ticker = self._exchange.fetch_ticker("BTC/USDT")
            return float(ticker["last"])
        except Exception as e:
            logger.error(f"[FEED] REST failed: {e}")
            return 0.0

    async def fetch_historical_candles(self, count: int = 25):
        logger.info(f"[FEED] Loading {count} historical candles...")
        try:
            ohlcv = self._exchange.fetch_ohlcv("BTC/USDT", "1m", limit=count)
            for row in ohlcv:
                c = Candle(
                    open_time = int(row[0]),
                    open      = float(row[1]),
                    high      = float(row[2]),
                    low       = float(row[3]),
                    close     = float(row[4]),
                    volume    = float(row[5]),
                    is_closed = True,
                )
                self.engine.add_candle(c)
            if ohlcv:
                self.current_price = float(ohlcv[-1][4])
            logger.info(f"[FEED] Loaded {len(ohlcv)} candles. BTC: ${self.current_price:,.2f}")
        except Exception as e:
            logger.error(f"[FEED] Historical load failed: {e}")


# ---------------------------------------------------------------------------
# SNIPER BOT — MAIN ORCHESTRATOR
# ---------------------------------------------------------------------------

class SniperBot:
    def __init__(self):
        self.engine   = SignalEngine()
        self.feed     = BinanceFeed(self.engine)
        self.orders   = OrderManager()
        self.window   = WindowState()
        self.previous_market: PreviousMarketData = PreviousMarketData()  # Track last market
        self._running = False
        self._session: Optional[aiohttp.ClientSession] = None
        self._trade_fired_this_window = False
        self._previous_window_start = 0

    async def start(self):
        logger.info("=" * 80)
        logger.info("[BOT] POLYMARKET BTC 5-MIN SNIPER BOT v6.0 (DUAL-MARKET ANALYSIS)")
        logger.info(f"   Mode:              {BOT_MODE.upper()}")
        logger.info(f"   Dry Run:           {DRY_RUN}")
        logger.info(f"   Trade Size:        ${TRADE_SIZE_USD:.2f} per order")
        logger.info(f"   Max Active Trades: {MAX_ACTIVE_TRADES}")
        logger.info(f"   Daily Limit:       ${DAILY_LIMIT_USD:.2f}")
        logger.info(f"   Min Balance:       ${MIN_BALANCE_USD:.2f}")
        logger.info(f"   Confidence Depth:  {CONFIDENCE_THRESHOLD:.2f}")
        logger.info("   Sniper Window:     T-180s to T-120s (2-3 min before close)")
        logger.info("   Analysis Type:     DUAL-MARKET (Previous + Current)")
        logger.info("=" * 80)

        if DRY_RUN:
            logger.warning("[DRY RUN] No real money at risk")
        else:
            logger.warning("[LIVE MODE] REAL MONEY TRADING ACTIVE!")

        self.orders.initialize()
        self._session = aiohttp.ClientSession()

        await self.feed.fetch_historical_candles(25)
        await self.feed.start()
        await asyncio.sleep(3)

        self._running = True
        logger.info("[BOT] All systems ready. Entering main loop...\n")

        try:
            await self._main_loop()
        finally:
            await self.shutdown()

    async def shutdown(self):
        logger.info("[BOT] Shutting down...")
        self._running = False
        await self.feed.stop()
        if self._session:
            await self._session.close()
        self.orders.print_daily_summary()

    async def _main_loop(self):
        last_window_start = 0
        last_summary_hour = -1
        window_discovered = False  # Track if CURRENT window's market is discovered

        while self._running:
            now = int(time.time())
            window_start, window_end = get_current_window_timestamps()
            seconds_to_close         = window_end - now

            # New window detected
            if window_start != last_window_start:
                last_window_start = window_start
                self._trade_fired_this_window = False
                window_discovered = False  # Reset discovery flag for new window
                logger.info(
                    f"[WINDOW] New 5-min window | Start: "
                    f"{datetime.utcfromtimestamp(window_start).strftime('%H:%M:%S')} UTC | "
                    f"Closes in: {seconds_to_close}s"
                )
                await self._discover_window_market(window_start)
                window_discovered = True  # Market discovered for THIS window

            # Sniper window: T-180s to T-120s (2-3 minutes left before market closes)
            # ONLY run if:
            # 1. Current window's market is already discovered (not just opened)
            # 2. Enough time left in this window (180-120s)
            # 3. Haven't traded this window yet
            if window_discovered and 120 <= seconds_to_close <= 180 and not self._trade_fired_this_window:
                logger.info(f"[SNIPER] Entering analysis window (T-{seconds_to_close}s, 2-3 min left)")
                await self._sniper_poll(window_start, window_end)

            # After close: check redemptions + CAPTURE PREVIOUS MARKET DATA
            if seconds_to_close < 0:
                await self.orders.check_and_redeem(self._session)
                
                # DUAL-MARKET: Capture the previous market outcome
                # This data will be used in the NEXT window's sniper poll
                if self.window.market_id and self._previous_window_start != window_start:
                    await self._capture_previous_market_outcome(self.window, window_start)
                    self._previous_window_start = window_start

            # Hourly summary
            current_hour = datetime.utcnow().hour
            if current_hour != last_summary_hour:
                last_summary_hour = current_hour
                self.orders.print_daily_summary()

            # Continuous price update
            self.window.window_start  = window_start
            self.window.window_end    = window_end
            self.window.current_price = (
                self.feed.current_price or await self.feed.get_price_rest()
            )

            # Smart sleep
            if seconds_to_close <= 20:
                await asyncio.sleep(1)
            elif seconds_to_close <= 120:
                await asyncio.sleep(2)
            else:
                await asyncio.sleep(5)

    async def _discover_window_market(self, window_start: int):
        """
        FIX 2: Correctly map yes_token / no_token from the flat dict returned
        by discover_btc_market() into window.yes_token_id / window.no_token_id.

        The original code only searched a nested "tokens" list that this dict
        does NOT contain, so token IDs were never populated and every order
        failed with "[ORDER] Missing token_id".
        """
        if not self._session:
            return

        market = await discover_btc_market(self._session, window_start)

        if not market:
            logger.warning("[MARKET] No active BTC 5-min market -- retrying next cycle")
            return

        # --- PRIMARY PATH: flat keys (always present) --------------------
        self.window.market_id    = market.get("condition_id") or market.get("conditionId", "")
        self.window.yes_token_id = market.get("yes_token", "")
        self.window.no_token_id  = market.get("no_token",  "")
        self.window.yes_price    = market.get("yes_price", 0.5)
        self.window.no_price     = market.get("no_price",  0.5)

        # Log which window/market was discovered
        window_start_time = datetime.utcfromtimestamp(window_start).strftime('%H:%M:%S')
        window_end_time = datetime.utcfromtimestamp(window_start + 300).strftime('%H:%M:%S')
        logger.info(f"[MARKET-WINDOW] Discovered market for window {window_start_time}-{window_end_time} UTC")
        logger.info(f"[MARKET-ID] Condition ID: {self.window.market_id[:32] if self.window.market_id else 'MISSING'}")

        # --- Fallback: nested "tokens" list (future-proofing) --------
        if not self.window.yes_token_id or not self.window.no_token_id:
            tokens = market.get("tokens") or market.get("outcomes", [])
            for tok in (tokens if isinstance(tokens, list) else []):
                outcome  = (tok.get("outcome") or tok.get("name", "")).upper()
                token_id = tok.get("token_id") or tok.get("clobTokenId", "")
                if outcome in ("YES", "UP", "HIGHER", "INCREASE"):
                    self.window.yes_token_id = token_id
                elif outcome in ("NO", "DOWN", "LOWER", "DECREASE"):
                    self.window.no_token_id  = token_id

        # Log token mapping result
        logger.debug(
            f"[MARKET] Token mapping -- "
            f"YES: {self.window.yes_token_id[:16] if self.window.yes_token_id else 'MISSING'} | "
            f"NO:  {self.window.no_token_id[:16]  if self.window.no_token_id  else 'MISSING'}"
        )

        # Set open price for the new window
        if self.window.open_price <= 0 or window_start != self.window.window_start:
            self.window.open_price = (
                self.feed.current_price or await self.feed.get_price_rest()
            )
            logger.info(f"[WINDOW] Open price: ${self.window.open_price:,.2f}")

    async def _capture_previous_market_outcome(self, window: WindowState, new_window_start: int):
        """
        LIVE MARKET CLOSE MONITORING: Capture PREVIOUS market's complete technical analysis.
        
        Called AFTER previous market closes (seconds_to_close < 0 for old window).
        
        Collects:
        1. Final outcome (YES/NO)
        2. Final movement % 
        3. Technical analysis snapshot (RSI, EMA, Volume, Volatility)
        4. Momentum classification
        
        This becomes the TEMPLATE for next window's live trading.
        """
        if not self._session or not window.market_id:
            return
        
        try:
            logger.info("=" * 80)
            logger.info("[PREV-MARKET-CLOSE] Capturing previous market technical analysis...")
            logger.info("=" * 80)
            
            # ─── STEP 1: Get Final Outcome ──────────────────────────────────────
            outcome = await self.orders._get_resolution(self._session, window.market_id)
            
            if not outcome:
                logger.warning("[PREV-CAPTURE] Could not fetch resolution")
                return
            
            # Normalize direction
            if outcome.upper() in ("YES", "UP", "HIGHER", "INCREASE"):
                direction = "YES"
            elif outcome.upper() in ("NO", "DOWN", "LOWER", "DECREASE"):
                direction = "NO"
            else:
                logger.warning(f"[PREV-CAPTURE] Unknown outcome: {outcome}")
                return
            
            # ─── STEP 2: Calculate Final Movement ───────────────────────────────
            if window.current_price > 0 and window.open_price > 0:
                movement_pct = (window.current_price - window.open_price) / window.open_price * 100
            else:
                movement_pct = 0.0
            
            # Classify movement
            if movement_pct > 0.05:
                momentum_signal = "UP"
            elif movement_pct < -0.05:
                momentum_signal = "DOWN"
            else:
                momentum_signal = "NEUTRAL"
            
            # ─── STEP 3: Technical Analysis Snapshot ────────────────────────────
            # Calculate RSI from engine's final candles
            rsi_at_close = 0.0
            closed = [c for c in self.engine.candles if c.is_closed]
            if len(closed) >= 15:
                closes = [c.close for c in closed]
                deltas = np.diff(closes[-15:])
                gains = np.where(deltas > 0, deltas, 0)
                losses = np.where(deltas < 0, -deltas, 0)
                avg_g = np.mean(gains) if len(gains) > 0 else 0
                avg_l = np.mean(losses) if len(losses) > 0 else 1e-9
                rs = avg_g / avg_l
                rsi_at_close = 100 - (100 / (1 + rs))
            
            # EMA status at close
            ema_status = "neutral"
            if len(self.engine.candles) >= 22:
                closes = np.array([c.close for c in self.engine.candles])
                def ema(values, period):
                    k = 2.0 / (period + 1)
                    e = values[0]
                    for v in values[1:]:
                        e = v * k + e * (1 - k)
                    return e
                ema9 = ema(closes, 9)
                ema21 = ema(closes, 21)
                ema_status = "bullish" if ema9 > ema21 else "bearish"
            
            # Volume signal at close
            volume_signal = "weak"
            if len(self.engine.candles) >= 11:
                vols = [c.volume for c in self.engine.candles if c.is_closed]
                if len(vols) >= 11:
                    avg10 = np.mean(vols[-11:-1])
                    curr_vol = vols[-1]
                    if curr_vol > avg10 * 1.5:
                        volume_signal = "strong"
            
            # Volatility (price range vs average)
            volatility_level = "low"
            if len(self.engine.candles) >= 10:
                recent = list(self.engine.candles)[-10:]
                ranges = [c.high - c.low for c in recent]
                avg_range = np.mean(ranges) if ranges else 0
                current_range = recent[-1].high - recent[-1].low if recent else 0
                if current_range > avg_range * 1.3:
                    volatility_level = "high"
            
            # ─── STEP 4: Store Previous Market Data ═════════════════════════════
            self.previous_market = PreviousMarketData(
                window_start=window.window_start,
                market_id=window.market_id,
                direction=direction,
                movement_pct=movement_pct,
                momentum_signal=momentum_signal,
                rsi_at_close=round(rsi_at_close, 1),
                ema_status=ema_status,
                volume_signal=volume_signal,
                volatility_level=volatility_level,
                timestamp=datetime.now(timezone.utc).isoformat()
            )
            
            # ─── DETAILED LOGGING ───────────────────────────────────────────────
            logger.info(f"[PREV-OUTCOME] Final result: {direction} (Resolved UP/DOWN)")
            logger.info(f"[PREV-MOVEMENT] Price change: {movement_pct:+.4f}% ({momentum_signal})")
            logger.info("")
            logger.info(f"[PREV-TECHNICAL] Technical Snapshot at Close:")
            logger.info(f"   RSI-14:          {rsi_at_close:.1f}")
            logger.info(f"   EMA Status:      {ema_status.upper()}")
            logger.info(f"   Volume Signal:   {volume_signal.upper()}")
            logger.info(f"   Volatility:      {volatility_level.upper()}")
            logger.info("")
            logger.info("[PREV-ANALYSIS-COMPLETE] Ready for next market comparison!")
            logger.info("=" * 80)
            
        except Exception as e:
            logger.error(f"[PREV-CAPTURE] Error: {e}")
            logger.debug(f"[PREV-CAPTURE] Full traceback: {e}", exc_info=True)

    async def _sniper_poll(self, window_start: int, window_end: int):
        """
        PREDICTIVE DUAL-MARKET SNIPER (ENHANCED)
        
        Logic:
        1. Analyze PREVIOUS market behavior → Learn the pattern
        2. Analyze CURRENT market technical → What signals say
        3. Check MARKET SENTIMENT → Where are traders leaning (token prices)
        4. Predict market direction based on: previous pattern + current technical + sentiment
        5. Trade if bot's score MATCHES the prediction + confidence high + 2 min left
        
        Entry at T-180s to T-120s (2-3 min before close).
        """
        now              = int(time.time())
        seconds_to_close = window_end - now

        logger.info("=" * 80)
        logger.info(f"[SNIPER-PREDICT] T-{seconds_to_close}s | PREDICTIVE ANALYSIS")
        logger.info("=" * 80)

        # ─── SANITY CHECKS ──────────────────────────────────────────────────────
        if not self.window.market_id:
            logger.warning("[SNIPER] No market_id -- cannot trade")
            return
        if not self.window.open_price or not self.feed.current_price:
            logger.warning("[SNIPER] Missing price data -- cannot trade")
            return

        # ─── STEP 1: LEARN FROM PREVIOUS MARKET ────────────────────────────────
        logger.info("")
        logger.info("[STEP-1] Learning from PREVIOUS market pattern...")
        logger.info("─" * 80)
        
        previous_direction = ""
        previous_pattern = ""
        
        if self.previous_market.direction:
            previous_direction = self.previous_market.direction
            
            # Build pattern description
            pattern_parts = []
            if self.previous_market.momentum_signal == "UP":
                pattern_parts.append("UPWARD momentum")
            elif self.previous_market.momentum_signal == "DOWN":
                pattern_parts.append("DOWNWARD momentum")
            else:
                pattern_parts.append("NEUTRAL momentum")
            
            if self.previous_market.ema_status == "bullish":
                pattern_parts.append("BULLISH EMA")
            else:
                pattern_parts.append("BEARISH EMA")
            
            if self.previous_market.volume_signal == "strong":
                pattern_parts.append("STRONG volume")
            else:
                pattern_parts.append("WEAK volume")
            
            previous_pattern = " + ".join(pattern_parts)
            
            logger.info(f"[PREV-PATTERN] Market resolved as: {previous_direction}")
            logger.info(f"[PREV-PATTERN] Pattern was: {previous_pattern}")
            logger.info(f"[PREV-PATTERN] Movement: {self.previous_market.movement_pct:+.4f}%")
            logger.info(f"[PREV-PATTERN] RSI at close: {self.previous_market.rsi_at_close:.1f}")
        else:
            logger.info("[PREV-PATTERN] No previous market (first window of session)")

        # ─── STEP 2: ANALYZE CURRENT MARKET TECHNICAL ──────────────────────────
        logger.info("")
        logger.info("[STEP-2] Analyzing CURRENT market technical signals...")
        logger.info("─" * 80)
        
        self.window.current_price = self.feed.current_price
        if self._session:
            yes_p, no_p = await get_token_prices(self._session, self.window.market_id)
            self.window.yes_price = yes_p
            self.window.no_price  = no_p

        signal = self.engine.compute(self.window)

        current_prediction = signal.direction
        
        sign = "+" if signal.window_delta_pct > 0 else "-"
        logger.info(f"[CURRENT-TECHNICAL] Price move: {sign} {signal.window_delta_pct:+.4f}%")
        logger.info(f"[CURRENT-TECHNICAL] Composite score: {signal.composite_score:+.2f}/15.0")
        logger.info(f"[CURRENT-TECHNICAL] Bot predicts: {current_prediction}")
        logger.info(f"[CURRENT-TECHNICAL] Confidence: {signal.confidence:.1%}")

        # ─── STEP 3: CHECK MARKET SENTIMENT ────────────────────────────────────
        logger.info("")
        logger.info("[STEP-3] Reading MARKET SENTIMENT (what traders think)...")
        logger.info("─" * 80)
        
        # Token prices tell us market sentiment
        # Price > 0.50 = traders think YES (UP), < 0.50 = traders think NO (DOWN)
        market_sentiment = ""
        market_direction = ""
        
        if self.window.yes_price > self.window.no_price + 0.05:
            market_sentiment = "BULLISH (traders leaning UP)"
            market_direction = "YES"
        elif self.window.no_price > self.window.yes_price + 0.05:
            market_sentiment = "BEARISH (traders leaning DOWN)"
            market_direction = "NO"
        else:
            market_sentiment = "NEUTRAL (split opinion)"
            market_direction = ""
        
        logger.info(f"[SENTIMENT] YES price: {self.window.yes_price:.4f}")
        logger.info(f"[SENTIMENT] NO price: {self.window.no_price:.4f}")
        logger.info(f"[SENTIMENT] Market sentiment: {market_sentiment}")

        # ─── STEP 4: PREDICT MARKET DIRECTION ──────────────────────────────────
        logger.info("")
        logger.info("[STEP-4] PREDICTING market direction...")
        logger.info("─" * 80)
        
        prediction_confidence = 0.0
        final_prediction = ""
        alignment_factors = 0
        max_alignment = 3
        
        # Factor 1: Bot technical signal
        logger.info(f"[FACTOR-1] Bot technical score: {current_prediction}")
        
        # Factor 2: Market sentiment
        if market_direction:
            logger.info(f"[FACTOR-2] Market sentiment: {market_direction}")
            if current_prediction == market_direction:
                alignment_factors += 1
                logger.info(f"[ALIGN-A] ✓ Bot score MATCHES market sentiment!")
        else:
            logger.info(f"[FACTOR-2] Market sentiment: NEUTRAL (no strong bias)")
        
        # Factor 3: Previous pattern (if exists)
        if previous_direction:
            logger.info(f"[FACTOR-3] Previous market went: {previous_direction}")
            # Check if current technical aligns with previous behavior
            # If previous went UP and current shows upward momentum, stronger signal
            if current_prediction == previous_direction:
                alignment_factors += 1
                logger.info(f"[ALIGN-B] ✓ Current direction MATCHES previous market pattern!")
            else:
                logger.info(f"[ALIGN-B] ✗ Current direction DIFFERS from previous pattern")
        
        # Factor 4: Price momentum direction
        if signal.window_delta_pct > 0.02:
            if current_prediction == "YES":
                alignment_factors += 1
                logger.info(f"[ALIGN-C] ✓ Price momentum UP matches YES prediction!")
        elif signal.window_delta_pct < -0.02:
            if current_prediction == "NO":
                alignment_factors += 1
                logger.info(f"[ALIGN-C] ✓ Price momentum DOWN matches NO prediction!")
        
        logger.info("")
        logger.info(f"[PREDICTION-SCORE] Alignment factors: {alignment_factors}/{max_alignment}")
        
        # Calculate prediction confidence
        if alignment_factors == 3:
            prediction_confidence = 0.90
            final_prediction = current_prediction
            logger.info(f"[PREDICTION] STRONG: All factors aligned = Direction {final_prediction}")
        elif alignment_factors == 2:
            prediction_confidence = 0.75
            final_prediction = current_prediction
            logger.info(f"[PREDICTION] GOOD: 2/3 factors aligned = Direction {final_prediction}")
        elif alignment_factors == 1:
            prediction_confidence = 0.60
            final_prediction = current_prediction
            logger.info(f"[PREDICTION] WEAK: Only 1/3 factors aligned = Direction {final_prediction}")
        else:
            logger.warning(f"[PREDICTION] NO ALIGNMENT: Factors scattered, skip trade")
            return

        # ─── STEP 5: MATCH BOT SCORE WITH PREDICTION ────────────────────────────
        logger.info("")
        logger.info("[STEP-5] Matching BOT SCORE with PREDICTION...")
        logger.info("─" * 80)
        
        score_matches_prediction = (current_prediction == final_prediction)
        
        if score_matches_prediction:
            logger.info(f"[MATCH] ✓✓✓ Bot score ({current_prediction}) = Prediction ({final_prediction})")
            logger.info(f"[MATCH] This is a HIGH CONFIDENCE setup!")
        else:
            logger.warning(f"[MISMATCH] Bot score ({current_prediction}) != Prediction ({final_prediction})")
            logger.warning(f"[MISMATCH] Conflicting signals detected, skipping trade")
            return

        # ─── STEP 6: APPLY PREDICTION CONFIDENCE BOOST ──────────────────────────
        logger.info("")
        logger.info("[STEP-6] Applying prediction confidence boost...")
        logger.info("─" * 80)
        
        original_confidence = signal.confidence
        prediction_boost = prediction_confidence * 0.25  # Max +0.25 (25%)
        
        logger.info(f"[BOOST] Alignment confidence: {prediction_confidence:.1%}")
        logger.info(f"[BOOST] Prediction boost: +{prediction_boost:.2f} ({prediction_boost*100:.0f}%)")
        
        signal.momentum_boost = prediction_boost
        signal.confidence = max(0.0, min(1.0, signal.confidence + prediction_boost))
        
        logger.info(f"[CONFIDENCE] {original_confidence:.1%} + boost({prediction_boost:+.2f}) = {signal.confidence:.1%}")

        # ─── STEP 7: FINAL FILTERS ─────────────────────────────────────────────
        logger.info("")
        logger.info("[STEP-7] Final entry filters...")
        logger.info("─" * 80)
        
        # Filter 1: Confidence threshold
        if signal.confidence < CONFIDENCE_THRESHOLD:
            logger.warning(f"[FILTER-1] FAIL: Confidence {signal.confidence:.1%} < {CONFIDENCE_THRESHOLD:.0%}")
            return
        else:
            logger.info(f"[FILTER-1] PASS: Confidence {signal.confidence:.1%} >= {CONFIDENCE_THRESHOLD:.0%}")
        
        # Filter 2: Meaningful price movement
        if signal.window_weight == 0:
            logger.warning(f"[FILTER-2] FAIL: No significant price movement")
            return
        else:
            logger.info(f"[FILTER-2] PASS: Price movement detected ({signal.window_delta_pct:+.4f}%)")
        
        # Filter 3: Time check (need 2 min min for entry)
        if seconds_to_close < 120:
            logger.warning(f"[FILTER-3] FAIL: Not enough time left (T-{seconds_to_close}s < 2 min)")
            return
        else:
            logger.info(f"[FILTER-3] PASS: Sufficient time for entry (T-{seconds_to_close}s >= 2 min)")
        
        # Filter 4: Safe mode edge check
        if BOT_MODE == "safe":
            if not signal.has_edge:
                logger.warning(f"[FILTER-4] FAIL: Safe mode requires positive edge")
                return
            else:
                logger.info(f"[FILTER-4] PASS: Positive edge detected ({signal.edge_size:+.4f})")
        
        # Filter 5: Aggressive mode (less strict)
        if BOT_MODE == "aggressive" and signal.edge_size < -0.02:
            logger.warning(f"[FILTER-5] FAIL: Edge too negative in aggressive mode")
            return
        else:
            logger.info(f"[FILTER-5] PASS: Edge acceptable ({signal.edge_size:+.4f})")

        # ─── STEP 8: FIRE TRADE ────────────────────────────────────────────────
        logger.info("")
        logger.warning("=" * 80)
        logger.warning(f"[SNIPER-FIRE] EXECUTING TRADE")
        logger.warning("=" * 80)
        
        token_price = self.window.yes_price if current_prediction == "YES" else self.window.no_price
        
        logger.info(f"[TRADE-SETUP]")
        logger.info(f"   Direction:       {current_prediction}")
        logger.info(f"   Token Price:     {token_price:.4f}")
        logger.info(f"   Bot Confidence:  {signal.confidence:.1%}")
        logger.info(f"   Prediction Type: {alignment_factors}/3 factors aligned")
        logger.info(f"   Time Remaining:  T-{seconds_to_close}s (~{seconds_to_close//60}m {seconds_to_close%60}s)")
        logger.info("")

        trade = await self.orders.place_order(self.window, signal)

        if trade:
            self._trade_fired_this_window = True
            logger.info(f"[TRADE-SUCCESS] Position opened!")
            logger.info(f"   Order ID: {trade.trade_id}")
            logger.info(f"   Direction: {trade.direction}")
            logger.info(f"   Entry Price: {trade.entry_price:.4f}")
            logger.info(f"   Size: ${trade.size_usd:.2f}")
        else:
            logger.error("[TRADE-FAILED] Order execution failed!")


# ---------------------------------------------------------------------------
# VALIDATION & ENTRY POINT
# ---------------------------------------------------------------------------

def ensure_gitignore():
    gitignore = Path(".gitignore")
    entries   = [".env", "logs/", "__pycache__/", "*.pyc", "venv/", ".venv/"]
    if not gitignore.exists():
        gitignore.write_text("\n".join(entries) + "\n")
    else:
        content = gitignore.read_text()
        missing = [e for e in entries if e not in content]
        if missing:
            with open(gitignore, "a") as f:
                f.write("\n" + "\n".join(missing) + "\n")


def validate_config():
    errors   = []
    warnings = []

    if not DRY_RUN and not PRIVATE_KEY:
        errors.append("PRIVATE_KEY not set in .env!")
    if not DRY_RUN and not POLYMARKET_FUNDER_ADDRESS:
        errors.append("POLYMARKET_FUNDER_ADDRESS not set in .env!")

    if TRADE_SIZE_USD > 10.0:
        errors.append(f"TRADE_SIZE_USD=${TRADE_SIZE_USD:.2f} is dangerously large!")
    elif TRADE_SIZE_USD > 5.0:
        warnings.append(f"TRADE_SIZE_USD=${TRADE_SIZE_USD:.2f} is HIGH for small accounts!")
    elif TRADE_SIZE_USD < 0.5:
        warnings.append(f"TRADE_SIZE_USD=${TRADE_SIZE_USD:.2f} may hit min order size")

    max_trades = int(DAILY_LIMIT_USD / TRADE_SIZE_USD) if TRADE_SIZE_USD > 0 else 0
    logger.info(f"[CONFIG] Max trades/day: ~{max_trades} trades (${DAILY_LIMIT_USD:.2f} / ${TRADE_SIZE_USD:.2f})")

    if CONFIDENCE_THRESHOLD < 0.30:
        warnings.append(f"CONFIDENCE_THRESHOLD={CONFIDENCE_THRESHOLD:.2f} very low -- high false-positive risk!")
    elif CONFIDENCE_THRESHOLD > 0.70:
        warnings.append(f"CONFIDENCE_THRESHOLD={CONFIDENCE_THRESHOLD:.2f} very high -- may miss trades!")

    if MIN_BALANCE_USD < TRADE_SIZE_USD:
        warnings.append(f"MIN_BALANCE_USD=${MIN_BALANCE_USD:.2f} < TRADE_SIZE=${TRADE_SIZE_USD:.2f}")

    for w in warnings:
        logger.warning(f"[CONFIG-WARN] {w}")
    for e in errors:
        logger.error(f"[CONFIG] {e}")

    if errors:
        logger.error("[CONFIG] Fatal errors. Fix .env and restart.")
        sys.exit(1)

    logger.info("[CONFIG] All validations passed [OK]")


def main():
    ensure_gitignore()
    validate_config()

    print("""
============================================================
      SUPERBOT v5.1 - POLYMARKET BTC 5-MIN SNIPER BOT
      poly5min Integrated Trading Engine
      [OK] CLOB Order Execution  [OK] Balance Tracking
      [OK] Auto Redemption       [OK] Smart Sizing
      [OK] Unicode Fix           [OK] Token ID Fix
============================================================
""")
    print(f"""
========= QUICK CONFIG =====================================
  Trade Size:        ${TRADE_SIZE_USD:.2f}
  Max Concurrent:    {MAX_ACTIVE_TRADES} positions
  Daily Limit:       ${DAILY_LIMIT_USD:.2f}
  Min Balance:       ${MIN_BALANCE_USD:.2f}
  Bot Mode:          {BOT_MODE.upper()}
  Confidence Depth:  {CONFIDENCE_THRESHOLD:.2f}
============================================================
""")
    if DRY_RUN:
        print("  [DRY RUN] Testing enabled\n")
    else:
        print("  [LIVE MODE] Real money trading active\n")

    bot = SniperBot()
    try:
        asyncio.run(bot.start())
    except KeyboardInterrupt:
        logger.info("[BOT] Stopped by user")
    except Exception as e:
        logger.error(f"[BOT] Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()