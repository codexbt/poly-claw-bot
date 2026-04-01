#!/usr/bin/env python3
"""
Polymarket 5-Min Sniper Bot v8 — No LLM, Pure Technical + Reversal Exit
Fully async, no SyntaxError.
"""

import asyncio
import json
import time
import csv
import os
import sys
import re
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional, List, Dict
import requests

try:
    from dotenv import load_dotenv
    load_dotenv(override=True)
except:
    pass

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import MarketOrderArgs, OrderType, LimitOrderArgs, BalanceAllowanceParams, AssetType
from py_clob_client.order_builder.constants import BUY, SELL

# ========== CONFIG (from .env) ==========
PRIVATE_KEY = os.getenv("PRIVATE_KEY", "")
CHAIN_ID = int(os.getenv("CHAIN_ID", "137"))
SIGNATURE_TYPE = int(os.getenv("SIGNATURE_TYPE", "0"))
POLYMARKET_FUNDER_ADDRESS = os.getenv("POLYMARKET_FUNDER_ADDRESS", "")
RELAYER_URL = os.getenv("RELAYER_URL", "https://relayer.polymarket.com")
RELAYER_API_KEY = os.getenv("RELAYER_API_KEY", "")
RELAYER_API_KEY_ADDRESS = os.getenv("RELAYER_API_KEY_ADDRESS", "")

DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"
INITIAL_BALANCE = float(os.getenv("STARTING_BALANCE", "100.0"))

# Trading parameters
MAX_TRADE_SIZE = float(os.getenv("MAX_TRADE_SIZE", "7.0"))
MIN_TRADE_SIZE = float(os.getenv("MIN_TRADE_SIZE", "7.0"))
ENTRY_WAIT_SECONDS = int(os.getenv("ENTRY_WAIT_SECONDS", "30"))
BLACKLIST_DURATION = 600          # 10 minutes

# Entry thresholds (L1 filter)
MIN_MOMENTUM_1M_PCT = 0.15        # 0.15% in last minute
MIN_MOMENTUM_5M_PCT = 0.25        # 0.25% in last 5 minutes
MIN_ORDERBOOK_IMBALANCE = 0.20    # 0.20 absolute
MIN_VOLUME_SURGE = 1.8            # 1.8x average

# Exit thresholds
TAKE_PROFIT_PCT = 0.20            # +20%
STOP_LOSS_PCT = -0.06             # -6%
TRAILING_ACTIVATE_PCT = 0.10      # start trailing at +10%
TRAILING_STEP_PCT = 0.02          # trail by 2%

# Reversal detection
REVERSAL_PRICE_FLAT_THRESH = 0.001    # 0.1% change over 5 samples
REVERSAL_VOL_DELTA_THRESH = -0.5      # net selling $0.5
REVERSAL_IMBALANCE_THRESH = -0.2

# Scan intervals
SCAN_INTERVAL_SEC = 10
MONITOR_INTERVAL_SEC = 5

# Binance
BINANCE_BASES = [
    "https://api.binance.com",
    "https://api1.binance.com",
    "https://api2.binance.com",
]
BINANCE_HEADERS = {"User-Agent": "Mozilla/5.0"}

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("sniper")

# ========== DATA CLASSES ==========
@dataclass
class TradeRecord:
    timestamp: str
    symbol: str
    direction: str          # "BUY_YES" or "BUY_NO"
    entry_price: float
    trade_size_usd: float
    entry_shares: float
    market_ts: str
    condition_id: str
    token_id: str
    outcome: str = "OPEN"
    pnl: float = 0.0
    exit_price: float = 0.0
    exit_timestamp: str = ""
    stop_price: float = 0.0
    trailing_activated: bool = False

@dataclass
class BotStats:
    initial_balance: float = INITIAL_BALANCE
    current_balance: float = INITIAL_BALANCE
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    daily_spent: float = 0.0
    daily_pnl: float = 0.0

