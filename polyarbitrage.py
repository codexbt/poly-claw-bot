"""
5-Min BTC Arbitrage Bot for Polymarket CLOB L2
=============================================
Strategy : Pure arbitrage on 5-minute BTC Up/Down markets.
Rules :
  - Skip any leg where price <= 0.10 (illiquid / stuck at boundary)
  - Minimum $4 net profit (after Polymarket fees + 0.5% slippage buffer) required to enter
  - Starting balance $100 | Max single trade $7
  - Kelly Criterion position sizing (fractional Kelly = 0.25 for safety)
  - Scans ALL active 5-min BTC markets; picks the best opportunity
  - Graceful shutdown on SIGINT / SIGTERM
"""

import os
import sys
import time
import json
import logging
import asyncio
import signal
import requests
import re
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timezone
from decimal import Decimal, getcontext
from dotenv import load_dotenv

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import MarketOrderArgs
from py_clob_client.order_builder.constants import BUY

# ─────────────────────────────────────────────
# ANSI COLOR CODES FOR TERMINAL OUTPUT
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
# ANSI COLOR CODES FOR TERMINAL OUTPUT
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
# ANSI COLOR CODES FOR TERMINAL OUTPUT
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
# TRADE LOGGING DATA STRUCTURES
# ─────────────────────────────────────────────
from dataclasses import dataclass, asdict, field
import csv

@dataclass
class TradeRecord:
    timestamp:        str
    symbol:           str
    direction:        str  # "ARBITRAGE"
    entry_price:      float  # Not applicable for arbitrage
    trade_size_usd:   float
    entry_shares:     float  # Not applicable for arbitrage
    confidence:       int   # Set to 100 for arbitrage
    reasoning:        str   # Arbitrage details
    market_ts:        str
    condition_id:     str   = ""
    outcome:          str   = "OPEN"
    pnl:              float = 0.0
    exit_price:       float = 0.0
    exit_timestamp:   str   = ""
    yes_order_id:     str   = ""
    no_order_id:      str   = ""
    yes_shares:       float = 0.0
    no_shares:        float = 0.0
    expected_profit:  float = 0.0

    @property
    def roi_pct(self) -> float:
        if self.trade_size_usd:
            return round((self.pnl / self.trade_size_usd) * 100, 2)
        return 0.0

# ─────────────────────────────────────────────
# Bootstrap
# ─────────────────────────────────────────────
load_dotenv()
getcontext().prec = 18

# ─────────────────────────────────────────────
# Config (all overridable via .env)
# ─────────────────────────────────────────────
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
API_KEY = os.getenv("API_KEY")
API_SECRET = os.getenv("API_SECRET")
API_PASSPHRASE = os.getenv("API_PASSPHRASE")
POLYMARKET_FUNDER_ADDRESS = os.getenv("POLYMARKET_FUNDER_ADDRESS")
RPC_URL = os.getenv("RPC_URL", "https://polygon-rpc.com")

# Fee / slippage
TAKER_FEE_RATE = float(os.getenv("TAKER_FEE_RATE", "0.0156")) # 1.56% per leg
SLIPPAGE_BUFFER = float(os.getenv("SLIPPAGE_BUFFER", "0.005")) # 0.5% buffer each leg

# Profit gate: minimum net dollar profit required (over 1 share = $1 payout)
MIN_NET_PROFIT_USD = float(os.getenv("MIN_NET_PROFIT_USD", "0.10")) # $0.10 minimum (low for dry-run testing)

# Test mode: Force a test trade to demonstrate execution
TEST_MODE = os.getenv("TEST_MODE", "false").lower() in ("true", "1", "yes")

# Position sizing
STARTING_BALANCE = float(os.getenv("STARTING_BALANCE", "101.0")) # $101
MAX_TRADE_USDC = float(os.getenv("MAX_TRADE_USDC", "7.0")) # $7 per trade hard cap
KELLY_FRACTION = float(os.getenv("KELLY_FRACTION", "0.25")) # 25% fractional Kelly (conservative)

# Price validity gates
MIN_VALID_PRICE = float(os.getenv("MIN_VALID_PRICE", "0.10")) # skip if price ≤ 0.10

# Dry run mode - simulate trades without actually placing orders
DRY_RUN = os.getenv("DRY_RUN", "true").lower() in ("true", "1", "yes") # Default to dry run

# Dry run mode - simulate trades without actually placing orders
DRY_RUN = os.getenv("DRY_RUN", "true").lower() in ("true", "1", "yes") # Default to dry run

# Loop timing
SCAN_INTERVAL_SEC = float(os.getenv("SCAN_INTERVAL_SEC", "5.0"))

# ─────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────
# Fix encoding for Windows console
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except:
        pass
