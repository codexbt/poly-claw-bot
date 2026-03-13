# ══════════════════════════════════════════════════════════════════
# POLYARBI v3.1 — Polymarket Bot
# Changes: removed CLOB min-trade restrictions, colorful trade logs,
#          detailed 5-min window summary with YES/NO breakdown
# Arbitrage bot updates - 2026-01-24
# ══════════════════════════════════════════════════════════════════

import asyncio
import logging
import sys
import time
import json
import os
import math
import hmac
import hashlib
import socket
import re as _re
from collections import deque
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Dict, List, Optional, Deque, Callable

import aiohttp
from aiohttp.resolver import AsyncResolver
import websockets
from dotenv import load_dotenv

# ══════════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════════

load_dotenv()

PRIVATE_KEY: str    = os.getenv("PRIVATE_KEY", "")
WALLET_ADDRESS: str = os.getenv("POLYMARKET_FUNDER_ADDRESS", "")
API_KEY: str        = os.getenv("API_KEY", "")
API_SECRET: str     = os.getenv("API_SECRET", "")
API_PASSPHRASE: str = os.getenv("API_PASSPHRASE", "")


def _env_bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).lower() in ("true", "1", "yes")


def _cli_bool(*flags: str) -> bool:
    return any(flag in sys.argv[1:] for flag in flags)


def _cli_value(name: str) -> Optional[str]:
    for arg in sys.argv[1:]:
        if arg.startswith(f"--{name}="):
            return arg.split("=", 1)[1]
    return None


DEFAULT_DRY_RUN_BANKROLL = 20.0
DEFAULT_LIVE_BANKROLL = 1000.0
DRY_RUN_DEFAULT = os.getenv("DRY_RUN", "true").lower() in ("true", "1", "yes")

INITIAL_BANKROLL = float(
    _cli_value("bankroll")
    or os.getenv("INITIAL_BANKROLL", str(DEFAULT_DRY_RUN_BANKROLL if DRY_RUN_DEFAULT else DEFAULT_LIVE_BANKROLL))
)
RESET_STATE = _env_bool("RESET_STATE") or _cli_bool("--reset", "--reset-state") or DRY_RUN_DEFAULT

CLOB_BASE_URL: str  = "https://clob.polymarket.com"
GAMMA_BASE_URL: str = os.getenv("GAMMA_BASE_URL", "https://gamma-api.polymarket.com").rstrip("/")
GAMMA_FALLBACK_BASE_URLS: List[str] = [
    os.getenv("GAMMA_FALLBACK_URL_1", "https://api.polymarket.com").rstrip("/"),
    os.getenv("GAMMA_FALLBACK_URL_2", "https://polymarket.com/api").rstrip("/"),
    os.getenv("GAMMA_FALLBACK_URL_3", "https://www.polymarket.com/api").rstrip("/"),
]
POLY_WS_URL: str    = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
BINANCE_WS_URL: str = "wss://stream.binance.com:9443/stream"
BINANCE_SYMBOLS: List[str] = ["btcusdt", "ethusdt"]

CONNECTIVITY_HOSTS = [
    ("gamma-api.polymarket.com", 443),
    ("api.polymarket.com", 443),
    ("polymarket.com", 443),
    ("clob.polymarket.com", 443),
    ("8.8.8.8", 53),
]


@dataclass
class StrategyConfig:
    min_liquidity_usdc: float         = 10.0
    max_time_to_expiry_hours: float   = 2.0
    min_time_to_expiry_minutes: float = 0.3

    min_edge_to_trade: float          = 0.01
    min_confidence_to_trade: float    = 0.10
    arb_min_gap: float                = 0.005
    arb_min_profit_usdc: float        = 0.10

    prior_decay_factor: float         = 0.85
    momentum_window_seconds: int      = 60

    kelly_fraction: float             = 0.25
    # No enforced platform minimum — CLOB min varies by account/tier
    # This is just a dust-order guard
    min_bet_usdc: float               = 0.50
    max_bet_usdc: float               = 200.0

    gamma: float                      = 0.1
    kappa: float                      = 1.5
    mm_spread_buffer: float           = 0.005
    mm_inventory_limit: float         = 0.60

    taker_fee: float                  = 0.02
    slippage_estimate: float          = 0.003


@dataclass
class RiskConfig:
    initial_bankroll: float       = INITIAL_BANKROLL
    max_daily_loss_pct: float     = 0.10
    max_drawdown_pct: float       = 0.20
    max_single_trade_pct: float   = 0.05
    max_open_positions: int       = 4
    max_correlated_positions: int = 2
    losing_streak_tightening: int = 3
    winning_streak_loosening: int = 4


@dataclass
class OperationalConfig:
    dry_run: bool                = os.getenv("DRY_RUN", "true").lower() in ("true", "1", "yes")
    reset_state: bool            = RESET_STATE
    scan_interval_seconds: float = 2.0
    full_scan_interval: float    = 8.0
    log_level: str               = "INFO"
    log_file: str                = "logs/polyarbi.log"
    state_file: str              = "state/polyarbi_state.json"
    max_order_retries: int       = 3
    order_timeout_seconds: float = 10.0
    scanner_retry_base: float    = 5.0
    scanner_retry_max: float     = 120.0
    scanner_connect_timeout: float = 10.0
    scanner_read_timeout: float  = 20.0


STRATEGY = StrategyConfig()
RISK     = RiskConfig()
OPS      = OperationalConfig()

# ── Terminal colors ──
RST   = "\033[0m"
BOLD  = "\033[1m"
DIM   = "\033[2m"
GREEN = "\033[92m"
RED   = "\033[91m"
YLW   = "\033[93m"
CYAN  = "\033[96m"
MAG   = "\033[95m"
BLUE  = "\033[94m"
WHITE = "\033[97m"
ORG   = "\033[38;5;208m"
LIME  = "\033[38;5;118m"
PINK  = "\033[38;5;213m"
GOLD  = "\033[38;5;220m"


def C(text, *codes) -> str:
    return "".join(codes) + str(text) + RST


def _bar(filled: int, total: int = 10, char: str = "█", empty: str = "░") -> str:
    f = max(0, min(int(filled), total))
    return char * f + empty * (total - f)


# ══════════════════════════════════════════════════════════════════
# LOGGING  (strip ANSI from file handlers)
# ══════════════════════════════════════════════════════════════════

class _StripAnsi(logging.Formatter):
    _pat = _re.compile(r'\033\[[0-9;]*m')
    def format(self, rec):
        return self._pat.sub('', super().format(rec))


os.makedirs("logs",  exist_ok=True)
os.makedirs("state", exist_ok=True)

_fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_ch = logging.StreamHandler(sys.stdout)
_ch.setFormatter(logging.Formatter(_fmt))
_fh1 = logging.FileHandler(OPS.log_file,        mode="a", encoding="utf-8")
_fh1.setFormatter(_StripAnsi(_fmt))
_fh2 = logging.FileHandler("polyarbilog.txt",   mode="a", encoding="utf-8")
_fh2.setFormatter(_StripAnsi(_fmt))
logging.basicConfig(level=getattr(logging, OPS.log_level), handlers=[_ch, _fh1, _fh2])
logger = logging.getLogger("polyarbi")


# ══════════════════════════════════════════════════════════════════
# PRETTY PRINT HELPERS
# ══════════════════════════════════════════════════════════════════

def _sep(char="═", width=64, color=CYAN) -> str:
    return C(char * width, color)


def _log_trade_open(side, asset, amount, price, shares, edge, conf,
                    strategy, mins_left, question):
    sc = LIME if side == "YES" else ORG
    w  = 60
    lines = [
        "",
        C("╔" + "═"*w + "╗", sc),
        C(f"║  {'🚀  TRADE OPENED  🚀':^{w-2}}║", sc, BOLD),
        C("╠" + "═"*w + "╣", sc),
        C(f"║  {'Asset':<14}{C(asset, GOLD, BOLD):<{w-16+len(GOLD+BOLD+RST)}}║", sc),
        C(f"║  {'Side':<14}{C(side,  sc,   BOLD):<{w-16+len(sc+BOLD+RST)  }}║", sc),
        C(f"║  {'Strategy':<14}{C(strategy.upper(), CYAN):<{w-16+len(CYAN+RST)}}║", sc),
        C(f"║  {'Amount':<14}{C(f'${amount:.4f} USDC', GOLD):<{w-16+len(GOLD+RST)}}║", sc),
        C(f"║  {'Price':<14}{C(f'{price:.4f}', WHITE):<{w-16+len(WHITE+RST)}}║", sc),
        C(f"║  {'Shares':<14}{C(f'{shares:.4f}', WHITE):<{w-16+len(WHITE+RST)}}║", sc),
        C(f"║  {'Edge':<14}{C(f'{edge:+.4f}', LIME if edge>0 else RED):<{w-16+len((LIME if edge>0 else RED)+RST)}}║", sc),
        C(f"║  {'Confidence':<14}{C(f'{conf*100:.1f}%  {_bar(int(conf*10))}', CYAN):<{w-16+len(CYAN+RST)}}║", sc),
        C(f"║  {'Expires':<14}{C(f'{mins_left:.1f} min', YLW):<{w-16+len(YLW+RST)}}║", sc),
        C(f"║  {'Q':<14}{C(question[:w-17], DIM):<{w-16+len(DIM+RST)}}║", sc),
        C("╚" + "═"*w + "╝", sc),
        "",
    ]
    for ln in lines:
        logger.info(ln)


