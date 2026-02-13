# """
# ╔══════════════════════════════════════════════════════════════════╗
# ║                    SWISSBOT v1.3 — FIXED                        ║
# ║         Copy-trade swisstony on Polymarket (CLOB only)          ║
# ║  FIXED: order_version_mismatch | funder addr | sig type         ║
# ║  FIXED: ApiCreds init | speed optimized | retry logic           ║
# ╚══════════════════════════════════════════════════════════════════╝
# Swiss bot fixes - 2026-02-13

# SETUP:
#     pip install py-clob-client-v2 requests python-dotenv colorama

# .env required keys:
#     PRIVATE_KEY
#     POLYMARKET_FUNDER_ADDRESS   ← CRITICAL: apna proxy wallet address
#     SIGNATURE_TYPE              ← 0=EOA, 1=POLY_PROXY, 2=POLY_GNOSIS_SAFE, 3=POLY_1271

#     NOTE: Older py-clob-client used 2 for proxy wallets. In py-clob-client-v2,
#     proxy wallets should use SIGNATURE_TYPE=1.

#     Optional overrides:
#     CLOB_API_KEY, CLOB_SECRET, CLOB_PASS_PHRASE
#     SIZE_MULTIPLIER, MAX_POSITION_PER_GAME, MAX_TRADE_SIZE
#     MIN_TRADE_SIZE, DRY_RUN, LOG_LEVEL

# RUN:
#     python swissbot.py
# """

# ─── Prefer IPv4, but don’t fail DNS resolution entirely ───────
import socket
_orig_gai = socket.getaddrinfo
def _ipv4_only(host, port, family=0, type=0, proto=0, flags=0):
    try:
        return _orig_gai(host, port, socket.AF_INET, type, proto, flags)
    except OSError:
        return _orig_gai(host, port, family, type, proto, flags)
socket.getaddrinfo = _ipv4_only
# ────────────────────────────────────────────────────────────────

import asyncio
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

import requests
from dotenv import load_dotenv
import colorama
colorama.init(autoreset=True)

for p in [os.path.dirname(os.path.abspath(__file__)), os.getcwd()]:
    if p and p not in sys.path:
        sys.path.insert(0, p)

try:
    from py_clob_client_v2 import ClobClient
    from py_clob_client_v2.clob_types import (
        ApiCreds, MarketOrderArgs, OrderType,
        BalanceAllowanceParams, AssetType,
    )
    from py_clob_client_v2.order_builder.constants import BUY as CLOB_BUY, SELL as CLOB_SELL
except ImportError as e:
    sys.exit(f"[FATAL] py-clob-client-v2 import failed: {e}\n"
             "Run: pip install py-clob-client-v2 requests python-dotenv colorama")

# ───────────────────────── CONFIGURATION ──────────────────────────
load_dotenv("swissbot.env")
load_dotenv(".env")

def _env(key, default=None, cast=str):
    val = os.getenv(key, default)
    if val is None: return None
    return cast(val) if cast != str else val

PRIVATE_KEY       = _env("PRIVATE_KEY")
CLOB_API_KEY      = _env("CLOB_API_KEY") or _env("API_KEY")
CLOB_SECRET       = _env("CLOB_SECRET") or _env("API_SECRET")
CLOB_PASS_PHRASE  = _env("CLOB_PASS_PHRASE") or _env("API_PASSPHRASE")

# FIX #1: Funder address - Polymarket proxy wallet ke liye MUST
FUNDER_ADDRESS    = _env("POLYMARKET_FUNDER_ADDRESS", "")
# RPC URL for contract detection
RPC_URL           = _env("RPC_URL", "https://polygon-rpc.com")
# ✅ FIX #2: Signature type — new py_clob_client_v2 values
SIGNATURE_TYPE_RAW = _env("SIGNATURE_TYPE", 0, cast=int)
SIGNATURE_TYPE     = SIGNATURE_TYPE_RAW
LEGACY_SIGNATURE_TYPE_WARNING = False


def _is_contract_address(address: str):
    if not address or not address.startswith("0x"):
        return False
    try:
        resp = requests.post(
            RPC_URL,
            json={"jsonrpc": "2.0", "id": 1, "method": "eth_getCode", "params": [address, "latest"]},
            timeout=10,
        )
        resp.raise_for_status()
        code = resp.json().get("result", "")
        return bool(code and code != "0x")
    except Exception as e:
        print("[WARN] Contract detection failed for", address, "using RPC_URL=", RPC_URL, ":", e)
        return False