# ========== REVERSAL DETECTOR ==========
class ReversalDetector:
    def __init__(self):
        self.prices = []
        self.imbalances = []
        self.cumulative_vol_delta = 0.0

    def update(self, price: float, bid_vol: float, ask_vol: float, trade_vol_delta: float):
        self.prices.append(price)
        tot = bid_vol + ask_vol
        imb = (bid_vol - ask_vol) / tot if tot > 0 else 0
        self.imbalances.append(imb)
        if len(self.prices) > 10:
            self.prices.pop(0)
            self.imbalances.pop(0)
        self.cumulative_vol_delta += trade_vol_delta

    def is_reversal(self) -> bool:
        if len(self.prices) < 5:
            return False
        pct_change = (self.prices[-1] - self.prices[-5]) / self.prices[-5]
        avg_imb = sum(self.imbalances[-5:]) / 5
        # Price flat/falling, net selling, negative imbalance
        if pct_change < REVERSAL_PRICE_FLAT_THRESH and self.cumulative_vol_delta < REVERSAL_VOL_DELTA_THRESH and avg_imb < REVERSAL_IMBALANCE_THRESH:
            return True
        # Blow-off top: price rose >1% but volume delta negative
        if pct_change > 0.01 and self.cumulative_vol_delta < -1.0:
            return True
        return False

# ========== BINANCE FETCHER ==========
class BinanceFetcher:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(BINANCE_HEADERS)

    def _request(self, path, params):
        for base in BINANCE_BASES:
            try:
                r = self.session.get(f"{base}{path}", params=params, timeout=5)
                r.raise_for_status()
                return r.json()
            except:
                continue
        raise Exception("Binance unreachable")

    def get_klines(self, symbol, interval, limit=20):
        return self._request("/api/v3/klines", {"symbol": symbol, "interval": interval, "limit": limit})

    def get_order_book(self, symbol, limit=20):
        return self._request("/api/v3/depth", {"symbol": symbol, "limit": limit})

    def get_recent_trades(self, symbol, limit=100):
        return self._request("/api/v3/trades", {"symbol": symbol, "limit": limit})

    def get_24h_volume(self, symbol):
        data = self._request("/api/v3/ticker/24hr", {"symbol": symbol})
        return float(data.get("volume", 0))

    def compute_imbalance(self, ob):
        bids = sum(float(b[1]) for b in ob.get("bids", []))
        asks = sum(float(a[1]) for a in ob.get("asks", []))
        return (bids - asks) / (bids + asks + 1e-6)

    def get_volume_delta(self, symbol, seconds=30):
        """Net buying volume in quote asset over last N seconds"""
        trades = self.get_recent_trades(symbol, 100)
        now = time.time() * 1000
        net = 0.0
        for t in trades:
            if now - t['time'] <= seconds * 1000:
                vol = float(t['quoteQty'])
                if t['isBuyerMaker']:
                    net -= vol
                else:
                    net += vol
        return net

    def avg_volume_per_minute(self, symbol):
        vol_24h = self.get_24h_volume(symbol)
        return vol_24h / 1440.0