def _log_trade_close(side, asset, entry, exit_p, shares, pnl, cost, reason):
    pc  = LIME if pnl >= 0 else RED
    sc  = LIME if side == "YES" else ORG
    em  = "✅" if pnl >= 0 else "❌"
    pct = (pnl / cost * 100) if cost else 0
    w   = 60
    lines = [
        "",
        C("╔" + "═"*w + "╗", pc),
        C(f"║  {em}  {'TRADE CLOSED':^{w-6}}  {em}  ║", pc, BOLD),
        C("╠" + "═"*w + "╣", pc),
        C(f"║  {'Asset':<14}{C(asset, GOLD, BOLD):<{w-16+len(GOLD+BOLD+RST)}}║", pc),
        C(f"║  {'Side':<14}{C(side, sc, BOLD):<{w-16+len(sc+BOLD+RST)}}║", pc),
        C(f"║  {'Reason':<14}{C(reason, YLW):<{w-16+len(YLW+RST)}}║", pc),
        C(f"║  {'Entry':<14}{C(f'{entry:.4f}', WHITE):<{w-16+len(WHITE+RST)}}║", pc),
        C(f"║  {'Exit':<14}{C(f'{exit_p:.4f}', WHITE):<{w-16+len(WHITE+RST)}}║", pc),
        C(f"║  {'Shares':<14}{C(f'{shares:.4f}', WHITE):<{w-16+len(WHITE+RST)}}║", pc),
        C(f"║  {'PnL':<14}{C(f'{pnl:+.4f}  ({pct:+.2f}%)', pc, BOLD):<{w-16+len(pc+BOLD+RST)}}║", pc),
        C("╚" + "═"*w + "╝", pc),
        "",
    ]
    for ln in lines:
        logger.info(ln)


def _log_window_summary(label, trades, yes_cnt, no_cnt,
                        yes_amt, no_amt, wpnl, start_bal, end_bal):
    total   = yes_cnt + no_cnt
    pc      = LIME if wpnl >= 0 else RED
    dc      = LIME if (end_bal - start_bal) >= 0 else RED
    yf      = int(yes_cnt / max(total, 1) * 10)
    nf      = int(no_cnt  / max(total, 1) * 10)
    ybar    = C("█"*yf + "░"*(10-yf), LIME)
    nbar    = C("█"*nf + "░"*(10-nf), ORG)
    w       = 60
    delta   = end_bal - start_bal
    lines = [
        "",
        C("╔" + "═"*w + "╗", MAG, BOLD),
        C(f"║  {'✨  5-MIN WINDOW SUMMARY  ✨':^{w-2}}║", MAG, BOLD),
        C(f"║  {C(label, GOLD, BOLD):<{w-2+len(GOLD+BOLD+RST)}}║", MAG),
        C("╠" + "═"*w + "╣", MAG),
        C(f"║  {'Total Trades':<22}{C(str(trades), WHITE, BOLD):<{w-24+len(WHITE+BOLD+RST)}}║", MAG),
        C("║" + " "*w + "║", MAG),
        C(f"║  {C('YES', LIME, BOLD):<{4+len(LIME+BOLD+RST)}} {C(f'{yes_cnt:>3}', LIME)}  {ybar}  {C(f'${yes_amt:.4f}', LIME):<{w-28+len(LIME+RST)}}║", MAG),
        C(f"║  {C('NO ', ORG,  BOLD):<{4+len(ORG+BOLD+RST)}} {C(f'{no_cnt:>3}',  ORG )}  {nbar}  {C(f'${no_amt:.4f}',  ORG ):<{w-28+len(ORG+RST)}}║", MAG),
        C("╠" + "═"*w + "╣", MAG),
        C(f"║  {'Window PnL':<22}{C(f'{wpnl:+.4f} USDC', pc, BOLD):<{w-24+len(pc+BOLD+RST)}}║", MAG),
        C(f"║  {'Balance Start':<22}{C(f'${start_bal:.4f}', WHITE):<{w-24+len(WHITE+RST)}}║", MAG),
        C(f"║  {'Balance End':<22}{C(f'${end_bal:.4f}', WHITE):<{w-24+len(WHITE+RST)}}║", MAG),
        C(f"║  {'Net Change':<22}{C(f'{delta:+.4f} USDC', dc, BOLD):<{w-24+len(dc+BOLD+RST)}}║", MAG),
        C("╚" + "═"*w + "╝", MAG, BOLD),
        "",
    ]
    for ln in lines:
        logger.info(ln)


# ══════════════════════════════════════════════════════════════════
# CONNECTIVITY DIAGNOSTICS
# ══════════════════════════════════════════════════════════════════

def _check_connectivity_sync() -> dict:
    results = {}
    for host, port in CONNECTIVITY_HOSTS:
        t0 = time.time()
        try:
            sock = socket.create_connection((host, port), timeout=5)
            sock.close()
            results[host] = {"ok": True, "ms": round((time.time() - t0) * 1000, 1)}
        except socket.gaierror as e:
            results[host] = {"ok": False, "error": f"DNS failure: {e}"}
        except OSError as e:
            results[host] = {"ok": False, "error": f"TCP error: {e}"}
    return results


def run_connectivity_check():
    logger.info(C("[Net] Running connectivity diagnostics...", CYAN))
    results = _check_connectivity_sync()
    poly_ok = True
    for host, info in results.items():
        port = dict(CONNECTIVITY_HOSTS).get(host, "?")
        if info["ok"]:
            logger.info(C(f"[Net] ✅ {host}:{port} — {info['ms']}ms", LIME))
        else:
            logger.warning(C(f"[Net] ❌ {host} — {info['error']}", RED))
            if "polymarket" in host:
                poly_ok = False
    if not poly_ok:
        logger.error(C(
            "\n"
            "╔══════════════════════════════════════════════╗\n"
            "║    POLYMARKET UNREACHABLE — FIXES            ║\n"
            "╠══════════════════════════════════════════════╣\n"
            "║ 1. Use a VPN (geo-restricted region)         ║\n"
            "║ 2. ipconfig /flushdns  then restart          ║\n"
            "║ 3. Disable firewall/antivirus temporarily    ║\n"
            "║ 4. set HTTPS_PROXY=http://proxy:port         ║\n"
            "║ Bot retries with backoff. Ctrl+C to quit.    ║\n"
            "╚══════════════════════════════════════════════╝",
            YLW
        ))
    return poly_ok


# ══════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════

def _safe_float(val, default=0.0) -> float:
    try:
        return float(val) if val is not None else default
    except (ValueError, TypeError):
        return default


def _parse_ts(date_str) -> float:
    if not date_str:
        return 0.0
    if isinstance(date_str, (int, float)):
        ts = float(date_str)
        return ts / 1000.0 if ts > 1e12 else ts
    try:
        from dateutil import parser as dp
        return dp.parse(str(date_str)).timestamp()
    except Exception:
        return 0.0


def _extract_volume(raw: dict) -> float:
    for k in ("volume24hr", "volumeClob", "volume24hrClob", "volumeNum", "liquidityNum", "volume"):
        v = raw.get(k)
        if v is not None:
            fv = _safe_float(v)
            if fv > 0:
                return fv
    return 0.0


def _extract_end_date(raw: dict) -> str:
    for k in ("endDate", "end_date_iso", "endDateIso", "endTime", "expiryDate", "expiry"):
        v = raw.get(k)
        if v:
            return str(v)
    return ""


def _extract_tokens(raw: dict):
    for key in ("tokens", "clobTokenIds", "clob_token_ids", "tokenIds", "token_ids"):
        rt = raw.get(key)
        if not rt:
            continue
        if isinstance(rt, str):
            try:
                rt = json.loads(rt)
            except Exception:
                parts = [p.strip() for p in rt.split(",") if p.strip()]
                if len(parts) >= 2:
                    return parts[0], parts[1]
                continue
        if isinstance(rt, list) and len(rt) >= 2:
            t0, t1 = rt[0], rt[1]
            if isinstance(t0, dict):
                def _out(t): return str(t.get("outcome", t.get("name", ""))).lower()
                st = sorted(rt[:2], key=lambda t: 0 if "yes" in _out(t) else 1)
                y = st[0].get("token_id", st[0].get("id", ""))
                n = st[1].get("token_id", st[1].get("id", ""))
                if y and n:
                    return y, n
            elif isinstance(t0, str) and t0:
                return t0, t1
        if isinstance(rt, dict):
            y = rt.get("Yes") or rt.get("YES") or rt.get("yes")
            n = rt.get("No")  or rt.get("NO")  or rt.get("no")
            if y and n:
                return y, n
    return None, None


def _extract_asset(question: str) -> str:
    if not question:
        return ""
    q = question.upper()
    if "BTC" in q or "BITCOIN" in q:  return "BTC"
    if "ETH" in q or "ETHEREUM" in q: return "ETH"
    if "SOL" in q or "SOLANA" in q:   return "SOL"
    return ""


def _is_5min_market(raw: dict, question: str) -> bool:
    slug  = str(raw.get("slug", "")).lower()
    q     = question.lower()
    group = str(raw.get("groupSlug", raw.get("group_slug", ""))).lower()
    res   = str(raw.get("resolution", raw.get("time_resolution", raw.get("interval", "")))).lower()
    return (
        "updown-5m" in slug or "-5m-" in slug or slug.endswith("-5m")
        or any(k in q for k in ["5 min", "5min", "5-min", "5-minute", "5 minute"])
        or "updown-5m" in group or "-5m" in group
        or res in ("5min", "5m", "5 min")
    )


# ══════════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ══════════════════════════════════════════════════════════════════

