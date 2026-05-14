#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════════╗
║              PBot-6 Lightning Copy Trader  v1.0                          ║
║   Mirror: 0x21d0a97aac03917e752857a551bbe5103a00e8d7 (PBot-6)           ║
║   Method: CLOB REST polling (200ms) + Polygon WebSocket (0ms)            ║
║   Size  : my_size = (my_balance / target_balance) × target_trade_size    ║
╠══════════════════════════════════════════════════════════════════════════╣
║  INSTALL:  pip install -r requirements.txt                               ║
║  DRY RUN:  DRY_RUN=true python copy_bot.py   (default — safe)           ║
║  LIVE:     DRY_RUN=false python copy_bot.py  (⚠️ real USDC!)            ║
╚══════════════════════════════════════════════════════════════════════════╝
"""

# ── Standard Library ────────────────────────────────────────────────────────
import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional, Set, Tuple

# ── Third-party ─────────────────────────────────────────────────────────────
import aiohttp
from dotenv import load_dotenv

load_dotenv()

# ════════════════════════════════════════════════════════════════════════════
# CONFIGURATION  (all from .env)
# ════════════════════════════════════════════════════════════════════════════

TARGET_WALLET: str = os.getenv(
    "TARGET_WALLET", "0x21d0a97aac03917e752857a551bbe5103a00e8d7"
).lower().strip()

PRIVATE_KEY: str    = os.getenv("PRIVATE_KEY", "")
API_KEY: str        = os.getenv("API_KEY", os.getenv("POLY_API_KEY", ""))
API_SECRET: str     = os.getenv("API_SECRET", os.getenv("POLY_API_SECRET", ""))
API_PASS: str       = os.getenv("API_PASSPHRASE", os.getenv("POLY_PASSPHRASE", ""))
CHAIN_ID: int       = int(os.getenv("CHAIN_ID", "137"))
SIGNATURE_TYPE: int = int(os.getenv("SIGNATURE_TYPE", "1"))

POLYMARKET_HOST: str  = os.getenv("POLYMARKET_HOST", "https://clob.polymarket.com")
WALLET_ADDRESS: str   = os.getenv(
    "WALLET_ADDRESS",
    os.getenv("POLYMARKET_ADDRESS", os.getenv("POLYMARKET_FUNDER_ADDRESS", ""))
).lower()

# Trading flags
DRY_RUN: bool       = os.getenv("DRY_RUN", "true").lower() == "true"
PAPER_TRADING: bool = os.getenv("PAPER_TRADING", "true").lower() == "true"

# Sizing
STARTING_BALANCE: float = float(
    os.getenv("STARTING_BALANCE", os.getenv("INITIAL_BANKROLL", "100.0"))
)
MAX_BET_USD: float   = float(os.getenv("MAX_BET_USD", "50.0"))
MIN_BET_USD: float   = float(os.getenv("MIN_TRADE_SIZE", "1.0"))
MIN_BALANCE_USD: float = float(os.getenv("MIN_BALANCE_USD", "5.0"))
DAILY_LIMIT: float   = float(os.getenv("DAILY_LIMIT", "500.0"))
SLIPPAGE_PCT: float  = float(os.getenv("SLIPPAGE_PCT", "0.02"))   # 2% slippage on market orders

# Speed
POLL_INTERVAL_MS: int = int(os.getenv("POLL_INTERVAL_MS", "100"))   # 100 ms ≈ 10 req/s
MAX_TRADE_AGE_SEC: int = 10                                          # Ignore trades older than 10s

# Telegram (optional)
TG_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_CHAT: str  = os.getenv("TELEGRAM_CHAT_ID", "")

# Polygon / contract constants
POLYMARKET_EXCHANGE = "0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e"
# OrderFilled(bytes32,address,address,bytes32,bytes32,uint256,uint256,uint8,uint8)
ORDER_FILLED_TOPIC  = "0xd0a08e8c493f9c94f29311604c9de1b4e8c8d4c06a6f2beabef5e51ebb03b5e3"

# ════════════════════════════════════════════════════════════════════════════
# LOGGING
# ════════════════════════════════════════════════════════════════════════════

LOG_LEVEL = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
_log_file = f"copybot_{datetime.now().strftime('%Y%m%d')}.log"
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s.%(msecs)03d │ %(levelname)-8s │ %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(_log_file, encoding="utf-8"),
    ],
)
log = logging.getLogger("CopyBot")

# ════════════════════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ════════════════════════════════════════════════════════════════════════════

@dataclass
class Trade:
    trade_id:    str
    market_id:   str       # conditionId / market address
    token_id:    str       # ERC-1155 outcome token id
    side:        str       # "BUY" | "SELL"
    size:        float     # shares
    price:       float     # 0.00 – 1.00
    amount_usd:  float     # USDC notional
    timestamp:   float     # unix seconds
    outcome:     str = ""  # "YES" | "NO"
    market_slug: str = ""
    tx_hash:     str = ""


@dataclass
class BotState:
    my_balance:              float = STARTING_BALANCE
    target_balance_estimate: float = 1_000.0          # bootstrap assumption
    seen_ids:                Set[str] = field(default_factory=set)
    daily_spent:             float = 0.0
    day_start:               float = field(default_factory=time.time)
    total_trades:            int   = 0
    paper_pnl:               float = 0.0              # running P&L for dry-run
    active_positions:        Dict[str, dict] = field(default_factory=dict)
    last_balance_fetch:      float = 0.0
    last_target_bal_fetch:   float = 0.0

# ════════════════════════════════════════════════════════════════════════════
# CLOB HTTP CLIENT  (async, no heavy SDK needed for monitoring)
# ════════════════════════════════════════════════════════════════════════════

class ClobAPI:
    """
    Lightweight async wrapper around Polymarket CLOB + data API.
    Authenticated calls use HMAC-SHA256 per the official spec.
    """

    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
        self.host    = POLYMARKET_HOST

    # ── Auth helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _sign(ts: str, method: str, path: str, body: str = "") -> str:
        """CLOB HMAC-SHA256: sign(timestamp + METHOD + path + body)"""
        raw_secret = base64.b64decode(API_SECRET + "==")   # pad if needed
        msg = (ts + method.upper() + path + body).encode()
        sig = hmac.new(raw_secret, msg, hashlib.sha256).digest()
        return base64.b64encode(sig).decode()

    def _auth_headers(self, method: str, path: str, body: str = "") -> dict:
        ts = str(int(time.time() * 1000))   # milliseconds
        return {
            "POLY-API-KEY":    API_KEY,
            "POLY-TIMESTAMP":  ts,
            "POLY-SIGNATURE":  self._sign(ts, method, path, body),
            "POLY-PASSPHRASE": API_PASS,
            "Content-Type":    "application/json",
            "Accept":          "application/json",
        }

    # ── Balance ─────────────────────────────────────────────────────────────

    async def get_my_usdc_balance(self) -> Optional[float]:
        """Returns USDC balance in dollars (CLOB authenticated endpoint)."""
        path = "/balance-allowance?asset_type=collateral"
        try:
            async with self.session.get(
                self.host + path,
                headers=self._auth_headers("GET", path),
                timeout=aiohttp.ClientTimeout(total=3),
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    if isinstance(data, dict):
                        raw = data.get("balance", data.get("allowance", 0))
                        return float(raw) / 1e6   # USDC has 6 decimals
                    log.debug(f"Balance endpoint returned non-dict: {type(data)}")
        except Exception as exc:
            log.debug(f"get_my_usdc_balance: {exc}")
        return None

    # ── Target activity monitoring ───────────────────────────────────────────

    async def get_target_activity(self, limit: int = 5) -> List[dict]:
        """Polymarket Data API — public activity feed for a wallet."""
        urls = [
            f"https://data-api.polymarket.com/activity?user={TARGET_WALLET}&limit={limit}",
            f"https://data-api.polymarket.com/activity?proxyWallet={TARGET_WALLET}&limit={limit}",
        ]
        for url in urls:
            try:
                async with self.session.get(
                    url, timeout=aiohttp.ClientTimeout(total=2)
                ) as r:
                    if r.status == 200:
                        data = await r.json()
                        # Flatten nested structures to get list of dicts
                        result = []
                        if isinstance(data, list):
                            for item in data:
                                if isinstance(item, list):
                                    result.extend([x for x in item if isinstance(x, dict)])
                                elif isinstance(item, dict):
                                    result.append(item)
                        elif isinstance(data, dict):
                            inner = data.get("data", data.get("activities", []))
                            if isinstance(inner, list):
                                for item in inner:
                                    if isinstance(item, list):
                                        result.extend([x for x in item if isinstance(x, dict)])
                                    elif isinstance(item, dict):
                                        result.append(item)
                        return result
            except Exception as exc:
                log.debug(f"get_target_activity: {exc}")
        return []

    async def get_clob_trades(self, limit: int = 5) -> List[dict]:
        """CLOB trades endpoint — using authenticated request like swissbot."""
        try:
            # Use authenticated request similar to swissbot approach
            url = f"https://data-api.polymarket.com/activity"
            params = {
                "user": TARGET_WALLET,
                "type": "TRADE",
                "limit": limit,
                "sortBy": "TIMESTAMP",
                "sortDirection": "DESC",
            }
            async with self.session.get(
                url, params=params, timeout=aiohttp.ClientTimeout(total=2)
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    records = data if isinstance(data, list) else data.get("data", [])
                    result = []
                    for raw in records:
                        if isinstance(raw, dict):
                            # Convert to expected format
                            converted = {
                                "id": raw.get("transactionHash", raw.get("transaction_hash", "")),
                                "tradeId": raw.get("transactionHash", raw.get("transaction_hash", "")),
                                "side": raw.get("side", raw.get("type", "BUY")),
                                "size": raw.get("usdcSize", raw.get("size", 0)),
                                "price": raw.get("price", raw.get("avgPrice", 0.5)),
                                "asset": raw.get("asset", raw.get("tokenId", "")),
                                "conditionId": raw.get("conditionId", raw.get("condition_id", "")),
                                "timestamp": raw.get("timestamp", raw.get("createdAt", int(time.time()))),
                                "outcome": raw.get("outcome", ""),
                                "marketSlug": raw.get("slug", raw.get("market", "")),
                                "txHash": raw.get("transactionHash", raw.get("transaction_hash", "")),
                            }
                            result.append(converted)
                    return result
        except Exception as exc:
            log.debug(f"get_clob_trades: {exc}")
        return []

    async def get_target_portfolio(self) -> Optional[dict]:
        """Returns portfolio/balance info for target wallet."""
        urls = [
            f"https://data-api.polymarket.com/value?user={TARGET_WALLET}",
            f"https://data-api.polymarket.com/portfolio?user={TARGET_WALLET}",
        ]
        for url in urls:
            try:
                async with self.session.get(
                    url, timeout=aiohttp.ClientTimeout(total=3)
                ) as r:
                    if r.status == 200:
                        data = await r.json()
                        if isinstance(data, dict):
                            return data
            except Exception as exc:
                log.debug(f"get_target_portfolio: {exc}")
        return None

    # ── Market info ─────────────────────────────────────────────────────────

    async def get_order_book(self, token_id: str) -> Optional[dict]:
        """Best bid/ask for sizing reference."""
        try:
            async with self.session.get(
                f"{self.host}/book?token_id={token_id}",
                timeout=aiohttp.ClientTimeout(total=1),
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    if isinstance(data, dict):
                        return data
        except Exception as exc:
            log.debug(f"get_order_book: {exc}")
        return None

    # ── Order placement ──────────────────────────────────────────────────────

    async def place_market_order(
        self,
        token_id: str,
        side: str,
        shares: float,
        ref_price: float,
    ) -> Optional[dict]:
        """
        Place a FOK (Fill-or-Kill) market order via py-clob-client.
        Returns order result dict, or None on failure.
        DRY_RUN / PAPER_TRADING → returns a synthetic dict, no real order.
        """
        if DRY_RUN or PAPER_TRADING:
            return {
                "dry_run": True,
                "orderID": f"PAPER-{int(time.time()*1000)}",
                "token_id": token_id,
                "side": side,
                "size": shares,
                "price": ref_price,
            }

        try:
            from py_clob_client.client import ClobClient
            from py_clob_client.clob_types import OrderArgs, OrderType
            from py_clob_client.order_builder.constants import BUY, SELL

            client = ClobClient(
                host=self.host,
                key=PRIVATE_KEY,
                chain_id=CHAIN_ID,
                api_creds={
                    "apiKey": API_KEY,
                    "apiSecret": API_SECRET,
                    "apiPassphrase": API_PASS,
                },
                signature_type=SIGNATURE_TYPE,
                funder=WALLET_ADDRESS or None,
            )

            # Add slippage buffer for market-like execution
            slip = SLIPPAGE_PCT
            exec_price = ref_price * (1 + slip) if side == "BUY" else ref_price * (1 - slip)
            exec_price = round(min(0.99, max(0.01, exec_price)), 4)

            order_args = OrderArgs(
                token_id=token_id,
                price=exec_price,
                size=round(shares, 2),
                side=BUY if side == "BUY" else SELL,
            )

            signed_order = client.create_and_sign_order(order_args)
            result = client.post_order(signed_order, OrderType.FOK)
            return result

        except ImportError:
            log.error("py-clob-client missing!  pip install py-clob-client")
        except Exception as exc:
            log.error(f"place_market_order error: {exc}")
        return None


# ════════════════════════════════════════════════════════════════════════════
# POSITION SIZER
# ════════════════════════════════════════════════════════════════════════════

def proportional_size(
    target_usd: float,
    target_price: float,
    state: BotState,
) -> Tuple[float, float]:
    """
    Returns (shares, usd) for our copy trade.

    Formula
    ───────
        ratio    = my_balance / target_balance
        raw_usd  = ratio × target_usd
        shares   = capped_usd / price
    """
    if state.target_balance_estimate <= 0 or target_price <= 0:
        return 0.0, 0.0

    ratio   = state.my_balance / state.target_balance_estimate
    raw_usd = ratio * target_usd

    # --- hard caps ---
    usd = min(raw_usd, MAX_BET_USD)

    # daily budget guard
    remaining_daily = max(0.0, DAILY_LIMIT - state.daily_spent)
    usd = min(usd, remaining_daily)

    # min balance guard
    available = max(0.0, state.my_balance - MIN_BALANCE_USD)
    usd = min(usd, available)

    if usd < MIN_BET_USD:
        return 0.0, 0.0

    shares = round(usd / target_price, 2)
    usd    = round(usd, 4)
    return shares, usd


# ════════════════════════════════════════════════════════════════════════════
# TRADE PARSER  (handles both data-api and CLOB formats)
# ════════════════════════════════════════════════════════════════════════════

def _safe_float(raw, default: float = 0.0) -> float:
    try:
        v = float(raw)
        return v if v == v else default   # NaN guard
    except (TypeError, ValueError):
        return default


def parse_trade(raw: dict) -> Optional[Trade]:
    """Convert raw API dict → Trade dataclass, enhanced for data-api format like swissbot."""
    try:
        # ── ID ───────────────────────────────────────────────────────────
        trade_id = str(
            raw.get("id")
            or raw.get("tradeId")
            or raw.get("txHash")
            or raw.get("transactionHash")
            or ""
        ).strip()
        if not trade_id:
            return None

        # ── Side ─────────────────────────────────────────────────────────
        tx_type = str(
            raw.get("type", raw.get("action", raw.get("side", "")))
        ).upper()
        side = "SELL" if any(k in tx_type for k in ("SELL", "REDEEM", "MERGE")) else "BUY"

        # ── Price ────────────────────────────────────────────────────────
        price = _safe_float(raw.get("price", raw.get("avgPrice", raw.get("outcomePrice", 0.5))))
        if price > 1.0:
            price /= 100.0   # some endpoints return cents
        price = max(0.001, min(0.999, price))

        # ── Size ─────────────────────────────────────────────────────────
        size = _safe_float(raw.get("size", raw.get("shares", raw.get("amount", 0))))
        amount_usd = _safe_float(
            raw.get("usdcSize", raw.get("investment", raw.get("cost", size * price)))
        )
        if amount_usd <= 0 and size > 0:
            amount_usd = size * price

        # ── IDs ──────────────────────────────────────────────────────────
        token_id = str(
            raw.get("asset", raw.get("tokenId", raw.get("outcomeTokenId", "")))
        )
        market_id = str(
            raw.get("market", raw.get("conditionId", raw.get("marketId", raw.get("marketConditionId", ""))))
        )

        # ── Timestamp ────────────────────────────────────────────────────
        ts_raw = raw.get("timestamp", raw.get("createdAt", raw.get("blockTimestamp", time.time())))
        if isinstance(ts_raw, str):
            try:
                ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00")).timestamp()
            except Exception:
                ts = time.time()
        else:
            ts = float(ts_raw or time.time())
        # If timestamp looks like milliseconds, convert
        if ts > 1e12:
            ts /= 1000.0

        # ── Outcome ──────────────────────────────────────────────────────
        outcome_raw = str(raw.get("outcome", raw.get("outcomeName", ""))).upper()
        outcome = "NO" if "NO" in outcome_raw else ("YES" if "YES" in outcome_raw else "")

        return Trade(
            trade_id=trade_id,
            market_id=market_id,
            token_id=token_id,
            side=side,
            size=size,
            price=price,
            amount_usd=amount_usd,
            timestamp=ts,
            outcome=outcome,
            market_slug=str(raw.get("slug", raw.get("marketSlug", raw.get("title", ""))),
            tx_hash=str(raw.get("txHash", raw.get("transactionHash", ""))),
        )
    except Exception as exc:
        log.debug(f"parse_trade error: {exc} | raw={json.dumps(raw)[:200]}")
        return None

        # ── Side ─────────────────────────────────────────────────────────
        tx_type = str(
            raw.get("type", raw.get("action", raw.get("side", "")))
        ).upper()
        side = "SELL" if any(k in tx_type for k in ("SELL", "REDEEM", "MERGE")) else "BUY"

        # ── Price ────────────────────────────────────────────────────────
        price = _safe_float(raw.get("price", raw.get("avgPrice", raw.get("outcomePrice", 0.5))))
        if price > 1.0:
            price /= 100.0   # some endpoints return cents
        price = max(0.001, min(0.999, price))

        # ── Size ─────────────────────────────────────────────────────────
        size = _safe_float(raw.get("size", raw.get("shares", raw.get("amount", 0))))
        amount_usd = _safe_float(
            raw.get("usdcSize", raw.get("investment", raw.get("cost", size * price)))
        )
        if amount_usd <= 0 and size > 0:
            amount_usd = size * price

        # ── IDs ──────────────────────────────────────────────────────────
        token_id = str(
            raw.get("asset", raw.get("tokenId", raw.get("outcomeTokenId", "")))
        )
        market_id = str(
            raw.get("market", raw.get("conditionId", raw.get("marketId", raw.get("marketConditionId", ""))))
        )

        # ── Timestamp ────────────────────────────────────────────────────
        ts_raw = raw.get("timestamp", raw.get("createdAt", raw.get("blockTimestamp", time.time())))
        if isinstance(ts_raw, str):
            try:
                ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00")).timestamp()
            except Exception:
                ts = time.time()
        else:
            ts = float(ts_raw or time.time())
        # If timestamp looks like milliseconds, convert
        if ts > 1e12:
            ts /= 1000.0

        # ── Outcome ──────────────────────────────────────────────────────
        outcome_raw = str(raw.get("outcome", raw.get("outcomeName", ""))).upper()
        outcome = "NO" if "NO" in outcome_raw else ("YES" if "YES" in outcome_raw else "")

        return Trade(
            trade_id=trade_id,
            market_id=market_id,
            token_id=token_id,
            side=side,
            size=size,
            price=price,
            amount_usd=amount_usd,
            timestamp=ts,
            outcome=outcome,
            market_slug=str(raw.get("slug", raw.get("marketSlug", raw.get("title", "")))),
            tx_hash=str(raw.get("txHash", raw.get("transactionHash", ""))),
        )
    except Exception as exc:
        log.debug(f"parse_trade error: {exc} | raw={json.dumps(raw)[:200]}")
        return None


# ════════════════════════════════════════════════════════════════════════════
# TELEGRAM
# ════════════════════════════════════════════════════════════════════════════

async def tg(session: aiohttp.ClientSession, text: str) -> None:
    """Fire-and-forget Telegram message. Silent on any error."""
    if not TG_TOKEN or not TG_CHAT:
        return
    try:
        await session.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT, "text": text, "parse_mode": "Markdown"},
            timeout=aiohttp.ClientTimeout(total=5),
        )
    except Exception:
        pass


# ════════════════════════════════════════════════════════════════════════════
# MAIN BOT
# ════════════════════════════════════════════════════════════════════════════

class CopyBot:

    def __init__(self):
        self.state   = BotState()
        self.session: Optional[aiohttp.ClientSession] = None
        self.clob:    Optional[ClobAPI] = None
        self.running  = True
        self._ws_triggered = asyncio.Event()   # WebSocket pokes poll loop

    # ── Startup ─────────────────────────────────────────────────────────────

    async def startup(self):
        connector = aiohttp.TCPConnector(
            limit=50,
            ttl_dns_cache=300,
            use_dns_cache=True,
            keepalive_timeout=60,
            enable_cleanup_closed=True,
        )
        self.session = aiohttp.ClientSession(connector=connector)
        self.clob    = ClobAPI(self.session)

        mode_str = "🔴  DRY RUN (paper trading)" if (DRY_RUN or PAPER_TRADING) else "🟢  LIVE — Real USDC!"
        log.info("═" * 65)
        log.info("    PBot-6 Lightning Copy Trader  v1.0")
        log.info(f"    Mode     : {mode_str}")
        log.info(f"    Target   : {TARGET_WALLET[:12]}…{TARGET_WALLET[-6:]}")
        log.info(f"    My Wallet: {WALLET_ADDRESS[:12]}…{WALLET_ADDRESS[-6:] if len(WALLET_ADDRESS) > 12 else '(not set)'}")
        log.info(f"    Balance  : ${self.state.my_balance:.2f}  (paper start)")
        log.info(f"    Max Bet  : ${MAX_BET_USD:.2f}  |  Daily Limit: ${DAILY_LIMIT:.2f}")
        log.info(f"    Poll     : {POLL_INTERVAL_MS} ms  |  Max Trade Age: {MAX_TRADE_AGE_SEC}s")
        log.info("═" * 65)

        if not (DRY_RUN or PAPER_TRADING):
            log.warning("⚠️  LIVE MODE active — real USDC will be spent on each copy trade!")

        await self._refresh_my_balance(force=True)

    # ── Balance management ───────────────────────────────────────────────────

    async def _refresh_my_balance(self, force: bool = False):
        now = time.time()
        if not force and (now - self.state.last_balance_fetch) < 30:
            return
        self.state.last_balance_fetch = now

        if not (DRY_RUN or PAPER_TRADING) and API_KEY:
            bal = await self.clob.get_my_usdc_balance()
            if bal is not None:
                self.state.my_balance = bal
                log.info(f"💰 My USDC balance (live): ${bal:.4f}")
        else:
            log.debug(f"Paper balance: ${self.state.my_balance:.4f}")

    async def _refresh_target_balance(self, raw_trades: List[dict]):
        """
        Best-effort target balance estimation:
        1. Use remainingBalance field in trade data (if present)
        2. Fetch portfolio endpoint
        3. Keep last known value
        """
        now = time.time()

        # Try field in recent trade data first (lowest latency)
        for raw in raw_trades:
            if not isinstance(raw, dict):
                continue
            for key in ("remainingBalance", "balance", "collateralBalance", "cashBalance", "value"):
                v = raw.get(key)
                if v is not None:
                    try:
                        bal = float(v)
                        if bal > 0:
                            self.state.target_balance_estimate = bal
                            log.debug(f"Target balance from trade field: ${bal:.2f}")
                            return
                    except (TypeError, ValueError):
                        pass

        # Throttle portfolio fetch to once per 10s
        if now - self.state.last_target_bal_fetch < 10:
            return
        self.state.last_target_bal_fetch = now

        portfolio = await self.clob.get_target_portfolio()
        if portfolio:
            for key in ("value", "balance", "usdc", "cashBalance", "portfolioValue", "total"):
                v = portfolio.get(key)
                if v is not None:
                    try:
                        bal = float(v)
                        if bal > 0:
                            self.state.target_balance_estimate = bal
                            log.info(f"🎯 Target balance (portfolio): ${bal:.2f}")
                            return
                    except (TypeError, ValueError):
                        pass

    # ── Daily reset ──────────────────────────────────────────────────────────

    def _check_daily_reset(self):
        if time.time() - self.state.day_start >= 86_400:
            self.state.daily_spent = 0.0
            self.state.day_start   = time.time()
            log.info("📅 Daily spend limit reset to $0")

    # ── Copy execution ───────────────────────────────────────────────────────

    async def execute_copy(self, trade: Trade):
        t0 = time.perf_counter()

        shares, usd = proportional_size(trade.amount_usd, trade.price, self.state)
        ratio = self.state.my_balance / max(self.state.target_balance_estimate, 1)

        log.info("─" * 60)
        log.info(f"  📡 TARGET TRADE  {trade.side} {'(' + trade.outcome + ')' if trade.outcome else ''}")
        log.info(f"     Market  : {trade.market_slug or trade.token_id[:24] or trade.market_id[:24]}")
        log.info(f"     Price   : {trade.price:.4f}  ({trade.price * 100:.1f}¢)")
        log.info(f"     Target  : {trade.size:.2f} shares  ≈ ${trade.amount_usd:.2f}")
        log.info(f"     Ratio   : {ratio:.2%}  (my ${self.state.my_balance:.2f}  ÷  target ${self.state.target_balance_estimate:.2f})")

        if shares <= 0 or usd <= 0:
            log.info(f"     ⏭️  SKIP — computed size ${usd:.4f} below minimum ${MIN_BET_USD}")
            return

        log.info(f"     My Copy : {shares:.2f} shares  ≈ ${usd:.2f}")

        result = await self.clob.place_market_order(
            token_id=trade.token_id or trade.market_id,
            side=trade.side,
            shares=shares,
            ref_price=trade.price,
        )

        elapsed_ms = (time.perf_counter() - t0) * 1_000
        order_id   = (result or {}).get("orderID", "?") if result else "FAILED"
        is_paper   = (result or {}).get("dry_run", False)

        if result:
            self.state.my_balance  -= usd
            self.state.daily_spent += usd
            self.state.total_trades += 1

            tag = "[PAPER]" if is_paper else "[LIVE]"
            log.info(f"     {'✅' if not is_paper else '🔵'} {tag} orderID={order_id}  latency={elapsed_ms:.1f}ms")

            # Track open paper position for later P&L
            if is_paper:
                pos_key = f"{trade.token_id}_{trade.side}"
                self.state.active_positions[pos_key] = {
                    "token_id": trade.token_id,
                    "slug": trade.market_slug,
                    "side": trade.side,
                    "shares": shares,
                    "entry_price": trade.price,
                    "usd_cost": usd,
                    "entry_time": time.time(),
                }

            mode_emoji = "🔵" if is_paper else "✅"
            await tg(
                self.session,
                f"{mode_emoji} *{'PAPER' if is_paper else 'LIVE'} COPY*\n"
                f"`{trade.market_slug or trade.token_id[:16]}`\n"
                f"Side: {trade.side} {trade.outcome} @ {trade.price:.3f}\n"
                f"Shares: {shares:.2f}  Cost: ${usd:.2f}\n"
                f"Balance: ${self.state.my_balance:.2f}  |  {elapsed_ms:.0f}ms\n"
                f"OrderID: `{order_id}`",
            )
        else:
            log.error(f"     ❌ Order placement FAILED for {trade.trade_id[:16]}")
            await tg(self.session, f"❌ Order FAILED: {trade.side} {trade.token_id[:20]}")

    # ── Main poll loop ───────────────────────────────────────────────────────

    async def poll_loop(self):
        log.info(f"🔄 Poll loop started  ({POLL_INTERVAL_MS}ms interval)")

        # ── Warm-up: seed seen_ids so we don't re-copy old trades ────────────
        log.info("⏳ Warm-up: fetching existing trade history…")
        for raw in (await self.clob.get_target_activity(limit=25)):
            if not isinstance(raw, dict):
                continue
            tid = str(raw.get("id") or raw.get("tradeId") or raw.get("txHash") or "")
            if tid:
                self.state.seen_ids.add(tid)
        for raw in (await self.clob.get_clob_trades(limit=15)):
            if not isinstance(raw, dict):
                continue
            tid = str(raw.get("id") or raw.get("tradeId") or "")
            if tid:
                self.state.seen_ids.add(tid)
        log.info(f"✓ Warm-up done.  {len(self.state.seen_ids)} existing trades marked seen.")

        consecutive_errors = 0
        last_status_ts     = time.time()

        while self.running:
            loop_t0 = time.perf_counter()

            try:
                # Fetch newest trades (both APIs in parallel for speed)
                activity_task = asyncio.create_task(self.clob.get_target_activity(limit=5))
                clob_task     = asyncio.create_task(self.clob.get_clob_trades(limit=5))
                activity, clob_trades = await asyncio.gather(activity_task, clob_task)

                log.info(f"📡 Fetched {len(activity)} activity, {len(clob_trades)} clob trades")

                # Merge, newest first; deduplicate by id
                seen_in_batch: Set[str] = set()
                all_raws: List[dict] = []
                for raw in (activity + clob_trades):
                    if not isinstance(raw, dict):
                        continue
                    tid = str(raw.get("id") or raw.get("tradeId") or raw.get("txHash") or "")
                    if tid and tid not in seen_in_batch:
                        seen_in_batch.add(tid)
                        all_raws.append(raw)

                await self._refresh_target_balance(all_raws)
                consecutive_errors = 0

                for raw in all_raws:
                    tid = str(raw.get("id") or raw.get("tradeId") or raw.get("txHash") or "")
                    if not tid or tid in self.state.seen_ids:
                        continue

                    self.state.seen_ids.add(tid)
                    trade = parse_trade(raw)
                    if trade is None:
                        continue

                    age = time.time() - trade.timestamp
                    if age > MAX_TRADE_AGE_SEC:
                        log.info(f"⏭️  Old trade skipped  age={age:.1f}s  id={tid[:16]}")
                        continue

                    log.info(f"🆕 New trade detected!  age={age*1000:.0f}ms  id={tid[:20]}")
                    await self._refresh_my_balance()
                    self._check_daily_reset()
                    await self.execute_copy(trade)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                consecutive_errors += 1
                log.warning(f"Poll error #{consecutive_errors}: {exc}")
                if consecutive_errors >= 10:
                    log.error("10 consecutive errors — sleeping 5s before retry")
                    await asyncio.sleep(5)
                    consecutive_errors = 0

            # ── Status heartbeat every 60s ────────────────────────────────
            if time.time() - last_status_ts >= 60:
                last_status_ts = time.time()
                log.info(
                    f"📊 Heartbeat │ balance=${self.state.my_balance:.2f} │ "
                    f"target≈${self.state.target_balance_estimate:.2f} │ "
                    f"trades={self.state.total_trades} │ "
                    f"daily=${self.state.daily_spent:.2f} │ "
                    f"seen_ids={len(self.state.seen_ids)}"
                )

            # ── If WebSocket fired, skip sleep for one iteration ──────────
            if self._ws_triggered.is_set():
                self._ws_triggered.clear()
            else:
                elapsed_ms = (time.perf_counter() - loop_t0) * 1_000
                sleep_ms   = max(0.0, POLL_INTERVAL_MS - elapsed_ms)
                if sleep_ms > 0:
                    await asyncio.sleep(sleep_ms / 1_000)

    # ── Polygon WebSocket monitor ────────────────────────────────────────────

    async def ws_monitor(self):
        """
        Subscribe to Polygon node's eth_subscribe logs for OrderFilled
        events on the Polymarket Exchange contract.

        When the target wallet appears in event data, set _ws_triggered
        so the poll loop fetches immediately (effective latency ≈ 0 ms).

        Requires: pip install websockets
        RPC: set POLYGON_WS_URL in .env, e.g. wss://polygon-mainnet.g.alchemy.com/v2/<KEY>
        """
        import websockets  # type: ignore

        ws_url = os.getenv(
            "POLYGON_WS_URL",
            "wss://polygon-bor-rpc.publicnode.com",  # free public node as fallback
        )

        sub_payload = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_subscribe",
            "params": [
                "logs",
                {
                    "address": POLYMARKET_EXCHANGE,
                    "topics": [ORDER_FILLED_TOPIC],
                },
            ],
        })

        target_bytes = TARGET_WALLET.replace("0x", "").lower()

        while self.running:
            try:
                log.info(f"🔌 WS: connecting → {ws_url[:45]}…")
                async with websockets.connect(
                    ws_url,
                    ping_interval=20,
                    ping_timeout=30,
                    close_timeout=5,
                ) as ws:
                    await ws.send(sub_payload)
                    resp = json.loads(await ws.recv())
                    sub_id = resp.get("result", "?")
                    log.info(f"✓ WS subscribed  sub_id={sub_id}")

                    while self.running:
                        try:
                            raw_msg = await asyncio.wait_for(ws.recv(), timeout=45)
                            msg = json.loads(raw_msg)
                            log_entry = msg.get("params", {}).get("result", {})

                            if not isinstance(log_entry, dict):
                                continue

                            tx_data = (log_entry.get("data", "") or "").lower()
                            tx_hash = log_entry.get("transactionHash", "")

                            # Quick check: is our target anywhere in the event data?
                            if target_bytes in tx_data:
                                log.info(f"⚡ WS HIT  tx={tx_hash[:18]}  — triggering immediate poll")
                                self._ws_triggered.set()

                        except asyncio.TimeoutError:
                            await ws.ping()   # keep-alive

            except Exception as exc:
                log.warning(f"WS error: {exc}  — reconnecting in {3}s")
                await asyncio.sleep(3)

    # ── Graceful shutdown ────────────────────────────────────────────────────

    async def shutdown(self):
        self.running = False
        log.info("\n" + "═" * 65)
        log.info("   BOT SHUTDOWN SUMMARY")
        log.info(f"   Trades copied  : {self.state.total_trades}")
        log.info(f"   Daily spent    : ${self.state.daily_spent:.2f}")
        log.info(f"   Final balance  : ${self.state.my_balance:.2f}")
        log.info(f"   Open positions : {len(self.state.active_positions)}")
        log.info(f"   Log file       : {_log_file}")
        log.info("═" * 65)
        if self.session and not self.session.closed:
            await self.session.close()

    # ── Entry ────────────────────────────────────────────────────────────────

    async def run(self):
        await self.startup()

        tasks = [
            asyncio.create_task(self.poll_loop(), name="poll_loop"),
        ]

        try:
            import websockets  # noqa: F401
            ws_url = os.getenv(
                "POLYGON_WS_URL",
                "wss://polygon-bor-rpc.publicnode.com",  # free public node as fallback
            )
            if "YOUR_KEY" in ws_url.upper():
                ws_url = "wss://polygon-bor-rpc.publicnode.com"  # use public node
                os.environ["POLYGON_WS_URL"] = ws_url  # update env for ws_monitor
                log.warning("POLYGON_WS_URL not configured → WebSocket disabled (polling only)")
            elif ws_url == "wss://polygon-bor-rpc.publicnode.com":
                log.warning("Using public node → WebSocket disabled (get Alchemy/Infura key for ~0ms detection)")
            else:
                tasks.append(asyncio.create_task(self.ws_monitor(), name="ws_monitor"))
                log.info("✓ WebSocket monitor task added (Polygon logs subscription)")
        except ImportError:
            log.warning("websockets not installed → WS monitor disabled.  pip install websockets")

        try:
            await asyncio.gather(*tasks)
        except (asyncio.CancelledError, KeyboardInterrupt):
            log.info("KeyboardInterrupt — initiating graceful shutdown…")
        finally:
            for t in tasks:
                t.cancel()
            try:
                await asyncio.gather(*tasks, return_exceptions=True)
            except Exception:
                pass
            await self.shutdown()


# ════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ════════════════════════════════════════════════════════════════════════════

BANNER = r"""
  ____       ____        _            __    _     _       _   _
 |  _ \ ___ | __ )  ___ | |_   __    / /_  | |   (_) __ _| |_| |_ _   _
 | |_) / _ \|  _ \ / _ \| __| / /   | '_ \ | |   | |/ _` | __| __| | | |
 |  __/ (_) | |_) | (_) | |_ / /    | (_) || |___| | (_| | |_| |_| |_| |
 |_|   \___/|____/ \___/ \__/_/      \___(_)_____|_|\__, |\__|\__|\__, |
  Copy Trader  v1.0  [PBot-6 Mirror]                |___/         |___/
"""

def validate_env():
    errors = []
    if not (DRY_RUN or PAPER_TRADING):
        if not PRIVATE_KEY:
            errors.append("PRIVATE_KEY is required for live trading")
        if not API_KEY or not API_SECRET or not API_PASS:
            errors.append("API_KEY / API_SECRET / API_PASSPHRASE required for live trading")
    if errors:
        for e in errors:
            log.error(f"❌ {e}")
        sys.exit(1)
    log.info("✓ Environment validated")


if __name__ == "__main__":
    print(BANNER)
    validate_env()
    try:
        asyncio.run(CopyBot().run())
    except KeyboardInterrupt:
        print("\n👋 Bot stopped by user.")