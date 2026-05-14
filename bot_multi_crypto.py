"""
╔══════════════════════════════════════════════════════════════╗
║  Polymarket Multi-Crypto 5-Min Momentum Bot - v3.0           ║
║  Trades: BTC, ETH, SOL simultaneously                         ║
║  Strategy: Temporal Lag + Momentum Mispricing                ║
║  Gas: Direct CLOB (Relayer fallback)                         ║
╚══════════════════════════════════════════════════════════════╝
"""

import os, time, logging, sys, json, re
from datetime import datetime, timezone, timedelta
from collections import deque
from typing import Optional, Tuple, Dict

import ccxt, requests
from dotenv import load_dotenv

try:
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import MarketOrderArgs, OrderArgs, OrderType
    from py_clob_client.order_builder.constants import BUY, SELL
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
    "SIGNATURE_TYPE":          int(os.getenv("SIGNATURE_TYPE", "0")),
    "POLYMARKET_FUNDER_ADDRESS": os.getenv("POLYMARKET_FUNDER_ADDRESS", ""),
    "API_KEY":                 os.getenv("API_KEY", ""),
    "API_SECRET":              os.getenv("API_SECRET", ""),
    "API_PASSPHRASE":          os.getenv("API_PASSPHRASE", ""),
    "RELAYER_API_KEY":         os.getenv("RELAYER_API_KEY", ""),
    "RELAYER_API_KEY_ADDRESS": os.getenv("RELAYER_API_KEY_ADDRESS", ""),
    # Sizing
    "BASE_TRADE_SIZE":  float(os.getenv("BASE_TRADE_SIZE", "1.0")),
    "DAILY_LIMIT":      float(os.getenv("DAILY_LIMIT", "300")),  # $300 for all 3 cryptos
    # Strategy
    "MOMENTUM_THRESHOLD": float(os.getenv("MOMENTUM_THRESHOLD", "0.05")),
    "STRONG_THRESHOLD":   float(os.getenv("STRONG_THRESHOLD", "0.10")),
    # Runtime
    "DRY_RUN":  os.getenv("DRY_RUN", "false").lower() == "true",
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
log = logging.getLogger("PM-BOT-MULTI")
log.setLevel(logging.DEBUG)
_ch = logging.StreamHandler(sys.stdout);  _ch.setLevel(logging.INFO);  _ch.setFormatter(_fmt)
_ch.stream.reconfigure(encoding='utf-8', errors='replace')
_fh = logging.FileHandler(f"logs/bot_multi_{datetime.now().strftime('%Y%m%d')}.log", encoding='utf-8')
_fh.setLevel(logging.DEBUG);    _fh.setFormatter(_fmt)
log.addHandler(_ch); log.addHandler(_fh)

# ══════════════════════════════════════════════════════════════
#  STATE - Per-Crypto Tracking
# ══════════════════════════════════════════════════════════════
class CryptoState:
    def __init__(self, symbol):
        self.symbol = symbol
        self.prices = deque(maxlen=180)
        self.last_trade_ts = 0.0
        self.trades = 0
        self.last_market_id = None
        self.open_position = None  # Track open position (token_id, entry_price, signal)
        self.entry_price = None

class State:
    def __init__(self):
        self.btc = CryptoState("BTC")
        self.eth = CryptoState("ETH")
        self.sol = CryptoState("SOL")
        self.daily_spent = 0.0
        self.trades_total = 0

S = State()

# ══════════════════════════════════════════════════════════════
#  PRICE FETCHING - Multi-Crypto
# ══════════════════════════════════════════════════════════════
_bn = ccxt.binance({"enableRateLimit": True})
_cb = ccxt.coinbase({"enableRateLimit": True})

def fetch_price(symbol: str) -> Optional[float]:
    """Fetch price from Binance + Coinbase (average)"""
    try:
        bn_pair = f"{symbol}/USDT"
        cb_pair = f"{symbol}/USD"
        
        b = c = None
        try:
            b = float(_bn.fetch_ticker(bn_pair)["last"])
        except:
            pass
        try:
            c = float(_cb.fetch_ticker(cb_pair)["last"])
        except:
            pass
        
        if b and c:
            if abs(b-c)/((b+c)/2)*100 > 0.5:
                return b
            return (b+c)/2
        return b or c
    except:
        return None