if SIGNATURE_TYPE_RAW == 2:
    SIGNATURE_TYPE = 1
    LEGACY_SIGNATURE_TYPE_WARNING = True

if FUNDER_ADDRESS and _is_contract_address(FUNDER_ADDRESS):
    if SIGNATURE_TYPE != 3:
        print("[WARNING] POLYMARKET_FUNDER_ADDRESS appears to be a contract wallet; switching SIGNATURE_TYPE to 3 (POLY_1271).")
        SIGNATURE_TYPE = 3

CLOB_HTTP_URL     = _env("CLOB_HTTP_URL", "https://clob.polymarket.com")
DATA_API_URL      = _env("DATA_API_URL",  "https://data-api.polymarket.com")

TARGET_WALLET     = _env(
    "TARGET_WALLET",
    "0x3c58ef422754ff22c7e806336feba0064d8b776b"
).lower()

SWISSTONY_BALANCE     = _env("SWISSTONY_BALANCE",       105000.0, cast=float)
MAX_POSITION_PER_GAME = _env("MAX_POSITION_PER_GAME",   150.0, cast=float)
MIN_TRADE_SIZE        = _env("MIN_TRADE_SIZE",           1.0, cast=float)
MAX_TRADE_SIZE        = _env("MAX_TRADE_SIZE",           500.0, cast=float)
MAX_SLIPPAGE          = _env("MAX_SLIPPAGE",             0.05, cast=float)
DAILY_LOSS_LIMIT      = _env("DAILY_LOSS_LIMIT",         -100.0, cast=float)
MIN_REMAINING_BALANCE = _env("MIN_REMAINING_BALANCE",    5.0, cast=float)
POLL_INTERVAL_SEC     = _env("POLL_INTERVAL_MS",         500, cast=int) / 1000.0
GAME_IDLE_TIMEOUT     = _env("GAME_IDLE_TIMEOUT",        1800, cast=int)
DEFAULT_BALANCE       = _env("DEFAULT_BALANCE",          1000.0, cast=float)
CIRCUIT_BREAKER_N     = 3
CHAIN_ID              = _env("CHAIN_ID", 137, cast=int)
DRY_RUN               = _env("DRY_RUN",  "false").lower() == "true"
LOG_LEVEL             = _env("LOG_LEVEL", "INFO").upper()

# ─────────────────────────── LOGGER ───────────────────────────────
class ColFmt(logging.Formatter):
    C = {
        'DEBUG':    colorama.Fore.CYAN,
        'INFO':     colorama.Fore.GREEN,
        'WARNING':  colorama.Fore.YELLOW,
        'ERROR':    colorama.Fore.RED,
        'CRITICAL': colorama.Fore.MAGENTA + colorama.Style.BRIGHT,
    }
    def format(self, record):
        c = self.C.get(record.levelname, colorama.Style.RESET_ALL)
        return f"{c}{super().format(record)}{colorama.Style.RESET_ALL}"

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("swissbot")
for h in (log.handlers or logging.root.handlers):
    h.setFormatter(ColFmt("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S"))

if LEGACY_SIGNATURE_TYPE_WARNING:
    print(
        "[WARNING] ⚠️  Legacy SIGNATURE_TYPE=2 detected. Mapped to 1 (POLY_PROXY) for py_clob_client_v2. Use SIGNATURE_TYPE=1 for proxy wallets, 2 only for Gnosis Safe wallets."
    )

# ─────────────────────────── MODELS ───────────────────────────────
class TradeSide(str, Enum):
    BUY  = "BUY"
    SELL = "SELL"

@dataclass
class SwisstonyTrade:
    tx_hash:      str
    condition_id: str
    token_id:     str
    side:         TradeSide
    usd_size:     float
    price:        float
    timestamp:    int
    outcome:      str
    market_slug:  str = ""

@dataclass
class GameLock:
    condition_id:     str
    market_slug:      str
    locked_at:        float = field(default_factory=time.time)
    last_trade_at:    float = field(default_factory=time.time)
    total_copied_usd: float = 0.0
    trade_count:      int   = 0
    trades:           list  = field(default_factory=list)
    token_ids:        set   = field(default_factory=set)
    settled:          bool  = False

@dataclass
class BotState:
    locks:              dict  = field(default_factory=dict)
    processed_hashes:   set   = field(default_factory=set)
    my_balance:         float = 0.0
    target_balance:     float = SWISSTONY_BALANCE
    daily_pnl:          float = 0.0
    consecutive_losses: int   = 0
    paused_until:       float = 0.0
    day_start:          float = field(default_factory=time.time)