# ========== POLYMARKET CLOB ==========
class PolymarketExecutor:
    def __init__(self):
        self.client = None
        self.relayer_enabled = False
        if not DRY_RUN and PRIVATE_KEY:
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
                log.info("✅ CLOB initialized (relayer=%s)", self.relayer_enabled)
            except Exception as e:
                log.warning(f"CLOB init failed: {e} -> dry run")
                self.client = None

    def get_token_price(self, token_id: str) -> float:
        try:
            resp = requests.get(f"https://clob.polymarket.com/midpoint?token_id={token_id}", timeout=5)
            return float(resp.json().get("mid", 0.5))
        except:
            return 0.5

    def _submit_relayer(self, signed):
        try:
            r = requests.post(
                f"{RELAYER_URL}/order",
                headers={
                    "RELAYER_API_KEY": RELAYER_API_KEY,
                    "RELAYER_API_KEY_ADDRESS": RELAYER_API_KEY_ADDRESS,
                },
                json=signed.dict() if hasattr(signed, "dict") else signed,
                timeout=10,
            )
            return r.json()
        except:
            return None

    async def place_limit_order(self, token_id: str, side: str, size_usd: float, price_offset=0.005, max_wait=30):
        if self.client is None or DRY_RUN:
            log.info(f"  DRY-RUN limit {side} ${size_usd}")
            price = self.get_token_price(token_id) * (1 - price_offset if side == BUY else 1 + price_offset)
            shares = size_usd / price
            return {"filled": True, "price": price, "shares": shares}
        target_price = self.get_token_price(token_id) * (1 - price_offset if side == BUY else 1 + price_offset)
        shares = size_usd / target_price
        order_args = LimitOrderArgs(token_id=token_id, price=target_price, size=shares, side=side)
        signed = self.client.create_limit_order(order_args)
        if self.relayer_enabled:
            resp = self._submit_relayer(signed)
        else:
            resp = self.client.post_order(signed, OrderType.LIMIT)
        if not resp:
            return None
        start = time.time()
        while time.time() - start < max_wait:
            status = self.client.get_order(resp.get("orderID") or resp.get("id"))
            if status.get("filled"):
                filled_size = float(status.get("filled_size", shares))
                return {"filled": True, "price": target_price, "shares": filled_size}
            await asyncio.sleep(1)   # now inside async function
        # fallback to market
        return await self.place_market_order(token_id, side, size_usd)

    async def place_market_order(self, token_id: str, side: str, size_usd: float):
        if self.client is None or DRY_RUN:
            price = self.get_token_price(token_id)
            shares = size_usd / price
            return {"filled": True, "price": price, "shares": shares}
        price = self.get_token_price(token_id)
        shares = size_usd / price
        order_args = MarketOrderArgs(token_id=token_id, amount=shares, side=side, order_type=OrderType.FOK)
        signed = self.client.create_market_order(order_args)
        if self.relayer_enabled:
            self._submit_relayer(signed)
        else:
            self.client.post_order(signed, OrderType.FOK)
        return {"filled": True, "price": price, "shares": shares}

    async def place_sell_order(self, token_id: str, shares: float):
        if self.client is None or DRY_RUN:
            log.info(f"  DRY-RUN SELL {shares:.4f} shares")
            return True
        price = self.get_token_price(token_id)
        order_args = MarketOrderArgs(token_id=token_id, amount=shares, side=SELL, order_type=OrderType.FOK)
        signed = self.client.create_market_order(order_args)
        if self.relayer_enabled:
            self._submit_relayer(signed)
        else:
            self.client.post_order(signed, OrderType.FOK)
        return True

    def get_balance_usd(self) -> float:
        if self.client is None:
            return INITIAL_BALANCE
        try:
            bal = self.client.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
            return int(bal.get("balance", 0)) / 1e6
        except:
            return INITIAL_BALANCE