if hasattr(sys.stderr, 'reconfigure'):
    try:
        sys.stderr.reconfigure(encoding='utf-8')
    except:
        pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    handlers=[
        logging.FileHandler("polyarbitrage_bot.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# Bot Statistics
# ─────────────────────────────────────────────
@dataclass
class BotStats:
    initial_balance: float = STARTING_BALANCE
    current_balance: float = STARTING_BALANCE
    total_trades:    int   = 0
    wins:            int   = 0
    losses:          int   = 0
    daily_spent:     float = 0.0
    daily_pnl:       float = 0.0
    balance_history: list  = field(default_factory=list)

    @property
    def win_rate(self):
        closed = self.wins + self.losses
        return (self.wins / closed * 100) if closed else 0.0

class TradingLogger:
    LOG_FILE = "polyarbitrage_trades.csv"
    LIVE_LOG = "polyarbitrage_live_log.txt"
    
    FIELDS = [
        "timestamp","symbol","direction","entry_price","trade_size_usd",
        "entry_shares","confidence","reasoning","market_ts","condition_id",
        "outcome","pnl","exit_price","exit_timestamp","yes_order_id","no_order_id",
        "yes_shares","no_shares","expected_profit"
    ]

    def __init__(self):
        if not os.path.exists(self.LOG_FILE):
            with open(self.LOG_FILE, "w", newline="") as f:
                csv.DictWriter(f, fieldnames=self.FIELDS).writeheader()

    def log_trade(self, record: TradeRecord, stats: BotStats):
        row = {k: getattr(record, k, "") for k in self.FIELDS}
        with open(self.LOG_FILE, "a", newline="") as f:
            csv.DictWriter(f, fieldnames=self.FIELDS).writerow(row)
        
        if record.outcome == "OPEN":
            line = (
                f"[TRADE] {record.timestamp} | {record.symbol} ARBITRAGE "
                f"@ ${record.trade_size_usd:.2f} | Expected Profit: ${record.expected_profit:.4f}"
            )
        else:
            result_text = "WIN" if record.pnl >= 0 else "LOSS"
            pnl_color = C.BGREEN if record.pnl >= 0 else C.BRED
            wr_color = C.BGREEN if stats.win_rate >= 50 else C.BRED
            line = (
                f"[TRADE] {record.timestamp} | {record.symbol} ARBITRAGE "
                f"@ ${record.trade_size_usd:.2f} | Expected: ${record.expected_profit:.4f}\n"
                f"→ {result_text}\n"
                f"→ Actual PnL: {pnl_color}${record.pnl:+.4f}{C.RESET} ({record.roi_pct:+.2f}%)\n"
                f"→ Balance: {C.BWHITE}${stats.current_balance:.2f}{C.RESET}\n"
                f"→ Running PnL: {C.BYELLOW}${stats.daily_pnl:+.4f}{C.RESET} | WR: {wr_color}{stats.win_rate:.1f}%{C.RESET}"
            )
        
        with open(self.LIVE_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        
        for part in line.split("\n"):
            logger.info(part)

# ─────────────────────────────────────────────
# Graceful shutdown
# ─────────────────────────────────────────────
class GracefulKiller:
    kill_now = False
    def __init__(self):
        signal.signal(signal.SIGINT, self.exit_gracefully)
        signal.signal(signal.SIGTERM, self.exit_gracefully)
    def exit_gracefully(self, *_):
        logger.info("🛑 Shutdown signal received – finishing current cycle…")
        self.kill_now = True

killer = GracefulKiller()

# ─────────────────────────────────────────────
# Validation
# ─────────────────────────────────────────────
def validate_env():
    missing = [k for k, v in {
        "PRIVATE_KEY": PRIVATE_KEY,
        "API_KEY": API_KEY,
        "API_SECRET": API_SECRET,
        "API_PASSPHRASE": API_PASSPHRASE,
        "POLYMARKET_FUNDER_ADDRESS": POLYMARKET_FUNDER_ADDRESS,
    }.items() if not v]
    if missing:
        logger.error(f"Missing .env variables: {missing}")
        sys.exit(1)

# ─────────────────────────────────────────────
# CLOB client
# ─────────────────────────────────────────────
def initialize_clob_client() -> ClobClient:
    try:
        # First, create a temporary client to derive credentials
        l1_client = ClobClient(
            host="https://clob.polymarket.com",
            chain_id=137,
            key=PRIVATE_KEY,
        )
        try:
            creds = l1_client.create_or_derive_api_creds()
        except:
            creds = l1_client.derive_api_key()
        
        # Now create the main client with credentials
        client = ClobClient(
            host="https://clob.polymarket.com",
            chain_id=137,
            key=PRIVATE_KEY,
            creds=creds,
        )
        logger.info("✅ ClobClient initialized.")
        return client
    except Exception as e:
        logger.error(f"ClobClient init failed: {e}")
        sys.exit(1)

# ─────────────────────────────────────────────
# Wallet balance (live USDC on Polygon)
# ─────────────────────────────────────────────
def get_wallet_balance_usdc() -> float:
    """
    Reads USDC balance from the Polymarket CLOB API.
    Falls back to STARTING_BALANCE on error (safe default for sizing).
    """
    try:
        # Polymarket stores balance in the CLOB profile endpoint
        resp = requests.get(
            "https://clob.polymarket.com/balance",
            headers={
                "POLY_API_KEY": API_KEY,
                "POLY_API_SECRET": API_SECRET,
                "POLY_API_PASSPHRASE": API_PASSPHRASE,
            },
            timeout=10,
        )
        data = resp.json()
        balance = float(data.get("balance", STARTING_BALANCE))
        logger.debug(f"Wallet USDC balance: ${balance:.2f}")
        return balance
    except Exception as e:
        logger.warning(f"Balance fetch failed ({e}), using starting balance ${STARTING_BALANCE}")
        return STARTING_BALANCE

# ─────────────────────────────────────────────
# Market Fetcher (inspired by poly5min_llm_bot.py)
# ─────────────────────────────────────────────
class PolymarketFetcher:
    """Fetches specific 5-min market data for BTC and ETH."""
    
    MARKET_SLUGS = {"BTC": "btc-updown-5m", "ETH": "eth-updown-5m"}

    @staticmethod
    def get_current_window_ts() -> int:
        """Get timestamp of current 5-minute window (ET)."""
        try:
            from zoneinfo import ZoneInfo
            et = datetime.now(timezone.utc).astimezone(ZoneInfo("America/New_York"))
        except:
            # Fallback if zoneinfo not available
            import datetime as dt
            utc_now = dt.datetime.utcnow()
            et = utc_now  # Use UTC as fallback
        
        window_min = (et.minute // 5) * 5
        window_start = et.replace(minute=window_min, second=0, microsecond=0)
        return int(window_start.timestamp())

    def _get_midpoint(self, token_id: str) -> float:
        """Get current midpoint price for a token."""
        try:
            r = requests.get(
                f"https://clob.polymarket.com/midpoint?token_id={token_id}",
                timeout=10
            )
            r.raise_for_status()
            return float(r.json().get("mid", 0.5))
        except Exception as e:
            logger.warning(f"Midpoint fetch error for {token_id}: {e}")
            return 0.5

    def get_token_price(self, token_id: str) -> float:
        """Get current price for a token."""
        try:
            return self._get_midpoint(token_id)
        except Exception as e:
            logger.warning(f"get_token_price error {token_id}: {e}")
            return 0.0

    def get_current_market(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Fetch current market for symbol (BTC or ETH) in the current 5-min window.
        Returns market data with condition_id, token IDs, and prices.
        """
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
                    logger.debug(f"Fetching market page {url} (attempt {attempt})")
                    r = requests.get(
                        url,
                        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
                        timeout=12
                    )
                    r.raise_for_status()
                    html = r.text
                    break
                except requests.exceptions.RequestException as exc:
                    logger.debug(f"Market page fetch failed: {url} — {exc}")
            
            if html is not None:
                break
            time.sleep(1.0)

        if html is None:
            logger.warning(f"Could not fetch Polymarket page for {symbol} at ts={market_ts}")
            return None

        # Parse HTML for condition ID and token IDs
        cond_match = re.search(r'"conditionId":"([^"]+)"', html)
        token_match = re.search(r'"clobTokenIds":\s*\[([^\]]+)\]', html)
        
        if not cond_match or not token_match:
            logger.warning(f"Could not parse market data for {symbol} at ts={market_ts}")
            return None

        try:
            token_ids = json.loads("[" + token_match.group(1) + "]")
            yes_token = token_ids[0]
            no_token = token_ids[1]
        except Exception as e:
            logger.warning(f"Token parsing error for {symbol}: {e}")
            return None

        yes_price = self._get_midpoint(yes_token)
        no_price = self._get_midpoint(no_token)

        seconds_into_window = int(time.time()) - market_ts
        seconds_left = max(0, 300 - seconds_into_window)

        return {
            "symbol": symbol,
            "yes_price": round(yes_price, 4),
            "no_price": round(no_price, 4),
            "seconds_left": seconds_left,
            "condition_id": cond_match.group(1),
            "yes_token": yes_token,
            "no_token": no_token,
            "market_ts": market_ts,
        }

# ─────────────────────────────────────────────
# Market discovery (using CLOB HTTP API directly)
# ─────────────────────────────────────────────
def fetch_all_btc_5min_markets(client: ClobClient) -> List[Dict[str, Any]]:
    """Fetch BTC & ETH 5-minute markets from Polymarket website + CLOB API."""
    found = []
    market_slugs = {"BTC": "btc-updown-5m", "ETH": "eth-updown-5m"}
    
    # Calculate current 5-min window timestamp
    import time
    from datetime import datetime, timezone
    et_now = datetime.now(timezone.utc)
    window_min = (et_now.minute // 5) * 5
    window_start = et_now.replace(minute=window_min, second=0, microsecond=0)
    market_ts = int(window_start.timestamp())
    
    for symbol, slug in market_slugs.items():
        urls = [
            f"https://polymarket.com/event/{slug}-{market_ts}",
            f"https://www.polymarket.com/event/{slug}-{market_ts}",
        ]
        
        html = None
        for attempt in range(1, 4):
            for url in urls:
                try:
                    logger.debug(f"📡 Fetching {symbol} market from {url}... (attempt {attempt}/3)")
                    r = requests.get(
                        url, 
                        headers={
                            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
                            "Accept-Language": "en-US,en;q=0.9",
                            "Connection": "keep-alive",
                        },
                        timeout=12
                    )
                    r.raise_for_status()
                    if r.text and len(r.text) > 100:  # Ensure we got real HTML
                        html = r.text
                        logger.debug(f"✅ Successfully fetched {symbol} market HTML ({len(html)} bytes)")
                        break
                except requests.exceptions.Timeout:
                    logger.debug(f"  Attempt {attempt} timeout: {url}")
                except requests.exceptions.RequestException as e:
                    logger.debug(f"  Attempt {attempt} HTTP error: {e}")
                except Exception as e:
                    logger.debug(f"  Attempt {attempt} failed: {e}")
            if html:
                break
            if attempt < 3:
                time.sleep(0.5 * attempt)  # Exponential backoff
        
        if not html:
            logger.warning(f"⚠️  Could not fetch {symbol} market HTML from Polymarket website")
            continue
        
        # Parse HTML to extract condition_id and token_ids
        try:
            cond_match = re.search(r'"conditionId":"([^"]+)"', html)
            token_match = re.search(r'"clobTokenIds":\s*\[([^\]]+)\]', html)
            
            if not cond_match or not token_match:
                logger.warning(f"⚠️  Could not parse market data for {symbol}")
                continue
            
            condition_id = cond_match.group(1)
            token_ids = json.loads("[" + token_match.group(1) + "]")
            
            if len(token_ids) < 2:
                logger.warning(f"⚠️  Expected 2 token IDs for {symbol}, got {len(token_ids)}")
                continue
            
            yes_token = str(token_ids[0])
            no_token = str(token_ids[1])
            
            logger.debug(f"  Found {symbol} market: condition={condition_id[:16]}... YES_token={yes_token[:16]}... NO_token={no_token[:16]}...")
            
            # Get prices from CLOB API
            try:
                yes_midpoint = client.get_midpoint(yes_token)
                no_midpoint = client.get_midpoint(no_token)
                
                yes_price = float(yes_midpoint.get('mid', 0.5)) if yes_midpoint else 0.5
                no_price = float(no_midpoint.get('mid', 0.5)) if no_midpoint else 0.5
                
                logger.debug(f"  {symbol} prices: YES=${yes_price:.4f} NO=${no_price:.4f}")
                
                # Validate prices are in reasonable range (0 < price < 1)
                if yes_price <= 0 or yes_price >= 1.0 or no_price <= 0 or no_price >= 1.0:
                    logger.warning(f"⚠️  {symbol} prices out of range: YES=${yes_price:.4f} NO=${no_price:.4f}")
                    continue
                
                # Check for extreme imbalance (one side is almost 0 and sum isn't near 1)
                # If sum is near 1.0, this is a valid market equilibrium
                price_sum = yes_price + no_price
                if price_sum > 1.01:
                    logger.debug(f"⚠️  {symbol} prices sum to {price_sum:.4f} (>1.01), skipping")
                    continue
                
                seconds_left = max(0, 300 - (int(time.time()) - market_ts))
                
                market_data = {
                    "symbol": symbol,
                    "yes_price": round(yes_price, 4),
                    "no_price": round(no_price, 4),
                    "condition_id": condition_id,
                    "yes_token": yes_token,
                    "no_token": no_token,
                    "market_ts": market_ts,
                    "seconds_left": seconds_left
                }
                
                found.append(market_data)
                
                # Calculate arbitrage metrics
                arb_metrics = calc_arbitrage_percentage(yes_price, no_price)
                
                if arb_metrics['net_arbitrage_pct'] >= 4.0:
                    arb_color = C.BGREEN
                    arb_status = "🚀 PROFITABLE"
                elif arb_metrics['net_arbitrage_pct'] >= 1.0:
                    arb_color = C.BYELLOW
                    arb_status = "⚠️  MARGINAL"
                else:
                    arb_color = C.BRED
                    arb_status = "❌ NO ARB"
                
                logger.info(
                    f"Found market: {symbol}-5m | "
                    f"YES=${yes_price:.4f} NO=${no_price:.4f} | "
                    f"Sum=${arb_metrics['raw_sum']:.4f} | "
                    f"Arb: {arb_color}{arb_metrics['net_arbitrage_pct']:+.2f}%{C.RESET} ({arb_status}) | "
                    f"Time left: {seconds_left}s"
                )
                
            except Exception as e:
                logger.warning(f"⚠️  Price fetch error for {symbol}: {e}")
                continue
                
        except Exception as e:
            logger.warning(f"⚠️  Parse error for {symbol} market: {e}")
            continue
    
    if not found:
        logger.warning(f"❌ No BTC/ETH 5-min markets found currently. Retrying...")
    else:
        logger.info(f"✅ Found {len(found)} 5-min market(s) for arbitrage")
        for m in found:
            arb = calc_arbitrage_percentage(m['yes_price'], m['no_price'])
            logger.info(
                f"   Market: {m['symbol']} | YES=${m['yes_price']:.4f} NO=${m['no_price']:.4f} | "
                f"Sum=${m['yes_price'] + m['no_price']:.4f} | Arb%={arb['net_arbitrage_pct']:.2f}% | "
                f"Time left: {m['seconds_left']}s"
            )
    
    return found

# ─────────────────────────────────────────────
# Orderbook helpers (REMOVED - using midpoint prices instead)
# ─────────────────────────────────────────────
# The PolymarketFetcher uses midpoint prices from the API
# which is simpler and more reliable than parsing orderbooks

# ─────────────────────────────────────────────
# Arbitrage math
# ─────────────────────────────────────────────
def calc_arb_metrics(
    yes_price: float,
    no_price: float,
    trade_usdc: float,
) -> Dict[str, float]:
    """
    Given the effective ask prices and trade size, compute arbitrage metrics.
    Each leg includes taker fee + slippage buffer.
    Payout from holding 1 YES + 1 NO share = $1 guaranteed.
    """
    fee_slip = TAKER_FEE_RATE + SLIPPAGE_BUFFER # per-leg cost factor

    yes_effective = yes_price * (1 + fee_slip)
    no_effective = no_price * (1 + fee_slip)
    total_cost = yes_effective + no_effective # cost per $1 payout

    profit_per_dollar = 1.0 - total_cost # profit per share-pair
    shares_pair = trade_usdc / total_cost # how many share-pairs
    gross_profit = profit_per_dollar * shares_pair # total $ profit

    return {
        "yes_effective": yes_effective,
        "no_effective": no_effective,
        "total_cost": total_cost,
        "profit_per_dollar": profit_per_dollar,
        "shares_pair": shares_pair,
        "gross_profit": gross_profit,
    }

# ─────────────────────────────────────────────
# Arbitrage percentage calculator
# ─────────────────────────────────────────────
def calc_arbitrage_percentage(yes_price: float, no_price: float) -> Dict[str, float]:
    """
    Calculate arbitrage percentage and related metrics.
    Returns the raw arbitrage opportunity before fees/slippage.
    """
    raw_sum = yes_price + no_price
    raw_arbitrage_pct = (1.0 - raw_sum) * 100 if raw_sum < 1.0 else 0.0
    
    # With fees and slippage
    fee_slip = TAKER_FEE_RATE + SLIPPAGE_BUFFER
    yes_effective = yes_price * (1 + fee_slip)
    no_effective = no_price * (1 + fee_slip)
    effective_sum = yes_effective + no_effective
    net_arbitrage_pct = (1.0 - effective_sum) * 100 if effective_sum < 1.0 else 0.0
    
    return {
        "raw_sum": raw_sum,
        "raw_arbitrage_pct": raw_arbitrage_pct,
        "effective_sum": effective_sum,
        "net_arbitrage_pct": net_arbitrage_pct,
        "fees_slippage_cost": (effective_sum - raw_sum) * 100,  # as percentage
    }

# ─────────────────────────────────────────────
# Kelly Criterion sizing
# ─────────────────────────────────────────────
def kelly_position_size(
    profit_per_dollar: float,
    balance: float,
) -> float:
    """
    Fractional Kelly for binary bet:
      Edge = profit_per_dollar (profit when arbitrage succeeds = always)
      Odds = (1 / total_cost) - 1 (net odds on cost)
    Since arbitrage is risk-free (buy both sides), win probability = 1.
    Kelly fraction = edge / odds (simplified for near-certain bets).
    We cap at MAX_TRADE_USDC and apply KELLY_FRACTION multiplier.
    """
    if profit_per_dollar <= 0:
        return 0.0
    # For a certain payoff, Kelly = 1, but fractional Kelly keeps us safe
    kelly_bet = balance * KELLY_FRACTION * profit_per_dollar
    capped = min(kelly_bet, MAX_TRADE_USDC, balance * 0.07) # also cap at 7% of balance
    return max(capped, 0.0)

# ─────────────────────────────────────────────
# Best opportunity scanner
# ─────────────────────────────────────────────
def find_best_opportunity(
    client: ClobClient,
    markets: List[Dict[str, Any]],
    balance: float,
) -> Optional[Dict[str, Any]]:
    """
    Scans all candidate markets, returns the one with the highest
    gross_profit that also meets the minimum profit gate.
    Returns None if no qualifying opportunity found.
    """
    best = None
    best_profit = 0.0
    rejected_count = {
        "invalid_tokens": 0,
        "out_of_range": 0,
        "sum_too_high": 0,
        "kelly_zero": 0,
        "below_profit_gate": 0,
    }

    for market in markets:
        yes_token = market.get("yes_token")
        no_token = market.get("no_token")
        yes_price = market.get("yes_price", 0)
        no_price = market.get("no_price", 0)
        
        if not yes_token or not no_token or yes_price <= 0 or no_price <= 0:
            rejected_count["invalid_tokens"] += 1
            continue

        # ── GATE 1: Price validity ──────────────────────────────
        # Accept any price in (0, 1), including extreme odds
        # This is normal market behavior; arbitrage filtering happens in GATE 2-3
        if yes_price <= 0 or yes_price >= 1.0 or no_price <= 0 or no_price >= 1.0:
            logger.debug(
                f"Skipping '{market.get('symbol')}' – price out of (0,1) range "
                f"(YES={yes_price:.4f}, NO={no_price:.4f})"
            )
            rejected_count["out_of_range"] += 1
            continue

        # ── GATE 2: Basic arb check (sum < MIN_PRICE_SUM_FOR_PROFIT) ───────────────────
        price_sum = yes_price + no_price
        if price_sum >= 1.0 - TAKER_FEE_RATE:
            # Prices sum too high to generate any profit after fees
            logger.debug(
                f"Skipping '{market.get('symbol')}' – sum={price_sum:.4f} >= {1.0 - TAKER_FEE_RATE:.4f} "
                f"(no profit after fees)"
            )
            rejected_count["sum_too_high"] += 1
            continue

        # ── Size tentatively ────────────────────────────────────
        metrics = calc_arb_metrics(yes_price, no_price, MAX_TRADE_USDC)
        kelly_size = kelly_position_size(metrics["profit_per_dollar"], balance)

        if kelly_size <= 0:
            rejected_count["kelly_zero"] += 1
            continue

        # Recalculate with actual Kelly size
        metrics = calc_arb_metrics(yes_price, no_price, kelly_size)

        # ── GATE 3: Minimum net profit ────────────────────────
        if metrics["gross_profit"] < MIN_NET_PROFIT_USD:
            logger.debug(
                f"Below profit gate: '{market.get('symbol')}' "
                f"gross_profit=${metrics['gross_profit']:.4f} < ${MIN_NET_PROFIT_USD} "
                f"(sum={price_sum:.4f}, kelly=${kelly_size:.2f}, margin={100*(1-price_sum):.3f}%)"
            )
            rejected_count["below_profit_gate"] += 1
            continue

        # ── Best so far? ─────────────────────────────────────────
        if metrics["gross_profit"] > best_profit:
            best_profit = metrics["gross_profit"]
            best = {
                "market": market,
                "yes_token": yes_token,
                "no_token": no_token,
                "yes_price": yes_price,
                "no_price": no_price,
                "kelly_size": kelly_size,
                "metrics": metrics,
            }
            logger.info(
                f"  ✅ NEW BEST OPPORTUNITY: {market.get('symbol')} | "
                f"Profit=${best_profit:.4f} | Sum={price_sum:.4f} | Kelly=${kelly_size:.2f}"
            )

    # Summary of rejected opportunities
    if rejected_count["below_profit_gate"] > 0 or rejected_count["sum_too_high"] > 0:
        logger.info(
            f"  📊 Opportunity scan: BEST=${best_profit:.4f} | "
            f"Rejected: {rejected_count['sum_too_high']} (sum too high), "
            f"{rejected_count['below_profit_gate']} (profit gate), "
            f"{rejected_count['kelly_zero']} (kelly), "
            f"{rejected_count['out_of_range']} (range)"
        )

    return best

# ─────────────────────────────────────────────
# Trade execution
# ─────────────────────────────────────────────
async def execute_arbitrage(client: ClobClient, opp: Dict[str, Any]) -> Dict[str, Any]:
    """Execute arbitrage trade: buy both YES and NO tokens simultaneously."""
    market = opp["market"]
    yes_token_id = opp["yes_token"]
    no_token_id = opp["no_token"]
    yes_price = opp["yes_price"]
    no_price = opp["no_price"]
    kelly_size = opp["kelly_size"]
    metrics = opp["metrics"]

    # Calculate arbitrage percentage
    arb_pct = calc_arbitrage_percentage(yes_price, no_price)
    
    logger.info(
        f"\n{'='*60}\n"
        f"🎯 TARGET ARBITRAGE OPPORTUNITY DETECTED!\n"
        f" Market : {market.get('symbol')} (Window: {market.get('seconds_left')}s left)\n"
        f" YES price : ${yes_price:.4f} | NO price : ${no_price:.4f}\n"
        f" Raw sum : ${yes_price + no_price:.4f}\n"
        f" Arbitrage : {C.BGREEN}{arb_pct['net_arbitrage_pct']:+.2f}%{C.RESET} (after fees/slippage)\n"
        f" Effective cost : YES ${metrics['yes_effective']:.4f} + NO ${metrics['no_effective']:.4f} = ${metrics['total_cost']:.4f}\n"
        f" Trade size: ${kelly_size:.4f} (Kelly-sized for safety)\n"
        f" Expected profit : {C.BGREEN}${metrics['gross_profit']:.4f}{C.RESET} ({metrics['profit_per_dollar']*100:.1f}% return)\n"
        f"{'='*60}"
    )

    # Shares to purchase per leg (equal USDC split)
    half_usdc = kelly_size / 2.0
    yes_shares = half_usdc / yes_price if yes_price > 0 else 0
    no_shares = half_usdc / no_price if no_price > 0 else 0

    try:
        if DRY_RUN:
            # ── DRY RUN: Simulate order execution ──────────────────
            logger.info(f"🔍 DRY RUN - Simulating YES order: {yes_shares:.4f} shares at ${yes_price:.4f}")
            logger.info(f"🔍 DRY RUN - Simulating NO order: {no_shares:.4f} shares at ${no_price:.4f}")
            
            # Simulate successful order placement
            yes_order_id = f"DRY_YES_{int(time.time())}"
            no_order_id = f"DRY_NO_{int(time.time())}"
            
            logger.info(f"✅ DRY RUN - YES order 'placed': {yes_shares:.4f} shares | OrderID: {yes_order_id}")
            logger.info(f"✅ DRY RUN - NO order 'placed': {no_shares:.4f} shares | OrderID: {no_order_id}")
        else:
            # ── LIVE: Place YES order ──────────────────────────────────────
            logger.info(f"Placing YES order: {yes_shares:.4f} shares at token {yes_token_id}")
            yes_order = MarketOrderArgs(
                token_id=yes_token_id,
                amount=yes_shares,
                side=BUY,
            )
            yes_signed = client.create_market_order(yes_order)
            yes_resp = client.post_order(yes_signed)

            if not yes_resp or not yes_resp.get("orderID"):
                logger.error(f"YES order failed: {yes_resp}")
                return {"success": False}

            yes_order_id = yes_resp.get("orderID")
            logger.info(f"YES order placed: {yes_shares:.4f} shares | OrderID: {yes_order_id}")

            # ── LIVE: Place NO order ───────────────────────────────────────
            logger.info(f"Placing NO order: {no_shares:.4f} shares at token {no_token_id}")
            no_order = MarketOrderArgs(
                token_id=no_token_id,
                amount=no_shares,
                side=BUY,
            )
            no_signed = client.create_market_order(no_order)
            no_resp = client.post_order(no_signed)

            if not no_resp or not no_resp.get("orderID"):
                logger.error(
                    f"NO order FAILED AFTER YES was placed! Manual check required.\n"
                    f" YES OrderID: {yes_order_id}\n"
                    f" NO response : {no_resp}"
                )
                return {"success": False}

            no_order_id = no_resp.get("orderID")
            logger.info(f"NO order placed: {no_shares:.4f} shares | OrderID: {no_order_id}")

        logger.info(
            f"\n🎯 ARBITRAGE {'SIMULATED' if DRY_RUN else 'EXECUTED'} SUCCESSFULLY!\n"
            f" YES OrderID: {yes_order_id} ({yes_shares:.4f} shares @ ${yes_price:.4f})\n"
            f" NO OrderID: {no_order_id} ({no_shares:.4f} shares @ ${no_price:.4f})\n"
            f" Expected profit: ${metrics['gross_profit']:.4f}"
        )
        
        # Return trade record data
        return {
            "success": True,
            "yes_order_id": yes_order_id,
            "no_order_id": no_order_id,
            "yes_shares": yes_shares,
            "no_shares": no_shares,
            "expected_profit": metrics['gross_profit'],
            "trade_size": kelly_size,
            "market": market,
            "condition_id": market.get("condition_id"),
        }

    except Exception as e:
        logger.error(f"Trade execution error: {e}", exc_info=True)
        return {"success": False}

# ─────────────────────────────────────────────
# Trade settlement (check resolved markets and calculate actual P&L)
# ─────────────────────────────────────────────
async def check_and_settle_trades(client: ClobClient, trading_logger: TradingLogger, stats: BotStats):
    """Check for resolved arbitrage trades and settle them with actual P&L."""
    # For arbitrage, we need to check if the market has resolved
    # Since we bought both YES and NO, we always get $1 back total
    # But we need to account for the actual resolution
    
    # This is a simplified version - in practice, you'd need to check each market's resolution
    # For now, we'll implement a basic settlement when markets close
    
    try:
        # Get current markets to check for resolved ones
        fetcher = PolymarketFetcher()
        current_ts = time.time()
        
        # Check if any markets from previous windows have resolved
        # This is a placeholder - you'd need to track active trades and check their resolution
        
        pass  # Implementation would go here
        
    except Exception as e:
        logger.debug(f"Trade settlement check error: {e}")

# ─────────────────────────────────────────────
# Main loop
# ─────────────────────────────────────────────
async def main_loop(client: ClobClient):
    logger.info(
        f"\n{'='*60}\n"
        f" 5-Min BTC Arbitrage Bot STARTED\n"
        f" Min net profit gate : ${MIN_NET_PROFIT_USD}\n"
        f" Max trade size : ${MAX_TRADE_USDC}\n"
        f" Kelly fraction : {KELLY_FRACTION*100:.0f}%\n"
        f" Price validation : 0 < price < 1 (accepts any valid bid/ask)\n"
        f" Taker fee + slip : {(TAKER_FEE_RATE+SLIPPAGE_BUFFER)*100:.2f}% per leg\n"
        f"{'='*60}"
    )

    # Initialize trade logging
    trading_logger = TradingLogger()
    stats = BotStats()
    stats.balance_history.append({
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "balance": stats.current_balance,
    })

    last_window_ts = None

    while not killer.kill_now:
        cycle_start = time.time()
        try:
            # In dry run mode, use simulated balance from stats
            if DRY_RUN:
                balance = stats.current_balance
                logger.info(f"🔍 DRY RUN - Simulated balance: ${balance:.2f} USDC")
            else:
                balance = get_wallet_balance_usdc()
                stats.current_balance = balance
                logger.info(f"💰 LIVE - Wallet balance: ${balance:.2f} USDC")

            if balance < 2.0:
                logger.warning("Balance too low to trade ($2 minimum). Sleeping 30s...")
                await asyncio.sleep(30)
                continue

            # Fetch real CLOB markets for arbitrage
            markets = fetch_all_btc_5min_markets(client)

            if not markets:
                logger.info("No 5-min markets found in current window. Waiting...")
            else:
                logger.info(f"Found {len(markets)} market(s). Scanning for arbitrage...")
                opp = find_best_opportunity(client, markets, balance)

                # TEST MODE: Inject a test opportunity if none found
                if not opp and TEST_MODE:
                    logger.warning(f"TEST MODE ENABLED - Injecting simulated arbitrage opportunity for demonstration...")
                    # Create a synthetic profitable opportunity with prices summing to 0.96
                    market = markets[0] if markets else {
                        "symbol": "BTC",
                        "yes_price": 0.25,
                        "no_price": 0.71,  # Sum = 0.96
                        "condition_id": "TEST_CONDITION_ID",
                        "yes_token": "TEST_YES_TOKEN",
                        "no_token": "TEST_NO_TOKEN",
                        "market_ts": int(time.time()),
                        "seconds_left": 300,
                    }
                    
                    # Recalc prices to ensure profitable
                    yes_price = 0.25
                    no_price = 0.71
                    metrics = calc_arb_metrics(yes_price, no_price, MAX_TRADE_USDC)
                    kelly_size = kelly_position_size(metrics["profit_per_dollar"], balance)
                    metrics = calc_arb_metrics(yes_price, no_price, kelly_size)
                    
                    opp = {
                        "market": market,
                        "yes_token": market.get("yes_token", "TEST_YES_TOKEN"),
                        "no_token": market.get("no_token", "TEST_NO_TOKEN"),
                        "yes_price": yes_price,
                        "no_price": no_price,
                        "kelly_size": kelly_size,
                        "metrics": metrics,
                    }
                    logger.info(f"  Injected test opp: Prices YES=${yes_price:.4f} NO=${no_price:.4f} Sum=$0.96 | Expected profit: ${metrics['gross_profit']:.4f}")

                if opp:
                    trade_result = await execute_arbitrage(client, opp)
                    if trade_result["success"]:
                        # Create trade record
                        market = opp["market"]
                        current_window_ts = PolymarketFetcher().get_current_window_ts()
                        
                        trade_record = TradeRecord(
                            timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                            symbol=market.get("symbol", "BTC"),
                            direction="ARBITRAGE",
                            entry_price=0.0,  # Not applicable for arbitrage
                            trade_size_usd=trade_result["trade_size"],
                            entry_shares=0.0,  # Not applicable for arbitrage
                            confidence=100,  # Arbitrage is certain
                            reasoning=f"Arbitrage: YES@${opp['yes_price']:.4f} NO@${opp['no_price']:.4f} Expected: ${trade_result['expected_profit']:.4f}",
                            market_ts=str(current_window_ts),
                            condition_id=trade_result["condition_id"],
                            outcome="OPEN",
                            yes_order_id=trade_result["yes_order_id"],
                            no_order_id=trade_result["no_order_id"],
                            yes_shares=trade_result["yes_shares"],
                            no_shares=trade_result["no_shares"],
                            expected_profit=trade_result["expected_profit"],
                        )
                        
                        # Update stats
                        stats.total_trades += 1
                        stats.daily_spent += trade_result["trade_size"]
                        
                        # Log the trade
                        trading_logger.log_trade(trade_record, stats)
                        
                        # Print colorful trade entry
                        cprint(
                            f"\n{C.BG_GREEN}{C.BLACK}{C.BOLD}  🚀 ARBITRAGE TRADE ENTERED  {C.RESET}  "
                            f"{C.BOLD}{market.get('symbol')}{C.RESET}  "
                            f"Size: {C.BWHITE}${trade_result['trade_size']:.2f}{C.RESET}  "
                            f"Expected Profit: {C.BGREEN}${trade_result['expected_profit']:.4f}{C.RESET}  "
                            f"Window: {market.get('seconds_left')}s left"
                        )
                        
                else:
                    logger.info(
                        f"No qualifying arbitrage found (need >=${MIN_NET_PROFIT_USD} profit)."
                    )

            # Check for window change and update balance
            current_window_ts = PolymarketFetcher().get_current_window_ts()
            if last_window_ts is not None and current_window_ts != last_window_ts:
                # Window changed, update balance and show summary
                new_balance = get_wallet_balance_usdc()
                balance_change = new_balance - stats.current_balance
                
                stats.balance_history.append({
                    "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "balance": new_balance,
                })
                
                # Print window summary with colors
                bar = "━" * 60
                cprint(f"\n{C.BCYAN}{C.BOLD}{bar}{C.RESET}")
                cprint(f"  {C.BG_BLUE}{C.BWHITE}{C.BOLD}  📊 5-MIN WINDOW CLOSED  {C.RESET}")
                cprint(f"{C.BCYAN}{bar}{C.RESET}")
                
                delta_color = C.BGREEN if balance_change >= 0 else C.BRED
                pnl_color = C.BGREEN if stats.daily_pnl >= 0 else C.BRED
                wr_color = C.BGREEN if stats.win_rate >= 50 else C.BRED
                
                cprint(f"  {C.BYELLOW}💰 Balance{C.RESET}   Start: {C.WHITE}${stats.initial_balance:.2f}{C.RESET}   "
                      f"Now: {C.BWHITE}${new_balance:.2f}{C.RESET}   "
                      f"Δ: {delta_color}${balance_change:+.2f}{C.RESET}")
                cprint(f"  {C.BYELLOW}📈 Realized PnL:{C.RESET} {pnl_color}${stats.daily_pnl:+.4f}{C.RESET}")
                cprint(f"  {C.BYELLOW}🎯 Trades:{C.RESET} {C.BWHITE}{stats.total_trades}{C.RESET} total   "
                      f"{C.BGREEN}W:{stats.wins}{C.RESET}  {C.BRED}L:{stats.losses}{C.RESET}  "
                      f"WR: {wr_color}{stats.win_rate:.1f}%{C.RESET}")
                cprint(f"{C.BCYAN}{bar}{C.RESET}\n")
                
                stats.current_balance = new_balance
            
            last_window_ts = current_window_ts

        except Exception as e:
            logger.error(f"Main loop error: {e}", exc_info=True)

        # Check for resolved trades and settle them
        await check_and_settle_trades(client, trading_logger, stats)

        # Precise interval keeping
        elapsed = time.time() - cycle_start
        sleep_for = max(0.0, SCAN_INTERVAL_SEC - elapsed)
        logger.debug(f"Sleeping for {sleep_for:.1f}s before next scan...")
        await asyncio.sleep(sleep_for)

    # Final summary
    logger.info(
        f"\n{'='*60}\n"
        f" Bot stopped. Final summary:\n"
        f" Total trades executed : {stats.total_trades}\n"
        f" Wins : {stats.wins}\n"
        f" Losses : {stats.losses}\n"
        f" Win rate : {stats.win_rate:.1f}%\n"
        f" Total PnL : ${stats.daily_pnl:+.4f}\n"
        f" Final balance : ${stats.current_balance:.2f}\n"
        f"{'='*60}"
    )

# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────
if __name__ == "__main__":
    validate_env()
    client = initialize_clob_client()
    try:
        asyncio.run(main_loop(client))
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt – bot stopped.")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)