# ──────────────── CLOB CLIENT — FIXED INIT ────────────────────────
_clob: Optional[ClobClient] = None

def _load_api_creds() -> Optional[ApiCreds]:
    if all([CLOB_API_KEY, CLOB_SECRET, CLOB_PASS_PHRASE]):
        return ApiCreds(
            api_key=CLOB_API_KEY,
            api_secret=CLOB_SECRET,
            api_passphrase=CLOB_PASS_PHRASE,
        )
    return None


def build_clob_client() -> ClobClient:
    """
    ✅ FIX: Proper ClobClient init sequence.
    
    order_version_mismatch kyun aata hai:
      1. funder address miss — proxy wallets fail karte hain
      2. signature_type wrong — 0=EOA, 1=POLY_PROXY, 2=POLY_GNOSIS_SAFE
      3. ApiCreds wrong — env se lena chahiye, naye nahi banana chahiye
    """
    if not PRIVATE_KEY:
        sys.exit("[FATAL] PRIVATE_KEY missing in .env")

    log.info("🔧 Initializing CLOB client | sig_type=%d | funder=%s",
             SIGNATURE_TYPE, FUNDER_ADDRESS[:12] + "..." if FUNDER_ADDRESS else "NOT SET")

    creds = _load_api_creds()
    if creds:
        log.info("🔑 Using existing API creds from env")

    client = ClobClient(
        host=CLOB_HTTP_URL,
        chain_id=CHAIN_ID,
        key=PRIVATE_KEY,
        creds=creds,
        signature_type=SIGNATURE_TYPE,
        funder=FUNDER_ADDRESS if SIGNATURE_TYPE != 0 and FUNDER_ADDRESS else None,
    )

    if creds is None:
        log.info("🔑 Deriving API key via create_or_derive_api_key()")
        try:
            creds = client.create_or_derive_api_key()
            client.set_api_creds(creds)
            log.info("✅ API key derived successfully")
        except Exception as e:
            log.error("❌ API key derivation failed: %s", e)

    # ✅ FIX: Test connection — order_version_mismatch check karo pehle
    _verify_client(client)
    return client


def _verify_client(client: ClobClient):
    """Startup pe verify karo ke client sahi se kaam kar raha hai."""
    try:
        params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        resp   = client.get_balance_allowance(params)
        bal    = round(int(resp.get("balance", 0)) / 1e6, 2)
        log.info("✅ CLOB verified | balance=$%.2f", bal)
        if bal == 0:
            log.warning("⚠️  Balance $0 — check POLYMARKET_FUNDER_ADDRESS in .env")
            log.warning("    Agar proxy wallet use kar rahe ho: SIGNATURE_TYPE=1 set karo (py_clob_client_v2 proxy)")
    except Exception as e:
        log.warning("⚠️  CLOB verify failed: %s", e)
        log.warning("    Possible fixes:")
        log.warning("    1. POLYMARKET_FUNDER_ADDRESS=<your wallet> .env me add karo")
        log.warning("    2. SIGNATURE_TYPE=1 try karo agar proxy wallet hai")


def get_my_balance() -> float:
    try:
        params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        resp   = _clob.get_balance_allowance(params)
        bal    = round(int(resp.get("balance", 0)) / 1e6, 2)
        return bal
    except Exception as exc:
        log.warning("⚠️  Balance fetch failed: %s", exc)
        return 0.0


def get_target_balance() -> float:
    for url in [
        f"{DATA_API_URL}/portfolio?user={TARGET_WALLET}",
        f"{DATA_API_URL}/value?user={TARGET_WALLET}",
    ]:
        try:
            r = requests.get(url, timeout=5)
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, list) and data:
                    data = data[0]
                for key in ("value","balance","usdc","cashBalance","portfolioValue","total"):
                    if data.get(key) is not None:
                        try: return float(data[key])
                        except: pass
        except: pass
    return SWISSTONY_BALANCE

# ─────────────────── ORDER BOOK HELPERS ───────────────────────────
# ✅ FIX: Best ask/bid for price checks
def get_best_ask(token_id: str) -> Optional[float]:
    try:
        book = _clob.get_order_book(token_id)
        asks = book.asks if hasattr(book, "asks") else book.get("asks", [])
        if asks:
            return float(asks[0].price if hasattr(asks[0], "price") else asks[0]["price"])
    except: pass
    return None