@dataclass
class PriceTick:
    symbol: str
    price: float
    timestamp: float
    volume_24h: float     = 0.0
    change_pct_24h: float = 0.0


@dataclass
class OrderBookLevel:
    price: float
    size: float


@dataclass
class MarketOrderBook:
    market_id: str
    yes_bids: List[OrderBookLevel] = field(default_factory=list)
    yes_asks: List[OrderBookLevel] = field(default_factory=list)
    no_bids:  List[OrderBookLevel] = field(default_factory=list)
    no_asks:  List[OrderBookLevel] = field(default_factory=list)
    last_updated: float = 0.0

    @property
    def best_yes_bid(self): return self.yes_bids[0].price if self.yes_bids else None
    @property
    def best_yes_ask(self): return self.yes_asks[0].price if self.yes_asks else None
    @property
    def best_no_bid(self):  return self.no_bids[0].price  if self.no_bids  else None
    @property
    def best_no_ask(self):  return self.no_asks[0].price  if self.no_asks  else None

    @property
    def mid_yes(self):
        if self.best_yes_bid and self.best_yes_ask:
            return (self.best_yes_bid + self.best_yes_ask) / 2
        return self.best_yes_bid or self.best_yes_ask

    @property
    def spread_sum(self):
        if self.best_yes_ask and self.best_no_ask:
            return self.best_yes_ask + self.best_no_ask
        return None


@dataclass
class PolyMarket:
    condition_id: str
    question: str
    yes_token_id: str
    no_token_id: str
    yes_price: float     = 0.5
    no_price: float      = 0.5
    volume_24h: float    = 0.0
    end_date_iso: str    = ""
    end_timestamp: float = 0.0
    active: bool         = True
    order_book: Optional[MarketOrderBook] = None
    asset_keyword: str   = ""


# ══════════════════════════════════════════════════════════════════
# DATA STORE
# ══════════════════════════════════════════════════════════════════

class DataStore:
    def __init__(self):
        self._lock = asyncio.Lock()
        self.prices: Dict[str, PriceTick] = {}
        self.price_history: Dict[str, Deque[PriceTick]] = {
            (s.upper() + ("" if s.upper().endswith("USDT") else "USDT")): deque(maxlen=300)
            for s in BINANCE_SYMBOLS
        }
        self.markets: Dict[str, PolyMarket]          = {}
        self.order_books: Dict[str, MarketOrderBook] = {}
        self._price_cbs: List[Callable] = []

    async def update_price(self, tick: PriceTick):
        sym = tick.symbol.upper()
        async with self._lock:
            self.prices[sym] = tick
            if sym not in self.price_history:
                self.price_history[sym] = deque(maxlen=300)
            self.price_history[sym].append(tick)
        for cb in self._price_cbs:
            asyncio.create_task(cb(tick))

    async def update_book(self, book: MarketOrderBook):
        async with self._lock:
            self.order_books[book.market_id] = book
            if book.market_id in self.markets:
                self.markets[book.market_id].order_book = book

    async def update_markets(self, markets: List[PolyMarket]):
        async with self._lock:
            for m in markets:
                self.markets[m.condition_id] = m

    def get_price(self, symbol: str) -> Optional[PriceTick]:
        sym = symbol.upper()
        if not sym.endswith("USDT"):
            sym += "USDT"
        return self.prices.get(sym)

    def get_recent_prices(self, symbol: str, seconds: int = 60) -> List[PriceTick]:
        sym = symbol.upper()
        if not sym.endswith("USDT"):
            sym += "USDT"
        now = time.time()
        return [t for t in self.price_history.get(sym, deque()) if now - t.timestamp <= seconds]

    def on_price_update(self, cb: Callable):
        self._price_cbs.append(cb)


# ══════════════════════════════════════════════════════════════════
# BINANCE FEED
# ══════════════════════════════════════════════════════════════════

class BinanceFeed:
    def __init__(self, store: DataStore):
        self.store = store
        self._running = False
        self._ws      = None
        self._delay   = 3.0

    async def run(self):
        self._running = True
        while self._running:
            try:
                streams = "/".join(f"{s}@ticker" for s in BINANCE_SYMBOLS)
                url = f"{BINANCE_WS_URL}?streams={streams}"
                logger.info(C("[Binance] Connecting...", CYAN))
                async with websockets.connect(url, ping_interval=20, max_size=2**20) as ws:
                    self._ws = ws
                    self._delay = 3.0
                    logger.info(C("[Binance] ✅ Connected", LIME))
                    async for raw in ws:
                        if not self._running: break
                        try: await self._handle(json.loads(raw))
                        except Exception: pass
            except Exception as e:
                logger.warning(C(f"[Binance] {e} — retry in {self._delay}s", YLW))
                await asyncio.sleep(self._delay)
                self._delay = min(self._delay * 1.5, 30.0)
            finally:
                self._ws = None

    async def _handle(self, msg):
        data = msg.get("data", msg)
        if isinstance(data, list):
            for d in data: await self._handle(d)
            return
        if not isinstance(data, dict) or data.get("e") != "24hrTicker":
            return
        sym = data.get("s")
        if not sym: return
        await self.store.update_price(PriceTick(
            symbol=sym, price=float(data.get("c", 0)),
            timestamp=time.time(),
            volume_24h=float(data.get("q", 0)),
            change_pct_24h=float(data.get("P", 0)),
        ))

    def stop(self): self._running = False

    async def close(self):
        self._running = False
        if self._ws:
            try: await self._ws.close()
            except Exception: pass


# ══════════════════════════════════════════════════════════════════
# POLYMARKET SCANNER
# ══════════════════════════════════════════════════════════════════