# ══════════════════════════════════════════════════════════════
#  MOMENTUM ENGINE - Per-Crypto
# ══════════════════════════════════════════════════════════════
def momentum(crypto_state: CryptoState, lookback=45) -> Tuple[Optional[float], str, float]:
    """Calculate momentum for specific crypto"""
    now = time.time()
    snap = sorted([(ts,px) for ts,px in crypto_state.prices if now-ts<=lookback])
    
    if len(snap) < 4:
        return None, "NEUTRAL", 0.0

    pct = (snap[-1][1] - snap[0][1]) / snap[0][1] * 100
    abs_pct = abs(pct)

    if abs_pct < CFG["MOMENTUM_THRESHOLD"]:
        return pct, "NEUTRAL", 0.0

    signal = "UP" if pct > 0 else "DOWN"
    raw_str = min(1.0, (abs_pct - CFG["MOMENTUM_THRESHOLD"]) / (3*CFG["STRONG_THRESHOLD"]))

    ticks = [px for _,px in snap[-6:]]
    agrees = sum(1 for i in range(1,len(ticks))
                 if (signal=="UP" and ticks[i]>=ticks[i-1])
                 or (signal=="DOWN" and ticks[i]<=ticks[i-1]))
    consistency = agrees / max(len(ticks)-1, 1)
    strength = raw_str * (0.5 + 0.5*consistency)

    return pct, signal, strength

