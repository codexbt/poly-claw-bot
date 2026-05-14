#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════╗
║        POLYMARKET MARKOV CHAIN TRADING BOT                          ║
║  Strategy: Transition Matrix + Kelly Criterion + CLOB L2 Execution  ║
║  Based on: 0xRicker's $1.33M/30-day framework                       ║
╚══════════════════════════════════════════════════════════════════════╝

SETUP:
    pip install py-clob-client numpy requests python-dotenv schedule

ENV VARS (.env file):
    PRIVATE_KEY=0x...          # Your Polygon wallet private key
    POLYMARKET_API_KEY=...     # From polymarket.com profile
    POLYMARKET_API_SECRET=...
    POLYMARKET_API_PASSPHRASE=...
    STRATEGY=bonereaper         # bonereaper | dual_mode | multi_asset
"""

import io
import os
import sys
import time
import json
import re
import logging
import schedule
import numpy as np
import requests
from datetime import datetime, timezone
from typing import Optional, Tuple
from collections import deque
from dotenv import load_dotenv

load_dotenv()

# ─── LOGGING ───────────────────────────────────────────────────────────────────
stream_handler = logging.StreamHandler(
    io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(levelname)-8s │ %(message)s",
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        stream_handler,
    ]
)
log = logging.getLogger("polybot")

# ─── STRATEGY CONFIGS (from article's eq. 2.6–2.7) ────────────────────────────
STRATEGIES = {
    # Bonereaper: Narrow window, low variance, 1-hour BTC/ETH
    "bonereaper": {
        "entry_min":      0.83,
        "entry_max":      0.97,
        "assets":         ["BTC", "ETH"],
        "window_minutes": 60,
        "shares_min":     1500,
        "shares_max":     2900,
        "description":    "High-confidence spread capture, 83–97¢ entry"
    },
    # 0xe1D6b514: Dual-mode EV — directional scalps + near-certainty locks
    "dual_mode": {
        "entry_directional_min": 0.64,
        "entry_directional_max": 0.83,
        "entry_lock_min":        0.995,
        "entry_lock_max":        0.998,
        "assets":                ["BTC", "ETH"],
        "window_minutes":        60,
        "description":           "Dual EV: scalps at 64–83¢ + locks at 99.5–99.8¢"
    },
    # 0xB27BC932: Multi-asset, 5-min windows, variance reduced 55%
    "multi_asset": {
        "entry_min":      0.013,
        "entry_max":      0.96,
        "assets":         ["BTC", "ETH", "SOL", "BNB", "XRP"],
        "window_minutes": 5,
        "shares_min":     500,
        "shares_max":     5000,
        "description":    "5-asset portfolio, 5-min windows, σ reduced 55%"
    }
}

# ─── MASTER CONFIG ─────────────────────────────────────────────────────────────
CFG = {
    # Polymarket CLOB endpoints
    "clob_host":   "https://clob.polymarket.com",
    "gamma_host":  "https://gamma-api.polymarket.com",
    "chain_id":    137,   # Polygon mainnet

    # Markov chain parameters (from article's eq. 2.2–2.3)
    "tau":         0.87,  # State persistence threshold (eq. 2.3)
    "eps":         0.05,  # Minimum arbitrage gap (eq. 2.2)
    "n_states":    10,    # Number of discrete price states
    "window_size": 30,    # Observations for transition matrix

    # Kelly criterion (eq. 2.17)
    "kelly_f":     0.71,  # f* — aggressive but ruin-safe

    # Risk limits
    "max_trade_usdc":    500,   # Max USDC per single trade
    "max_total_usdc":    3000,  # Max total open exposure
    "scan_interval_sec": 1,     # Scan every second

    # Active strategy — change to switch modes
    "strategy": os.getenv("STRATEGY", "bonereaper"),

    # Auth
    "private_key":  os.getenv("PRIVATE_KEY", ""),
    "api_key":      os.getenv("POLYMARKET_API_KEY", ""),
    "api_secret":   os.getenv("POLYMARKET_API_SECRET", ""),
    "api_passphrase": os.getenv("POLYMARKET_API_PASSPHRASE", ""),
    "dry_run":      os.getenv("DRY_RUN", "false").strip().lower() in ("1", "true", "yes"),
}

# ─── MARKOV ENGINE ─────────────────────────────────────────────────────────────
class MarkovEngine:
    """
    Builds transition matrix P from streaming price observations.
    Implements eq. 2.1–2.5 from the article.

    States: price bucketed into [0, 1] divided into n_states equal bins.
    e.g. n_states=10 → bins [0,0.1), [0.1,0.2), ..., [0.9,1.0]
    """

    def __init__(self, n_states: int = 10, window: int = 30):
        self.n_states  = n_states
        self.window    = window
        self.obs: deque = deque(maxlen=window)   # raw price observations
        self.P = np.zeros((n_states, n_states))   # transition count matrix

    def _quantize(self, price: float) -> int:
        """Map price [0,1] → discrete state index."""
        price = max(0.001, min(0.999, price))
        return int(price * self.n_states)

    def update(self, price: float):
        """Add a new price observation and rebuild transition matrix."""
        state = self._quantize(price)
        if self.obs:
            prev = self.obs[-1]
            self.P[prev][state] += 1
        self.obs.append(state)
        self._normalize()

    def _normalize(self):
        """Row-normalize count matrix to get probability matrix."""
        row_sums = self.P.sum(axis=1, keepdims=True)
        row_sums[row_sums == 0] = 1   # avoid divide-by-zero
        self.P_norm = self.P / row_sums

    def ready(self) -> bool:
        """Need at least window/2 observations before trusting the matrix."""
        return len(self.obs) >= max(10, self.window // 2)

    def current_state(self) -> int:
        if not self.obs:
            return 0
        return self.obs[-1]

    def should_enter(self, market_price: float, tau: float, eps: float) -> Tuple[bool, dict]:
        """
        Eq. 2.2 + 2.3 — dual entry condition.
        Returns (enter: bool, diagnostics: dict)
        """
        if not self.ready():
            return False, {"reason": "insufficient_data", "obs": len(self.obs)}

        P = self.P_norm
        s = self.current_state()

        j_star  = int(np.argmax(P[s]))          # most likely next state
        p_hat   = float(P[s][j_star])           # model probability
        persist = float(P[j_star][j_star])      # diagonal: state persistence (eq. 2.3)
        gap     = p_hat - market_price          # arbitrage gap (eq. 2.2)

        enter = (gap >= eps) and (persist >= tau)

        diag = {
            "current_state":    s,
            "j_star":           j_star,
            "p_hat":            round(p_hat, 4),
            "persist":          round(persist, 4),
            "gap":              round(gap, 4),
            "market_price":     round(market_price, 4),
            "tau_ok":           persist >= tau,
            "eps_ok":           gap >= eps,
            "enter":            enter,
        }
        return enter, diag


# ─── KELLY POSITION SIZER ──────────────────────────────────────────────────────
def kelly_size(p_win: float, price: float, bankroll: float, f_star: float,
               max_trade: float) -> float:
    """
    Eq. 2.17 — Kelly criterion position size.
    f* = (p_win - (1-p_win) / odds)
    odds = (1 - price) / price   (binary market payout)
    Returns USDC amount to bet.
    """
    if price <= 0 or price >= 1:
        return 0.0
    odds   = (1.0 - price) / price            # net payout per dollar risked
    q_lose = 1.0 - p_win
    f      = (p_win - q_lose / odds) if odds > 0 else 0
    f      = max(0.0, min(f, f_star))         # cap at configured f*
    size   = f * bankroll
    return min(size, max_trade)


# ─── POLYMARKET CLOB CLIENT ───────────────────────────────────────────────────
class PolyClient:
    """
    Thin wrapper around Polymarket's CLOB REST API.
    Handles auth, market discovery, order placement.
    """

    def __init__(self):
        self.dry_run = CFG.get("dry_run", False)
        if self.dry_run:
            log.info("⚠️  DRY-RUN mode enabled via DRY_RUN env")
            self.client = None
            self._authenticated = False
        else:
            try:
                from py_clob_client.client import ClobClient
                from py_clob_client.clob_types import ApiCreds
                creds = ApiCreds(
                    api_key        = CFG["api_key"],
                    api_secret     = CFG["api_secret"],
                    api_passphrase = CFG["api_passphrase"],
                )
                self.client = ClobClient(
                    host     = CFG["clob_host"],
                    chain_id = CFG["chain_id"],
                    key      = CFG["private_key"],
                    creds    = creds,
                )
                self._authenticated = True
                log.info("✅ CLOB client authenticated")
            except Exception as e:
                log.warning(f"⚠️  CLOB client init failed: {e}")
                log.warning("    Running in DRY-RUN mode (paper trading)")
                self.client = None
                self._authenticated = False

        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0",
        })

    MARKET_SLUGS = {"BTC": "btc-updown-5m", "ETH": "eth-updown-5m"}

    @staticmethod
    def _get_current_window_ts() -> int:
        from zoneinfo import ZoneInfo
        et = datetime.now(timezone.utc).astimezone(ZoneInfo("America/New_York"))
        window_min   = (et.minute // 5) * 5
        window_start = et.replace(minute=window_min, second=0, microsecond=0)
        return int(window_start.timestamp())

    def _fetch_event_page(self, slug: str) -> Optional[str]:
        urls = [
            f"https://polymarket.com/event/{slug}",
            f"https://www.polymarket.com/event/{slug}",
        ]
        for attempt in range(1, 4):
            for url in urls:
                try:
                    log.info("  Fetching market page %s (attempt %d)", url, attempt)
                    r = self.session.get(url, timeout=12)
                    r.raise_for_status()
                    return r.text
                except requests.exceptions.RequestException as exc:
                    log.warning("  Market page fetch failed: %s — %s", url, exc)
            time.sleep(1.5)
        return None

    def _parse_event_page(self, html: str) -> Optional[dict]:
        cond_match = re.search(r'"conditionId":"([^\"]+)"', html)
        token_match = re.search(r'"clobTokenIds":\s*\[([^\]]+)\]', html)
        if not cond_match or not token_match:
            return None
        token_ids = json.loads("[" + token_match.group(1) + "]")
        if len(token_ids) < 2:
            return None
        return {
            "condition_id": cond_match.group(1),
            "clob_token_ids": token_ids,
        }

    def get_current_market_for_symbol(self, asset_keyword: str) -> Optional[dict]:
        slug = self.MARKET_SLUGS.get(asset_keyword, asset_keyword.lower() + "-updown-5m")
        market_ts = self._get_current_window_ts()
        event_slug = f"{slug}-{market_ts}"
        html = self._fetch_event_page(event_slug)
        if not html:
            return None
        market = self._parse_event_page(html)
        if not market:
            return None
        market["question"] = f"{asset_keyword} up/down 5min market"
        market["id"] = market["condition_id"]
        return market

    # ── Market Discovery ──────────────────────────────────────────────────────

    def _normalize_market_list(self, raw: object) -> list[dict]:
        if isinstance(raw, dict):
            if "markets" in raw:
                raw = raw["markets"]
            elif "data" in raw:
                raw = raw["data"]
            else:
                raw = [raw]
        return raw if isinstance(raw, list) else []

    def get_markets(self, asset_keyword: str) -> list[dict]:
        """Fetch active Polymarket markets matching asset keyword."""
        market = self.get_current_market_for_symbol(asset_keyword)
        if market:
            log.info(f"  Direct event page discovery found market for {asset_keyword}")
            return [market]

        if self._authenticated:
            try:
                raw = self.client.get_markets()
                markets = self._normalize_market_list(raw)
                filtered = [
                    m for m in markets
                    if asset_keyword.upper() in m.get("question", "").upper()
                ]
                if filtered:
                    log.info(f"  Fallback: found {len(filtered)} markets from CLOB get_markets()")
                    return filtered
            except Exception as e2:
                log.error(f"clob get_markets fallback error: {e2}")

        log.warning("  No market discovered for %s via direct event page or CLOB fallback", asset_keyword)
        return []

    def get_orderbook(self, token_id: str) -> Optional[dict]:
        """Fetch current orderbook (best bid/ask) for a token."""
        try:
            url = f"{CFG['clob_host']}/book"
            r = self.session.get(url, params={"token_id": token_id}, timeout=5)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            log.debug(f"orderbook error {token_id}: {e}")
            return None

    def get_midprice(self, token_id: str) -> Optional[float]:
        """Compute mid-price from best bid + ask."""
        book = self.get_orderbook(token_id)
        if not book:
            return None
        try:
            bids = book.get("bids", [])
            asks = book.get("asks", [])
            if not bids or not asks:
                return None
            best_bid = float(bids[0]["price"])
            best_ask = float(asks[0]["price"])
            return (best_bid + best_ask) / 2.0
        except Exception:
            return None

    def get_price_history(self, market_id: str, interval: str = "1m",
                          limit: int = 60) -> list[float]:
        """Fetch price series for transition matrix seeding."""
        try:
            url = f"{CFG['gamma_host']}/prices-history"
            params = {
                "market":   market_id,
                "interval": interval,
                "fidelity": limit,
            }
            r = self.session.get(url, params=params, timeout=10)
            r.raise_for_status()
            data = r.json()
            history = data.get("history", [])
            return [float(p["p"]) for p in history if "p" in p]
        except Exception as e:
            log.debug(f"price history error {market_id}: {e}")
            return []

    def get_balance(self) -> float:
        """Return USDC balance available for trading."""
        if not self._authenticated:
            return 100.0   # simulated balance for dry-run
        try:
            from py_clob_client.clob_types import AssetType, BalanceAllowanceParams
            params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            bal = self.client.get_balance_allowance(params)

            if isinstance(bal, dict):
                if "balance" in bal:
                    return float(bal["balance"] or 0.0)
                for item in bal.get("balances", []) or []:
                    if item.get("asset_type") == "COLLATERAL":
                        return float(item.get("available") or item.get("balance") or item.get("amount") or 0.0)
                if "available" in bal:
                    return float(bal["available"] or 0.0)
            return 0.0
        except Exception as e:
            log.error(f"balance error: {e}")
            return 0.0

    # ── Order Execution ───────────────────────────────────────────────────────

    def place_buy_order(self, token_id: str, price: float,
                        size_usdc: float) -> Optional[dict]:
        """
        Place a limit BUY order on the CLOB.
        size = size_usdc / price  (shares to buy)
        """
        size_shares = size_usdc / price if price > 0 else 0
        if size_shares < 1:
            log.warning(f"  Skip: size_shares={size_shares:.2f} < 1")
            return None

        if not self._authenticated:
            log.info(f"  [DRY-RUN] BUY {size_shares:.0f} shares @ {price:.4f}  "
                     f"(${size_usdc:.2f} USDC) token={token_id[:8]}…")
            return {"dry_run": True, "token_id": token_id, "price": price,
                    "size": size_shares, "usdc": size_usdc}

        try:
            from py_clob_client.clob_types import MarketOrderArgs
            from py_clob_client.order_builder.constants import BUY
            from py_clob_client.clob_types import OrderType
            order_args = MarketOrderArgs(
                token_id  = token_id,
                amount    = round(size_usdc, 2),
                side      = BUY,
                order_type = OrderType.FOK,
            )
            resp = self.client.create_market_order(order_args)
            if resp and hasattr(resp, 'dict'):
                resp = resp.dict()
            if resp and (resp.get("orderID") or resp.get("id")):
                log.info(f"  ✅ ORDER PLACED: {resp}")
                return resp
            log.error(f"  ❌ order failed: {resp}")
            return None
        except Exception as e:
            log.error(f"  ❌ order error: {e}")
            return None


# ─── POSITION TRACKER ─────────────────────────────────────────────────────────
class PositionTracker:
    """Simple in-memory tracker for open positions and PnL."""

    def __init__(self):
        self.positions: list[dict] = []
        self.closed:    list[dict] = []
        self.total_pnl: float      = 0.0

    def add(self, token_id: str, asset: str, market_q: str,
            price: float, size: float, usdc: float):
        self.positions.append({
            "token_id":    token_id,
            "asset":       asset,
            "question":    market_q[:60],
            "entry_price": price,
            "shares":      size,
            "cost_usdc":   usdc,
            "opened_at":   datetime.now(timezone.utc).isoformat(),
        })

    def total_exposure(self) -> float:
        return sum(p["cost_usdc"] for p in self.positions)

    def summary(self) -> str:
        return (f"Open={len(self.positions)}  "
                f"Exposure=${self.total_exposure():.2f}  "
                f"Closed={len(self.closed)}  "
                f"PnL=${self.total_pnl:.2f}")


# ─── MAIN BOT ─────────────────────────────────────────────────────────────────
class PolyBot:
    """
    Main trading loop.
    Scans all relevant markets every CFG['scan_interval_sec'] seconds.
    For each market:
      1. Fetch mid-price + price history
      2. Update Markov engine
      3. Check entry conditions (eq. 2.2 + 2.3)
      4. Size position via Kelly (eq. 2.17)
      5. Execute on CLOB if conditions met
    """

    def __init__(self):
        self.strategy   = STRATEGIES[CFG["strategy"]]
        self.client     = PolyClient()
        self.tracker    = PositionTracker()
        self.engines:   dict[str, MarkovEngine] = {}  # token_id → engine
        self.scan_count = 0

        log.info("══════════════════════════════════════════")
        log.info(f"  POLYMARKET MARKOV BOT  │  {CFG['strategy'].upper()}")
        log.info(f"  {self.strategy['description']}")
        log.info(f"  τ={CFG['tau']}  ε={CFG['eps']}  f*={CFG['kelly_f']}")
        log.info("══════════════════════════════════════════")

    def _get_engine(self, token_id: str) -> MarkovEngine:
        if token_id not in self.engines:
            self.engines[token_id] = MarkovEngine(
                n_states = CFG["n_states"],
                window   = CFG["window_size"],
            )
        return self.engines[token_id]

    def _seed_engine(self, engine: MarkovEngine, market_id: str):
        """Seed transition matrix with historical prices before live trading."""
        if engine.ready():
            return
        history = self.client.get_price_history(market_id, interval="1m", limit=40)
        if history:
            for p in history:
                engine.update(p)
            log.debug(f"  Seeded engine with {len(history)} historical prices")

    def _in_entry_range(self, price: float) -> bool:
        """Check if price falls within this strategy's entry window."""
        s = self.strategy
        if CFG["strategy"] == "dual_mode":
            in_directional = s["entry_directional_min"] <= price <= s["entry_directional_max"]
            in_lock        = s["entry_lock_min"] <= price <= s["entry_lock_max"]
            return in_directional or in_lock
        return s.get("entry_min", 0) <= price <= s.get("entry_max", 1)

    def scan_markets(self):
        """Main scan loop — called every 60 seconds."""
        self.scan_count += 1
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        log.info(f"\n── Scan #{self.scan_count} at {ts} UTC "
                 f"│ {self.tracker.summary()} ──")

        balance = self.client.get_balance()
        log.info(f"  Balance: ${balance:.2f} USDC")

        if self.tracker.total_exposure() >= CFG["max_total_usdc"]:
            log.warning("  ⚠️  Max exposure reached — skipping scan")
            return

        for asset in self.strategy["assets"]:
            markets = self.client.get_markets(asset)
            if not markets:
                log.debug(f"  No {asset} markets found")
                continue

            log.info(f"  [{asset}] {len(markets)} markets found")

            for market in markets[:5]:   # top 5 markets per asset
                self._process_market(market, asset, balance)

    def _process_market(self, market: dict, asset: str, balance: float):
        """Evaluate a single market and place order if conditions met."""
        # Polymarket token IDs live in 'clob_token_ids' or 'tokens'
        tokens    = market.get("clob_token_ids") or market.get("tokens", [])
        market_id = market.get("id") or market.get("condition_id", "")
        question  = market.get("question", "unknown")

        if not tokens:
            return

        token_id = tokens[0]   # YES token
        engine   = self._get_engine(token_id)

        # Seed with history if engine is fresh
        self._seed_engine(engine, market_id)

        # Fetch live mid-price
        price = self.client.get_midprice(token_id)
        if price is None:
            return

        # Update Markov engine with latest observation
        engine.update(price)

        # Gate 1: Is price in our entry window?
        if not self._in_entry_range(price):
            return

        # Gate 2: Markov dual condition (eq. 2.2 + 2.3)
        enter, diag = engine.should_enter(price, CFG["tau"], CFG["eps"])

        if not enter:
            log.debug(f"    {asset} @ {price:.4f} │ "
                      f"persist={diag['persist']:.3f} gap={diag['gap']:.3f} → skip")
            return

        log.info(f"  🟢 SIGNAL │ {asset} @ {price:.4f}")
        log.info(f"     Persist={diag['persist']:.3f}≥{CFG['tau']}  "
                 f"Gap={diag['gap']:.3f}≥{CFG['eps']}")
        log.info(f"     Market: {question[:60]}")

        # Gate 3: Kelly position sizing (eq. 2.17)
        p_win    = diag["p_hat"]
        size_usd = kelly_size(
            p_win     = p_win,
            price     = price,
            bankroll  = balance,
            f_star    = CFG["kelly_f"],
            max_trade = CFG["max_trade_usdc"],
        )

        if size_usd < 5:
            log.info(f"     Kelly size ${size_usd:.2f} too small — skip")
            return

        log.info(f"     Kelly size: ${size_usd:.2f} USDC  "
                 f"(p_win={p_win:.3f}, bankroll=${balance:.0f})")

        # Execute order
        result = self.client.place_buy_order(token_id, price, size_usd)
        if result:
            shares = size_usd / price
            self.tracker.add(token_id, asset, question, price, shares, size_usd)
            log.info(f"  ✅ ENTERED: {shares:.0f} shares @ {price:.4f} "
                     f"(${size_usd:.2f}) │ {question[:50]}")

    def run(self):
        """Start the bot with scheduled scans."""
        log.info("🚀 Bot started — first scan immediately, then every second")
        self.scan_markets()   # immediate first scan
        schedule.every(CFG["scan_interval_sec"]).seconds.do(self.scan_markets)

        while True:
            schedule.run_pending()
            time.sleep(1)


# ─── ENTRY POINT ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    bot = PolyBot()
    bot.run()