def get_best_bid(token_id: str) -> Optional[float]:
    try:
        book = _clob.get_order_book(token_id)
        bids = book.bids if hasattr(book, "bids") else book.get("bids", [])
        if bids:
            return float(bids[0].price if hasattr(bids[0], "price") else bids[0]["price"])
    except: pass
    return None

def opposite_outcome(outcome: str) -> str:
    ou = outcome.upper()
    if any(x in ou for x in ("YES","UP","LONG")): return "NO"
    if any(x in ou for x in ("NO","DOWN","SHORT")): return "YES"
    return outcome

# ─────────────────── MARKET INFO / DISPLAY ────────────────────────
_market_cache = {}

def get_market_info(condition_id: str) -> dict:
    if condition_id in _market_cache:
        return _market_cache[condition_id]
    try:
        r = requests.get(f"{CLOB_HTTP_URL}/markets/{condition_id}", timeout=5)
        if r.status_code == 200:
            d    = r.json()
            info = {"name": d.get("question",""), "resolved_outcome": None}
            for oc in d.get("outcomes", []):
                if oc.get("winner", False):
                    info["resolved_outcome"] = oc.get("title","").upper().strip()
                    break
            _market_cache[condition_id] = info
            return info
    except: pass
    return {"name":"","resolved_outcome":None}

def format_market_name(condition_id, market_slug="", outcome=""):
    name = get_market_info(condition_id)["name"]
    readable = name or (market_slug.replace("-"," ").title() if len(market_slug)>5 else f"Market {condition_id[:12]}")
    if outcome: readable = f"{readable} -> {outcome}"
    url = (f"https://polymarket.com/event/{market_slug}"
           if len(market_slug)>5 else f"https://polymarket.com/market/{condition_id}")
    return readable, url

# ──────────────────────── POSITION SIZER ──────────────────────────
def calculate_copy_size(swiss_usd, my_balance, target_balance, already_used) -> float:
    if target_balance <= 0: target_balance = SWISSTONY_BALANCE
    ratio   = my_balance / target_balance
    raw_usd = ratio * swiss_usd
    remaining = MAX_POSITION_PER_GAME - already_used
    capped  = min(raw_usd, remaining, my_balance * 0.9)
    final   = max(MIN_TRADE_SIZE, min(capped, MAX_TRADE_SIZE))
    log.debug("Sizing: swiss=$%.0f ratio=%.3f raw=$%.2f final=$%.2f", swiss_usd, ratio, raw_usd, final)
    return final

# ─────────────────────────── RISK CHECKS ──────────────────────────
def risk_ok(trade, copy_size, used, state) -> tuple:
    now = time.time()
    if now < state.paused_until:
        return False, f"Circuit breaker active — paused {int(state.paused_until-now)}s more"
    if state.daily_pnl < DAILY_LOSS_LIMIT:
        return False, f"Daily loss limit hit (PnL=${state.daily_pnl:.2f})"
    if copy_size < MIN_TRADE_SIZE:
        return False, f"Size ${copy_size:.2f} below minimum ${MIN_TRADE_SIZE}"
    if trade.side == TradeSide.BUY:
        after = state.my_balance - copy_size
        if after < MIN_REMAINING_BALANCE:
            return False, f"Insufficient balance after trade (${after:.2f} < ${MIN_REMAINING_BALANCE})"
    if used + copy_size > MAX_POSITION_PER_GAME:
        return False, f"Per-game cap reached (${used:.2f}+${copy_size:.2f} > ${MAX_POSITION_PER_GAME})"
    return True, ""