# ══════════════════════════════════════════════════════════════
#  MARKET DISCOVERY - Generic for any crypto
# ══════════════════════════════════════════════════════════════
def find_crypto_5min_market(symbol: str) -> Optional[dict]:
    """
    Find active 5-minute market for any crypto (BTC, ETH, SOL).
    Uses timestamp-based URL and HTML extraction.
    """
    try:
        import pytz
        
        et_tz = pytz.timezone('America/New_York')
        et_now = datetime.now(timezone.utc).astimezone(et_tz)
        window_start_min = (et_now.minute // 5) * 5
        window_start = et_now.replace(minute=window_start_min, second=0, microsecond=0)
        
        market_timestamp = int(window_start.timestamp())
        
        # Symbol translation for URLs
        url_map = {
            "BTC": "btc-updown-5m",
            "ETH": "eth-updown-5m",
            "SOL": "sol-updown-5m"
        }
        
        market_slug = url_map.get(symbol, f"{symbol.lower()}-updown-5m")
        market_url = f"https://polymarket.com/event/{market_slug}-{market_timestamp}"
        
        log.info(f"  Searching: {symbol} market")
        log.info(f"  URL: {market_url}")
        
        headers = {'User-Agent': 'Mozilla/5.0'}
        r = requests.get(market_url, headers=headers, timeout=10)
        r.raise_for_status()
        
        html = r.text
        
        # Extract condition ID
        cond_match = re.search(r'"conditionId":"([^"]+)"', html)
        if cond_match:
            condition_id = cond_match.group(1)
            
            # Extract token IDs
            token_match = re.search(r'"clobTokenIds":\s*\[([^\]]+)\]', html)
            if token_match:
                token_ids_str = '[' + token_match.group(1) + ']'
                token_ids = json.loads(token_ids_str)
                
                if len(token_ids) >= 2:
                    log.info(f"  FOUND {symbol} Market!")
                    return {
                        "symbol": symbol,
                        "condition_id": condition_id,
                        "yes_token": token_ids[0],
                        "no_token": token_ids[1],
                        "timestamp": market_timestamp,
                        "url": market_url
                    }
        
        log.warning(f"  {symbol} market not found")
        return None
        
    except Exception as e:
        log.warning(f"  {symbol} market search failed: {e}")
        return None

# ══════════════════════════════════════════════════════════════
#  CLOB CLIENT & ORDER EXECUTION
# ══════════════════════════════════════════════════════════════
client = None

def init_client():
    global client
    if CFG["DRY_RUN"]:
        return None
    if not CLOB_OK or not CFG["PRIVATE_KEY"]:
        return None
    try:
        client = ClobClient(
            host=CFG["CLOB"],
            key=CFG["PRIVATE_KEY"],
            chain_id=CFG["CHAIN_ID"],
            signature_type=CFG["SIGNATURE_TYPE"],
            funder=CFG["POLYMARKET_FUNDER_ADDRESS"]
        )
        
        api_creds = client.create_or_derive_api_creds()
        client.set_api_creds(api_creds)
        sig_name = "Email wallet" if CFG["SIGNATURE_TYPE"] == 1 else "EOA wallet"
        log.info(f"✅ CLOB client ready ({sig_name})")
        return client
    except Exception as e:
        log.error(f"CLOB init failed: {e}")
        return None

def place_market_order(token_id: str, side: str, amount: float = 1.0) -> Optional[dict]:
    """Execute market order"""
    if not client or CFG['DRY_RUN']:
        log.info(f"  [DRY RUN] Would trade ${amount}")
        return None
    
    try:
        order = MarketOrderArgs(
            token_id=token_id,
            amount=amount,
            side=BUY if side == "UP" else SELL,
            order_type=OrderType.FOK
        )
        signed = client.create_market_order(order)
        response = client.post_order(signed, OrderType.FOK)
        return response
    except Exception as e:
        log.error(f"  Order failed: {e}")
        return None

# ══════════════════════════════════════════════════════════════
#  MAIN TRADING LOOP
# ══════════════════════════════════════════════════════════════
def check_and_exit_reversals(btc, eth, sol, now):
    """Check if any open positions should exit due to reversal"""
    REVERSAL_THRESHOLD = 0.5  # Exit if >0.5% move against entry
    
    for crypto_state, symbol, current_price in [(S.btc, "BTC", btc), (S.eth, "ETH", eth), (S.sol, "SOL", sol)]:
        if not crypto_state.entry_price or not crypto_state.open_position:
            continue
        
        entry_price, entry_signal = crypto_state.entry_price, crypto_state.open_position
        price_change_pct = (current_price - entry_price) / entry_price * 100
        
        # Check for reversal against entry signal
        is_reversal = False
        if entry_signal == "UP" and price_change_pct < -REVERSAL_THRESHOLD:
            is_reversal = True
        elif entry_signal == "DOWN" and price_change_pct > REVERSAL_THRESHOLD:
            is_reversal = True
        
        if is_reversal:
            log.warning(f"\n  !!! REVERSAL EXIT: {symbol} {entry_signal} signal reversed!")
            log.warning(f"      Entry: ${entry_price:.2f} → Current: ${current_price:.2f} ({price_change_pct:+.2f}%)")
            crypto_state.entry_price = None
            crypto_state.open_position = None
        else:
            # Still holding, show position status
            log.debug(f"  [{symbol} HOLD] Entry: ${entry_price:.2f} → Current: ${current_price:.2f} ({price_change_pct:+.2f}%)")

def tick():
    """Main trading cycle - check all three cryptos"""
    
    now = time.time()
    ts_utc = datetime.now(timezone.utc).strftime("%H:%M:%S")
    
    # 1. FETCH PRICES for all cryptos
    btc = fetch_price("BTC")
    eth = fetch_price("ETH")
    sol = fetch_price("SOL")
    
    if not all([btc, eth, sol]):
        log.warning(f"Price fetch failed: BTC={btc} ETH={eth} SOL={sol}")
        return
    
    # 2. STORE PRICES
    S.btc.prices.append((now, btc))
    S.eth.prices.append((now, eth))
    S.sol.prices.append((now, sol))
    
    # 2.5 CHECK FOR REVERSALS & EXIT
    check_and_exit_reversals(btc, eth, sol, now)
    
    # 3. CALCULATE MOMENTUM for each
    btc_pct, btc_signal, btc_strength = momentum(S.btc)
    eth_pct, eth_signal, eth_strength = momentum(S.eth)
    sol_pct, sol_signal, sol_strength = momentum(S.sol)
    
    btc_pct_str = f"{btc_pct:+.4f}" if btc_pct is not None else "N/A"
    eth_pct_str = f"{eth_pct:+.4f}" if eth_pct is not None else "N/A"
    sol_pct_str = f"{sol_pct:+.4f}" if sol_pct is not None else "N/A"
    
    log.info(f"[{ts_utc}] BTC ${btc:,.0f} | Momentum {btc_pct_str}% {btc_signal} | Str {btc_strength:.2f}")
    log.info(f"           ETH ${eth:,.0f} | Momentum {eth_pct_str}% {eth_signal} | Str {eth_strength:.2f}")
    log.info(f"           SOL ${sol:,.0f} | Momentum {sol_pct_str}% {sol_signal} | Str {sol_strength:.2f}")
    
    # 4. CHECK DAILY LIMIT
    if S.daily_spent >= CFG["DAILY_LIMIT"]:
        log.warning(f"Daily limit reached: ${S.daily_spent:.2f}")
        return
    
    trade_size = CFG["BASE_TRADE_SIZE"]
    
    # 5. TRADE OPPORTUNITIES - Check all cryptos
    for crypto_state, symbol, signal, strength, price in [
        (S.btc, "BTC", btc_signal, btc_strength, btc),
        (S.eth, "ETH", eth_signal, eth_strength, eth),
        (S.sol, "SOL", sol_signal, sol_strength, sol),
    ]:
        # Skip if no signal
        if signal == "NEUTRAL":
            continue
        
        # Skip if too soon after last trade
        if now - crypto_state.last_trade_ts < 35:
            continue
        
        # Skip if already traded this market window
        market = find_crypto_5min_market(symbol)
        if not market:
            continue
        
        if crypto_state.last_market_id == market["condition_id"]:
            log.info(f"  Already traded {symbol} this window")
            continue
        
        # EXECUTE TRADE
        log.info(f"\n  >>> TRADING SIGNAL: {symbol} {signal}")
        log.info(f"      Momentum: {signal} | Strength: {strength:.2f}")
        
        token_id = market["yes_token"] if signal == "UP" else market["no_token"]
        response = place_market_order(token_id, signal, trade_size)
        
        if response and response.get("orderID"):
            log.info(f"  ✅ Order placed: {response['orderID'][:20]}...")
            log.info(f"     Link: {market['url']}")
            
            # Store entry info for exit monitoring
            crypto_state.entry_price = price
            crypto_state.open_position = signal
            
            crypto_state.last_trade_ts = now
            crypto_state.last_market_id = market["condition_id"]
            crypto_state.trades += 1
            S.daily_spent += trade_size
            S.trades_total += 1

# ══════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════
def main():
    log.info("""
╔════════════════════════════════════════════════════════════════╗
║    Polymarket Multi-Crypto 5-Min Momentum Bot - v3.0           ║
║    Monitoring: BTC | ETH | SOL                                 ║
╚════════════════════════════════════════════════════════════════╝
    """)
    
    if not CFG["PRIVATE_KEY"]:
        log.error("PRIVATE_KEY not set in .env")
        return
    
    if CFG["DRY_RUN"]:
        log.warning("🔵 DRY RUN MODE - No real trades will execute")
    else:
        log.warning("🔴 LIVE MODE - REAL USDC WILL BE USED")
        log.warning("Press Ctrl+C within 3 seconds to abort...")
        time.sleep(3)
    
    init_client()
    
    log.info(f"""
    Mode: {'DRY RUN' if CFG['DRY_RUN'] else 'LIVE TRADING'}
    Trade size: ${CFG['BASE_TRADE_SIZE']:.2f} per signal
    Daily limit: ${CFG['DAILY_LIMIT']:.2f}
    Check interval: {CFG['LOOP_SEC']}s
    Momentum threshold: {CFG['MOMENTUM_THRESHOLD']}%
    
    Cryptos: BTC | ETH | SOL
    Status: Ready to trade!
    """)
    
    while True:
        try:
            tick()
            time.sleep(CFG["LOOP_SEC"])
        except KeyboardInterrupt:
            log.info("\n\nBot stopped by user")
            break
        except Exception as e:
            log.error(f"Cycle error: {e}")
            time.sleep(CFG["LOOP_SEC"])

if __name__ == "__main__":
    main()