class PolymarketScanner:
    def __init__(self, store: DataStore):
        self.store = store
        self._session: Optional[aiohttp.ClientSession] = None
        self._scan_fail_count   = 0
        self._scan_retry_delay  = OPS.scanner_retry_base
        self._last_success_time: float = 0.0

    def _make_connector(self):
        resolver = AsyncResolver(nameservers=["8.8.8.8", "1.1.1.1"])
        return aiohttp.TCPConnector(
            family=socket.AF_INET,
            ssl=True, limit=10,
            ttl_dns_cache=300, use_dns_cache=True,
            enable_cleanup_closed=True,
            resolver=resolver,
        )

    async def _sess(self) -> aiohttp.ClientSession:
        if not self._session or self._session.closed:
            timeout = aiohttp.ClientTimeout(
                total=30,
                connect=OPS.scanner_connect_timeout,
                sock_read=OPS.scanner_read_timeout,
            )
            proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
            if proxy:
                logger.info(C(f"[Scanner] Using proxy: {proxy}", CYAN))
            self._session = aiohttp.ClientSession(
                timeout=timeout,
                connector=self._make_connector(),
                headers={"User-Agent": "polyarbi-bot/3.1"},
            )
        return self._session

    def _on_scan_success(self):
        self._scan_fail_count  = 0
        self._scan_retry_delay = OPS.scanner_retry_base
        self._last_success_time = time.time()

    def _on_scan_failure(self, err: str):
        self._scan_fail_count += 1
        self._scan_retry_delay = min(self._scan_retry_delay * 1.5, OPS.scanner_retry_max)
        ms = (time.time() - self._last_success_time) / 60 if self._last_success_time else 0
        logger.warning(C(
            f"[Scanner] ❌ Fail #{self._scan_fail_count}: {err} | "
            f"retry in {self._scan_retry_delay:.0f}s | "
            f"last ok: {f'{ms:.1f}min ago' if self._last_success_time else 'never'}",
            YLW
        ))

    async def scan_markets(self) -> List[PolyMarket]:
        now = time.time()
        markets: List[PolyMarket] = []
        for sym in ("BTC", "ETH"):
            m = await self._slug_lookup(sym, now)
            if m:
                markets.append(m)
                logger.info(C(
                    f"[Scanner] ✅ {sym} → {m.condition_id[:10]} | "
                    f"vol={m.volume_24h:.0f} | "
                    f"expires in {(m.end_timestamp-now)/60:.1f}min",
                    LIME
                ))
        if not markets:
            markets = await self._bulk_scan(now)
        if markets:
            self._on_scan_success()
            await self.store.update_markets(markets)
        else:
            logger.warning(C(
                f"[Scanner] No markets found — retry in {self._scan_retry_delay:.0f}s",
                YLW
            ))
        return markets

    @staticmethod
    def _window_ts() -> int:
        return (int(time.time()) // 300) * 300

    @staticmethod
    def _gamma_endpoints() -> List[str]:
        endpoints = [GAMMA_BASE_URL]
        for url in GAMMA_FALLBACK_BASE_URLS:
            if url and url not in endpoints:
                endpoints.append(url)
        return endpoints

    async def _slug_lookup(self, symbol: str, now: float) -> Optional[PolyMarket]:
        base = {"BTC": "btc-updown-5m", "ETH": "eth-updown-5m"}.get(symbol, "")
        if not base: return None
        sess = await self._sess()
        wts  = self._window_ts()
        for endpoint in self._gamma_endpoints():
            if endpoint != GAMMA_BASE_URL:
                logger.info(C(f"[Scanner] Trying alternate API base: {endpoint}", CYAN))
            for offset in (0, 300, -300, 600):
                slug = f"{base}-{wts + offset}"
                try:
                    async with sess.get(f"{endpoint}/markets", params={"slug": slug}) as r:
                        if r.status != 200: continue
                        data    = await r.json()
                        results = data if isinstance(data, list) else data.get("results", [])
                        for raw in results:
                            m = self._parse(raw, now, force_asset=symbol)
                            if m: return m
                except aiohttp.ClientConnectorError as e:
                    self._on_scan_failure(f"Conn err: {e}"); return None
                except asyncio.TimeoutError:
                    self._on_scan_failure(f"Timeout: {slug}"); return None
                except Exception as e:
                    logger.debug(f"[Scanner] slug {slug}: {e}")
            try:
                async with sess.get(f"{endpoint}/markets", params={"slug": base}) as r:
                    if r.status == 200:
                        data    = await r.json()
                        results = data if isinstance(data, list) else data.get("results", [])
                        cands   = [m for raw in results
                                   for m in [self._parse(raw, now, force_asset=symbol)] if m]
                        if cands:
                            return sorted(cands, key=lambda x: x.end_timestamp)[0]
            except Exception:
                pass
        return None

    async def _bulk_scan(self, now: float) -> List[PolyMarket]:
        try:
            sess = await self._sess()
            logger.info(C("[Scanner] Bulk scan fallback...", CYAN))
            for endpoint in self._gamma_endpoints():
                if endpoint != GAMMA_BASE_URL:
                    logger.info(C(f"[Scanner] Trying alternate bulk scan base: {endpoint}", CYAN))
                try:
                    async with sess.get(f"{endpoint}/markets", params={
                        "active": "true", "closed": "false",
                        "limit": 500, "order": "volume24hr", "ascending": "false",
                    }) as r:
                        if r.status != 200:
                            self._on_scan_failure(f"HTTP {r.status} ({endpoint})"); continue
                        data = await r.json()
                except aiohttp.ClientConnectorError as e:
                    self._on_scan_failure(f"Cannot connect: {e}"); continue
                except asyncio.TimeoutError:
                    self._on_scan_failure(f"Timeout during bulk scan ({endpoint})"); continue
                except Exception as e:
                    self._on_scan_failure(f"Unexpected: {e}"); continue
                results = data if isinstance(data, list) else data.get("results", [])
                markets, rejects = [], {}
                for raw in results:
                    m = self._parse(raw, now)
                    if m:
                        markets.append(m)
                    else:
                        q   = raw.get("question", raw.get("title", ""))
                        rsn = ("no_q" if not q else "no_asset" if not _extract_asset(q)
                               else "not_5min" if not _is_5min_market(raw, q)
                               else "no_tok"   if not _extract_tokens(raw)[0]
                               else "expired"  if _parse_ts(_extract_end_date(raw)) < now
                               else "other")
                        rejects[rsn] = rejects.get(rsn, 0) + 1
                logger.info(C(
                    f"[Scanner] Bulk: {len(markets)} passed | "
                    f"rejects: {dict(sorted(rejects.items(), key=lambda x:-x[1])[:5])}",
                    CYAN
                ))
                return markets
        except Exception as e:
            self._on_scan_failure(f"Unexpected: {e}")
        return []

    def _parse(self, raw: dict, now: float, force_asset: str = "") -> Optional[PolyMarket]:
        try:
            q = raw.get("question", raw.get("title", ""))
            if not q: return None
            asset = force_asset or _extract_asset(q)
            if not asset: return None
            if not force_asset and not _is_5min_market(raw, q): return None
            yes_tok, no_tok = _extract_tokens(raw)
            if not yes_tok or not no_tok: return None
            cid = (raw.get("conditionId") or raw.get("condition_id") or
                   raw.get("id") or raw.get("marketId") or "")
            if not cid: return None
            end_ts = _parse_ts(_extract_end_date(raw))
            if end_ts == 0:
                end_ts = float((int(now) // 300 + 1) * 300)
            if end_ts < now - 30: return None
            vol = _extract_volume(raw)
            yes_price = _safe_float(
                raw.get("bestAsk") or (raw.get("outcomePrices") or [None])[0], 0.5
            )
            yes_price = max(0.01, min(0.99, yes_price))
            return PolyMarket(
                condition_id=cid, question=q,
                yes_token_id=yes_tok, no_token_id=no_tok,
                yes_price=yes_price, no_price=1.0 - yes_price,
                volume_24h=vol, end_date_iso=str(_extract_end_date(raw)),
                end_timestamp=end_ts, asset_keyword=asset,
            )
        except Exception as e:
            logger.debug(f"[Scanner] parse err: {e}")
            return None

    async def fetch_order_book(self, market: PolyMarket) -> Optional[MarketOrderBook]:
        try:
            sess = await self._sess()
            book = MarketOrderBook(market_id=market.condition_id)
            for tok, side in [(market.yes_token_id, "yes"), (market.no_token_id, "no")]:
                if not tok: continue
                async with sess.get(f"{CLOB_BASE_URL}/book", params={"token_id": tok}) as r:
                    if r.status != 200:
                        logger.debug(f"[Book] {side} → {r.status}"); continue
                    data = await r.json()
                bids = sorted([OrderBookLevel(float(b["price"]), float(b["size"]))
                               for b in data.get("bids", [])], key=lambda x: -x.price)
                asks = sorted([OrderBookLevel(float(a["price"]), float(a["size"]))
                               for a in data.get("asks", [])], key=lambda x: x.price)
                if side == "yes": book.yes_bids, book.yes_asks = bids, asks
                else:             book.no_bids,  book.no_asks  = bids, asks
            book.last_updated = time.time()
            await self.store.update_book(book)
            mins = (market.end_timestamp - time.time()) / 60
            logger.info(
                f"[Book] {C(market.asset_keyword, GOLD)} | "
                f"YES bid={C(str(book.best_yes_bid), LIME)} ask={C(str(book.best_yes_ask), LIME)} | "
                f"NO bid={C(str(book.best_no_bid), ORG)} ask={C(str(book.best_no_ask), ORG)} | "
                f"sum={C(f'{book.spread_sum:.4f}' if book.spread_sum else 'N/A', WHITE)} | "
                f"{C(f'{mins:.1f}min left', YLW)}"
            )
            return book
        except aiohttp.ClientConnectorError as e:
            logger.warning(C(f"[Book] Conn error: {e}", YLW))
        except asyncio.TimeoutError:
            logger.warning(C("[Book] Timeout", YLW))
        except Exception as e:
            logger.error(C(f"[Book] {e}", RED))
        return None

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()


# ══════════════════════════════════════════════════════════════════
# POLYMARKET WEBSOCKET
# ══════════════════════════════════════════════════════════════════

class PolymarketWSFeed:
    def __init__(self, store: DataStore):
        self.store = store
        self._ids: List[str] = []
        self._running = False
        self._ws      = None

    async def run(self, ids: List[str]):
        self._ids = ids
        self._running = True
        while self._running:
            try:
                logger.info(C("[PolyWS] Connecting...", CYAN))
                async with websockets.connect(POLY_WS_URL) as ws:
                    self._ws = ws
                    logger.info(C("[PolyWS] ✅ Connected", LIME))
                    await ws.send(json.dumps({
                        "auth": {}, "type": "Market",
                        "assets_ids": self._ids[:50],
                    }))
                    async for raw in ws:
                        if not self._running: break
                        try: await self._handle(json.loads(raw))
                        except Exception: pass
            except Exception as e:
                logger.warning(C(f"[PolyWS] {e} — reconnecting...", YLW))
                await asyncio.sleep(3)
            finally:
                self._ws = None

    async def _handle(self, msg: dict):
        if msg.get("event_type") not in ("book", "price_change"): return
        mid  = msg.get("asset_id", "")
        book = self.store.order_books.get(mid)
        if not book: return
        asks = msg.get("asks", [])
        bids = msg.get("bids", [])
        if asks: book.yes_asks = [OrderBookLevel(float(a[0]), float(a[1])) for a in asks[:5]]
        if bids: book.yes_bids = [OrderBookLevel(float(b[0]), float(b[1])) for b in bids[:5]]
        book.last_updated = time.time()

    def stop(self): self._running = False

    async def close(self):
        self._running = False
        if self._ws:
            try: await self._ws.close()
            except Exception: pass


# ══════════════════════════════════════════════════════════════════
# PROBABILITY ENGINE
# ══════════════════════════════════════════════════════════════════

@dataclass
class ProbabilityEstimate:
    market_id: str
    yes_prob: float
    no_prob: float
    market_yes_price: float
    market_no_price: float
    edge_yes: float
    edge_no: float
    confidence: float
    arb_gap: float
    momentum: float
    flow: float
    timestamp: float = 0.0

    @property
    def best_edge(self) -> float: return max(self.edge_yes, self.edge_no)
    @property
    def best_side(self) -> str:   return "YES" if self.edge_yes >= self.edge_no else "NO"
    @property
    def has_arb(self) -> bool:    return self.arb_gap < -STRATEGY.arb_min_gap
    @property
    def has_edge(self) -> bool:
        return (self.best_edge >= STRATEGY.min_edge_to_trade
                and self.confidence >= STRATEGY.min_confidence_to_trade)


class BayesianUpdater:
    def __init__(self, alpha: float = 2.0, beta: float = 2.0):
        self.alpha = alpha
        self.beta  = beta

    def update(self, p_yes: float, strength: float = 1.0):
        d = STRATEGY.prior_decay_factor
        self.alpha = self.alpha * d + (1 - d) + p_yes       * strength
        self.beta  = self.beta  * d + (1 - d) + (1 - p_yes) * strength

    @property
    def mean(self) -> float:
        return self.alpha / (self.alpha + self.beta)

    @property
    def confidence(self) -> float:
        n   = self.alpha + self.beta
        raw = min(n / 15.0, 1.0)
        var = (self.alpha * self.beta) / (n * n * (n + 1))
        return raw * (1.0 - min(var * 50, 0.5))


class ProbabilityEngine:
    def __init__(self, store: DataStore):
        self.store = store
        self._bays: Dict[str, BayesianUpdater] = {}

    def _bay(self, mid: str, mid_yes: float) -> BayesianUpdater:
        if mid not in self._bays:
            c = 3.0
            self._bays[mid] = BayesianUpdater(mid_yes * c, (1 - mid_yes) * c)
        return self._bays[mid]

    def estimate(self, market: PolyMarket) -> Optional[ProbabilityEstimate]:
        book = self.store.order_books.get(market.condition_id)
        if not book: return None
        mid_yes = max(0.01, min(0.99, book.mid_yes or market.yes_price))
        ss      = book.spread_sum
        arb_gap = (ss - 1.0) if ss else 0.0
        hist     = self.store.get_recent_prices(market.asset_keyword + "USDT", STRATEGY.momentum_window_seconds)
        momentum = self._momentum(hist, market.question)
        vol_sig  = self._vol_signal(hist)
        flow     = self._flow(book)
        bay = self._bay(market.condition_id, mid_yes)
        if len(hist) >= 3:
            combined = (0.50 * (0.5 + momentum * 0.35)
                        + 0.20 * (0.5 + vol_sig  * 0.15)
                        + 0.30 * (0.5 + flow     * 0.20))
            bay.update(combined, min(len(hist) / 20.0, 1.0))
        p    = bay.mean
        conf = bay.confidence
        yes_ask  = book.best_yes_ask or mid_yes + 0.01
        no_ask   = book.best_no_ask  or (1 - mid_yes) + 0.01
        edge_yes = p       - yes_ask - STRATEGY.taker_fee
        edge_no  = (1 - p) - no_ask  - STRATEGY.taker_fee
        return ProbabilityEstimate(
            market_id=market.condition_id,
            yes_prob=p, no_prob=1-p,
            market_yes_price=mid_yes, market_no_price=1-mid_yes,
            edge_yes=edge_yes, edge_no=edge_no,
            confidence=conf, arb_gap=arb_gap,
            momentum=momentum, flow=flow,
            timestamp=time.time(),
        )

    def _momentum(self, hist: List[PriceTick], question: str) -> float:
        if len(hist) < 3: return 0.0
        prices = [t.price for t in hist]
        ret    = (prices[-1] - prices[0]) / (prices[0] + 1e-9)
        norm   = max(-1.0, min(1.0, ret / 0.002))
        q      = question.upper()
        down   = sum(1 for k in ["DOWN","BELOW","LOWER","UNDER","BEAR","DROP","FALL"] if k in q)
        up     = sum(1 for k in ["UP","ABOVE","HIGHER","BULL","RISE"] if k in q)
        return -norm if down > up else norm

    def _vol_signal(self, hist: List[PriceTick]) -> float:
        if len(hist) < 3: return 0.0
        avg = sum(t.volume_24h for t in hist) / len(hist)
        if avg == 0: return 0.0
        ratio = hist[-1].volume_24h / avg - 1.0
        direction = 1.0 if hist[-1].price >= hist[-2].price else -1.0
        return max(-1.0, min(1.0, ratio * direction))

    def _flow(self, book: MarketOrderBook) -> float:
        bv = sum(b.size for b in book.yes_bids[:5])
        av = sum(a.size for a in book.yes_asks[:5])
        t  = bv + av
        if t == 0: return 0.0
        return max(-1.0, min(1.0, (bv - av) / t * 2.0))

    def reset(self, mid: str):
        self._bays.pop(mid, None)


# ══════════════════════════════════════════════════════════════════
# MARKET MAKER
# ══════════════════════════════════════════════════════════════════

@dataclass
class MMQuotes:
    market_id: str
    bid_price: float
    ask_price: float
    spread: float
    timestamp: float = 0.0

    @property
    def is_valid(self):
        return (0.01 <= self.bid_price <= 0.99
                and 0.01 <= self.ask_price <= 0.99
                and self.ask_price > self.bid_price
                and self.spread < 0.20)


class StoikovMM:
    def __init__(self):
        self.inv: Dict[str, float] = {}

    def quotes(self, market: PolyMarket, est: ProbabilityEstimate,
               returns: List[float]) -> Optional[MMQuotes]:
        now   = time.time()
        t_hrs = (market.end_timestamp - now) / 3600.0
        if t_hrs < 3 / 60: return None
        s      = est.yes_prob
        q      = self.inv.get(market.condition_id, 0.0)
        sigma2 = self._var(returns)
        t_sc   = min(t_hrs / 2.0, 1.0)
        g, k   = STRATEGY.gamma, STRATEGY.kappa
        rp     = max(0.02, min(0.98, s - q * g * sigma2 * t_sc))
        hs     = max(min((g * sigma2 * t_sc + (2/g) * math.log(1 + g/k)) / 2, 0.08),
                     STRATEGY.mm_spread_buffer)
        return MMQuotes(market_id=market.condition_id,
                        bid_price=max(0.01, round(rp - hs, 4)),
                        ask_price=min(0.99, round(rp + hs, 4)),
                        spread=round(2 * hs, 4), timestamp=now)

    def _var(self, rets: List[float]) -> float:
        if len(rets) < 3: return 0.0001
        n = len(rets); mean = sum(rets) / n
        return max(sum((r - mean)**2 for r in rets) / max(n-1, 1), 1e-8)

    def update_inv(self, mid: str, d: float):
        self.inv[mid] = self.inv.get(mid, 0.0) + d

    def inv_ok(self, mid: str) -> bool:
        return abs(self.inv.get(mid, 0.0)) <= STRATEGY.mm_inventory_limit


# ══════════════════════════════════════════════════════════════════
# KELLY SIZER
# ══════════════════════════════════════════════════════════════════

@dataclass
class SizingResult:
    recommended_usdc: float
    kelly_fraction: float
    side: str
    win_prob: float
    expected_value: float


class KellySizer:
    def __init__(self):
        self.wins = 0; self.losses = 0

    def size(self, est: ProbabilityEstimate, bankroll: float,
             yes_ask: float, no_ask: float) -> SizingResult:
        side  = est.best_side
        p     = est.yes_prob if side == "YES" else est.no_prob
        price = yes_ask      if side == "YES" else no_ask
        edge  = est.edge_yes if side == "YES" else est.edge_no
        if price <= 0 or price >= 1 or edge <= 0:
            return SizingResult(0.0, 0.0, side, p, 0.0)
        b   = (1 - price) / price
        kf  = max(0.0, (b * p - (1 - p)) / b)
        frac = self._adj(STRATEGY.kelly_fraction)
        amt = min(kf * frac * bankroll,
                  bankroll * RISK.max_single_trade_pct,
                  STRATEGY.max_bet_usdc)
        if amt < STRATEGY.min_bet_usdc:
            amt = STRATEGY.min_bet_usdc
        ev = p * amt * b - (1-p) * amt - amt * STRATEGY.taker_fee
        if ev <= 0:
            return SizingResult(0.0, 0.0, side, p, 0.0)
        return SizingResult(round(amt, 4), kf * frac, side, p, round(ev, 4))

    def _adj(self, f: float) -> float:
        if self.losses >= RISK.losing_streak_tightening: return f * 0.60
        if self.wins   >= RISK.winning_streak_loosening: return min(f * 1.15, 0.40)
        return f

    def win(self):  self.wins  += 1; self.losses = 0
    def loss(self): self.losses += 1; self.wins  = 0


# ══════════════════════════════════════════════════════════════════
# RISK MANAGER
# ══════════════════════════════════════════════════════════════════

@dataclass
class Position:
    market_id: str
    side: str
    shares: float
    entry_price: float
    cost_usdc: float
    asset: str
    open_time: float = 0.0
    question: str    = ""


class RiskManager:
    def __init__(self, bankroll: float):
        self.bankroll    = bankroll
        self.peak        = bankroll
        self.day_start   = bankroll
        self.day_reset_t = time.time()
        self.positions: Dict[str, Position] = {}
        self.daily_pnl   = 0.0
        self.total_pnl   = 0.0
        self.halted      = False
        self.halt_reason = ""
        self.trades      = 0
        self.wins        = 0
        self.losses      = 0

    def _day_reset(self):
        if time.time() - self.day_reset_t > 86400:
            self.day_start = self.bankroll; self.daily_pnl = 0.0
            self.day_reset_t = time.time()

    def approve(self, est, sizing, asset) -> tuple:
        self._day_reset()
        if sizing.recommended_usdc <= 0: return False, "Zero size"
        if OPS.dry_run:
            if sizing.recommended_usdc > self.bankroll: return False, "Insufficient bankroll"
            if len(self.positions) >= RISK.max_open_positions: return False, "Max positions"
            corr = sum(1 for p in self.positions.values() if p.asset == asset)
            if corr >= RISK.max_correlated_positions: return False, f"Too many {asset}"
            return True, ""
        if self.halted: return False, f"Halted: {self.halt_reason}"
        dd = (self.day_start - self.bankroll) / max(self.day_start, 1)
        if dd >= RISK.max_daily_loss_pct:
            self._halt(f"Daily loss {dd*100:.1f}%"); return False, "Daily loss"
        self.peak = max(self.peak, self.bankroll)
        td = (self.peak - self.bankroll) / max(self.peak, 1)
        if td >= RISK.max_drawdown_pct:
            self._halt(f"Drawdown {td*100:.1f}%"); return False, "Drawdown"
        if sizing.recommended_usdc > self.bankroll: return False, "Insufficient bankroll"
        if len(self.positions) >= RISK.max_open_positions: return False, "Max positions"
        corr = sum(1 for p in self.positions.values() if p.asset == asset)
        if corr >= RISK.max_correlated_positions: return False, f"Too many {asset}"
        return True, ""

    def open(self, pos: Position):
        self.positions[pos.market_id] = pos
        self.bankroll -= pos.cost_usdc
        self.trades   += 1

    def close(self, mid: str, pnl: float, cost_usdc: float):
        self.positions.pop(mid, None)
        self.bankroll  += cost_usdc + pnl
        self.daily_pnl += pnl
        self.total_pnl += pnl
        self.peak = max(self.peak, self.bankroll)
        if pnl > 0:
            self.wins += 1
        else:
            self.losses += 1

    def _halt(self, r: str):
        self.halted = True; self.halt_reason = r
        logger.critical(C(f"[Risk] 🛑 HALT: {r}", RED, BOLD))

    @property
    def win_rate(self):
        t = self.wins + self.losses
        return self.wins / t if t > 0 else 0.0

    def status(self):
        return {
            "bankroll": round(self.bankroll, 4),
            "daily_pnl": round(self.daily_pnl, 4),
            "total_pnl": round(self.total_pnl, 4),
            "drawdown_pct": round((self.peak - self.bankroll) / max(self.peak, 1) * 100, 2),
            "positions": len(self.positions),
            "win_rate": round(self.win_rate * 100, 1),
            "trades": self.trades, "halted": self.halted,
        }


# ══════════════════════════════════════════════════════════════════
# STATE MANAGER
# ══════════════════════════════════════════════════════════════════

@dataclass
class BotState:
    bankroll: float
    peak: float
    total_pnl: float
    daily_pnl: float
    open_positions: Dict[str, dict] = field(default_factory=dict)
    trade_history: List[dict]       = field(default_factory=list)
    total_trades: int    = 0
    win_count: int       = 0
    loss_count: int      = 0
    session_start: float = 0.0
    last_save: float     = 0.0


class StateManager:
    def __init__(self):
        os.makedirs(os.path.dirname(OPS.state_file), exist_ok=True)
        self.state = self._load()

    def _load(self) -> BotState:
        if OPS.reset_state and os.path.exists(OPS.state_file):
            try:
                os.remove(OPS.state_file)
                logger.info(C(f"[State] Reset: removed {OPS.state_file}", YLW))
            except Exception as e:
                logger.warning(C(f"[State] Reset failed: {e}", YLW))
        if os.path.exists(OPS.state_file):
            try:
                with open(OPS.state_file) as f:
                    d = json.load(f)
                s = BotState(**{k: v for k, v in d.items() if k in BotState.__dataclass_fields__})
                logger.info(C(f"[State] Loaded: ${s.bankroll:.4f} | pnl=${s.total_pnl:.4f}", LIME))
                return s
            except Exception as e:
                logger.warning(C(f"[State] Load failed: {e}", YLW))
        logger.info(C(f"[State] Fresh start: ${RISK.initial_bankroll:.4f}", CYAN))
        return BotState(bankroll=RISK.initial_bankroll, peak=RISK.initial_bankroll,
                        total_pnl=0.0, daily_pnl=0.0, session_start=time.time())

    def save(self):
        self.state.last_save = time.time()
        try:
            with open(OPS.state_file, "w") as f:
                json.dump(asdict(self.state), f, indent=2)
        except Exception as e:
            logger.error(C(f"[State] Save error: {e}", RED))

    def record_open(self, pos: Position, strategy: str):
        self.state.open_positions[pos.market_id] = {**asdict(pos), "strategy": strategy}
        self.save()

    def record_close(self, mid: str, ep: float, pnl: float):
        pd = self.state.open_positions.pop(mid, None)
        if not pd: return
        cost = pd.get("cost_usdc", 0)
        self.state.trade_history.append({
            "trade_id": f"T{self.state.total_trades+1:06d}",
            "market_id": mid, "question": pd.get("question",""),
            "side": pd.get("side",""), "entry": pd.get("entry_price",0),
            "exit": ep, "pnl": round(pnl,4),
            "pnl_pct": round(pnl/cost*100 if cost else 0, 2),
            "asset": pd.get("asset",""), "strategy": pd.get("strategy",""),
            "dry_run": OPS.dry_run,
        })
        self.state.total_pnl    += pnl
        self.state.daily_pnl    += pnl
        self.state.bankroll     += pnl + cost
        self.state.peak         = max(self.state.peak, self.state.bankroll)
        self.state.total_trades += 1
        if pnl > 0: self.state.win_count  += 1
        else:       self.state.loss_count += 1
        self.save()

    def sync(self, br: float):
        self.state.bankroll = br
        self.state.peak     = max(self.state.peak, br)


# ══════════════════════════════════════════════════════════════════
# EXECUTION ENGINE
# ══════════════════════════════════════════════════════════════════

@dataclass
class OrderRequest:
    token_id: str
    side: str
    price: float
    size_usdc: float
    order_type: str    = "LIMIT"
    time_in_force: str = "GTC"


class ExecutionEngine:
    def __init__(self):
        self._sess_obj = None
        self.dry_run   = OPS.dry_run
        self._n        = 0
        logger.info(C(f"[Exec] Mode: {'🟡 DRY-RUN' if self.dry_run else '🔴 LIVE'}", GOLD if self.dry_run else RED))

    async def _get_sess(self) -> aiohttp.ClientSession:
        if not self._sess_obj or self._sess_obj.closed:
            self._sess_obj = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=OPS.order_timeout_seconds))
        return self._sess_obj

    def _sign(self, ts, method, path, body=""):
        return hmac.new(API_SECRET.encode(),
                        (ts+method.upper()+path+body).encode(),
                        hashlib.sha256).hexdigest()

    def _headers(self, method, path, body=""):
        ts = str(int(time.time() * 1000))
        return {
            "Content-Type": "application/json",
            "POLY_ADDRESS": WALLET_ADDRESS,
            "POLY_SIGNATURE": self._sign(ts, method, path, body),
            "POLY_TIMESTAMP": ts,
            "POLY_API_KEY": API_KEY,
            "POLY_PASSPHRASE": API_PASSPHRASE,
        }

    async def place(self, req: OrderRequest) -> bool:
        if req.price <= 0 or req.price >= 1: return False
        # No platform minimum enforced here — CLOB handles per-account
        shares   = req.size_usdc / req.price
        mode_tag = C("[DRY]", GOLD) if self.dry_run else C("[LIVE]", RED)
        side_tag = C(req.side, LIME if req.side == "BUY" else ORG)
        logger.info(
            f"[Exec] {mode_tag} {side_tag} {req.order_type} "
            f"tok={req.token_id[:10]}... "
            f"price={C(f'{req.price:.4f}', WHITE)} "
            f"size={C(f'${req.size_usdc:.4f}', GOLD)} "
            f"({C(f'{shares:.4f}sh', CYAN)})"
        )
        if self.dry_run:
            self._n += 1
            return True

        payload = {
            "order": {
                "salt": int(time.time()*1e6), "maker": WALLET_ADDRESS,
                "signer": WALLET_ADDRESS,
                "taker": "0x0000000000000000000000000000000000000000",
                "tokenId": req.token_id,
                "makerAmount": str(int(shares * 1e6)),
                "takerAmount": str(int(req.size_usdc * 1e6)),
                "expiration": str(int(time.time()+3600)), "nonce": "0",
                "feeRateBps": "0" if req.order_type == "LIMIT" else "200",
                "side": "0" if req.side == "BUY" else "1",
                "signatureType": 0, "signature": "",
            },
            "owner": WALLET_ADDRESS, "orderType": req.order_type,
        }
        body = json.dumps(payload)
        sess = await self._get_sess()
        for _ in range(OPS.max_order_retries):
            try:
                async with sess.post(f"{CLOB_BASE_URL}/order",
                                     headers=self._headers("POST","/order",body),
                                     data=body) as r:
                    if r.status in (200, 201): return True
                    logger.warning(C(f"[Exec] {r.status}: {await r.text()}", RED))
            except Exception as e:
                logger.error(C(f"[Exec] {e}", RED))
            await asyncio.sleep(1)
        return False

    async def cancel_all(self):
        if self.dry_run: return
        sess = await self._get_sess()
        body = json.dumps({"owner": WALLET_ADDRESS})
        async with sess.delete(f"{CLOB_BASE_URL}/orders",
                               headers=self._headers("DELETE","/orders",body),
                               data=body):
            pass

    async def close(self):
        if self._sess_obj and not self._sess_obj.closed:
            await self._sess_obj.close()


# ══════════════════════════════════════════════════════════════════
# MAIN BOT
# ══════════════════════════════════════════════════════════════════

class PolyarbiBot:
    def __init__(self):
        logger.info(_sep("═", 64, CYAN))
        logger.info(C("  🤖  POLYARBI BOT v3.1", CYAN, BOLD))
        logger.info(C(f"  Mode: {'🟡 DRY-RUN' if OPS.dry_run else '🔴 LIVE ⚠️'}", GOLD if OPS.dry_run else RED))
        logger.info(_sep("═", 64, CYAN))

        self.store   = DataStore()
        self.state   = StateManager()
        self.risk    = RiskManager(self.state.state.bankroll)
        self.prob    = ProbabilityEngine(self.store)
        self.mm      = StoikovMM()
        self.sizer   = KellySizer()
        self.exec    = ExecutionEngine()
        self.scanner = PolymarketScanner(self.store)
        self.binance = BinanceFeed(self.store)
        self.poly_ws = PolymarketWSFeed(self.store)
        self.store.on_price_update(self._on_price)

        self._running  = False
        self._tasks    = []
        self._shutdown = False
        self._last_scan = 0.0

        # 5-min window tracking
        self._win_start_ts  = self._current_window_start()
        self._win_trades    = 0
        self._win_pnl       = 0.0
        self._win_yes_cnt   = 0
        self._win_no_cnt    = 0
        self._win_yes_amt   = 0.0
        self._win_no_amt    = 0.0
        self._win_start_bal = self.risk.bankroll

    async def _on_price(self, tick: PriceTick):
        dc = LIME if tick.change_pct_24h >= 0 else RED
        logger.info(
            f"[Price] {C(tick.symbol, GOLD)} "
            f"{C(f'{tick.price:.2f}', WHITE)} | "
            f"Δ={C(f'{tick.change_pct_24h:+.2f}%', dc)}"
        )

    # ─────────────────────────────────────────────────────────────
    async def start(self):
        if not OPS.dry_run:
            print("\n" + "⚠️  " * 20)
            print(C(f"LIVE MODE — Real money! ${self.risk.bankroll:.4f} USDC", RED, BOLD))
            if input("Type 'LIVE' to confirm: ").strip() != "LIVE":
                print("Aborted."); return
            await asyncio.sleep(3)

        run_connectivity_check()
        self._running = True
        logger.info(C(
            f"[Main] scan={OPS.scan_interval_seconds}s | rescan={OPS.full_scan_interval}s",
            CYAN
        ))
        self._tasks = [
            asyncio.create_task(self.binance.run(),  name="binance"),
            asyncio.create_task(self._trade_loop(),  name="trade"),
            asyncio.create_task(self._status_loop(), name="status"),
        ]
        logger.info(C("[Main] Waiting 3s for Binance data...", CYAN))
        await asyncio.sleep(3)
        logger.info(C("[Main] Initial market scan...", CYAN))
        await self.scanner.scan_markets()
        ws_ids = [m.yes_token_id for m in self.store.markets.values()]
        if ws_ids:
            self._tasks.append(asyncio.create_task(self.poly_ws.run(ws_ids), name="poly_ws"))
        logger.info(C(
            f"[Main] ✅ Started | Markets={len(self.store.markets)} | ${self.risk.bankroll:.4f}",
            LIME
        ))
        try:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        except Exception as e:
            logger.error(C(f"[Main] {e}", RED))
        finally:
            await self.shutdown()

    # ─────────────────────────────────────────────────────────────
    def _current_window_start(self) -> int:
        return int(time.time() // 300 * 300)

    def _window_label(self, ts: int) -> str:
        s = datetime.fromtimestamp(ts).strftime("%H:%M")
        e = datetime.fromtimestamp(ts + 300).strftime("%H:%M")
        return f"{s}  →  {e}"

    def _reset_window(self, ts: int):
        self._win_start_ts  = ts
        self._win_trades    = 0
        self._win_pnl       = 0.0
        self._win_yes_cnt   = 0
        self._win_no_cnt    = 0
        self._win_yes_amt   = 0.0
        self._win_no_amt    = 0.0
        self._win_start_bal = self.risk.bankroll

    def _maybe_roll_window(self):
        now_ts = self._current_window_start()
        while self._win_start_ts < now_ts:
            _log_window_summary(
                label     = self._window_label(self._win_start_ts),
                trades    = self._win_trades,
                yes_cnt   = self._win_yes_cnt,
                no_cnt    = self._win_no_cnt,
                yes_amt   = self._win_yes_amt,
                no_amt    = self._win_no_amt,
                wpnl      = self._win_pnl,
                start_bal = self._win_start_bal,
                end_bal   = self.risk.bankroll,
            )
            self._win_start_ts += 300
            self._reset_window(self._win_start_ts)

    def _record_open(self, cost: float, side: str):
        self._maybe_roll_window()
        self._win_trades += 1
        if side == "YES":
            self._win_yes_cnt += 1; self._win_yes_amt += cost
        else:
            self._win_no_cnt  += 1; self._win_no_amt  += cost

    def _record_close(self, pnl: float):
        self._maybe_roll_window()
        self._win_pnl += pnl

    # ─────────────────────────────────────────────────────────────
    async def _trade_loop(self):
        while self._running:
            try:
                t0 = time.time()
                await self._cycle()
                await asyncio.sleep(max(0.1, OPS.scan_interval_seconds - (time.time() - t0)))
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(C(f"[Loop] {e}", RED), exc_info=True)
                await asyncio.sleep(5)

    async def _cycle(self):
        now = time.time()
        if now - self._last_scan > max(OPS.full_scan_interval, self.scanner._scan_retry_delay):
            await self.scanner.scan_markets()
            self._last_scan = now

        best_opp   = None
        best_score = -999.0

        for mid, market in list(self.store.markets.items()):
            try:
                mins_left = (market.end_timestamp - now) / 60
                if mins_left < STRATEGY.min_time_to_expiry_minutes:
                    continue
                book = self.store.order_books.get(mid)
                if not book or (now - book.last_updated) > 30:
                    book = await self.scanner.fetch_order_book(market)
                    if not book: continue
                est = self.prob.estimate(market)
                if not est: continue

                ec = LIME if est.best_edge > 0 else RED
                logger.info(
                    f"[Eval] {C(market.asset_keyword, GOLD, BOLD)} | "
                    f"YES={C(str(book.best_yes_ask), LIME)} "
                    f"NO={C(str(book.best_no_ask), ORG)} | "
                    f"sum={C(f'{book.spread_sum:.4f}' if book.spread_sum else 'N/A', WHITE)} | "
                    f"edge={C(f'{est.best_edge:+.4f}({est.best_side})', ec)} "
                    f"conf={C(f'{est.confidence:.3f}', CYAN)} | "
                    f"arb={C(f'{est.arb_gap:+.4f}', MAG)} "
                    f"mom={C(f'{est.momentum:+.3f}', BLUE)} | "
                    f"{C(f'{mins_left:.1f}min', YLW)} | "
                    f"arb={C(str(est.has_arb), LIME if est.has_arb else DIM)} "
                    f"edge={C(str(est.has_edge), LIME if est.has_edge else DIM)}"
                )

                if est.has_arb and book.spread_sum:
                    score = 1000.0 + abs(est.arb_gap) * 500
                    if score > best_score:
                        best_score = score; best_opp = ("arb", market, est, book)
                    continue

                if est.has_edge:
                    score = est.best_edge*50 + est.confidence*30 + abs(est.momentum)*20
                    if score > best_score:
                        best_score = score; best_opp = ("directional", market, est, book)
                    continue

                if self.mm.inv_ok(mid):
                    hist = self.store.get_recent_prices(market.asset_keyword + "USDT", 120)
                    rets = []
                    if len(hist) >= 2:
                        ps   = [t.price for t in hist]
                        rets = [(ps[i]-ps[i-1])/ps[i-1] for i in range(1, len(ps))]
                    q = self.mm.quotes(market, est, rets)
                    if q and q.is_valid and q.spread >= 0.008:
                        score = q.spread*80 + est.confidence*5
                        if score > best_score:
                            best_score = score; best_opp = ("mm", market, est, book, q)

            except Exception as e:
                logger.debug(f"[Cycle] {mid[:8]}: {e}")

        await self._monitor_positions()

        if best_opp:
            if self.risk.halted:
                logger.info(C(f"[Trade] 🛑 Halted — {self.risk.halt_reason}", RED))
            else:
                await self._execute(best_opp)
        else:
            if self.store.markets:
                logger.info(C("[Trade] ⏳ No tradable opportunity this cycle", YLW))
            else:
                logger.info(C(
                    f"[Trade] ⏳ Waiting for markets (retry in {self.scanner._scan_retry_delay:.0f}s)",
                    YLW
                ))

        self.state.sync(self.risk.bankroll)

    # ─────────────────────────────────────────────────────────────
    async def _execute(self, opp):
        kind = opp[0]
        if kind == "arb":
            _, market, est, book = opp
            await self._exec_arb(market, book)

        elif kind == "directional":
            _, market, est, book = opp
            if market.condition_id in self.risk.positions:
                logger.info(C(
                    f"[Trade] ⏭  SKIPPED {market.asset_keyword} — already open", YLW
                ))
                return
            yes_ask = book.best_yes_ask or 0.5
            no_ask  = book.best_no_ask  or 0.5
            sizing  = self.sizer.size(est, self.risk.bankroll, yes_ask, no_ask)
            if sizing.recommended_usdc <= 0:
                logger.info(C(f"[Trade] ❌ SKIPPED {market.asset_keyword} — EV negative", RED))
                return
            ok, reason = self.risk.approve(est, sizing, market.asset_keyword)
            if not ok:
                logger.info(C(f"[Trade] ❌ REJECTED {market.asset_keyword} — {reason}", RED))
                return
            mins_left = (market.end_timestamp - time.time()) / 60
            await self._exec_directional(market, est, sizing, book, mins_left)

        elif kind == "mm":
            _, market, est, book, quotes = opp
            if market.condition_id in self.risk.positions:
                return
            size = max(STRATEGY.min_bet_usdc * 2, min(self.risk.bankroll * 0.02, 30.0))
            if size > self.risk.bankroll:
                logger.info(C(f"[Trade] ❌ MM SKIPPED {market.asset_keyword} — bankroll too small", RED))
                return
            await self._exec_mm(market, quotes, size)

    async def _exec_directional(self, market, est, sizing, book, mins_left):
        side     = est.best_side
        token_id = market.yes_token_id if side == "YES" else market.no_token_id
        price    = (book.best_yes_ask if side == "YES" else book.best_no_ask) or 0.5
        if price <= 0:
            logger.info(C(f"[Trade] ❌ FAILED {market.asset_keyword} — invalid price", RED))
            return
        ok = await self.exec.place(OrderRequest(
            token_id=token_id, side="BUY",
            price=round(price, 4), size_usdc=sizing.recommended_usdc,
        ))
        if not ok:
            logger.info(C(f"[Trade] ❌ FAILED {market.asset_keyword} — order rejected", RED))
            return
        shares = sizing.recommended_usdc / price
        _log_trade_open(
            side=side, asset=market.asset_keyword,
            amount=sizing.recommended_usdc, price=price, shares=shares,
            edge=est.best_edge, conf=est.confidence,
            strategy="directional", mins_left=mins_left,
            question=market.question,
        )
        self._record_open(sizing.recommended_usdc, side)
        pos = Position(
            market_id=market.condition_id, side=side,
            shares=shares, entry_price=price,
            cost_usdc=sizing.recommended_usdc, asset=market.asset_keyword,
            open_time=time.time(), question=market.question,
        )
        self.risk.open(pos)
        self.state.record_open(pos, "directional")
        self.mm.update_inv(market.condition_id, pos.shares if side == "YES" else -pos.shares)

    async def _exec_arb(self, market, book):
        ya, na = book.best_yes_ask, book.best_no_ask
        if not ya or not na:
            logger.info(C(f"[Trade] ❌ ARB FAILED {market.asset_keyword} — missing prices", RED))
            return
        total  = ya + na
        profit = 1.0 - total
        if profit < STRATEGY.arb_min_profit_usdc:
            logger.info(C(
                f"[Trade] ❌ ARB SKIPPED {market.asset_keyword} — profit ${profit:.4f} < min",
                YLW
            ))
            return
        size   = min(self.risk.bankroll * RISK.max_single_trade_pct * 2, STRATEGY.max_bet_usdc)
        shares = size / total
        yr, nr = await asyncio.gather(
            self.exec.place(OrderRequest(market.yes_token_id, "BUY", ya, shares*ya)),
            self.exec.place(OrderRequest(market.no_token_id,  "BUY", na, shares*na)),
        )
        if yr and nr:
            logger.info(C(
                f"\n  💰  ARB HIT ✅  {market.asset_keyword}\n"
                f"  YES@{ya:.4f} + NO@{na:.4f} = {total:.4f}\n"
                f"  Profit = ${profit*shares:.4f} USDC\n",
                LIME, BOLD
            ))
            self._record_open(shares * ya, "YES")
            self._record_open(shares * na, "NO")
        else:
            logger.info(C(f"[Trade] ❌ ARB FAILED {market.asset_keyword} — orders failed", RED))

    async def _exec_mm(self, market, q, size_usdc):
        quote_size = size_usdc / 2
        if size_usdc > self.risk.bankroll:
            logger.info(C(f"[Trade] ❌ MM SKIPPED — bankroll too small", RED))
            return
        br, ar = await asyncio.gather(
            self.exec.place(OrderRequest(market.yes_token_id, "BUY",  q.bid_price, quote_size)),
            self.exec.place(OrderRequest(market.yes_token_id, "SELL", q.ask_price, quote_size)),
        )
        if br or ar:
            logger.info(C(
                f"[Trade] 📊 MM {market.asset_keyword} | "
                f"bid={q.bid_price:.4f} ask={q.ask_price:.4f} spread={q.spread:.4f}",
                CYAN
            ))
        else:
            logger.info(C(f"[Trade] ❌ MM FAILED {market.asset_keyword} — quotes failed", RED))

    # ─────────────────────────────────────────────────────────────
    async def _monitor_positions(self):
        now = time.time()
        for mid, pos in list(self.risk.positions.items()):
            market = self.store.markets.get(mid)
            if not market: continue
            mins_left = (market.end_timestamp - now) / 60
            if mins_left < 1.5:
                await self._close_pos(mid, pos, "expiry"); continue
            est = self.prob.estimate(market)
            if est:
                if pos.side == "YES" and est.edge_no > 0.05:
                    await self._close_pos(mid, pos, "edge_reversed")
                elif pos.side == "NO" and est.edge_yes > 0.05:
                    await self._close_pos(mid, pos, "edge_reversed")

    async def _close_pos(self, mid: str, pos: Position, reason: str):
        book = self.store.order_books.get(mid)
        ep   = 0.5
        if book:
            ep = (book.best_yes_bid if pos.side == "YES" else book.best_no_bid) or 0.5
        pnl = (ep - pos.entry_price) * pos.shares - pos.cost_usdc * STRATEGY.taker_fee
        m   = self.store.markets.get(mid)
        if m:
            tid = m.yes_token_id if pos.side == "YES" else m.no_token_id
            await self.exec.place(OrderRequest(tid, "SELL", ep, pos.shares * ep))
        self.risk.close(mid, pnl, pos.cost_usdc)
        self.state.record_close(mid, ep, pnl)
        self.sizer.win() if pnl > 0 else self.sizer.loss()
        self._record_close(pnl)
        _log_trade_close(
            side=pos.side, asset=pos.asset,
            entry=pos.entry_price, exit_p=ep,
            shares=pos.shares, pnl=pnl,
            cost=pos.cost_usdc, reason=reason,
        )

    # ─────────────────────────────────────────────────────────────
    async def _status_loop(self):
        while self._running:
            try:
                self._maybe_roll_window()
                await asyncio.sleep(30)
                s      = self.risk.status()
                prices = []
                for sym in BINANCE_SYMBOLS:
                    t = self.store.get_price(sym)
                    if t:
                        dc = LIME if t.change_pct_24h >= 0 else RED
                        prices.append(
                            f"{C(sym[:3].upper(), GOLD)}="
                            f"{C(f'{t.price:.2f}', WHITE)}"
                            f"{C(f'({t.change_pct_24h:+.2f}%)', dc)}"
                        )
                pc = LIME if s['total_pnl'] >= 0 else RED
                bankroll_str = C(f'${s["bankroll"]}', GOLD)
                total_pnl_str = C(f'{s["total_pnl"]:+.4f}', pc)
                pos_str = C(str(s['positions']), WHITE)
                trades_str = C(str(s['trades']), WHITE)
                wr_str = C(f'{s["win_rate"]:.0f}%', LIME)
                dd_str = C(f'{s["drawdown_pct"]:.1f}%', YLW)
                logger.info(
                    f"{C('[STATUS]', CYAN, BOLD)} "
                    f"💰{bankroll_str} | "
                    f"pnl={total_pnl_str} | "
                    f"pos={pos_str} | "
                    f"trades={trades_str} | "
                    f"wr={wr_str} | "
                    f"dd={dd_str} | "
                    f"{' '.join(prices)}"
                )
            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(5)

    # ─────────────────────────────────────────────────────────────
    async def shutdown(self):
        if self._shutdown: return
        self._shutdown = True
        logger.info(C("[Main] Shutting down...", YLW))
        self._running = False
        self.binance.stop(); self.poly_ws.stop()
        for fn in (self.binance.close, self.poly_ws.close):
            try: await fn()
            except Exception: pass
        if not OPS.dry_run:
            try: await self.exec.cancel_all()
            except Exception: pass
        for t in self._tasks:
            if not t.done(): t.cancel()
        try: await asyncio.gather(*self._tasks, return_exceptions=True)
        except Exception: pass
        self.state.save()
        try:
            await self.exec.close()
            await self.scanner.close()
        except Exception: pass

        s  = self.risk.status()
        pc = LIME if s['total_pnl'] >= 0 else RED
        logger.info("")
        logger.info(_sep("═", 64, CYAN))
        logger.info(C("  🏁  FINAL SESSION SUMMARY", CYAN, BOLD))
        logger.info(_sep("─", 64, CYAN))
        logger.info(C(f"  Balance   : ${s['bankroll']}", GOLD))
        logger.info(C(f"  Total PnL : {s['total_pnl']:+.4f} USDC", pc))
        logger.info(C(f"  Trades    : {s['trades']}", WHITE))
        logger.info(C(f"  Win Rate  : {s['win_rate']:.0f}%", LIME))
        logger.info(C(f"  Drawdown  : {s['drawdown_pct']:.1f}%", YLW))
        logger.info(_sep("═", 64, CYAN))
        logger.info("")


# ══════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════

async def main():
    bot = PolyarbiBot()
    try:
        await bot.start()
    except asyncio.CancelledError:
        logger.info(C("[Main] Cancelled", YLW))
        await bot.shutdown()
    except KeyboardInterrupt:
        logger.info(C("[Main] KeyboardInterrupt", YLW))
        await bot.shutdown()
    except Exception as e:
        logger.exception(C(f"[Main] Fatal: {e}", RED))
        await bot.shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info(C("[Main] KeyboardInterrupt at top level", YLW))
# Updated 2026-02-28: Strengthen orderbook imbalance comment
# Updated 2026-03-03: Update LLM validation note
# Updated 2026-03-05: Refine documentation details
# Updated 2026-03-11: Update LLM validation note
# Updated 2026-03-13: Adjust comments for strategy clarity