# ───────────────────── ORDER EXECUTION — FIXED ────────────────────
async def execute_copy_trade(trade: SwisstonyTrade, copy_size: float, state: BotState) -> bool:
    """
    ✅ FIX: order_version_mismatch ka solution:
    
    Problem:  FAK order type mismatch, precision issues, wrong amount type
    Solution: FOK use karo (Polymarket me better supported), 
              amount 2 decimal places pe round karo,
              retry logic with exponential backoff
    """
    side_const = CLOB_BUY if trade.side == TradeSide.BUY else CLOB_SELL
    side_str   = trade.side.value

    if DRY_RUN:
        log.info("[DRY-RUN] %s $%.2f '%s' @ %.4f", side_str, copy_size, trade.outcome, trade.price)
        _update_state(trade, copy_size, success=True, state=state)
        return True

    # ✅ Amount calculation: BUY=USDC spend, SELL=shares count
    if trade.side == TradeSide.BUY:
        amount = round(copy_size, 2)
    else:
        shares = copy_size / trade.price if trade.price > 0 else 0
        amount = round(shares, 2)

    if amount < 0.01:
        log.warning("⚠️  Amount %.4f too small after rounding — skip", amount)
        _update_state(trade, 0, success=False, state=state)
        return False

    log.info("⚡ Placing order | %s $%.2f | token=%s... | ref=%.4f",
             side_str, amount, trade.token_id[:10], trade.price)

    # ✅ FIX: Retry with both FOK and FAK order types
    # order_version_mismatch kabi kabi order type mismatch se aata hai
    order_types_to_try = [OrderType.FOK, OrderType.FAK]
    last_error = None

    for attempt, order_type in enumerate(order_types_to_try, 1):
        try:
            market_order = MarketOrderArgs(
                token_id   = trade.token_id,
                amount     = amount,
                side       = side_const,
                order_type = order_type,
            )

            signed = _clob.create_market_order(market_order)
            result = _clob.post_order(signed, order_type)

            log.debug("Order result (attempt %d, %s): %s", attempt, order_type, result)

            err_msg = result.get("errorMsg", "")
            if "order_version_mismatch" in str(err_msg).lower():
                log.warning("⚠️  order_version_mismatch on %s — check SIGNATURE_TYPE/.env", order_type)
                last_error = err_msg
                continue  # dusra order type try karo

            size_matched = float(result.get("size_matched", 0) or 0)
            success      = result.get("success", True) and (size_matched > 0 or not err_msg)

            if success or size_matched > 0:
                filled_usd = round(size_matched * trade.price, 4) if size_matched > 0 else amount
                filled_usd = min(filled_usd, amount)
                log.info("✅ Order filled | matched=%.4f | usd≈$%.2f | order_id=%s",
                         size_matched, filled_usd, result.get("orderID","?"))
                _update_state(trade, filled_usd, success=True, state=state)
                return True
            else:
                log.warning("⚠️  Order not filled (%s): %s", order_type, err_msg or result)
                last_error = err_msg or str(result)

        except Exception as exc:
            err_str = str(exc)
            log.error("❌ Order error (attempt %d): %s", attempt, err_str)
            last_error = err_str

            if "invalid signature" in err_str.lower():
                log.warning("⚠️  Invalid signature detected — refreshing API creds and retrying")
                try:
                    creds = _clob.create_or_derive_api_key()
                    _clob.set_api_creds(creds)
                    log.info("✅ API creds refreshed successfully")
                    continue
                except Exception as refresh_exc:
                    log.error("❌ API creds refresh failed: %s", refresh_exc)

            # ✅ FIX: order_version_mismatch pe specific guidance
            if "order_version_mismatch" in err_str.lower():
                log.error("╔══════════════════════════════════════════════════════╗")
                log.error("║  ORDER_VERSION_MISMATCH — Fix karo:                 ║")
                log.error("║  1. .env me POLYMARKET_FUNDER_ADDRESS=<wallet> add  ║")
                log.error("║  2. SIGNATURE_TYPE=1 try karo (proxy wallet)        ║")
                log.error("║  3. Ya SIGNATURE_TYPE=0 try karo (EOA wallet)       ║")
                log.error("╚══════════════════════════════════════════════════════╝")
            continue

        if attempt < len(order_types_to_try):
            await asyncio.sleep(0.3)

    log.error("❌ All order attempts failed. Last error: %s", last_error)
    _update_state(trade, 0, success=False, state=state)
    return False


def _update_state(trade, filled_usd, success, state):
    lock = state.locks.get(trade.condition_id)
    if not lock:
        log.error("No lock for condition %s", trade.condition_id)
        return

    if success and filled_usd > 0:
        state.consecutive_losses  = 0
        lock.total_copied_usd    += filled_usd
        lock.trade_count         += 1
        lock.last_trade_at        = time.time()
        lock.trades.append((trade.outcome, filled_usd, trade.price, trade.token_id))
        lock.token_ids.add(trade.token_id)
        if trade.side == TradeSide.BUY:
            state.my_balance = max(state.my_balance - filled_usd, 0.0)
        else:
            state.my_balance += filled_usd

        market_name, market_url = format_market_name(
            trade.condition_id, trade.market_slug or lock.market_slug, trade.outcome)
        shares = filled_usd / trade.price if trade.price > 0 else 0

        sep = "+" + "-"*63 + "+"
        print(f"\n{sep}")
        print(f"| {'TRADE EXECUTED':^61} |")
        print(sep)
        print(f"| Action  : {'BOUGHT' if trade.side==TradeSide.BUY else 'SOLD':<53} |")
        print(f"| Amount  : ${filled_usd:<8.2f} ({shares:.2f} shares){'':30} |")
        print(f"| Outcome : {trade.outcome[:53]:<53} |")
        print(f"| Price   : {trade.price:<53.4f} |")
        print(f"| Market  : {market_name[:53]:<53} |")
        print(f"| Balance : ${state.my_balance:.2f} (remaining){'':38} |")
        print(f"| Link    : {market_url[:53]:<53} |")
        print(f"{sep}\n")
        log.info("💰 New balance: $%.2f", state.my_balance)
    else:
        state.consecutive_losses += 1
        if state.consecutive_losses >= CIRCUIT_BREAKER_N:
            state.paused_until = time.time() + 300
            log.warning("[CB] Circuit breaker — %d consecutive failures, pausing 300s",
                        state.consecutive_losses)
            state.consecutive_losses = 0

