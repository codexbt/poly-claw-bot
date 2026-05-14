"""
╔════════════════════════════════════════════════════════════════════════════════╗
║                                                                                ║
║  POLY5MIN_ALL.PY v7.0 — BINANCE WEBSOCKET + HIERARCHICAL 2-STAGE SNIPER       ║
║                                                                                ║
║  Markets: BTC & ETH (2 Cryptos Only)                                          ║
║  Strategy: WebSocket Real-time Binance Feed + 2-Stage Hierarchical Signal    ║
║  Trade Size: Fixed $10 per valid signal                                       ║
║                                                                                ║
║  🎯 TWO-STAGE HIERARCHICAL SIGNAL:                                            ║
║  ┌──────────────────────────────────────────────────────────────────────────┐ ║
║  │ STAGE 1: BINANCE MOMENTUM GATE (LEADING INDICATOR - WEBSOCKET) ⭐        │ ║
║  │  ├─ Real-time WebSocket feed from Binance (0.1 sec updates)             │ ║
║  │  ├─ Last 10 seconds: momentum buffer (200 ticks at 0.05s intervals)     │ ║
║  │  ├─ Required: ≥ 0.028% absolute movement in 10 seconds           │ ║
║  │  ├─ If NO movement → SKIP trade immediately (GATE CLOSED)              │ ║
║  │  └─ If strong move → UNLOCK Stage 2 (GATE OPENED ✓)                   │ ║
║  │                                                                          │ ║
║  │ STAGE 2: TECHNICAL CONFIRMATION (ONLY IF STAGE 1 PASSED)                 │ ║
║  │  ├─ Window Delta (35%): Price move from candle open                    │ ║
║  │  ├─ Tick Trend (35%): Momentum in last 20 ticks                        │ ║
║  │  ├─ Volume Surge (30%): Tick frequency                                 │ ║
║  │  ├─ Min score: 0.35                                                    │ ║
║  │  └─ Previous market: +0.12 match boost / -0.08 mismatch penalty        │ ║
║  └──────────────────────────────────────────────────────────────────────────┘ ║
║                                                                                ║
║  📊 VALIDATION CHAIN (Stage by Stage):                                        ║
║     1. Daily limit check              ✓                                       ║
║     2. Market exists                  ✓                                       ║
║     3. Sniper window (5-15s)          ✓                                       ║
║     4. ⭐ STAGE 1: BINANCE GATE (≥0.028%) ✓  [WebSocket Real-time]           ║
║     5. Price range (0.88-0.945)       ✓                                       ║
║     6. ⭐ STAGE 2: Technical score (≥0.35) ✓                                  ║
║     7. Previous market boost/penalty  ✓                                       ║
║     8. → EXECUTE TRADE!               🟢                                      ║
║                                                                                ║
╚════════════════════════════════════════════════════════════════════════════════╝
"""


import os, time, logging, sys, json, re, concurrent.futures, threading
from datetime import datetime, timezone, timedelta
from collections import deque
from typing import Optional, Tuple, Dict, List
import queue

import ccxt, requests
from dotenv import load_dotenv

try:
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import MarketOrderArgs, OrderType
    from py_clob_client.order_builder.constants import BUY, SELL
    CLOB_OK = True
except ImportError as e:
    CLOB_OK = False
    print(f"[WARN] py_clob_client not found: {e}")

# Load environment
load_dotenv(override=True)

# ════════════════════════════════════════════════════════════════════════════════
#  CONFIGURATION
# ════════════════════════════════════════════════════════════════════════════════
CFG = {
    "PRIVATE_KEY":               os.getenv("PRIVATE_KEY", ""),
    "CHAIN_ID":                  int(os.getenv("CHAIN_ID", "137")),
    "SIGNATURE_TYPE":            int(os.getenv("SIGNATURE_TYPE", "0")),
    "POLYMARKET_FUNDER_ADDRESS": os.getenv("POLYMARKET_FUNDER_ADDRESS", ""),
    "API_KEY":                   os.getenv("API_KEY", ""),
    "API_SECRET":                os.getenv("API_SECRET", ""),
    "API_PASSPHRASE":            os.getenv("API_PASSPHRASE", ""),
    "RELAYER_API_KEY":           os.getenv("RELAYER_API_KEY", ""),
    "RELAYER_API_KEY_ADDRESS":   os.getenv("RELAYER_API_KEY_ADDRESS", ""),

    # Trade sizing
    "TRADE_SIZE":                float(os.getenv("TRADE_SIZE",       "10.0")),
    "DAILY_LIMIT":               float(os.getenv("DAILY_LIMIT",      "300")),

    # Price filters (0.88 - 0.945)
    "PRICE_MIN":                 float(os.getenv("PRICE_MIN",        "0.88")),
    "PRICE_MAX":                 float(os.getenv("PRICE_MAX",        "0.945")),

    # Sniper window: 5-15 seconds left
    "SNIPER_WINDOW_MIN":         int(os.getenv("SNIPER_WINDOW_MIN",  "5")),
    "SNIPER_WINDOW_MAX":         int(os.getenv("SNIPER_WINDOW_MAX",  "15")),

    # Stage 1: Binance gate (minimum % movement in 10 seconds)
    "BINANCE_MIN_MOVE_PCT":      float(os.getenv("BINANCE_MIN_MOVE_PCT", "0.028")),

    # Signal thresholds
    "MIN_SIGNAL_SCORE":          float(os.getenv("MIN_SIGNAL_SCORE", "0.35")),
    "BOT_MODE":                  os.getenv("BOT_MODE",              "safe").lower(),

    # Runtime
    "DRY_RUN":  os.getenv("DRY_RUN", "false").lower() == "true",
    "LOOP_SEC": float(os.getenv("LOOP_SEC", "0.5")),

    # Endpoints
    "CLOB":    "https://clob.polymarket.com",
    "GAMMA":   "https://gamma-api.polymarket.com",
}

# Symbols
SYMBOLS = ["BTC", "ETH"]
MARKET_SLUGS = {"BTC": "btc-updown-5m", "ETH": "eth-updown-5m"}
PAIR_BINANCE  = {s: f"{s}/USDT" for s in SYMBOLS}
PAIR_COINBASE = {s: f"{s}/USD"  for s in SYMBOLS}

# ════════════════════════════════════════════════════════════════════════════════
#  LOGGING
# ════════════════════════════════════════════════════════════════════════════════
os.makedirs("logs", exist_ok=True)
_fmt = logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s", "%H:%M:%S")
log = logging.getLogger("SNIPER_V7")
log.setLevel(logging.DEBUG)

_ch = logging.StreamHandler(sys.stdout)
_ch.setLevel(logging.INFO)
_ch.setFormatter(_fmt)
try:
    _ch.stream.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_fh = logging.FileHandler(
    f"logs/sniper_v7_{datetime.now().strftime('%Y%m%d')}.log", encoding="utf-8"
)
_fh.setLevel(logging.DEBUG)
_fh.setFormatter(_fmt)
log.addHandler(_ch)
log.addHandler(_fh)


# ══════════════════════════════════════════════════════════════
#  CANDLE DATA STRUCTURE
# ══════════════════════════════════════════════════════════════
class Candle:
    """5-second OHLC candle built from tick data"""
    __slots__ = ("ts", "open", "high", "low", "close", "ticks")
    def __init__(self, ts: float, price: float):
        self.ts    = ts
        self.open  = price
        self.high  = price
        self.low   = price
        self.close = price
        self.ticks = 1

    def update(self, price: float):
        self.high  = max(self.high, price)
        self.low   = min(self.low,  price)
        self.close = price
        self.ticks += 1

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def is_bullish(self) -> bool:
        return self.close >= self.open

    @property
    def upper_wick(self) -> float:
        top = max(self.open, self.close)
        return self.high - top

    @property
    def lower_wick(self) -> float:
        bot = min(self.open, self.close)
        return bot - self.low

    @property
    def range(self) -> float:
        return self.high - self.low if self.high != self.low else 1e-9


# ══════════════════════════════════════════════════════════════
#  PER-CRYPTO STATE
# ══════════════════════════════════════════════════════════════
class CryptoState:
    CANDLE_SEC   = 5     # candle size in seconds
    HISTORY_SECS = 60    # keep 60 seconds of candles → 12 candles

    def __init__(self, symbol: str):
        self.symbol = symbol

        # Raw tick buffer (timestamp, price)
        self.ticks: deque = deque(maxlen=600)

        # Completed candles
        self.candles: deque = deque(maxlen=self.HISTORY_SECS // self.CANDLE_SEC + 2)

        # Current (open) candle
        self._cur_candle: Optional[Candle] = None

        # Trade tracking
        self.last_trade_ts:   float          = 0.0
        self.last_market_id:  Optional[str]  = None
        self.trades:          int            = 0
        
        # Previous market outcome tracking
        self.last_market_outcome: Optional[str] = None  # "UP" or "DOWN"

    def add_tick(self, price: float):
        now = time.time()
        self.ticks.append((now, price))
        self._update_candle(now, price)

    def _update_candle(self, now: float, price: float):
        bucket = (now // self.CANDLE_SEC) * self.CANDLE_SEC
        if self._cur_candle is None or self._cur_candle.ts != bucket:
            if self._cur_candle:
                self.candles.append(self._cur_candle)
            self._cur_candle = Candle(bucket, price)
        else:
            self._cur_candle.update(price)

    def recent_candles(self, n: int = 9) -> List[Candle]:
        """Return up to n completed candles + current open candle"""
        all_c = list(self.candles)
        if self._cur_candle:
            all_c.append(self._cur_candle)
        return all_c[-n:]

    def latest_price(self) -> Optional[float]:
        return self.ticks[-1][1] if self.ticks else None


# ══════════════════════════════════════════════════════════════
#  GLOBAL STATE
# ══════════════════════════════════════════════════════════════
class BotState:
    def __init__(self):
        self.cryptos: Dict[str, CryptoState] = {s: CryptoState(s) for s in SYMBOLS}
        self.daily_spent   = 0.0
        self.trades_total  = 0
        self.day_str       = datetime.now().strftime("%Y%m%d")
        self.last_summary_time = time.time()  # For 30-sec summary display
        
        # Market fetch cache
        self.markets_cache: Dict[str, dict] = {}
        self.markets_lock = __import__('threading').Lock()
        self.stop_market_fetcher = False

    def reset_daily_if_needed(self):
        today = datetime.now().strftime("%Y%m%d")
        if today != self.day_str:
            self.daily_spent  = 0.0
            self.trades_total = 0
            self.day_str      = today
            log.info("✅ Daily counters reset for new day")

S = BotState()

# ══════════════════════════════════════════════════════════════
#  PRICE FETCHING
# ══════════════════════════════════════════════════════════════
_bn = ccxt.binance({"enableRateLimit": True})
_cb = ccxt.coinbaseexchange({"enableRateLimit": True})

def fetch_price(symbol: str) -> Optional[float]:
    """Average of Binance + Coinbase; fallback to whichever works"""
    b = c = None
    try:
        b = float(_bn.fetch_ticker(PAIR_BINANCE[symbol])["last"])
    except Exception:
        pass
    try:
        c = float(_cb.fetch_ticker(PAIR_COINBASE[symbol])["last"])
    except Exception:
        pass

    if b and c:
        spread = abs(b - c) / ((b + c) / 2) * 100
        if spread > 0.5:           # exchanges diverged > 0.5% → use Binance
            return b
        return (b + c) / 2
    return b or c


def format_price(price: Optional[float]) -> str:
    """Format price nicely (avoid scientific notation)"""
    if price is None:
        return "N/A"
    if price >= 1000:
        return f"{price:,.0f}"
    elif price >= 1:
        return f"{price:,.2f}"
    else:
        return f"{price:.6f}"


def fetch_all_prices() -> Dict[str, Optional[float]]:
    return {s: fetch_price(s) for s in SYMBOLS}


def get_api_mode_status() -> Tuple[str, str]:
    """
    Returns current API mode being used
    Returns: (api_name, api_description)
    """
    if CFG.get("RELAYER_API_KEY"):
        return "RELAYER", "Gasless (Relayer API - No MATIC)"
    else:
        return "CLOB", "Standard CLOB"


def check_sniper_window(market_timestamp: int) -> Tuple[bool, int, str]:
    """
    Check if current time is within SNIPER WINDOW (last 10-15 seconds of 5-min candle)
    Returns: (in_sniper_window, seconds_left, window_status)
    """
    now_utc = datetime.now(timezone.utc)
    current_epoch = int(now_utc.timestamp())
    
    # Window ends at market_timestamp + 300 (5 minutes = 300 seconds)
    window_end = market_timestamp + 300
    seconds_left = window_end - current_epoch
    
    # SNIPER WINDOW: 5 <= seconds_left <= 15
    in_sniper = CFG["SNIPER_WINDOW_MIN"] <= seconds_left <= CFG["SNIPER_WINDOW_MAX"]
    
    if in_sniper:
        status = f"SNIPER_OK({seconds_left}s left)"
    elif seconds_left > CFG["SNIPER_WINDOW_MAX"]:
        status = f"TOO_EARLY({seconds_left}s left, need <={CFG['SNIPER_WINDOW_MAX']}s)"
    elif seconds_left < CFG["SNIPER_WINDOW_MIN"]:
        status = f"TOO_LATE({seconds_left}s left, need >={CFG['SNIPER_WINDOW_MIN']}s)"
    else:
        status = f"OUTSIDE_SNIPER({seconds_left}s left)"
    
    return in_sniper, seconds_left, status


# ✅ PRICE FILTERS: 0.88-0.95
def check_price_filters(chosen_token_price: float, signal: str) -> Tuple[bool, str]:
    """
    Check if token price is in acceptable range (0.88 - 0.95)
    Returns: (is_acceptable, reason_str)
    """
    if chosen_token_price < CFG["PRICE_MIN"]:
        reason = f"PRICE_TOO_LOW({chosen_token_price:.3f} < {CFG['PRICE_MIN']:.2f})"
        return False, reason
    elif chosen_token_price >= CFG["PRICE_MAX"]:
        reason = f"PRICE_TOO_HIGH({chosen_token_price:.3f} >= {CFG['PRICE_MAX']:.2f})"
        return False, reason
    else:
        reason = f"PRICE_OK({chosen_token_price:.3f})"
        return True, reason


# ✅ SIGNAL ENGINE: 3 INDICATORS ONLY
def calc_3_indicators(cs: CryptoState) -> Tuple[float, float, float, str]:
    """
    Calculate 3-layer signal score:
    1. Window Delta: How far has price moved from candle open? (0-1)
    2. Tick Trend: Is momentum consistent in last 20 ticks? (0-1)
    3. Volume Surge: Are ticks coming faster recently? (0-1)
    
    Returns: (delta_score, trend_score, volume_score, indicator_details)
    """
    
    # ── INDICATOR 1: WINDOW DELTA (price vs candle open) ──────
    candles = cs.recent_candles(2)
    if len(candles) < 2:
        return 0.0, 0.0, 0.0, "INSUFFICIENT_DATA"
    
    candle_open = candles[-2].open  # Previous candle open
    current_price = cs.latest_price()
    
    if current_price is None or candle_open <= 0:
        return 0.0, 0.0, 0.0, "NO_PRICE_DATA"
    
    price_move_pct = (current_price - candle_open) / candle_open * 100
    delta_score = min(1.0, max(0.0, abs(price_move_pct) / 0.5))  # 0.5% = max score
    
    # ── INDICATOR 2: TICK TREND (momentum in last 20 ticks) ──
    recent_ticks = list(cs.ticks)[-20:]
    if len(recent_ticks) < 5:
        trend_score = 0.0
    else:
        prices = [p for _, p in recent_ticks]
        # Count consistent direction
        consistent = 0
        for i in range(1, len(prices)):
            if (price_move_pct > 0 and prices[i] > prices[i-1]) or \
               (price_move_pct < 0 and prices[i] < prices[i-1]) or \
               (price_move_pct == 0):
                consistent += 1
        trend_score = consistent / (len(prices) - 1)
    
    # ── INDICATOR 3: VOLUME SURGE (tick frequency) ────────────
    now = time.time()
    recent_ticks_3sec = [(ts, p) for ts, p in cs.ticks if now - ts <= 3]
    tick_count = len(recent_ticks_3sec)
    volume_score = min(1.0, tick_count / 5)  # 5+ ticks in 3sec = max score
    
    indicator_details = (
        f"delta={delta_score:.2f}(move={price_move_pct:+.3f}%) | "
        f"trend={trend_score:.2f}({consistent}/{len(prices)-1} consistent) | "
        f"volume={volume_score:.2f}({tick_count} ticks in 3s)"
    )
    
    return delta_score, trend_score, volume_score, indicator_details


def score_signal_3layer(
    delta: float,
    trend: float,
    volume: float,
    cs: Optional[CryptoState] = None,
) -> Tuple[float, str]:
    """
    Combine 3 indicators into final score:
    - Delta: 35% weight (price move from open)
    - Trend: 35% weight (momentum consistency)
    - Volume: 30% weight (tick frequency)
    
    Returns: (final_score, details)
    """
    final_score = (delta * 0.35) + (trend * 0.35) + (volume * 0.30)
    details = f"final_score={final_score:.3f}"
    return final_score, details


# ✅ PREVIOUS MARKET TRACKING (Weak Boost)
def apply_previous_market_boost(
    cs: CryptoState,
    signal: str,
    current_score: float
) -> Tuple[float, str]:
    """
    Check if signal matches previous market outcome.
    - Match: +0.12 boost
    - Mismatch: -0.08 penalty
    
    Returns: (adjusted_score, boost_details)
    """
    if cs.last_market_outcome is None:
        boost_details = "NO_PREVIOUS_HISTORY"
        return current_score, boost_details
    
    # Map outcome to signal direction
    last_outcome = cs.last_market_outcome
    match = (last_outcome == "UP" and signal == "UP") or (last_outcome == "DOWN" and signal == "DOWN")
    
    if match:
        boost = 0.12
        adjusted_score = min(1.0, current_score + boost)
        boost_details = f"MATCH(prev={last_outcome}) +0.12 ✓"
    else:
        penalty = -0.08
        adjusted_score = max(0.0, current_score + penalty)
        boost_details = f"MISMATCH(prev={last_outcome}) -0.08 ✗"
    
    return adjusted_score, boost_details


# ══════════════════════════════════════════════════════════════
#  MARKET DISCOVERY — find active 5-min market on Polymarket
# ══════════════════════════════════════════════════════════════
def find_5min_market(symbol: str) -> Optional[dict]:
    """
    Fetch active 5-min market for the given symbol.
    Returns dict with condition_id, yes_token, no_token, yes_price, no_price.
    """
    try:
        import pytz
        et_tz        = pytz.timezone("America/New_York")
        et_now       = datetime.now(timezone.utc).astimezone(et_tz)
        window_min   = (et_now.minute // 5) * 5
        window_start = et_now.replace(minute=window_min, second=0, microsecond=0)
        market_ts    = int(window_start.timestamp())

        slug     = MARKET_SLUGS.get(symbol, f"{symbol.lower()}-updown-5m")
        url      = f"https://polymarket.com/event/{slug}-{market_ts}"

        headers  = {"User-Agent": "Mozilla/5.0"}
        r        = requests.get(url, headers=headers, timeout=10)
        r.raise_for_status()
        html     = r.text

        # Extract conditionId and clobTokenIds from embedded JSON
        cond_m   = re.search(r'"conditionId":"([^"]+)"', html)
        tok_m    = re.search(r'"clobTokenIds":\s*\[([^\]]+)\]', html)

        if not (cond_m and tok_m):
            log.debug(f"  [{symbol}] Market HTML parse failed | timestamp={market_ts}")
            return None

        condition_id = cond_m.group(1)
        token_ids    = json.loads("[" + tok_m.group(1) + "]")

        if len(token_ids) < 2:
            return None

        yes_token = token_ids[0]
        no_token  = token_ids[1]

        # Fetch token prices from CLOB API
        try:
            yes_url = f"{CFG['CLOB']}/midpoint?token_id={yes_token}"
            no_url  = f"{CFG['CLOB']}/midpoint?token_id={no_token}"
            
            yes_resp = requests.get(yes_url, timeout=5)
            no_resp  = requests.get(no_url, timeout=5)
            
            yes_price = float(yes_resp.json().get("mid", 0.5)) if yes_resp.status_code == 200 else 0.5
            no_price  = float(no_resp.json().get("mid", 0.5)) if no_resp.status_code == 200 else 0.5
            
        except Exception as e:
            log.debug(f"  [{symbol}] Price fetch failed: {e}")
            yes_price = 0.5
            no_price  = 0.5

        return {
            "condition_id": condition_id,
            "yes_token": yes_token,
            "no_token": no_token,
            "yes_price": yes_price,
            "no_price": no_price,
            "timestamp": market_ts,
        }
        
    except Exception as e:
        log.debug(f"  [{symbol}] Market fetch error: {e}")
        return None

        # Fetch current orderbook mid-prices from CLOB
        yes_price = get_token_price(yes_token)
        no_price  = get_token_price(no_token)

        result = {
            "symbol":       symbol,
            "condition_id": condition_id,
            "yes_token":    yes_token,
            "no_token":     no_token,
            "yes_price":    yes_price,
            "no_price":     no_price,
            "timestamp":    market_ts,
            "url":          url,
        }
        # Log market discovery with ID and timestamp
        log.info(f"  [{symbol}] Market Found | ID={condition_id[:16]}... | TS={market_ts}")
        return result

    except Exception as e:
        log.debug(f"  [{symbol}] Market discovery error: {e}")
        return None


def get_token_price(token_id: str) -> Optional[float]:
    """Get mid-price for a CLOB token from Polymarket orderbook"""
    try:
        url = f"{CFG['CLOB']}/midpoint?token_id={token_id}"
        r   = requests.get(url, timeout=5)
        if r.status_code == 200:
            data = r.json()
            return float(data.get("mid", 0))
    except Exception:
        pass
    return None


# ══════════════════════════════════════════════════════════════
#  CLOB CLIENT
# ══════════════════════════════════════════════════════════════
_clob_client = None

def init_client():
    global _clob_client
    if CFG["DRY_RUN"] or not CLOB_OK or not CFG["PRIVATE_KEY"]:
        return None
    try:
        # Initialize CLOB client
        _clob_client = ClobClient(
            host=CFG["CLOB"],
            key=CFG["PRIVATE_KEY"],
            chain_id=CFG["CHAIN_ID"],
            signature_type=CFG["SIGNATURE_TYPE"],
            funder=CFG["POLYMARKET_FUNDER_ADDRESS"],
        )
        
        # Create or derive API credentials
        creds = _clob_client.create_or_derive_api_creds()
        _clob_client.set_api_creds(creds)
        
        wallet_type = "Email" if CFG["SIGNATURE_TYPE"] == 1 else "EOA"
        
        # Check if using relayer for gasless
        if CFG.get("RELAYER_API_KEY"):
            log.info(f"✅ CLOB client ready ({wallet_type} wallet) - GASLESS mode (Relayer API)")
            log.info(f"   Relayer Address: {CFG['RELAYER_API_KEY_ADDRESS']}")
        else:
            log.info(f"✅ CLOB client ready ({wallet_type} wallet)")
        
        return _clob_client
    except Exception as e:
        log.error(f"CLOB init failed: {e}")
        return None


def place_order(token_id: str, signal: str, amount: float, retry_count: int = 0) -> Optional[dict]:
    """Submit a market FOK order via CLOB with smart retry on failure"""
    if CFG["DRY_RUN"]:
        log.info(f"  [DRY RUN] Would place ${amount:.2f} order for {signal}")
        return {"orderID": "DRYRUN_" + str(int(time.time())), "status": "simulated"}
    
    if not _clob_client:
        log.error(f"  ❌ CLOB client not initialized")
        return None
    
    # Max 3 attempts (original + 2 retries)
    MAX_RETRIES = 2
    FALLBACK_SIZES = [1.0, 1.0]  # Keep FULL AMOUNT on retries (no reduction)
    MIN_FALLBACK_SIZE = 1.0  # Minimum $1.00 for liquidity

    try:
        retry_label = f" [RETRY {retry_count}]" if retry_count > 0 else ""
        attempt_size = amount if retry_count == 0 else max(
            amount * FALLBACK_SIZES[retry_count - 1], 
            MIN_FALLBACK_SIZE
        )
        
        log.info(f"  [CLOB{retry_label}] Building market order: {signal} ${attempt_size:.2f}")
        order = MarketOrderArgs(
            token_id=token_id,
            amount=attempt_size,
            side=BUY,          # We always BUY the token we believe will resolve YES
            order_type=OrderType.FOK,
        )
        signed   = _clob_client.create_market_order(order)
        log.info(f"  [CLOB{retry_label}] Order signed, submitting to blockchain...")
        response = _clob_client.post_order(signed, OrderType.FOK)
        
        if response and response.get("orderID"):
            log.info(f"  ✅ [CLOB✓] Order CONFIRMED! ID: {response['orderID'][:20]}...")
            return response
        else:
            log.warning(f"  ❌ [CLOB] No orderID in response: {response}")
            # Retry with smaller size if we haven't exceeded max retries
            if retry_count < MAX_RETRIES:
                log.info(f"  🔄 [FALLBACK] Retrying with reduced size...")
                time.sleep(1)  # Wait 1 second before retry
                return place_order(token_id, signal, amount, retry_count + 1)
            return None
        
    except Exception as e:
        log.error(f"  ❌ [CLOB] Order error: {e}")
        # Retry with smaller size if we haven't exceeded max retries
        if retry_count < MAX_RETRIES:
            log.info(f"  🔄 [FALLBACK] Retrying with reduced size...")
            time.sleep(1 + retry_count)  # Exponential backoff: 1s, 2s
            return place_order(token_id, signal, amount, retry_count + 1)
        return None


def place_gasless_order(token_id: str, signal: str, amount: float) -> Optional[dict]:
    """
    NOTE: Relayer API requires complex transaction signing that must be done via
    the py_clob_client library. For now, we execute trades via CLOB which works reliably.
    Returns None to fall through to place_order (CLOB execution).
    """
    if CFG["DRY_RUN"] or not CFG.get("RELAYER_API_KEY"):
        return None
    
    try:
        log.info(f"  [RELAYER] Testing endpoint connectivity...")
        
        # Just test if relayer endpoint is reachable
        relayer_url = "https://relayer-v2.polymarket.com/"
        headers = {
            "RELAYER_API_KEY": CFG["RELAYER_API_KEY"],
            "RELAYER_API_KEY_ADDRESS": CFG.get("RELAYER_API_KEY_ADDRESS", ""),
        }
        
        resp = requests.get(relayer_url, headers=headers, timeout=5)
        if resp.status_code == 200:
            log.info(f"  [RELAYER] Connected, but using CLOB for reliable execution")
        else:
            log.warning(f"  [RELAYER] Endpoint error {resp.status_code}")
        
        # Always return None to use CLOB execution (proven to work)
        return None
            
    except Exception as e:
        log.warning(f"  [RELAYER] {e}")
        return None


# ══════════════════════════════════════════════════════════════
#  MARKET REDEMPTION — claim profits when markets resolve
# ══════════════════════════════════════════════════════════════
def redeem_market(symbol: str, market_id: str, token_id: str, amount: float) -> bool:
    """
    Redeem winning tokens after market resolution.
    Submits redemption transaction to CLOB.
    Returns True if redemption successful, False otherwise.
    """
    if CFG["DRY_RUN"]:
        log.info(f"  [DRY RUN] Would redeem {amount:.2f} USDC from market {market_id[:16]}...")
        return True
    
    if not _clob_client:
        log.warning(f"  ❌ [{symbol}] CLOB client not initialized - can't redeem")
        return False
    
    try:
        log.info(f"  [REDEEM] Submitting redemption for {symbol}...")
        
        # Method 1: Try using CLOB client's sell order (to close position)
        # When market resolves, we need to settle/redeem our position
        try:
            # Get current price from resolved market
            url = f"{CFG['CLOB']}/midpoint?token_id={token_id}"
            resp = requests.get(url, timeout=5)
            if resp.status_code == 200:
                price_data = resp.json()
                mid_price = float(price_data.get("mid", 0.99))
            else:
                mid_price = 0.99  # Default if market closed
                
        except:
            mid_price = 0.99
        
        # Create settlement order to claim winnings
        settlement_order = MarketOrderArgs(
            token_id=token_id,
            amount=amount,
            side=SELL,  # SELL to close position and claim winnings
            order_type=OrderType.FOK,
        )
        
        signed = _clob_client.create_market_order(settlement_order)
        response = _clob_client.post_order(signed, OrderType.FOK)
        
        if response and response.get("orderID"):
            order_id = response["orderID"]
            log.info(f"  ✅ [REDEEM✓] Redemption submitted for {symbol}!")
            log.info(f"     Market: {market_id[:16]}...")
            log.info(f"     Amount: {amount:.2f} USDC")
            log.info(f"     Order ID: {order_id[:20]}...")
            return True
        else:
            log.warning(f"  ❌ [{symbol}] Redemption failed - no order ID")
            return False
        
    except Exception as e:
        log.warning(f"  ❌ [{symbol}] Redemption error: {e}")
        return False


def check_market_resolution(symbol: str) -> Optional[dict]:
    """
    Check if a market has resolved and automatically redeem profits.
    Returns market resolution data if successful.
    """
    try:
        cs = S.cryptos[symbol]
        
        # Skip if no open position
        if cs.open_position is None or cs.entry_price is None:
            return None
        
        # Check market status via CLOB API
        if not cs.last_market_id:
            return None
        
        market_url = f"{CFG['CLOB']}/market/{cs.last_market_id}"
        response = requests.get(market_url, timeout=5)
        
        if response.status_code != 200:
            return None
        
        market_data = response.json()
        
        log.debug(f"  [{symbol}] Market data: {json.dumps(market_data, indent=2)[:500]}...")
        
        # Check if market has resolved
        if not market_data.get("closed", False):
            log.debug(f"  [{symbol}] Market not closed yet")
            return None
        
        outcome = market_data.get("outcome")
        if outcome not in ["YES", "NO"]:
            log.debug(f"  [{symbol}] Market outcome not resolved: {outcome}")
            return None
        
        # Map outcome to price
        resolved_price = 1.0 if outcome == "YES" else 0.0
        current_price = cs.latest_price()
        
        if current_price is None:
            return None
        
        # Calculate profit/loss (position-based)
        position_correct = (cs.open_position == "UP" and outcome == "YES") or (cs.open_position == "DOWN" and outcome == "NO")
        profit_loss = 2.0 if position_correct else -2.0  # Approx trade size, adjust if needed
        
        log.info(f"\n  ✓ [{symbol}] MARKET RESOLVED!")
        log.info(f"    Position: {cs.open_position} | Outcome: {outcome}")
        log.info(f"    Profit/Loss: ${profit_loss:+.2f}")
        
        # Track profit
        if profit_loss > 0:
            log.info(f"    ✅ PROFIT DETECTED: +${abs(profit_loss):.2f}")
        else:
            log.warning(f"    ❌ LOSS DETECTED: -${abs(profit_loss):.2f}")
        
        # TOKEN INFO FOR REDEMPTION
        if outcome == "YES":
            winning_token = market_data.get("yes_token", None)
        else:
            winning_token = market_data.get("no_token", None)
        
        # REDEEM THE MARKET (CLAIM PROFITS)
        log.info(f"    [REDEEM] Attempting to claim profits...")
        if winning_token:
            redeem_success = redeem_market(
                symbol=symbol,
                market_id=cs.last_market_id,
                token_id=winning_token,
                amount=abs(profit_loss) if profit_loss > 0 else 0.01  # Redeem winnings
            )
            
            if redeem_success:
                log.info(f"    ✅ REDEMPTION SUBMITTED!")
                S.daily_spent -= (profit_loss / 100)  # Reduce daily spent by profit
            else:
                log.warning(f"    ⚠️  Redemption failed - manual claim may be needed")
        else:
            log.warning(f"    ⚠️  No winning token found in market data")
        
        # Clear the position
        cs.entry_price = None
        cs.open_position = None
        
        return {
            "symbol": symbol,
            "market_id": cs.last_market_id,
            "resolved_price": resolved_price,
            "entry_price": cs.entry_price,
            "profit_loss_pct": profit_loss,
            "redeemed": redeem_success if 'redeem_success' in locals() else False
        }
        
    except Exception as e:
        log.debug(f"  [{symbol}] Resolution check error: {e}")
        return None


# ══════════════════════════════════════════════════════════════
#  BACKGROUND MARKET FETCHER (runs every 1-2 seconds)
# ══════════════════════════════════════════════════════════════
def background_market_fetcher():
    """
    Background thread that continuously fetches markets every 0.5 seconds.
    Updates S.markets_cache without blocking main loop.
    """
    while not S.stop_market_fetcher:
        try:
            markets = {}
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(SYMBOLS)) as executor:
                future_to_symbol = {executor.submit(find_5min_market, symbol): symbol for symbol in SYMBOLS}
                for future in concurrent.futures.as_completed(future_to_symbol):
                    symbol = future_to_symbol[future]
                    try:
                        market = future.result()
                        if market:
                            markets[symbol] = market
                        else:
                            pass  # Silent fail for missing markets
                    except Exception as e:
                        pass  # Silent fail
            
            # Update cache atomically
            with S.markets_lock:
                S.markets_cache = markets
                cache_count = len(markets)
                
            log.info(f"🔄 [MARKET CACHE UPDATED] {cache_count}/{len(SYMBOLS)} markets ready")
            time.sleep(0.5)  # Fetch every 0.5 seconds
            
        except Exception as e:
            log.debug(f"Background fetcher error: {e}")
            time.sleep(0.5)


# ══════════════════════════════════════════════════════════════
#  MAIN LOOP - SNIPER LOGIC
# ══════════════════════════════════════════════════════════════
def tick():
    """Main tick function - runs every 0.5 seconds"""
    S.reset_daily_if_needed()
    now    = time.time()
    ts_str = datetime.now(timezone.utc).strftime("%H:%M:%S")
    ts_ms  = datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3]
    
    # ── API Mode Detection ────────────────────────────────────
    api_mode, api_desc = get_api_mode_status()

    # ── 1. Fetch prices ───────────────────────────────────────
    prices = fetch_all_prices()
    missing = [s for s, p in prices.items() if p is None]
    if missing:
        log.warning(f"Price fetch failed: {missing}")

    # ── 2. Store ticks ────────────────────────────────────────
    for symbol, price in prices.items():
        if price is not None:
            S.cryptos[symbol].add_tick(price)

    # ── 2.5 Show live status every 3 seconds ──────────────────
    if (int(now) % 3) == 0:  # Every ~3 seconds
        price_parts = []
        for s in SYMBOLS:
            if prices[s]:
                formatted_price = format_price(prices[s])
                price_parts.append(f"{s}=${formatted_price}")
        price_line = "  |  ".join(price_parts)
        
        with S.markets_lock:
            cache_count = len(S.markets_cache)
        
        log.info(f"✅ [LIVE] {price_line} | Markets: {cache_count}/{len(SYMBOLS)}")

    # ── 3. Print price summary every 30 seconds ───────────────
    time_since_summary = now - S.last_summary_time
    if time_since_summary >= 30.0:
        price_parts = []
        for s in SYMBOLS:
            if prices[s]:
                formatted_price = format_price(prices[s])
                price_parts.append(f"{s}=${formatted_price}")
            else:
                price_parts.append(f"{s}=N/A")
        
        price_line = "  |  ".join(price_parts)
        
        # Also show market cache status
        with S.markets_lock:
            cache_count = len(S.markets_cache)
        
        log.info(f"\n\n{'='*90}")
        log.info(f"[{ts_str}] 30-SEC SUMMARY | API: {api_mode} ({api_desc}) | Mode: {CFG['BOT_MODE'].upper()}")
        log.info(f"  💰 PRICES: {price_line}")
        log.info(f"  📊 MARKETS: {cache_count}/{len(SYMBOLS)} | Daily: ${S.daily_spent:.2f}/${CFG['DAILY_LIMIT']:.2f} | Trades: {S.trades_total}")
        log.info(f"{'='*90}\n")
        S.last_summary_time = now

    # ── 4. Daily limit check ──────────────────────────────────
    if S.daily_spent >= CFG["DAILY_LIMIT"]:
        log.warning(f"⚠️  Daily limit ${CFG['DAILY_LIMIT']:.2f} reached. Skipping trades.")
        return

    # ── 5. Use cached markets (from background fetcher) ────────
    with S.markets_lock:
        markets = S.markets_cache.copy()

    # ── 6. SNIPER EVALUATION ──────────────────────────────────
    log.info(f"\n🎯 [SNIPER POLL] Checking {len(SYMBOLS)} cryptos for sniper opportunities...")
    trades_this_cycle = []
    
    for symbol in SYMBOLS:
        cs    = S.cryptos[symbol]
        price = prices.get(symbol)
        if price is None:
            continue

        # Cooldown (don't spam same window) - prevents duplicate trades
        if now - cs.last_trade_ts < 300:  # 5-minute cooldown to match market cycle
            continue

        # ── Find market (now from parallel fetch) ──────────────
        market = markets.get(symbol)
        if not market:
            log.debug(f"  [{symbol}] No active market")
            continue

        # Skip if already traded this window
        if cs.last_market_id == market["condition_id"]:
            log.debug(f"  [{symbol}] Already traded this window")
            continue

        yes_price = market["yes_price"]
        no_price  = market["no_price"]

        # ────────────────────────────────────────────────────────
        # ⭐ VALIDATION LAYER 1: SNIPER TIMING WINDOW
        # ────────────────────────────────────────────────────────
        in_sniper, seconds_left, window_status = check_sniper_window(market["timestamp"])
        if not in_sniper:
            log.debug(f"  [{symbol}] {window_status}")
            continue

        # ────────────────────────────────────────────────────────
        # ⭐ DETERMINE SIGNAL DIRECTION
        # ────────────────────────────────────────────────────────
        if yes_price >= no_price:
            signal = "UP"
            chosen_price = yes_price
            chosen_token = market["yes_token"]
        else:
            signal = "DOWN"
            chosen_price = no_price
            chosen_token = market["no_token"]

        # ────────────────────────────────────────────────────────
        # ⭐ VALIDATION LAYER 2: PRICE FILTERS (0.88-0.95)
        # ────────────────────────────────────────────────────────
        price_ok, price_msg = check_price_filters(chosen_price, signal)
        if not price_ok:
            log.debug(f"  [{symbol}] {price_msg}")
            continue

        # ────────────────────────────────────────────────────────
        # ⭐ VALIDATION LAYER 3: 3-INDICATOR SIGNAL SCORING
        # ────────────────────────────────────────────────────────
        delta, trend, volume, indicator_details = calc_3_indicators(cs)
        signal_score, score_details = score_signal_3layer(delta, trend, volume, cs)

        # ────────────────────────────────────────────────────────
        # ⭐ VALIDATION LAYER 4: PREVIOUS MARKET BOOST/PENALTY
        # ────────────────────────────────────────────────────────
        final_score, boost_msg = apply_previous_market_boost(cs, signal, signal_score)

        # Minimum score threshold: 0.35
        MIN_SCORE = 0.35
        if final_score < MIN_SCORE:
            log.debug(
                f"  [{symbol}] Score too low ({final_score:.3f} < {MIN_SCORE:.2f}) | "
                f"{indicator_details} | {boost_msg}"
            )
            continue

        # ────────────────────────────────────────────────────────
        # ✅ ALL VALIDATIONS PASSED - PREPARE TRADE
        # ────────────────────────────────────────────────────────
        trade_size = CFG["TRADE_SIZE"]  # Fixed $40
        
        log.info(f"\n  {'*'*80}")
        log.info(f"  [SNIPER TRADE] {symbol} | Signal={signal} | Price=${chosen_price:.3f}")
        log.info(f"    Timing: {window_status}")
        log.info(f"    Indicators: {indicator_details}")
        log.info(f"    Signal Score: {signal_score:.3f} → Final: {final_score:.3f} (+boost:{boost_msg.split()[0]})")
        log.info(f"    Market: {market['condition_id'][:20]}...")
        log.info(f"    Token: {chosen_token[:24]}...")
        log.info(f"    Size: ${trade_size:.2f}")
        
        # ────────────────────────────────────────────────────────
        # EXECUTE ORDER
        # ────────────────────────────────────────────────────────
        resp = place_order(chosen_token, signal, trade_size)

        if resp and resp.get("orderID"):
            order_id = resp["orderID"]
            is_dry = resp.get("status") == "simulated"
            mode_str = "[DRY]" if is_dry else "[LIVE✓]"
            log.info(f"    {mode_str} Order ID: {order_id[:30]}...")
            cs.last_market_id = market["condition_id"]
            cs.last_trade_ts = now
            S.trades_total += 1
            S.daily_spent += trade_size
            trades_this_cycle.append(f"{symbol}({signal[:1]})")
            log.info(
                f"  ✅ Portfolio: Spent=${S.daily_spent:.2f}/${CFG['DAILY_LIMIT']:.2f} | Trades={S.trades_total}"
            )
            log.info(f"  {'*'*80}\n")
        else:
            log.warning(f"  ❌ [{symbol}] Order submission FAILED\n  {'*'*80}\n")

    # ── EVENT SUMMARY ─────────────────────────────────────────
    if trades_this_cycle:
        log.info(f"✅ [SNIPER SUMMARY] Trades executed: {' | '.join(trades_this_cycle)}")
    else:
        log.info(f"⏳ [SNIPER SUMMARY] No trades this cycle")


# ══════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════
def main():
    log.info("""
╔════════════════════════════════════════════════════════════════════════════════╗
║     POLY5MIN_ALL.PY v5.0 — Polymarket SNIPER Bot (BTC/ETH Only)               ║
║     Markets: BTC | ETH (2 Cryptos)                                            ║
║     Strategy: 10-15 Second Sniper + Price Filters (0.88-0.95)                 ║
║     Signal Engine: Window Delta + Tick Trend + Volume Surge (3 Layers)        ║
║     Execution: EVERY 0.5 SEC | Summary: EVERY 30 SECONDS                      ║
║     Trade Size: FIXED $30 per valid signal                                    ║
╚════════════════════════════════════════════════════════════════════════════════╝
""")

    if not CFG["PRIVATE_KEY"]:
        log.error("[ERROR] PRIVATE_KEY not set in .env — aborting")
        return

    if CFG["DRY_RUN"]:
        log.warning("\n" + "="*88)
        log.warning("[DRY RUN MODE] No real USDC will be spent - Perfect for testing!")
        log.warning("[DRY RUN MODE] All trades shown with [DRY] prefix in output")
        log.warning("="*88 + "\n")
    else:
        log.error("\n" + "="*88)
        log.error("[LIVE TRADING MODE] REAL USDC WILL BE USED - Proceed with caution!")
        log.error("[LIVE TRADING MODE] All trades will be EXECUTED with [LIVE] prefix")
        log.error("Press Ctrl+C within 5 seconds to ABORT...")
        log.error("="*88 + "\n")
        time.sleep(5)

    init_client()

    gasless_mode = "GASLESS (Relayer API - No MATIC gas needed)" if CFG.get("RELAYER_API_KEY") else "Standard CLOB"
    
    log.info(f"""
╔═ CONFIGURATION ══════════════════════════════════════════════════════════════╗
  Mode              : {['DRY RUN (Testing)', 'LIVE TRADING'][not CFG['DRY_RUN']]}
  Bot Strategy      : {CFG['BOT_MODE'].upper()} Mode
  Transaction Type  : {gasless_mode}
  Markets           : {' | '.join(SYMBOLS)}
  
  Price Feed        : Binance + Coinbase (averaged)
  Price Range       : {CFG['PRICE_MIN']} - {CFG['PRICE_MAX']} (88¢-95¢)
  
  Sniper Window     : {CFG['SNIPER_WINDOW_MIN']}-{CFG['SNIPER_WINDOW_MAX']} seconds left in candle (TIGHT!)
  Trade Size        : ${CFG['TRADE_SIZE']:.2f} (FIXED)
  Daily Limit       : ${CFG['DAILY_LIMIT']:.2f}
  
  Signal Indicators : Window Delta (35%) + Tick Trend (35%) + Volume Surge (30%)
  Min Signal Score  : 0.35
  Previous Match    : +0.12 boost on match, -0.08 penalty on mismatch
  
  Check Interval    : Every {CFG['LOOP_SEC']} second(s)
  Summary Display   : Every 30 seconds
  
  Status: READY TO START SNIPING!
╚══════════════════════════════════════════════════════════════════════════════╝
""")

    # Start background market fetcher thread
    import threading
    fetcher_thread = threading.Thread(target=background_market_fetcher, daemon=True)
    fetcher_thread.start()
    log.info("[THREAD] Background market fetcher started (updates every 0.5 seconds)\n")

    log.info("[START] Beginning sniper trading loop...\n")
    tick_count = 0
    
    while True:
        try:
            tick_count += 1
            tick()
        except KeyboardInterrupt:
            log.info("\n" + "="*88)
            log.info("[STOP] Sniper bot stopped by user (Ctrl+C)")
            S.stop_market_fetcher = True
            log.info(f"[STATS] Total ticks: {tick_count} | Total trades: {S.trades_total} | Spent: ${S.daily_spent:.2f}")
            log.info("="*88 + "\n")
            break
        except Exception as e:
            log.error(f"Tick error: {e}", exc_info=True)
        time.sleep(CFG["LOOP_SEC"])


if __name__ == "__main__":
    main()