# ========== POLYMARKET EVENT FETCHER ==========
class PolymarketEventFetcher:
    @staticmethod
    def get_current_window_ts() -> int:
        from zoneinfo import ZoneInfo
        et = datetime.now(timezone.utc).astimezone(ZoneInfo("America/New_York"))
        window_min = (et.minute // 5) * 5
        window_start = et.replace(minute=window_min, second=0, microsecond=0)
        return int(window_start.timestamp())

    def get_market(self, symbol: str, window_ts: int) -> Optional[Dict]:
        slug = f"{symbol.lower()}-updown-5m-{window_ts}"
        url = f"https://polymarket.com/event/{slug}"
        try:
            r = requests.get(url, timeout=8)
            html = r.text
            cond_match = re.search(r'"conditionId":"([^"]+)"', html)
            token_match = re.search(r'"clobTokenIds":\s*\[([^\]]+)\]', html)
            if not cond_match or not token_match:
                return None
            token_ids = json.loads("[" + token_match.group(1) + "]")
            return {
                "condition_id": cond_match.group(1),
                "yes_token": token_ids[0],
                "no_token": token_ids[1],
                "symbol": symbol,
                "window_ts": window_ts,
            }
        except Exception as e:
            log.warning(f"Failed to fetch market {symbol}: {e}")
            return None

# ========== MAIN BOT ==========
class SniperBot:
    def __init__(self):
        self.binance = BinanceFetcher()
        self.executor = PolymarketExecutor()
        self.event_fetcher = PolymarketEventFetcher()
        self.stats = BotStats()
        self.open_trades: List[TradeRecord] = []
        self.blacklisted_symbols: Dict[str, float] = {}
        self.reversal_detectors: Dict[str, ReversalDetector] = {}
        self.last_scan_ts = 0

    def _is_blacklisted(self, sym):
        return sym in self.blacklisted_symbols and self.blacklisted_symbols[sym] > time.time()

    def _blacklist(self, sym):
        self.blacklisted_symbols[sym] = time.time() + BLACKLIST_DURATION

    def _check_entry_signals(self, symbol: str) -> Optional[Dict]:
        """Return {"action": "BUY_YES" or "BUY_NO", "confidence": 0-100} or None"""
        bin_sym = f"{symbol}USDT"
        try:
            # Get data
            klines_1m = self.binance.get_klines(bin_sym, "1m", 10)
            klines_5m = self.binance.get_klines(bin_sym, "5m", 6)
            if len(klines_1m) < 5 or len(klines_5m) < 2:
                return None

            cur_price = float(klines_1m[-1][4])
            # 1-minute momentum
            price_1m_ago = float(klines_1m[-2][4]) if len(klines_1m) >= 2 else cur_price
            mom_1m_pct = (cur_price - price_1m_ago) / price_1m_ago * 100
            # 5-minute momentum (from 5m candle)
            prev_5m_close = float(klines_5m[-2][4])
            mom_5m_pct = (cur_price - prev_5m_close) / prev_5m_close * 100

            # Order book imbalance
            ob = self.binance.get_order_book(bin_sym)
            imb = self.binance.compute_imbalance(ob)

            # Volume surge
            avg_vol_per_min = self.binance.avg_volume_per_minute(bin_sym)
            last_vol = float(klines_1m[-1][5])
            vol_surge = last_vol / avg_vol_per_min if avg_vol_per_min > 0 else 1.0

            # Decision logic
            bullish = (mom_1m_pct > MIN_MOMENTUM_1M_PCT and mom_5m_pct > MIN_MOMENTUM_5M_PCT and
                       imb > MIN_ORDERBOOK_IMBALANCE and vol_surge > MIN_VOLUME_SURGE)
            bearish = (mom_1m_pct < -MIN_MOMENTUM_1M_PCT and mom_5m_pct < -MIN_MOMENTUM_5M_PCT and
                       imb < -MIN_ORDERBOOK_IMBALANCE and vol_surge > MIN_VOLUME_SURGE)

            if not bullish and not bearish:
                return None

            action = "BUY_YES" if bullish else "BUY_NO"
            confidence = 60 + min(20, int(abs(mom_1m_pct) * 10) + int(abs(imb) * 50) + int(vol_surge * 10))
            confidence = min(95, confidence)
            return {"action": action, "confidence": confidence}

        except Exception as e:
            log.error(f"Entry check error for {symbol}: {e}")
            return None

    async def _execute_trade(self, symbol: str, signal: dict, market: dict):
        action = signal["action"]
        token_id = market["yes_token"] if action == "BUY_YES" else market["no_token"]
        size_usd = MIN_TRADE_SIZE

        # Pre-entry limit order
        result = await self.executor.place_limit_order(token_id, BUY if action == "BUY_YES" else SELL, size_usd, price_offset=0.005, max_wait=ENTRY_WAIT_SECONDS)
        if not result or not result.get("filled"):
            log.warning(f"Order not filled for {symbol} {action}")
            return

        fill_price = result["price"]
        shares = result["shares"]
        # Deduct from balance (paper or live)
        if DRY_RUN:
            self.stats.current_balance -= size_usd
        else:
            self.stats.current_balance = self.executor.get_balance_usd()

        trade = TradeRecord(
            timestamp=datetime.now().isoformat(),
            symbol=symbol,
            direction=action,
            entry_price=fill_price,
            trade_size_usd=size_usd,
            entry_shares=shares,
            market_ts=str(market["window_ts"]),
            condition_id=market["condition_id"],
            token_id=token_id,
            stop_price=fill_price * (1 + STOP_LOSS_PCT/100),
        )
        self.open_trades.append(trade)
        self.stats.total_trades += 1
        self.stats.daily_spent += size_usd
        log.info(f"🎯 ENTER {symbol} {action} @ {fill_price:.4f} | ${size_usd:.2f} | conf={signal['confidence']}%")

    async def _monitor_positions(self):
        for trade in self.open_trades[:]:
            if trade.outcome != "OPEN":
                continue
            bin_sym = f"{trade.symbol}USDT"
            try:
                # Get current price
                klines = self.binance.get_klines(bin_sym, "1m", 1)
                cur_price = float(klines[-1][4]) if klines else trade.entry_price

                # Update reversal detector
                ob = self.binance.get_order_book(bin_sym)
                bid_vol = sum(float(b[1]) for b in ob.get("bids", []))
                ask_vol = sum(float(a[1]) for a in ob.get("asks", []))
                vol_delta = self.binance.get_volume_delta(bin_sym, 30)
                if trade.condition_id not in self.reversal_detectors:
                    self.reversal_detectors[trade.condition_id] = ReversalDetector()
                det = self.reversal_detectors[trade.condition_id]
                det.update(cur_price, bid_vol, ask_vol, vol_delta)

                # Check reversal
                if det.is_reversal():
                    log.info(f"🔁 REVERSAL {trade.symbol} at ${cur_price:.4f}")
                    await self._close_position(trade, cur_price, "REVERSAL")
                    continue

                # PnL %
                pnl_pct = (cur_price - trade.entry_price) / trade.entry_price * 100

                # Trailing stop
                if pnl_pct >= TRAILING_ACTIVATE_PCT and not trade.trailing_activated:
                    trade.trailing_activated = True
                    trade.stop_price = trade.entry_price * (1 + (TRAILING_ACTIVATE_PCT - TRAILING_STEP_PCT)/100)
                if trade.trailing_activated and cur_price > trade.stop_price:
                    trade.stop_price = cur_price * (1 - TRAILING_STEP_PCT/100)

                # Check exits
                if pnl_pct >= TAKE_PROFIT_PCT:
                    await self._close_position(trade, cur_price, "TAKE_PROFIT")
                elif pnl_pct <= STOP_LOSS_PCT:
                    await self._close_position(trade, cur_price, "STOP_LOSS")
                elif trade.trailing_activated and cur_price <= trade.stop_price:
                    await self._close_position(trade, cur_price, "TRAILING_STOP")
            except Exception as e:
                log.error(f"Monitor error {trade.symbol}: {e}")

    async def _close_position(self, trade: TradeRecord, exit_price: float, reason: str):
        # Sell order
        success = await self.executor.place_sell_order(trade.token_id, trade.entry_shares)
        if not success and not DRY_RUN:
            log.warning(f"Sell failed for {trade.symbol}")
            return
        pnl = (exit_price - trade.entry_price) * trade.entry_shares
        trade.exit_price = exit_price
        trade.pnl = pnl
        trade.outcome = "WIN" if pnl >= 0 else "LOSS"
        trade.exit_timestamp = datetime.now().isoformat()
        # Update stats
        if pnl >= 0:
            self.stats.wins += 1
        else:
            self.stats.losses += 1
            self._blacklist(trade.symbol)
        self.stats.daily_pnl += pnl
        self.stats.current_balance += pnl
        self.open_trades.remove(trade)
        log.info(f"✅ CLOSE {trade.symbol} {reason} @ {exit_price:.4f} | PnL=${pnl:.2f}")

    async def _scan_window(self):
        window_ts = self.event_fetcher.get_current_window_ts()
        seconds_left = window_ts + 300 - time.time()
        if seconds_left < 60:
            log.info("⏳ Window almost closed, skipping new entries")
            return
        for symbol in ["BTC", "ETH"]:
            if self._is_blacklisted(symbol):
                continue
            signal = self._check_entry_signals(symbol)
            if not signal:
                continue
            market = self.event_fetcher.get_market(symbol, window_ts)
            if not market:
                continue
            # Ensure we don't already have an open trade for this market
            if any(t.symbol == symbol and t.market_ts == str(window_ts) for t in self.open_trades):
                continue
            await self._execute_trade(symbol, signal, market)
            await asyncio.sleep(1)  # avoid rate limit

    async def run(self):
        log.info("🚀 Sniper Bot v8 (No LLM) started")
        log.info(f"   Dry run: {DRY_RUN} | Max trade: ${MAX_TRADE_SIZE}")
        while True:
            try:
                await self._monitor_positions()
                await self._scan_window()
                # Print status
                winrate = (self.stats.wins / (self.stats.wins + self.stats.losses) * 100) if (self.stats.wins + self.stats.losses) > 0 else 0
                log.info(f"📊 Balance: ${self.stats.current_balance:.2f} | Trades: {self.stats.total_trades} | W/L: {self.stats.wins}/{self.stats.losses} ({winrate:.1f}%) | Open: {len(self.open_trades)}")
                await asyncio.sleep(SCAN_INTERVAL_SEC)
            except KeyboardInterrupt:
                break
            except Exception as e:
                log.exception(f"Error in main loop: {e}")
                await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(SniperBot().run())
# Updated 2026-03-07: Strengthen orderbook imbalance comment
# Updated 2026-03-09: Add inline guidance for dry-run mode
# Updated 2026-03-15: Improve config hints and notes
# Updated 2026-03-18: Update LLM validation note
# Updated 2026-03-24: Refine documentation details
# Updated 2026-03-30: Adjust comments for strategy clarity
# Updated 2026-04-01: Refine documentation details