# ─────────────────── SETTLEMENT ENGINE ────────────────────────────
def _settle_lock(lock, winner, state, reason):
    if lock.settled: return
    lock.settled = True
    old_bal = state.my_balance
    pnl = 0.0; wins = 0; losses = 0

    for outcome, usd_cost, entry_price, token_id in lock.trades:
        shares = usd_cost / entry_price if entry_price > 0 else 0
        if outcome.upper() == winner.upper():
            pnl += shares - usd_cost; wins += 1
        else:
            pnl -= usd_cost; losses += 1

    state.my_balance  = max(state.my_balance + pnl, 0.0)
    state.daily_pnl  += pnl
    new_bal = state.my_balance

    pnl_sign = "+" if pnl >= 0 else ""
    col = colorama.Fore.GREEN if pnl >= 0 else colorama.Fore.RED
    sep = "=" * 65
    market_name, market_url = format_market_name(lock.condition_id, lock.market_slug)
    print(f"\n{colorama.Style.BRIGHT}{sep}")
    print(f"  🏁 MARKET SETTLED | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(sep)
    print(f"  Market  : {market_name[:55]}")
    print(f"  Winner  : {colorama.Fore.CYAN}{winner}{colorama.Style.RESET_ALL}")
    print(f"  Trades  : {lock.trade_count}  W:{colorama.Fore.GREEN}{wins}{colorama.Style.RESET_ALL}  L:{colorama.Fore.RED}{losses}{colorama.Style.RESET_ALL}")
    print(f"  P&L     : {col}{colorama.Style.BRIGHT}{pnl_sign}${pnl:.2f}{colorama.Style.RESET_ALL}")
    print(f"  Balance : ${old_bal:.2f} → {col}{colorama.Style.BRIGHT}${new_bal:.2f}{colorama.Style.RESET_ALL}")
    print(f"  Link    : {market_url}")
    print(f"{colorama.Style.BRIGHT}{sep}{colorama.Style.RESET_ALL}\n")
    log.info("🏁 Settled [%s] W=%s | pnl=%s%.2f | $%.2f→$%.2f",
             lock.market_slug[:20], winner, pnl_sign, pnl, old_bal, new_bal)


def check_market_resolved(condition_id, state) -> Optional[str]:
    try:
        r = requests.get(f"{CLOB_HTTP_URL}/markets/{condition_id}", timeout=5)
        if r.status_code == 200:
            d = r.json()
            if not d.get("active", True) or d.get("closed", False):
                for oc in d.get("outcomes", []):
                    if oc.get("winner", False):
                        w = oc.get("title","").upper().strip()
                        if w: return w
    except: pass
    return None

def check_price_resolution(lock) -> Optional[str]:
    for outcome, usd_cost, entry_price, token_id in lock.trades:
        if not token_id: continue
        p = get_best_ask(token_id)
        if p is None: continue
        if p >= 0.99: return outcome
        if p <= 0.01: return opposite_outcome(outcome)
    return None

async def release_locks_if_needed(state: BotState):
    now = time.time()
    to_remove = []
    for cid, lock in list(state.locks.items()):
        if lock.settled:
            to_remove.append(cid); continue
        winner  = await asyncio.to_thread(check_market_resolved, cid, state)
        if not winner:
            winner = await asyncio.to_thread(check_price_resolution, lock)
        if winner:
            _settle_lock(lock, winner, state, f"market resolved — winner: {winner}")
            to_remove.append(cid)
        elif (now - lock.last_trade_at) > GAME_IDLE_TIMEOUT:
            log.warning("⏱️  Game timed out — releasing: %s", lock.market_slug)
            lock.settled = True; to_remove.append(cid)
    for cid in to_remove:
        state.locks.pop(cid, None)

# ──────────────────────── DATA API POLLER ─────────────────────────
def parse_trade(raw: dict) -> Optional[SwisstonyTrade]:
    try:
        tx = raw.get("transactionHash") or raw.get("transaction_hash","")
        if not tx: return None
        raw_side = (raw.get("side") or raw.get("type") or raw.get("activityType") or "").upper()
        if "BUY" in raw_side or "LONG" in raw_side:
            side = TradeSide.BUY
        elif "SELL" in raw_side or "SHORT" in raw_side:
            side = TradeSide.SELL
        else:
            return None
        usd  = float(raw.get("usdcSize") or raw.get("size") or 0)
        price= float(raw.get("price") or raw.get("avgPrice") or 0)
        if usd <= 0 or price <= 0: return None
        return SwisstonyTrade(
            tx_hash      = tx,
            condition_id = raw.get("conditionId") or raw.get("condition_id",""),
            token_id     = raw.get("asset") or raw.get("tokenId") or raw.get("token_id",""),
            side         = side,
            usd_size     = usd,
            price        = price,
            timestamp    = int(raw.get("timestamp") or raw.get("createdAt") or time.time()),
            outcome      = raw.get("outcome") or raw.get("side","Yes"),
            market_slug  = raw.get("market") or raw.get("question") or raw.get("slug",""),
        )
    except Exception as e:
        log.debug("Trade parse error: %s", e)
        return None


async def poll_trades(queue: asyncio.Queue, state: BotState):
    log.info("📡 Poller started (interval=%.1fs)", POLL_INTERVAL_SEC)
    while True:
        try:
            resp = await asyncio.to_thread(
                requests.get,
                f"{DATA_API_URL}/activity",
                params={"user": TARGET_WALLET, "type": "TRADE", "limit": 50,
                        "sortBy": "TIMESTAMP", "sortDirection": "DESC"},
                timeout=8,
            )
            if resp.status_code == 200:
                data    = resp.json()
                records = data if isinstance(data, list) else data.get("data", [])
                for raw in records:
                    trade = parse_trade(raw)
                    if trade and trade.tx_hash not in state.processed_hashes:
                        await queue.put(trade)
            else:
                log.debug("Data API status %d", resp.status_code)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("Poller error: %s", e)
        await asyncio.sleep(POLL_INTERVAL_SEC)

# ─────────────────────────── MAIN LOOP ────────────────────────────
async def process_trade(trade: SwisstonyTrade, state: BotState):
    state.processed_hashes.add(trade.tx_hash)

    if trade.condition_id not in state.locks:
        state.locks[trade.condition_id] = GameLock(
            condition_id = trade.condition_id,
            market_slug  = trade.market_slug or trade.condition_id[:12],
        )
        market_name, market_url = format_market_name(trade.condition_id, trade.market_slug, trade.outcome)
        log.info(
            "🔒 Tracking game: [%s] (condition=%s...) 🔗 %s",
            market_name, trade.condition_id[:12], market_url,
        )

    lock = state.locks[trade.condition_id]

    winner = check_market_resolved(trade.condition_id, state)
    if winner:
        log.info("Market %s already resolved (%s), skipping copy", trade.condition_id[:12], winner)
        return

    # Check if market is active
    try:
        r = requests.get(f"{CLOB_HTTP_URL}/markets/{trade.condition_id}", timeout=5)
        if r.status_code == 200:
            data = r.json()
            if not data.get("active", True):
                log.info("Market %s not active yet, skipping", trade.condition_id[:12])
                return
    except Exception as e:
        log.debug("Market active check error: %s", e)

    # Get correct token_id from market
    try:
        r = requests.get(f"{CLOB_HTTP_URL}/markets/{trade.condition_id}", timeout=5)
        if r.status_code == 200:
            data = r.json()
            outcomes = data.get("outcomes", [])
            for outcome in outcomes:
                title = outcome.get("title", "").strip().upper()
                if title == trade.outcome.upper():
                    trade.token_id = outcome.get("token_id")
                    break
    except Exception as e:
        log.warning("Failed to get token_id from market: %s", e)

    used      = lock.total_copied_usd
    copy_size = calculate_copy_size(trade.usd_size, state.my_balance, state.target_balance, used)

    ok, reason = risk_ok(trade, copy_size, used, state)
    if not ok:
        log.warning("🚫 Trade blocked — %s", reason)
        return

    market_name, market_url = format_market_name(
        trade.condition_id,
        trade.market_slug or lock.market_slug,
        trade.outcome
    )
    shares_to_buy  = copy_size / trade.price if trade.price > 0 else 0
    target_shares  = trade.usd_size / trade.price if trade.price > 0 else 0
    log.info(
        "[COPY] Swisstony: %s $%.0f (%.2f shares) '%s' @ %.4f | We copy: $%.2f (%.2f shares) | %s 🔗 %s",
        trade.side.value, trade.usd_size, target_shares, trade.outcome, trade.price,
        copy_size, shares_to_buy, market_name, market_url,
    )
    await execute_copy_trade(trade, copy_size, state)


async def balance_refresh_loop(state: BotState):
    while True:
        await asyncio.sleep(60)
        bal = get_my_balance()
        if bal > 0:
            diff = bal - state.my_balance
            state.my_balance = bal
            if abs(diff) > 0.01:
                log.info("💰 Balance refreshed: $%.2f (%+.2f)", bal, diff)
        if time.time() - state.day_start > 86400:
            state.daily_pnl = 0.0
            state.day_start = time.time()
            log.info("[DAILY] PnL counter reset")


async def target_refresh_loop(state: BotState):
    while True:
        await asyncio.sleep(60)
        old = state.target_balance
        state.target_balance = get_target_balance()
        if abs(state.target_balance - old) > 0.01:
            log.info("🎯 Target balance: $%.2f → $%.2f", old, state.target_balance)


async def resolution_loop(state: BotState):
    while True:
        await asyncio.sleep(5)
        await release_locks_if_needed(state)


async def main():
    log.info("=" * 60)
    log.info("  SWISSBOT v1.3 | DRY_RUN=%s | sig_type=%d", DRY_RUN, SIGNATURE_TYPE)
    log.info("  Target: %s", TARGET_WALLET)
    log.info("=" * 60)

    if not FUNDER_ADDRESS and not DRY_RUN:
        log.warning("╔══════════════════════════════════════════════════════╗")
        log.warning("║  ⚠️  POLYMARKET_FUNDER_ADDRESS not set!              ║")
        log.warning("║  order_version_mismatch ho sakta hai.                ║")
        log.warning("║  .env me add karo:                                   ║")
        log.warning("║  POLYMARKET_FUNDER_ADDRESS=0x<your_wallet_address>   ║")
        log.warning("╚══════════════════════════════════════════════════════╝")

    global _clob
    _clob = build_clob_client()

    state = BotState()
    bal   = get_my_balance()
    if bal > 0:
        state.my_balance = bal
    else:
        state.my_balance = DEFAULT_BALANCE
        if not DRY_RUN:
            log.warning("⚠️  Live trading with default balance $%.2f — ensure funder wallet has sufficient USDC!", DEFAULT_BALANCE)
        else:
            log.warning("⚠️  On-chain balance $0 — using default $%.2f", DEFAULT_BALANCE)

    log.info("💰 Starting balance: $%.2f USDC (min keep: $%.2f)", state.my_balance, MIN_REMAINING_BALANCE)
    log.info("📐 max_per_game=$%.0f | max_trade=$%.0f | min_trade=$%.0f",
             MAX_POSITION_PER_GAME, MAX_TRADE_SIZE, MIN_TRADE_SIZE)

    queue: asyncio.Queue = asyncio.Queue()
    tasks = [
        asyncio.create_task(poll_trades(queue, state),      name="poller"),
        asyncio.create_task(balance_refresh_loop(state),    name="balance"),
        asyncio.create_task(resolution_loop(state),         name="resolution"),
        asyncio.create_task(target_refresh_loop(state),     name="target_bal"),
    ]

    log.info("🚀 All systems GO — watching swisstony...")

    try:
        while True:
            try:
                trade = await asyncio.wait_for(queue.get(), timeout=60)
                if trade.tx_hash in state.processed_hashes: continue
                if not trade.condition_id or not trade.token_id:
                    log.debug("Skipping incomplete trade"); continue
                await process_trade(trade, state)
            except asyncio.TimeoutError:
                now = datetime.now().strftime("%H:%M:%S")
                print(f"\n[HEARTBEAT {now}] Balance: ${state.my_balance:.2f} | "
                      f"PnL: {'+' if state.daily_pnl>=0 else ''}${state.daily_pnl:.2f} | "
                      f"Active: {len(state.locks)}")
    except (KeyboardInterrupt, asyncio.CancelledError):
        log.info("[SHUTDOWN] Stopping SwissBot...")
    finally:
        for t in tasks: t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        log.info("[EXIT] SwissBot stopped.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass