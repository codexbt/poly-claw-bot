"""
Polymarket BTC & ETH 5-Minute Sniper Bot - Speed Optimized
Two-Layer LLM Decision System using OpenRouter (qwen/qwen3.6-plus)

FIXES:
1. LLM response ke TURANT BAAD current window mein trade execute hoti hai — koi queue nahi, koi wait nahi
2. LLM hard wall-clock timeout 25s (streaming + threading) — response guaranteed under 30s
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
from typing import Optional, Dict, List
from zoneinfo import ZoneInfo
import requests

try:
    from dotenv import load_dotenv
    dotenv_path = os.path.join(os.path.dirname(__file__), ".env")
    load_dotenv(dotenv_path, override=True)
    log_loaded_dotenv = True
except Exception:
    load_dotenv = None
    log_loaded_dotenv = False
    print("⚠️  python-dotenv not installed — .env will not be loaded. Install with pip install python-dotenv")

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import MarketOrderArgs, OrderType, BalanceAllowanceParams, AssetType
from py_clob_client.order_builder.constants import BUY

# ─────────────────────────────────────────────
# CONFIG - OPTIMIZED FOR SPEED
# ─────────────────────────────────────────────
OPENROUTER_API_KEY = (
    os.getenv("OPENROUTER_API_KEY")
    or os.getenv("OPENROUTER_KEY")
    or os.getenv("OR_API_KEY")
)
OPENROUTER_API_KEY_SOURCE = next(
    (name for name in ("OPENROUTER_API_KEY", "OPENROUTER_KEY", "OR_API_KEY") if os.getenv(name)),
    None
)
OPENROUTER_MODEL   = "qwen/qwen3.6-plus"
FALLBACK_MODEL     = "deepseek/deepseek-r1"

PRIVATE_KEY = os.getenv("PRIVATE_KEY", "") or os.getenv("FUNDING_PRIVATE_KEY", "")
CHAIN_ID = int(os.getenv("CHAIN_ID", "137"))
SIGNATURE_TYPE = int(os.getenv("SIGNATURE_TYPE", "0"))
POLYMARKET_FUNDER_ADDRESS = os.getenv("POLYMARKET_FUNDER_ADDRESS", "")

BINANCE_BASE       = "https://api.binance.com"
DRY_RUN            = os.getenv("DRY_RUN", "false").lower() == "true"
INITIAL_BALANCE    = 100.0
LOOP_INTERVAL      = 10

# FIX #2: Hard wall-clock LLM timeout — streaming stops after this many seconds
LLM_HARD_TIMEOUT   = 25   # Wall-clock seconds; LLM will NEVER exceed 30s total

# Layer-1 thresholds
L1_WINDOW_DELTA_THRESH  = 0.00025
L1_MOMENTUM_30S_THRESH  = 0.00035
L1_VOL_SURGE_THRESH     = 2.0

TRADE_SIZES = {(60,79): 1.5, (80,89): 5.0, (90,100): 10.0}
MIN_TRADE_SIZE = 1.0
MAX_TRADE_SIZE = 10.0

LOG_FILE   = "trades_log.csv"
LIVE_LOG   = "live_log.txt"
DASHBOARD_FILE = "dashboard_data.json"
MAX_LIVE_EVENTS = 80

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("bot")

# ─────────────────────────────────────────────
# DATA STRUCTURES
# ─────────────────────────────────────────────
@dataclass
class TradeRecord:
    timestamp: str
    symbol: str
    direction: str
    entry_price: float
    trade_size_usd: float
    confidence: int
    reasoning: str
    market_ts: str
    condition_id: str = ""
    outcome: str = "OPEN"
    pnl: float = 0.0


@dataclass
class BotStats:
    initial_balance: float = INITIAL_BALANCE
    current_balance: float = INITIAL_BALANCE
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    daily_spent: float = 0.0
    daily_pnl: float = 0.0
    llm_calls: int = 0
    llm_history: list[dict] = field(default_factory=list)
    balance_history: list[dict] = field(default_factory=list)

    @property
    def win_rate(self):
        closed = self.wins + self.losses
        return (self.wins / closed * 100) if closed else 0.0

# ─────────────────────────────────────────────
# BINANCE DATA FETCHER
# ─────────────────────────────────────────────
class BinanceFetcher:
    def get_klines(self, symbol: str, interval: str, limit: int) -> list:
        url = f"{BINANCE_BASE}/api/v3/klines"
        params = {"symbol": symbol, "interval": interval, "limit": limit}
        r = requests.get(url, params=params, timeout=5)
        r.raise_for_status()
        return r.json()

    def get_trades(self, symbol: str, limit: int = 50) -> list:
        url = f"{BINANCE_BASE}/api/v3/trades"
        r = requests.get(url, params={"symbol": symbol, "limit": limit}, timeout=5)
        r.raise_for_status()
        return r.json()

    def get_24h_ticker(self, symbol: str) -> dict:
        url = f"{BINANCE_BASE}/api/v3/ticker/24hr"
        r = requests.get(url, params={"symbol": symbol}, timeout=5)
        r.raise_for_status()
        return r.json()

    def summarize_klines(self, klines: list) -> dict:
        """Summarize candles efficiently for LLM input - SHORT VERSION."""
        if not klines:
            return {"count": 0, "trend": "UNKNOWN", "change_pct": 0, "avg_vol": 0}

        closes = [float(k[4]) for k in klines]
        volumes = [float(k[5]) for k in klines]

        trend = "UP" if closes[-1] > closes[0] else "DOWN"
        change_pct = round((closes[-1] - closes[0]) / closes[0] * 100, 3)
        avg_vol = round(sum(volumes) / len(volumes), 2)

        last_o, last_h, last_l, last_c = float(klines[-1][1]), float(klines[-1][2]), float(klines[-1][3]), closes[-1]
        last_body = abs(last_c - last_o)
        last_range = last_h - last_l
        is_doji = last_body < (last_range * 0.25) if last_range > 0 else False

        return {
            "count": len(klines),
            "trend": trend,
            "change_pct": change_pct,
            "avg_vol": avg_vol,
            "last_close": round(closes[-1], 2),
            "is_doji": is_doji
        }

# ─────────────────────────────────────────────
# POLYMARKET PRICE FETCHER
# ─────────────────────────────────────────────
class PolymarketFetcher:
    MARKET_SLUGS = {
        "BTC": "btc-updown-5m",
        "ETH": "eth-updown-5m",
    }

    def _get_market_timestamp(self) -> int:
        et = datetime.now(timezone.utc).astimezone(ZoneInfo("America/New_York"))
        window_min = (et.minute // 5) * 5
        window_start = et.replace(minute=window_min, second=0, microsecond=0)
        return int(window_start.timestamp())

    def _get_midpoint(self, token_id: str) -> float:
        url = f"https://clob.polymarket.com/midpoint?token_id={token_id}"
        resp = requests.get(url, timeout=5)
        resp.raise_for_status()
        return float(resp.json().get("mid", 0.5))

    def get_current_market(self, symbol: str, window_open_price: float, current_price: float) -> dict:
        market_ts = self._get_market_timestamp()
        slug = self.MARKET_SLUGS.get(symbol, symbol.lower() + "-updown-5m")
        url = f"https://polymarket.com/event/{slug}-{market_ts}"

        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        r.raise_for_status()
        html = r.text

        cond_match = re.search(r'"conditionId":"([^"]+)"', html)
        token_match = re.search(r'"clobTokenIds":\s*\[([^\]]+)\]', html)

        if not cond_match or not token_match:
            raise ValueError(f"Could not parse market data for {symbol} at {market_ts}")

        token_ids = json.loads("[" + token_match.group(1) + "]")
        yes_token = token_ids[0]
        no_token = token_ids[1]

        yes_price = self._get_midpoint(yes_token)
        no_price = self._get_midpoint(no_token)

        return {
            "symbol": symbol,
            "yes_price": round(yes_price, 4),
            "no_price": round(no_price, 4),
            "window_delta_pct": 0 if window_open_price == 0 else round((current_price - window_open_price) / window_open_price * 100, 5),
            "seconds_left": max(0, 300 - (int(time.time()) % 300)),
            "condition_id": cond_match.group(1),
            "yes_token": yes_token,
            "no_token": no_token,
            "market_ts": market_ts,
        }

    def get_next_market(self, symbol: str, current_price: float) -> dict:
        """
        Fetch the NEXT 5-min window's market data so we can place an advance order.
        next_market_ts = current_bucket + 300
        """
        current_bucket = self._get_market_timestamp()
        next_market_ts = current_bucket + 300

        slug = self.MARKET_SLUGS.get(symbol, symbol.lower() + "-updown-5m")
        url = f"https://polymarket.com/event/{slug}-{next_market_ts}"

        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        r.raise_for_status()
        html = r.text

        cond_match = re.search(r'"conditionId":"([^"]+)"', html)
        token_match = re.search(r'"clobTokenIds":\s*\[([^\]]+)\]', html)

        if not cond_match or not token_match:
            raise ValueError(f"Could not parse NEXT market data for {symbol} at {next_market_ts}")

        token_ids = json.loads("[" + token_match.group(1) + "]")
        yes_token = token_ids[0]
        no_token = token_ids[1]

        yes_price = self._get_midpoint(yes_token)
        no_price = self._get_midpoint(no_token)

        return {
            "symbol": symbol,
            "yes_price": round(yes_price, 4),
            "no_price": round(no_price, 4),
            "window_delta_pct": 0.0,  # next window hasn't started yet
            "seconds_left": 300,      # full window ahead
            "condition_id": cond_match.group(1),
            "yes_token": yes_token,
            "no_token": no_token,
            "market_ts": next_market_ts,
        }

    def get_market_outcome(self, condition_id: str) -> Optional[bool]:
        try:
            url = f"https://clob.polymarket.com/markets/{condition_id}"
            resp = requests.get(url, timeout=5)
            resp.raise_for_status()
            data = resp.json()
            if not data.get('active', True) and 'winner' in data:
                winner = data['winner']
                return winner == 'YES'
            return None
        except Exception as e:
            log.warning("Failed to get market outcome for %s: %s", condition_id, e)
            return None

# ─────────────────────────────────────────────
# LLM DECIDER - HARD 25s WALL-CLOCK TIMEOUT
# ─────────────────────────────────────────────
class LLMDecider:
    """
    FIX #2: Uses a background thread + threading.Event to enforce a strict
    25-second wall-clock deadline regardless of network/model latency.
    The request is made with stream=True so we can abort early.
    """

    SYSTEM_PROMPT = (
        "Return only valid JSON: "
        "{\"action\":\"BUY_YES\"|\"BUY_NO\"|\"NO_TRADE\","
        "\"confidence\":int,\"trade_size_usd\":float,"
        "\"reasoning\":string,\"suggested_entry_seconds_left\":int}. "
        "If confidence < 60 use NO_TRADE. Be concise."
    )

    def _extract_json(self, text: str) -> str:
        text = text.strip()
        if text.startswith("```"):
            parts = text.split("```")
            text = parts[1] if len(parts) >= 2 else parts[-1]
            if text.startswith("json"):
                text = text[4:]
        if text.startswith("{") and text.endswith("}"):
            return text
        start = text.find("{")
        if start == -1:
            raise ValueError("No JSON object found in LLM response")
        depth = 0
        for i, ch in enumerate(text[start:], start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
        raise ValueError("Unbalanced JSON braces in LLM response")

    def _call_with_hard_timeout(self, model: str, user_msg: str, hard_timeout: int) -> Optional[str]:
        """
        Makes the API call in a background thread.
        Main thread waits at most `hard_timeout` seconds, then returns None.
        Uses stream=True so partial content is collected before abort.
        """
        result_holder = [None]
        error_holder = [None]
        done_event = threading.Event()

        def _worker():
            try:
                headers = {
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "Content-Type": "application/json",
                }
                payload = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": self.SYSTEM_PROMPT},
                        {"role": "user", "content": user_msg},
                    ],
                    "max_tokens": 200,
                    "temperature": 0.03,
                    "stream": True,
                }
                # Connect timeout=5s, read timeout per chunk=hard_timeout
                resp = requests.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers=headers,
                    json=payload,
                    stream=True,
                    timeout=(5, hard_timeout),
                )
                if resp.status_code != 200:
                    error_holder[0] = f"HTTP {resp.status_code}: {resp.text[:200]}"
                    done_event.set()
                    return

                collected = []
                for chunk in resp.iter_lines():
                    if done_event.is_set():
                        # Main thread already timed out — stop reading
                        break
                    if not chunk:
                        continue
                    line = chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                        delta = data["choices"][0].get("delta", {})
                        token = delta.get("content", "")
                        if token:
                            collected.append(token)
                    except Exception:
                        pass

                result_holder[0] = "".join(collected)
                done_event.set()

            except Exception as e:
                error_holder[0] = str(e)
                done_event.set()

        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()

        finished_in_time = done_event.wait(timeout=hard_timeout)

        if not finished_in_time:
            # Signal worker to stop even if still reading chunks
            done_event.set()
            log.warning("  LLM hard timeout (%ds) hit for model %s", hard_timeout, model)
            return None

        if error_holder[0]:
            log.warning("  LLM worker error for %s: %s", model, error_holder[0])
            return None

        return result_holder[0]

    def call(self, market_snapshot: dict) -> Optional[dict]:
        user_msg = f"PREDICT NEXT 5MIN WINDOW:\n{json.dumps(market_snapshot, separators=(',', ':'))}"

        models = [OPENROUTER_MODEL, FALLBACK_MODEL]
        for model in models:
            t0 = time.time()
            log.info("  Calling LLM model=%s (hard_timeout=%ds)", model, LLM_HARD_TIMEOUT)
            raw = self._call_with_hard_timeout(model, user_msg, hard_timeout=LLM_HARD_TIMEOUT)
            elapsed = time.time() - t0
            log.info("  LLM response in %.2fs (model=%s)", elapsed, model)

            if not raw:
                log.warning("  No content from %s, trying fallback...", model)
                continue

            try:
                json_text = self._extract_json(raw)
                result = json.loads(json_text)
            except Exception as e:
                log.warning("  JSON parse failed for %s: %s | raw=%r", model, e, raw[:200])
                continue

            required = ["action", "confidence", "trade_size_usd", "reasoning", "suggested_entry_seconds_left"]
            if not all(k in result for k in required):
                log.warning("  LLM response missing fields: %s", list(result.keys()))
                continue

            result["confidence"] = int(result["confidence"])
            if result["confidence"] < 60:
                result["action"] = "NO_TRADE"
            result["trade_size_usd"] = float(result["trade_size_usd"])
            result["trade_size_usd"] = max(MIN_TRADE_SIZE, min(MAX_TRADE_SIZE, result["trade_size_usd"]))
            result["reasoning"] = str(result["reasoning"]).strip()
            result["suggested_entry_seconds_left"] = int(result["suggested_entry_seconds_left"])
            if not (5 <= result["suggested_entry_seconds_left"] <= 295):
                result["suggested_entry_seconds_left"] = 150
            if result["action"] not in {"BUY_YES", "BUY_NO", "NO_TRADE"}:
                log.warning("  LLM invalid action: %s", result["action"])
                continue

            return result

        log.error("All LLM models failed or timed out.")
        return None

# ─────────────────────────────────────────────
# LAYER-1 FILTER
# ─────────────────────────────────────────────
class Layer1Filter:
    def __init__(self):
        self._price_history = {}
        self._condition_counts = {}

    def _get_bucket(self, ts: float) -> int:
        return int(ts // 300) * 300

    def update_price(self, symbol: str, price: float):
        ts = time.time()
        if symbol not in self._price_history:
            self._price_history[symbol] = []
        self._price_history[symbol].append((ts, price))
        cutoff = ts - 600
        self._price_history[symbol] = [(t, p) for t, p in self._price_history[symbol] if t > cutoff]

    def momentum(self, symbol: str, seconds: int) -> float:
        hist = self._price_history.get(symbol, [])
        if not hist:
            return 0.0
        now = time.time()
        cutoff = now - seconds
        past = [p for t, p in hist if t <= cutoff]
        if not past:
            return 0.0
        return (hist[-1][1] - past[-1]) / past[-1]

    def volume_surge(self, avg_vol: float, last_vol: float) -> float:
        return last_vol / avg_vol if avg_vol > 0 else 1.0

    def _record_conditions(self, symbol: str, conditions: list, passed: bool):
        ts = time.time()
        bucket = self._get_bucket(ts)
        if symbol not in self._condition_counts:
            self._condition_counts[symbol] = {}
        if bucket not in self._condition_counts[symbol]:
            self._condition_counts[symbol][bucket] = {
                "window_delta": 0, "momentum_30s": 0, "vol_surge": 0,
                "checks": 0, "pass_count": 0,
            }
        counts = self._condition_counts[symbol][bucket]
        for condition in conditions:
            counts[condition] += 1
        counts["checks"] += 1
        if passed:
            counts["pass_count"] += 1

    def current_window_summary(self, symbol: str) -> dict:
        ts = time.time()
        bucket = self._get_bucket(ts)
        return self._condition_counts.get(symbol, {}).get(bucket, {
            "window_delta": 0, "momentum_30s": 0, "vol_surge": 0,
            "checks": 0, "pass_count": 0,
        })

    def should_call_llm(self, symbol: str, window_delta: float, momentum_30s: float, vol_surge: float, is_doji: bool) -> tuple[bool, list]:
        if is_doji:
            return False, ["doji_detected"]
        conditions_met = []
        if abs(window_delta) > L1_WINDOW_DELTA_THRESH:
            conditions_met.append("window_delta")
        if abs(momentum_30s) > L1_MOMENTUM_30S_THRESH:
            conditions_met.append("momentum_30s")
        if vol_surge > L1_VOL_SURGE_THRESH:
            conditions_met.append("vol_surge")
        should_trade = len(conditions_met) >= 2
        self._record_conditions(symbol, conditions_met, should_trade)
        return should_trade, conditions_met

# ─────────────────────────────────────────────
# TRADE EXECUTOR (Live)
# ─────────────────────────────────────────────
class TradeExecutor:
    def __init__(self):
        if DRY_RUN:
            log.info("🔵 DRY_RUN enabled — live CLOB disabled")
            self.client = None
            return

        if not PRIVATE_KEY:
            log.warning("Missing PRIVATE_KEY / FUNDING_PRIVATE_KEY — live CLOB disabled")
            self.client = None
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
            log.info("✅ CLOB client initialized successfully")
        except Exception as e:
            log.warning("Failed to initialize ClobClient: %s. Running in simulation mode.", e)
            self.client = None

    def get_balance_usd(self) -> float:
        if self.client is None:
            return INITIAL_BALANCE
        try:
            if hasattr(self.client, "get_balance_allowance"):
                response = self.client.get_balance_allowance(
                    BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
                )
                micro_usdc = int(response.get("balance", 0))
                return round(micro_usdc / 1e6, 2)
            if hasattr(self.client, "get_balances"):
                balances = self.client.get_balances()
                return float(balances.get("USDC", 0))
            return INITIAL_BALANCE
        except Exception as e:
            log.error("Failed to get balance: %s", e)
            return INITIAL_BALANCE

    def _place_order(self, token_id: str, action: str, amount: float, retry_count: int = 0) -> Optional[dict]:
        if self.client is None:
            log.error("CLOB client not initialized, cannot place live order.")
            return None

        MAX_RETRIES = 2
        FALLBACK_SIZES = [1.0, 1.0]
        MIN_FALLBACK_SIZE = 1.0

        try:
            retry_label = f" [RETRY {retry_count}]" if retry_count > 0 else ""
            attempt_size = amount if retry_count == 0 else max(
                amount * FALLBACK_SIZES[retry_count - 1], MIN_FALLBACK_SIZE
            )
            log.info("  [CLOB%s] Building market order: %s $%.2f", retry_label, action, attempt_size)
            market_order = MarketOrderArgs(
                token_id=token_id,
                amount=attempt_size,
                side=BUY,
                order_type=OrderType.FOK,
            )
            signed = self.client.create_market_order(market_order)
            log.info("  [CLOB%s] Order signed, submitting...", retry_label)
            resp = self.client.post_order(signed, OrderType.FOK)
            log.info("  [CLOB%s] Response: %s", retry_label, resp)

            if resp and resp.get("orderID"):
                log.info("  ✅ [CLOB%s] Order confirmed: %s", retry_label, resp["orderID"])
                return resp

            log.warning("  ❌ [CLOB%s] Order failed or missing orderID: %s", retry_label, resp)
            if retry_count < MAX_RETRIES:
                time.sleep(1)
                return self._place_order(token_id, action, amount, retry_count + 1)
            return None

        except Exception as e:
            log.error("  ❌ [CLOB] Order error: %s", e)
            if retry_count < MAX_RETRIES:
                time.sleep(1 + retry_count)
                return self._place_order(token_id, action, amount, retry_count + 1)
            return None

    def execute(self, decision: dict, market: dict, stats: BotStats) -> Optional[TradeRecord]:
        action = decision.get("action")
        if action == "NO_TRADE":
            return None

        confidence = int(decision.get("confidence", 0))
        if "trade_size_usd" in decision:
            size = float(decision["trade_size_usd"])
        else:
            if 60 <= confidence < 80:
                size = 1.5
            elif 80 <= confidence < 90:
                size = 5.0
            elif confidence >= 90:
                size = 10.0
            else:
                return None

        symbol = market["symbol"]
        yes_p = market["yes_price"]
        no_p = market["no_price"]
        entry_price = yes_p if action == "BUY_YES" else no_p
        if entry_price <= 0:
            log.warning("Invalid entry price: %f, skipping trade", entry_price)
            return None

        if self.client is None:
            log.warning("Live CLOB disabled; running fallback simulation.")
            stats.current_balance -= size
            stats.daily_spent += size
            stats.total_trades += 1
            return TradeRecord(
                timestamp=datetime.now().isoformat(timespec="seconds"),
                symbol=symbol,
                direction=action,
                entry_price=entry_price,
                trade_size_usd=size,
                confidence=confidence,
                reasoning=decision.get("reasoning", ""),
                market_ts=str(market.get("market_ts", "")),
                condition_id=market.get("condition_id", ""),
            )

        token_id = market["yes_token"] if action == "BUY_YES" else market["no_token"]
        if not token_id:
            log.error("Missing token_id for action %s; skipping order.", action)
            return None

        resp = self._place_order(token_id, action, size)
        if not resp:
            log.error("Order placement failed for %s %s", symbol, action)
            return None

        stats.current_balance = self.get_balance_usd()
        stats.daily_spent += size
        stats.total_trades += 1

        return TradeRecord(
            timestamp=datetime.now().isoformat(timespec="seconds"),
            symbol=symbol,
            direction=action,
            entry_price=entry_price,
            trade_size_usd=size,
            confidence=confidence,
            reasoning=decision.get("reasoning", ""),
            market_ts=str(market.get("market_ts", "")),
            condition_id=market.get("condition_id", ""),
        )

# ─────────────────────────────────────────────
# LOGGER
# ─────────────────────────────────────────────
class TradingLogger:
    def __init__(self):
        self._write_csv_header()

    def _write_csv_header(self):
        if not os.path.exists(LOG_FILE):
            with open(LOG_FILE, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=[
                    "timestamp", "symbol", "direction", "entry_price",
                    "trade_size_usd", "confidence", "reasoning", "market_ts",
                    "condition_id", "outcome", "pnl"])
                w.writeheader()

    def log_trade(self, record: TradeRecord, stats: BotStats):
        row = asdict(record)
        with open(LOG_FILE, "a", newline="") as f:
            csv.DictWriter(f, fieldnames=row.keys()).writerow(row)

        outcome_str = f" | outcome={record.outcome}" if record.outcome != "OPEN" else ""
        line = (f"[TRADE] {record.timestamp} | {record.symbol} {record.direction} "
                f"@ {record.entry_price:.4f} | ${record.trade_size_usd} | "
                f"conf={record.confidence}% | market_id={record.market_ts}{outcome_str} | {record.reasoning[:80]}")
        with open(LIVE_LOG, "a") as f:
            f.write(line + "\n")
        log.info(line)
        self.print_stats(stats)

    def print_stats(self, stats: BotStats):
        bar = "─" * 58
        print(f"\n{bar}")
        print(f"  📊 BOT STATS  {'[DRY-RUN]' if DRY_RUN else '[LIVE]'}")
        print(f"  Balance:      ${stats.initial_balance:.2f} → ${stats.current_balance:.2f}")
        print(f"  Trades today: {stats.total_trades}   W:{stats.wins} / L:{stats.losses}   WR:{stats.win_rate:.1f}%")
        print(f"  Daily spent:  ${stats.daily_spent:.2f}   PnL: ${stats.daily_pnl:+.2f}")
        print(f"{bar}\n")

# ─────────────────────────────────────────────
# DASHBOARD WRITER
# ─────────────────────────────────────────────
class DashboardWriter:
    def __init__(self, path: str = DASHBOARD_FILE, max_live: int = MAX_LIVE_EVENTS):
        self.path = path
        self.max_live = max_live
        self._ensure_file()

    def _ensure_file(self):
        if not os.path.exists(self.path):
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump({"updated_at": None}, f, indent=2)

    def write(self, stats: BotStats, live_events: list, trade_history: list, symbol_summary: dict):
        payload = {
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "stats": {
                "initial_balance": stats.initial_balance,
                "current_balance": stats.current_balance,
                "total_trades": stats.total_trades,
                "wins": stats.wins,
                "losses": stats.losses,
                "win_rate": round(stats.win_rate, 1),
                "today_pnl": round(stats.current_balance - stats.initial_balance, 2),
                "daily_spent": round(stats.daily_spent, 2),
                "llm_calls": stats.llm_calls,
            },
            "live_events": live_events[-self.max_live:],
            "trade_history": [asdict(record) for record in trade_history],
            "symbol_summary": symbol_summary,
            "llm_confidence_history": stats.llm_history[-8:],
            "balance_history": stats.balance_history[-50:],
        }
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

    def add_event(self, live_events: list, message: str):
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "message": message,
        }
        live_events.append(event)
        if len(live_events) > self.max_live:
            live_events.pop(0)

# ─────────────────────────────────────────────
# MAIN BOT LOOP
# ─────────────────────────────────────────────
class SniperBot:
    SYMBOLS = [("BTCUSDT", "BTC"), ("ETHUSDT", "ETH")]

    def __init__(self):
        self.binance     = BinanceFetcher()
        self.polymarket  = PolymarketFetcher()
        self.l1_filter   = Layer1Filter()
        self.llm         = LLMDecider()
        self.executor    = TradeExecutor()
        self.logger      = TradingLogger()
        self.dashboard   = DashboardWriter()
        self.stats       = BotStats()
        self.stats.current_balance = self.executor.get_balance_usd()
        self.stats.initial_balance = self.stats.current_balance
        self.stats.balance_history.append({
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "balance": self.stats.current_balance,
        })
        self.open_trades: list[TradeRecord] = []
        self.live_events: list[dict] = []
        self.symbol_summary: dict = {}
        self.llm_called_buckets: dict[int, set] = {}

    def _build_snapshot(self, sym_bin: str, sym_label: str, market: dict, window_delta: float, momentum_30s: float, vol_surge: float) -> dict:
        klines_1m = self.binance.get_klines(sym_bin, "1m", 20)
        last_1m = klines_1m[-1]
        prior_1m = klines_1m[-5] if len(klines_1m) >= 5 else last_1m
        return {
            "symbol": sym_label,
            "price": round(float(last_1m[4]), 4),
            "open_5m": round(float(prior_1m[1]), 4),
            "window_delta_pct": round(window_delta * 100, 4),
            "momentum_30s": round(momentum_30s * 100, 4),
            "vol_surge": round(vol_surge, 3),
            "yes_price": market["yes_price"],
            "no_price": market["no_price"],
            "seconds_left": market["seconds_left"],
        }

    def _settle_expired_trades(self):
        now = time.time()
        to_settle = [t for t in self.open_trades if now > int(t.market_ts) + 300]
        for trade in to_settle:
            outcome = self.polymarket.get_market_outcome(trade.condition_id)
            if outcome is not None:
                won = (trade.direction == "BUY_YES" and outcome) or (trade.direction == "BUY_NO" and not outcome)
                if won:
                    profit = trade.trade_size_usd
                    self.stats.current_balance += trade.trade_size_usd + profit
                    self.stats.wins += 1
                    trade.outcome = "WIN"
                    trade.pnl = profit
                else:
                    self.stats.losses += 1
                    trade.outcome = "LOSS"
                    trade.pnl = -trade.trade_size_usd
                self.logger.log_trade(trade, self.stats)
                self.dashboard.add_event(
                    self.live_events,
                    f"SETTLED {trade.symbol} {trade.direction} @ market_id={trade.market_ts} | outcome={trade.outcome} | pnl=${trade.pnl:+.2f}"
                )
                self.open_trades.remove(trade)
        if to_settle:
            self.dashboard.write(self.stats, self.live_events, self.open_trades, self.symbol_summary)

    def _get_current_market_bucket(self) -> int:
        return self.polymarket._get_market_timestamp()

    async def run(self):
        log.info("🚀  Polymarket Sniper Bot started  (model=%s  dry_run=%s)", OPENROUTER_MODEL, DRY_RUN)
        self.logger.print_stats(self.stats)
        self.dashboard.write(self.stats, self.live_events, self.open_trades, self.symbol_summary)

        while True:
            try:
                await self._tick()
            except KeyboardInterrupt:
                log.info("Bot stopped by user.")
                break
            except Exception as e:
                log.error("Tick error: %s", e)
            await asyncio.sleep(LOOP_INTERVAL)

    async def _tick(self):
        current_bucket = self._get_current_market_bucket()
        current_ts = time.time()

        # Clean up old buckets
        for old_bucket in list(self.llm_called_buckets.keys()):
            if old_bucket < current_bucket - 600:
                del self.llm_called_buckets[old_bucket]

        for sym_bin, sym_label in self.SYMBOLS:
            log.info("── Checking %s ──", sym_label)
            try:
                klines_1m = self.binance.get_klines(sym_bin, "1m", 20)
                summary_1m = self.binance.summarize_klines(klines_1m)
                ticker    = self.binance.get_24h_ticker(sym_bin)
                cur_price = float(klines_1m[-1][4])

                self.l1_filter.update_price(sym_label, cur_price)

                momentum_30s = self.l1_filter.momentum(sym_label, 30)
                open_5m  = float(klines_1m[-5][1]) if len(klines_1m) >= 5 else cur_price
                if open_5m == 0:
                    open_5m = cur_price
                win_delta = (cur_price - open_5m) / open_5m

                avg_vol  = float(ticker.get("volume", 1))
                last_vol = float(klines_1m[-1][5])
                vol_surge = self.l1_filter.volume_surge(avg_vol / 1440, last_vol)

                seconds_left = max(0, int(current_bucket + 300 - current_ts))
                self.symbol_summary[sym_label] = {
                    "current_price": round(cur_price, 4),
                    "window_delta_pct": round(win_delta * 100, 4),
                    "momentum_30s": round(momentum_30s * 100, 4),
                    "vol_surge": round(vol_surge, 3),
                    "seconds_left": seconds_left,
                }

                should_trade, conditions = self.l1_filter.should_call_llm(
                    sym_label, win_delta, momentum_30s, vol_surge, summary_1m["is_doji"])
                summary = self.l1_filter.current_window_summary(sym_label)
                event_text = (f"{sym_label} L1 pass={should_trade} | delta={win_delta*100:.4f}% "
                              f"mom30s={momentum_30s*100:.4f}% vol={vol_surge:.2f}")
                self.dashboard.add_event(self.live_events, event_text)
                log.info("  L1: delta=%.5f%% | m30s=%.5f%% | vol_surge=%.2f | pass=%s %s",
                         win_delta*100, momentum_30s*100, vol_surge, should_trade, conditions)
                log.info("  L1 5m summary: window_delta=%d momentum_30s=%d vol_surge=%d checks=%d pass=%d",
                         summary["window_delta"], summary["momentum_30s"], summary["vol_surge"],
                         summary["checks"], summary["pass_count"])

                if not should_trade:
                    log.info("  ⏭  L1 gate: not enough signals, skipping LLM.")
                    self.dashboard.write(self.stats, self.live_events, self.open_trades, self.symbol_summary)
                    continue

                if current_bucket not in self.llm_called_buckets:
                    self.llm_called_buckets[current_bucket] = set()
                if sym_label in self.llm_called_buckets[current_bucket]:
                    log.info("  ⏭  LLM already called for %s in this window, skipping.", sym_label)
                    self.dashboard.write(self.stats, self.live_events, self.open_trades, self.symbol_summary)
                    continue

                market = self.polymarket.get_current_market(sym_label, open_5m, cur_price)
                snapshot = self._build_snapshot(sym_bin, sym_label, market, win_delta, momentum_30s, vol_surge)

                log.info("  🧠  Calling LLM (Layer 2)...")
                self.stats.llm_calls += 1
                # Run LLM in executor so it doesn't block the event loop
                decision = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: self.llm.call(snapshot)
                )

                if not decision:
                    log.warning("  LLM returned no decision.")
                    self.dashboard.write(self.stats, self.live_events, self.open_trades, self.symbol_summary)
                    continue

                self.llm_called_buckets[current_bucket].add(sym_label)

                confidence = int(decision.get("confidence", 0))
                suggested_seconds = decision.get("suggested_entry_seconds_left", 150)
                action = decision.get("action", "NO_TRADE")

                log.info("  LLM → action=%s conf=%d%% entry_at=%ds reason=%s",
                         action, confidence, suggested_seconds,
                         decision.get("reasoning", "")[:60])

                if action != "NO_TRADE":
                    # ── IMMEDIATE ORDER on NEXT window ─────────────────────
                    # LLM ne current data dekh ke NEXT window predict ki hai.
                    # Ab turant next window ka market fetch karo aur order place karo.
                    log.info("  ⚡ Fetching NEXT window market for %s to place advance order...", sym_label)
                    try:
                        next_market = await asyncio.get_event_loop().run_in_executor(
                            None, lambda: self.polymarket.get_next_market(sym_label, cur_price)
                        )
                        log.info("  📋 Next market: market_ts=%d | YES=%.4f | NO=%.4f",
                                 next_market["market_ts"], next_market["yes_price"], next_market["no_price"])
                    except Exception as e:
                        log.error("  ❌ Failed to fetch next market for %s: %s — skipping trade.", sym_label, e)
                        self.dashboard.write(self.stats, self.live_events, self.open_trades, self.symbol_summary)
                        continue

                    self.dashboard.add_event(
                        self.live_events,
                        f"EXECUTING {sym_label} {action} @ conf={confidence}% on NEXT market_ts={next_market['market_ts']}"
                    )
                    record = await asyncio.get_event_loop().run_in_executor(
                        None, lambda: self.executor.execute(decision, next_market, self.stats)
                    )
                    if record:
                        self.logger.log_trade(record, self.stats)
                        self.open_trades.append(record)
                        self.dashboard.add_event(
                            self.live_events,
                            f"✅ EXECUTED {record.symbol} {record.direction} @ {record.entry_price:.4f} | ${record.trade_size_usd} | conf={record.confidence}% | next_market={next_market['market_ts']}"
                        )
                        log.info("  ✅ Advance order placed: %s %s @ %.4f | $%.2f | next_market_ts=%d",
                                 record.symbol, record.direction, record.entry_price,
                                 record.trade_size_usd, next_market["market_ts"])
                    else:
                        log.warning("  ❌ execute() returned None — order may have failed or been skipped.")
                else:
                    log.info("  ⏹ NO_TRADE from LLM.")
                    self.dashboard.add_event(
                        self.live_events,
                        f"PREDICT {sym_label} NO_TRADE @ conf={confidence}%"
                    )

                self.dashboard.write(self.stats, self.live_events, self.open_trades, self.symbol_summary)

            except requests.exceptions.RequestException as e:
                log.error("  Network error for %s: %s", sym_label, e)
            except Exception as e:
                log.error("  Error processing %s: %s", sym_label, e)

        # Settle expired trades
        self._settle_expired_trades()

# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────
if __name__ == "__main__":
    if not OPENROUTER_API_KEY:
        print("⚠️  Set OPENROUTER_API_KEY in your .env file before running.")
        print("   Example: OPENROUTER_API_KEY=sk-or-xxxxxxxxxxxxxxxxxxxxxxxx")
        sys.exit(1)

    if OPENROUTER_API_KEY_SOURCE and OPENROUTER_API_KEY_SOURCE != "OPENROUTER_API_KEY":
        log.info("Using OpenRouter API key from %s", OPENROUTER_API_KEY_SOURCE)

    if not OPENROUTER_API_KEY.startswith("sk-or-"):
        print("⚠️  The OpenRouter key does not look valid. Make sure it starts with sk-or-.")
        sys.exit(1)

    if not DRY_RUN and not PRIVATE_KEY:
        print("⚠️  Set PRIVATE_KEY or FUNDING_PRIVATE_KEY environment variable for live trading.")
        sys.exit(1)

    log.info("Starting bot with live execution=%s", not DRY_RUN)
    bot = SniperBot()
    asyncio.run(bot.run())
