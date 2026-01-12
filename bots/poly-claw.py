"""
╔══════════════════════════════════════════════════════════════╗
║     Polymarket BTC 5-Min Momentum Bot  —  v2.1               ║
║     Strategy : Temporal Lag + Momentum Mispricing            ║
║     NEW v2.1 : Relayer API  →  Gasless Transactions          ║
║     Max Trade: $30  │  One trade per 5-min window            ║
╚══════════════════════════════════════════════════════════════╝

RELAYER API EXPLANATION (new in v2.1):
  Polymarket's Relayer lets you submit on-chain transactions WITHOUT
  paying Polygon gas fees yourself.

  Normal path : your wallet → signs TX → sends to Polygon (costs MATIC)
  Relayer path: your wallet → signs locally → POST to Relayer → Relayer pays gas

  Required headers for every Relayer request:
    RELAYER_API_KEY         : your secret key  (from Polymarket profile)
    RELAYER_API_KEY_ADDRESS : your signer addr  (0x326f0bb36...)

  The bot uses Relayer for: submitting orders, cancelling limits.
  Signing still happens locally via py-clob-client (your key never leaves).
"""

import os, time, logging, sys
from datetime import datetime, timezone, timedelta
from collections import deque
from typing import Optional, Tuple

import ccxt, requests
from dotenv import load_dotenv

try:
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import MarketOrderArgs, OrderArgs, OrderType
    CLOB_OK = True
except ImportError as e:
    CLOB_OK = False
    print(f"Import error: {e}")

load_dotenv()

# ══════════════════════════════════════════════════════════════
#  CONFIG
# ══════════════════════════════════════════════════════════════
CFG = {
    "PRIVATE_KEY":             os.getenv("PRIVATE_KEY", ""),
    "CHAIN_ID":                int(os.getenv("CHAIN_ID", "137")),
    "SIGNATURE_TYPE":          int(os.getenv("SIGNATURE_TYPE", "0")),  # 0=EOA, 1=Email
    "POLYMARKET_FUNDER_ADDRESS": os.getenv("POLYMARKET_FUNDER_ADDRESS", ""),
    "API_KEY":                 os.getenv("API_KEY", ""),
    "API_SECRET":              os.getenv("API_SECRET", ""),
    "API_PASSPHRASE":          os.getenv("API_PASSPHRASE", ""),
    # Relayer credentials (from Polymarket → Profile → Relayer API Keys)
    "RELAYER_API_KEY":         os.getenv("RELAYER_API_KEY", ""),
    "RELAYER_API_KEY_ADDRESS": os.getenv("RELAYER_API_KEY_ADDRESS", ""),
    # Sizing
    "BASE_TRADE_SIZE":  float(os.getenv("BASE_TRADE_SIZE", "10")),
    "MAX_TRADE_SIZE":   float(os.getenv("MAX_TRADE_SIZE",  "30")),
    "MIN_TRADE_SIZE":   float(os.getenv("MIN_TRADE_SIZE",  "5")),
    "DAILY_LIMIT":      float(os.getenv("DAILY_LIMIT",     "150")),
    # Strategy
    "MOMENTUM_THRESHOLD": float(os.getenv("MOMENTUM_THRESHOLD", "0.05")),
    "STRONG_THRESHOLD":   float(os.getenv("STRONG_THRESHOLD",   "0.10")),
    "MIN_PRICE":    float(os.getenv("MIN_PRICE", "0.32")),
    "MAX_PRICE":    float(os.getenv("MAX_PRICE", "0.68")),
    "WINDOW_MIN_PCT": float(os.getenv("WINDOW_MIN_PCT", "0.38")),
    "WINDOW_MAX_PCT": float(os.getenv("WINDOW_MAX_PCT", "0.85")),
    # Orders
    "SLIPPAGE_PCT":     float(os.getenv("SLIPPAGE_PCT",     "0.20")),
    "HYBRID_MODE":      os.getenv("HYBRID_MODE", "false").lower() == "true",
    "LIMIT_OFFSET_PCT": float(os.getenv("LIMIT_OFFSET_PCT", "0.03")),
    "LIMIT_WAIT_SEC":   int(os.getenv("LIMIT_WAIT_SEC",     "7")),
    # Runtime
    "DRY_RUN":  os.getenv("DRY_RUN", "true").lower() == "true",
    "LOOP_SEC": int(os.getenv("LOOP_SEC", "40")),
    # Endpoints
    "GAMMA":   "https://gamma-api.polymarket.com",
    "CLOB":    "https://clob.polymarket.com",
    "RELAYER": "https://relayer.polymarket.com",
}

# ══════════════════════════════════════════════════════════════
#  LOGGING
# ══════════════════════════════════════════════════════════════
os.makedirs("logs", exist_ok=True)
_fmt = logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s", "%H:%M:%S")
log = logging.getLogger("PM-BOT")
log.setLevel(logging.DEBUG)
_ch = logging.StreamHandler(sys.stdout);  _ch.setLevel(logging.INFO);  _ch.setFormatter(_fmt)
_ch.stream.reconfigure(encoding='utf-8', errors='replace')
_fh = logging.FileHandler(f"logs/bot_{datetime.now().strftime('%Y%m%d')}.log", encoding='utf-8')
_fh.setLevel(logging.DEBUG);    _fh.setFormatter(_fmt)
log.addHandler(_ch); log.addHandler(_fh)

# ══════════════════════════════════════════════════════════════
#  STATE
# ══════════════════════════════════════════════════════════════
class State:
    def __init__(self):
        self.prices        = deque(maxlen=180)
        self.daily_spent   = 0.0
        self.trades        = 0
        self.wins = self.losses = 0
        self.last_market_id = None
        self.last_trade_ts  = 0.0
        self.relayer_ok     = False

    def win_rate(self):
        t = self.wins + self.losses
        return self.wins / t * 100 if t else 0.0

S = State()

# ══════════════════════════════════════════════════════════════
#  SECTION 0 — RELAYER API (gasless transactions)
# ══════════════════════════════════════════════════════════════

def relayer_headers() -> dict:
    """
    Headers required for every Polymarket Relayer request.
    These authenticate you so the Relayer can submit TXs on your behalf.
    """
    return {
        "Content-Type":             "application/json",
        "RELAYER_API_KEY":          CFG["RELAYER_API_KEY"],
        "RELAYER_API_KEY_ADDRESS":  CFG["RELAYER_API_KEY_ADDRESS"],
    }


def verify_relayer() -> bool:
    """
    Test Relayer connectivity at startup.
    Prints clear status: gasless active or fallback to direct CLOB.
    """
    if not CFG["RELAYER_API_KEY"]:
        log.warning("RELAYER_API_KEY not set — will use direct CLOB (needs MATIC for gas)")
        return False

    if CFG["DRY_RUN"]:
        log.info("🔵 [DRY RUN] Relayer check skipped — would be gasless in live mode")
        return True

    try:
        r = requests.get(f"{CFG['RELAYER']}/health",
                         headers=relayer_headers(), timeout=8)
        if r.status_code in (200, 204):
            log.info(
                f"✅ Relayer API verified — GASLESS transactions ACTIVE\n"
                f"   Signer: {CFG['RELAYER_API_KEY_ADDRESS']}"
            )
            return True
        log.warning(f"Relayer health check {r.status_code} — falling back to direct CLOB")
        return False
    except Exception as e:
        log.warning(f"Relayer unreachable ({e}) — falling back to direct CLOB")
        return False


def relayer_submit_order(signed_payload: dict) -> Optional[dict]:
    """
    Submit a signed order via Relayer (gasless).

    Flow:
      1. py-clob-client signs the order locally (private key never leaves device)
      2. Signed payload is POSTed to Relayer endpoint
      3. Relayer submits the on-chain TX and pays the MATIC gas
      4. You pay $0 gas — only the USDC trade cost

    Args:
        signed_payload: Signed order dict from client.create_market_order()
    """
    if CFG["DRY_RUN"]:
        log.info("  🔵 [DRY RUN] Would submit via Relayer (gasless)")
        return {"dry_run": True, "mode": "relayer"}

    try:
        r = requests.post(
            f"{CFG['RELAYER']}/order",
            headers=relayer_headers(),
            json=signed_payload,
            timeout=15
        )
        r.raise_for_status()
        data = r.json()
        log.info(f"  ⛽ Gasless order submitted | orderID: {data.get('orderID', data.get('id','?'))}")
        return data
    except requests.HTTPError as e:
        log.error(f"  Relayer HTTP {e.response.status_code}: {e.response.text[:200]}")
        return None
    except Exception as e:
        log.error(f"  Relayer submit error: {e}")
        return None


def relayer_cancel(order_id: str) -> bool:
    """Cancel an open order via Relayer (also gasless)."""
    if CFG["DRY_RUN"]:
        log.info(f"  🔵 [DRY RUN] Would cancel {order_id[:12]}... via Relayer")
        return True
    try:
        r = requests.delete(
            f"{CFG['RELAYER']}/order/{order_id}",
            headers=relayer_headers(), timeout=10
        )
        ok = r.status_code in (200, 204)
        if ok:
            log.info(f"  ✅ Cancelled {order_id[:12]}... via Relayer (gasless)")
        else:
            log.warning(f"  Cancel returned {r.status_code}")
        return ok
    except Exception as e:
        log.error(f"  Relayer cancel error: {e}")
        return False


def relayer_order_status(order_id: str) -> Optional[dict]:
    """Check fill status via Relayer (used in hybrid mode)."""
    if CFG["DRY_RUN"]:
        return {"sizeFilled": "0", "status": "OPEN"}
    try:
        r = requests.get(f"{CFG['RELAYER']}/order/{order_id}",
                         headers=relayer_headers(), timeout=8)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.debug(f"Order status error: {e}")
        return None


# ══════════════════════════════════════════════════════════════
#  SECTION 1 — BTC PRICE
# ══════════════════════════════════════════════════════════════
_bn = ccxt.binance({"enableRateLimit": True})
_cb = ccxt.coinbase({"enableRateLimit": True})

def _price_binance():
    try:    return float(_bn.fetch_ticker("BTC/USDT")["last"])
    except: return None

def _price_coinbase():
    try:    return float(_cb.fetch_ticker("BTC/USD")["last"])
    except: return None

def btc_price() -> Optional[float]:
    b, c = _price_binance(), _price_coinbase()
    if b and c:
        if abs(b-c)/((b+c)/2)*100 > 0.5:
            log.warning("Large exchange spread — using Binance")
            return b
        return (b+c)/2
    return b or c


# ══════════════════════════════════════════════════════════════
#  SECTION 2 — MOMENTUM ENGINE
# ══════════════════════════════════════════════════════════════

def momentum(lookback=45) -> Tuple[Optional[float], str, float]:
    """
    Detect BTC momentum over last `lookback` seconds.
    Returns (pct, signal, strength 0-1).
    Strength = raw magnitude × tick consistency (reduces false signals).
    """
    now  = time.time()
    snap = sorted([(ts,px) for ts,px in S.prices if now-ts<=lookback])
    if len(snap) < 4:
        return None, "NEUTRAL", 0.0

    pct     = (snap[-1][1] - snap[0][1]) / snap[0][1] * 100
    abs_pct = abs(pct)

    if abs_pct < CFG["MOMENTUM_THRESHOLD"]:
        return pct, "NEUTRAL", 0.0

    signal   = "UP" if pct > 0 else "DOWN"
    raw_str  = min(1.0, (abs_pct - CFG["MOMENTUM_THRESHOLD"]) / (3*CFG["STRONG_THRESHOLD"]))

    ticks = [px for _,px in snap[-6:]]
    agrees = sum(1 for i in range(1,len(ticks))
                 if (signal=="UP" and ticks[i]>=ticks[i-1])
                 or (signal=="DOWN" and ticks[i]<=ticks[i-1]))
    consistency = agrees / max(len(ticks)-1, 1)
    strength    = raw_str * (0.5 + 0.5*consistency)

    log.debug(f"Mom {pct:+.4f}% raw={raw_str:.2f} cons={consistency:.2f} str={strength:.2f}")
    return pct, signal, strength


# ══════════════════════════════════════════════════════════════
#  SECTION 3 — MARKET DISCOVERY
# ══════════════════════════════════════════════════════════════

def find_btc_5min_market() -> Optional[dict]:
    """
    Find active 5-minute BTC market by:
    1. Calculate market timestamp (ET-based window ID)
    2. Fetch Polymarket page HTML 
    3. Extract condition ID using regex
    4. Return market object with condition ID
    
    Market ID = Unix timestamp of current 5-min window start (ET)
    Links: https://polymarket.com/event/btc-updown-5m-{timestamp}
    """
    try:
        import pytz
        import re
        
        # Calculate current market window (ET)
        et_tz = pytz.timezone('America/New_York')
        et_now = datetime.now(timezone.utc).astimezone(et_tz)
        minutes_in_hour = et_now.minute
        window_start_min = (minutes_in_hour // 5) * 5
        window_start = et_now.replace(minute=window_start_min, second=0, microsecond=0)
        
        # Market ID = timestamp of window start
        market_timestamp = int(window_start.timestamp())
        
        # Format window info  
        window_end = window_start + timedelta(minutes=5)
        month_day = window_start.strftime("%B %d").lstrip('0').replace(' 0', ' ')
        start_hour = window_start.strftime("%I").lstrip('0')
        start_min = window_start.strftime("%M")
        end_hour = window_end.strftime("%I").lstrip('0')
        end_min = window_end.strftime("%M")
        end_period = window_end.strftime("%p")
        
        window_info = f"Bitcoin Up or Down - 5 Minutes\n{month_day}, {start_hour}:{start_min}-{end_hour}:{end_min}{end_period} ET"
        market_url = f"https://polymarket.com/event/btc-updown-5m-{market_timestamp}"
        
        log.info(f"\n  [SEARCHING FOR MARKET - HTML EXTRACTION]")
        log.info(f"  {window_info}")
        log.info(f"  Market Timestamp: {market_timestamp}")
        log.info(f"  Direct URL: {market_url}")
        
        # Fetch market page HTML and extract condition ID
        try:
            headers = {'User-Agent': 'Mozilla/5.0'}
            r = requests.get(market_url, headers=headers, timeout=10)
            r.raise_for_status()
            
            html = r.text
            
            # Extract condition ID from HTML using regex
            match = re.search(r'"conditionId":"([^"]+)"', html)
            if match:
                condition_id = match.group(1)
                log.info(f"  FOUND MARKET!")
                log.info(f"  Condition ID: {condition_id}")
                
                # Extract token IDs if available
                token_match = re.search(r'"clobTokenIds":\s*\[([^\]]+)\]', html)
                token_ids = []
                if token_match:
                    token_str = token_match.group(1)
                    token_ids = [t.strip().strip('"') for t in token_str.split(',')]
                    log.info(f"  Token IDs: {token_ids}")
                
                # Create market object with essential fields
                market = {
                    "conditionId": condition_id,
                    "id": condition_id,
                    "question": "Bitcoin Up or Down - 5 Minutes (Current Window)",
                    "clobTokenIds": token_ids if token_ids else [condition_id + "-YES", condition_id + "-NO"],
                    "direct_url": market_url,
                    "market_timestamp": market_timestamp,
                    "startDate": window_start.isoformat() + "Z",
                    "endDate": window_end.isoformat() + "Z",
                }
                return market
            else:
                log.warning(f"  Condition ID not found in market page HTML")
                log.warning(f"  Market URL: {market_url}")
                log.warning(f"  (Will retry in next window...)")
                return None
                
        except Exception as e:
            log.error(f"  Error fetching market page: {e}")
            log.warning(f"  Direct URL: {market_url}")
            return None
        
    except Exception as e:
        log.error(f"  Market window calculation error: {e}")
        return None


def _midprice(token_id) -> Optional[float]:
    try:
        r = requests.get(f"{CFG['CLOB']}/midpoint", params={"token_id":token_id}, timeout=5)
        r.raise_for_status()
        return float(r.json().get("mid",0))
    except: return None


def market_prices(market) -> Tuple[Optional[float],Optional[float],Optional[str],Optional[str]]:
    up_id = down_id = None
    for t in market.get("tokens",[]):
        o = t.get("outcome","").upper()
        tid = t.get("token_id") or t.get("tokenId")
        if "UP" in o:   up_id   = tid
        if "DOWN" in o: down_id = tid
    if not up_id or not down_id:
        ids = market.get("clobTokenIds",[])
        if len(ids)>=2: up_id, down_id = ids[0], ids[1]
    if not up_id or not down_id: return None,None,None,None
    return _midprice(up_id), _midprice(down_id), up_id, down_id


# ══════════════════════════════════════════════════════════════
#  SECTION 4 — WINDOW TIMING
# ══════════════════════════════════════════════════════════════

def window_pct(market) -> Optional[float]:
    try:
        s = market.get("startDateIso") or market.get("startDate","")
        e = market.get("endDateIso")   or market.get("endDate","")
        if not s or not e: return None
        start = datetime.fromisoformat(s.replace("Z","+00:00"))
        end   = datetime.fromisoformat(e.replace("Z","+00:00"))
        now   = datetime.now(timezone.utc)
        total = (end-start).total_seconds()
        return max(0.0, min(1.0, (now-start).total_seconds()/total)) if total>0 else None
    except: return None


# ══════════════════════════════════════════════════════════════
#  SECTION 5 — POSITION SIZING
# ══════════════════════════════════════════════════════════════

def size_for_signal(strength, mkt_price) -> float:
    raw = CFG["BASE_TRADE_SIZE"] * strength * 2.0
    raw = max(CFG["MIN_TRADE_SIZE"], min(CFG["MAX_TRADE_SIZE"], raw))
    if mkt_price > 0.58: raw *= 0.70
    return round(raw, 2)


# ══════════════════════════════════════════════════════════════
#  SECTION 6 — ORDER PLACEMENT (Relayer-aware)
# ══════════════════════════════════════════════════════════════

def _dry(label, size, price):
    log.info(f"  🔵 [DRY RUN] {label}  ${size:.2f} @ ${price:.3f}")
    return {"dry_run": True}


def market_order(client, token_id, size_usd, price, direction) -> Optional[dict]:
    """
    Market order with automatic Relayer routing (gasless if available).

    Routing logic:
      S.relayer_ok=True  → sign locally → submit via Relayer (no MATIC cost)
      S.relayer_ok=False → sign locally → submit via direct CLOB (costs MATIC)

    SLIPPAGE (SLIPPAGE_PCT, default 20%):
      Thin 5-min books mean price can move between request and fill.
      20% tolerance ensures fills; lower if you want stricter pricing.
    """
    if CFG["DRY_RUN"]:
        tag = "GASLESS" if S.relayer_ok else "DIRECT"
        return _dry(f"MARKET {direction} [{tag}]", size_usd, price)
    try:
        # Create market order with side parameter (BUY for UP, SELL for DOWN)
        order_side = "BUY" if direction == "UP" else "SELL"
        signed = client.create_market_order(MarketOrderArgs(
            token_id=token_id, 
            amount=size_usd,
            side=order_side
        ))

        if S.relayer_ok:
            result = relayer_submit_order(signed)
            if result: return result
            log.warning("  Relayer failed — falling back to direct CLOB")

        resp = client.post_order(signed, OrderType.FOK)
        log.info(f"  SUCCESS! Order placed | orderID: {resp.get('orderID','?')}")
        return resp
    except Exception as e:
        log.error(f"  Market order error: {e}")
        return None


def hybrid_order(client, token_id, size_usd, price, direction) -> Optional[dict]:
    """
    Hybrid: limit order first (better price), market fallback.
    Limit posting, status check, and cancellation all via Relayer if available.
    """
    if CFG["DRY_RUN"]:
        tag = "GASLESS" if S.relayer_ok else "DIRECT"
        return _dry(f"HYBRID {direction} [{tag}]", size_usd, price)

    limit_px = round(price * (1 - CFG["LIMIT_OFFSET_PCT"]), 4)
    oid = None
    try:
        shares = size_usd / limit_px
        signed = client.create_limit_order(
            LimitOrderArgs(token_id=token_id, price=limit_px, size=shares, side=Side.BUY))

        resp = relayer_submit_order(signed) if S.relayer_ok else client.post_order(signed, OrderType.GTC)
        if resp:
            oid = resp.get("orderID") or resp.get("id")
            log.info(f"  📋 Limit @ ${limit_px:.3f} | ID: {oid}")

        time.sleep(CFG["LIMIT_WAIT_SEC"])

        status = relayer_order_status(oid) if (S.relayer_ok and oid) else (client.get_order(oid) if oid else None)
        if status and float(status.get("sizeFilled",0)) > 0:
            log.info("  ✅ Limit filled!")
            return status

        log.info(f"  ⏱  Unfilled after {CFG['LIMIT_WAIT_SEC']}s → cancel → market")
        if oid:
            if S.relayer_ok: relayer_cancel(oid)
            else:
                try: client.cancel(oid)
                except: pass
    except Exception as e:
        log.warning(f"  Hybrid error: {e}")
        if oid:
            if S.relayer_ok: relayer_cancel(oid)
            else:
                try: client.cancel(oid)
                except: pass

    return market_order(client, token_id, size_usd, price, direction)


# ══════════════════════════════════════════════════════════════
#  SECTION 6A — MARKET WINDOW FORMATTING
# ══════════════════════════════════════════════════════════════

def format_market_window():
    """
    Format current 5-minute BTC market window matching Polymarket format.
    
    Example output:
      Bitcoin Up or Down - 5 Minutes
      April 7, 1:20-1:25PM ET
      Link: https://polymarket.com/event/btc-updown-5m-1775505600
      
    Market ID increments by 300 seconds (5 min) for each window:
      1775505600 → 1775505900 → 1775506200 ...
    """
    import pytz
    
    # Get UTC now and convert to Eastern Time
    utc_now = datetime.now(timezone.utc)
    et_tz = pytz.timezone('America/New_York')
    et_now = utc_now.astimezone(et_tz)
    
    # Calculate current 5-minute window (0, 5, 10, 15... minutes)
    minutes_in_hour = et_now.minute
    window_start_min = (minutes_in_hour // 5) * 5
    window_end_min = window_start_min + 5
    
    # Build start and end times (ET)
    window_start = et_now.replace(minute=window_start_min, second=0, microsecond=0)
    window_end = et_now.replace(minute=window_end_min % 60, second=0, microsecond=0)
    if window_end_min >= 60:
        window_end = window_end + timedelta(hours=1)
    
    # Format: "April 7" (remove leading zeros)
    month_day = window_start.strftime("%B %d").lstrip('0').replace(' 0', ' ')
    
    # Format time range: "1:20-1:25PM" (no leading zero on hour)
    start_hour = window_start.strftime("%-I" if "linux" in sys.platform else "%I").lstrip('0')
    start_min = window_start.strftime("%M")
    end_hour = window_end.strftime("%-I" if "linux" in sys.platform else "%I").lstrip('0')  
    end_min = window_end.strftime("%M")
    end_period = window_end.strftime("%p")
    time_range = f"{start_hour}:{start_min}-{end_hour}:{end_min}{end_period}"
    
    # Build readable format
    readable = f"Bitcoin Up or Down - 5 Minutes\n{month_day}, {time_range} ET"
    
    # Market ID based on Unix timestamp of window start  
    market_id = int(window_start.timestamp())
    link = f"https://polymarket.com/event/btc-updown-5m-{market_id}"
    
    return market_id, readable, link


# ══════════════════════════════════════════════════════════════
#  SECTION 7 — RISK GATE
# ══════════════════════════════════════════════════════════════

def risk_ok(size) -> Tuple[bool, str]:
    if S.daily_spent + size > CFG["DAILY_LIMIT"]:
        return False, f"Daily limit ${CFG['DAILY_LIMIT']} breached"
    if size > CFG["MAX_TRADE_SIZE"]:
        return False, f"${size:.2f} > hard cap ${CFG['MAX_TRADE_SIZE']}"
    if time.time() - S.last_trade_ts < 35 and S.last_trade_ts > 0:
        return False, f"Cooldown ({time.time()-S.last_trade_ts:.0f}s < 35s)"
    return True, "OK"


# ══════════════════════════════════════════════════════════════
#  SECTION 8 — MAIN DECISION TICK
# ══════════════════════════════════════════════════════════════

def tick(client) -> bool:
    # 1. BTC price
    px = btc_price()
    if not px: log.warning("No BTC price"); return False
    S.prices.append((time.time(), px))

    # 2. Momentum
    mom_pct, signal, strength = momentum(45)
    arrow = {"UP":"UP","DOWN":"DOWN","NEUTRAL":"NEUTRAL"}[signal]
    gas   = "GASLESS" if S.relayer_ok else "DIRECT"
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")

    log.info(f"[{ts}] BTC ${px:,.2f} | Momentum {(mom_pct or 0):+.4f}% {arrow} | Strength {strength:.2f} | {signal} | {gas}")

    if signal == "NEUTRAL" or strength < 0.20:
        log.info("  Signal too weak, scanning for available markets..."); 
        market = find_btc_5min_market()
        if market:
            market_suffix, market_name, mkt_link = format_market_window()
            log.info(f"\n  Available market:")
            log.info(f"  {market_name}")
            log.info(f"  Link: {mkt_link}\n")
        return False

    # 3. Market  
    log.info("  Strong signal detected! Looking for matching market...")
    market = find_btc_5min_market()
    if not market: log.info("  No market found"); return False
    q   = market.get("question","?")
    mid = market.get("conditionId") or market.get("id","")
    
    # Get direct URL from market object (timestamp-based)
    mkt_link = market.get("direct_url") or f"https://polymarket.com/event/btc-updown-5m-{market.get('market_timestamp', '?')}"
    
    # Get formatted market window info
    market_suffix, market_name, _ = format_market_window()
    
    ts_full = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    log.info(f"\n  ===== MARKET FOUND =====")
    log.info(f"  {market_name}")
    log.info(f"  ID: {mid}")
    log.info(f"  Link: {mkt_link}")
    log.info(f"  Question: {q[:70]}\n")
    
    if mid == S.last_market_id: log.info("  Already traded this window"); return False

    # 4. Timing
    wpct = window_pct(market)
    if wpct is not None:
        log.info(f"  Window progress: {wpct*100:.0f}% elapsed")
        if wpct < CFG["WINDOW_MIN_PCT"]: log.info("  Too early in window"); return False
        if wpct > CFG["WINDOW_MAX_PCT"]: log.info("  Too late in window");  return False

    # 5. Prices
    up_px, dn_px, up_id, dn_id = market_prices(market)
    if up_px is None: log.warning("  No token prices found"); return False
    log.info(f"  Market odds: UP ${up_px:.3f} | DOWN ${dn_px:.3f}")

    target_px = up_px if signal=="UP" else dn_px
    target_id = up_id if signal=="UP" else dn_id

    # 6. Value zone
    if not (CFG["MIN_PRICE"] <= target_px <= CFG["MAX_PRICE"]):
        log.info(f"  Price ${target_px:.3f} outside value zone"); return False

    # 7. Size
    size = size_for_signal(strength, target_px)
    if size < CFG["MIN_TRADE_SIZE"]: log.info(f"  Size ${size:.2f} too small"); return False

    # 8. Risk
    ok, reason = risk_ok(size)
    if not ok: log.warning(f"  Risk check failed: {reason}"); return False

    # 9. Execute
    gas_label = "GASLESS via Relayer" if S.relayer_ok else "Direct CLOB (MATIC needed)"
    trade_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    log.info(
        f"\n  ===== TRADE EXECUTION =====\n"
        f"  Timestamp   : {trade_ts}\n"
        f"  Market      : {market_name}\n"
        f"  Market ID   : {mid}\n"
        f"  Market Link : {mkt_link}\n"
        f"  Direction   : {'BTC UP' if signal=='UP' else 'BTC DOWN'}\n"
        f"  BTC Price   : ${px:,.2f}\n"
        f"  Momentum    : {(mom_pct or 0):+.4f}%\n"
        f"  Strength    : {strength:.2f}\n"
        f"  Market Price: ${target_px:.3f}\n"
        f"  Trade Size  : ${size:.2f}\n"
        f"  Gas Mode    : {gas_label}\n"
        f"  Order Mode  : {'HYBRID' if CFG['HYBRID_MODE'] else 'MARKET ORDER'}\n"
        f"  ==========================\n"
    )

    fn     = hybrid_order if CFG["HYBRID_MODE"] else market_order
    result = fn(client, target_id, size, target_px, signal)

    if result:
        S.daily_spent   += size
        S.trades        += 1
        S.last_market_id = mid
        S.last_trade_ts  = time.time()
        log.info(f"  SUCCESS! Total Spent: ${S.daily_spent:.2f}/${CFG['DAILY_LIMIT']}  Trades: {S.trades}\n")
        return True

    log.error("  FAILED! Order error"); return False


# ══════════════════════════════════════════════════════════════
#  CLOB CLIENT
# ══════════════════════════════════════════════════════════════

def init_client():
    """
    Initialise CLOB client for LOCAL ORDER SIGNING.
    Note: Even with Relayer, signing happens locally — keys stay on your device.
    Relayer only handles on-chain submission (gas payment).
    """
    if CFG["DRY_RUN"]: return None
    if not CLOB_OK:
        log.warning("py-clob-client missing — DRY_RUN forced"); CFG["DRY_RUN"]=True; return None
    if not CFG["PRIVATE_KEY"]:
        log.warning("PRIVATE_KEY missing — DRY_RUN forced"); CFG["DRY_RUN"]=True; return None
    try:
        c = ClobClient(host=CFG["CLOB"], key=CFG["PRIVATE_KEY"],
                       chain_id=CFG["CHAIN_ID"], signature_type=CFG["SIGNATURE_TYPE"],
                       funder=CFG["POLYMARKET_FUNDER_ADDRESS"])
        
        # Derive and set API credentials from private key
        try:
            api_creds = c.create_or_derive_api_creds()
            c.set_api_creds(api_creds)
            sig_type_name = "Email wallet" if CFG["SIGNATURE_TYPE"] == 1 else "EOA wallet"
            log.info(f"✅ CLOB client ready ({sig_type_name}, derived API credentials)")
        except Exception as e:
            log.warning(f"Could not derive API creds: {e}")
            log.info("✅ CLOB client ready (basic mode)")
        
        return c
    except Exception as e:
        log.error(f"CLOB init failed: {e}"); CFG["DRY_RUN"]=True; return None


# ══════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════

def main():
    print("""
==========================================================
      Polymarket BTC 5-Min Momentum Bot v2.1
      + Relayer API  Zero Gas Transactions
==========================================================""")
    mode = "[DRY RUN] no real orders" if CFG["DRY_RUN"] else "[LIVE] REAL USDC TRADING"
    signer = CFG["RELAYER_API_KEY_ADDRESS"] or "not set"
    print(f"  Mode    : {mode}")
    print(f"  Signer  : {signer[:28]}...")
    print(f"  Sizes   : ${CFG['BASE_TRADE_SIZE']} per trade / Daily limit: ${CFG['DAILY_LIMIT']}")
    print(f"  Slippage: {CFG['SLIPPAGE_PCT']*100:.0f}%  Hybrid: {CFG['HYBRID_MODE']}  Check every: {CFG['LOOP_SEC']}s")
    print("=========================================================\n")

    if not CFG["DRY_RUN"]:
        print("*** WARNING: LIVE MODE ACTIVE ***")
        print("*** REAL USDC WILL BE USED FOR TRADES ***")
        print("*** Press Ctrl+C within 3 seconds to abort ***\n")
        time.sleep(3)

    S.relayer_ok = verify_relayer()
    client       = init_client()
    last_summary = time.time()
    loop         = 0

    gas_mode = "GASLESS via Relayer" if S.relayer_ok else "Direct CLOB"
    log.info(f"\n\n========== BOT STARTED ==========")
    log.info(f"Mode: {('DRY RUN' if CFG['DRY_RUN'] else 'LIVE TRADING')}")
    log.info(f"Gas: {gas_mode}")
    log.info(f"Check interval: {CFG['LOOP_SEC']}s")
    log.info(f"Trade size: ${CFG['BASE_TRADE_SIZE']}")
    log.info(f"Daily limit: ${CFG['DAILY_LIMIT']}")
    log.info(f"==================================\n")

    while True:
        try:
            loop += 1
            log.debug(f"[Tick #{loop}]")
            tick(client)

            if time.time() - last_summary > 300:
                log.info(f"SUMMARY: Trades:{S.trades}  Spent:${S.daily_spent:.2f}  W/L:{S.wins}/{S.losses}  Win%:{S.win_rate():.0f}%")
                last_summary = time.time()

            time.sleep(CFG["LOOP_SEC"])

        except KeyboardInterrupt:
            log.info(f"\n🛑 Stopped | Trades:{S.trades}  Spent:${S.daily_spent:.2f}")
            break
        except Exception as e:
            log.error(f"Loop error: {e}", exc_info=True)
            log.info("⏳ 60s retry..."); time.sleep(60)


if __name__ == "__main__":
    main()

# Updated 2025-12-16: Adjust comments for strategy clarity
# Updated 2025-12-18: Adjust comments for strategy clarity
# Updated 2025-12-20: Update LLM validation note
# Updated 2025-12-24: Adjust comments for strategy clarity
# Updated 2025-12-29: Add inline guidance for dry-run mode
# Updated 2026-01-08: Add inline guidance for dry-run mode
# Updated 2026-01-10: Tighten strategy commentary
# Updated 2026-01-13: Adjust comments for strategy clarity