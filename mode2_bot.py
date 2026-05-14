"""
Polymarket Mode 2 Trading Bot
Assets: BTC, ETH, SOL, XRP, DOGE, BNB, HYPE
Timeframe: 5-minute candles
Logic: Dynamic sizing based on momentum + candle pattern + market confirmation
"""

import os
import time
import logging
import json
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional
import requests

# ─── Logging Setup ────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("bot.log"),
    ],
)
log = logging.getLogger("mode2_bot")

# ─── Config from .env ─────────────────────────────────────────────────────────
POLYMARKET_API_KEY    = os.getenv("POLYMARKET_API_KEY", "")
POLYMARKET_SECRET     = os.getenv("POLYMARKET_SECRET", "")
POLYMARKET_PASSPHRASE = os.getenv("POLYMARKET_PASSPHRASE", "")
CLOB_BASE_URL         = os.getenv("CLOB_BASE_URL", "https://clob.polymarket.com")
GAMMA_BASE_URL        = os.getenv("GAMMA_BASE_URL", "https://gamma-api.polymarket.com")

# Dry-run: set to False only when you are ready to place real orders
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"

# ─── Mode 2 Parameters (tune here) ───────────────────────────────────────────
PRICE_THRESHOLD  = float(os.getenv("PRICE_THRESHOLD",  "0.86"))  # 86¢ min
TOTAL_GATE       = float(os.getenv("TOTAL_GATE",       "0.40"))  # min score
MIN_SIZE         = float(os.getenv("MIN_SIZE",         "1.00"))
MAX_SIZE         = float(os.getenv("MAX_SIZE",         "3.00"))
W_MOM            = float(os.getenv("W_MOM",            "0.40"))  # momentum weight
W_CANDLE         = float(os.getenv("W_CANDLE",         "0.35"))  # candle weight
W_MARKET         = float(os.getenv("W_MARKET",         "0.25"))  # market weight
SCAN_INTERVAL    = int(os.getenv("SCAN_INTERVAL",      "300"))   # seconds (5 min)
MAX_OPEN_TRADES  = int(os.getenv("MAX_OPEN_TRADES",    "3"))     # concurrent cap
MOM_SCORE_CUTOFF = float(os.getenv("MOM_SCORE_CUTOFF", "0.30"))  # Mode 2 only when mom < this

# Asset → Polymarket market slug mapping (5-min BTC/ETH/SOL/XRP/DOGE/BNB/HYPE)
# These are the "will X be above Y at time Z" binary markets.
# Slugs must be kept current; run `python mode2_bot.py --list-markets` to refresh.
ASSET_MARKET_MAP: dict[str, str] = {
    "BTC":  os.getenv("MARKET_BTC",  ""),
    "ETH":  os.getenv("MARKET_ETH",  ""),
    "SOL":  os.getenv("MARKET_SOL",  ""),
    "XRP":  os.getenv("MARKET_XRP",  ""),
    "DOGE": os.getenv("MARKET_DOGE", ""),
    "BNB":  os.getenv("MARKET_BNB",  ""),
    "HYPE": os.getenv("MARKET_HYPE", ""),
}

# Candle pattern → base score
PATTERN_SCORES: dict[str, float] = {
    "BULLISH_ENGULF":  0.25,
    "BEARISH_ENGULF":  0.25,
    "HAMMER":          0.20,
    "SHOOTING_STAR":   0.20,
    "THREE_BULL":      0.15,
    "THREE_BEAR":      0.15,
    "PLAIN":           0.00,
    "DOJI":           -0.15,
}

# ─── Data Classes ─────────────────────────────────────────────────────────────
@dataclass
class Candle:
    open:   float
    high:   float
    low:    float
    close:  float
    volume: float = 0.0

@dataclass
class MarketInfo:
    asset:       str
    condition_id: str
    yes_token_id: str
    no_token_id:  str
    yes_price:    float = 0.0
    no_price:     float = 0.0
    description:  str = ""

@dataclass
class TradeSignal:
    asset:        str
    signal:       str          # "UP" or "DOWN"
    token_id:     str
    price:        float
    size_usd:     float
    total_score:  float
    m_score:      float
    c_score:      float
    p_score:      float
    market_score: float
    reason:       str
    pattern:      str = "PLAIN"

@dataclass
class BotState:
    open_trades: list[str] = field(default_factory=list)   # condition_ids
    trade_count: int = 0
    total_profit: float = 0.0


# ─── Candle / Price Feed ───────────────────────────────────────────────────────
class PriceFeed:
    """
    Fetches OHLC data from Binance public API (no key needed).
    Falls back to CoinGecko simple price for YES/NO price (you need Polymarket CLOB).
    """

    BINANCE_URL = "https://api.binance.com/api/v3/klines"

    BINANCE_SYMBOLS = {
        "BTC":  "BTCUSDT",
        "ETH":  "ETHUSDT",
        "SOL":  "SOLUSDT",
        "XRP":  "XRPUSDT",
        "DOGE": "DOGEUSDT",
        "BNB":  "BNBUSDT",
        "HYPE": "HYPEUSDT",
    }

    def get_candles(self, asset: str, limit: int = 10) -> list[Candle]:
        symbol = self.BINANCE_SYMBOLS.get(asset)
        if not symbol:
            log.warning(f"No Binance symbol for {asset}")
            return []
        try:
            r = requests.get(
                self.BINANCE_URL,
                params={"symbol": symbol, "interval": "5m", "limit": limit},
                timeout=10,
            )
            r.raise_for_status()
            raw = r.json()
            candles = []
            for k in raw:
                candles.append(Candle(
                    open=float(k[1]),
                    high=float(k[2]),
                    low=float(k[3]),
                    close=float(k[4]),
                    volume=float(k[5]),
                ))
            return candles
        except Exception as e:
            log.error(f"PriceFeed error for {asset}: {e}")
            return []


# ─── Polymarket CLOB Client ────────────────────────────────────────────────────
class PolymarketClient:
    """
    Thin wrapper around Polymarket CLOB REST API.
    Auth uses L2 headers (API key + HMAC).
    """

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
            "POLY_API_KEY": POLYMARKET_API_KEY,
        })

    def _auth_headers(self, method: str, path: str, body: str = "") -> dict:
        """
        Polymarket CLOB uses HMAC-SHA256 L2 auth.
        Signature = HMAC(secret, timestamp + method + path + body)
        """
        import hmac, hashlib, base64
        ts = str(int(time.time() * 1000))
        msg = ts + method.upper() + path + body
        sig = hmac.new(
            POLYMARKET_SECRET.encode(),
            msg.encode(),
            hashlib.sha256,
        ).digest()
        sig_b64 = base64.b64encode(sig).decode()
        return {
            "POLY_TIMESTAMP":  ts,
            "POLY_SIGNATURE":  sig_b64,
            "POLY_PASSPHRASE": POLYMARKET_PASSPHRASE,
        }

    def get_market(self, condition_id: str) -> Optional[dict]:
        path = f"/markets/{condition_id}"
        try:
            r = self.session.get(
                CLOB_BASE_URL + path,
                headers=self._auth_headers("GET", path),
                timeout=10,
            )
            r.raise_for_status()
            return r.json()
        except Exception as e:
            log.error(f"get_market error: {e}")
            return None

    def get_orderbook(self, token_id: str) -> Optional[dict]:
        path = f"/book?token_id={token_id}"
        try:
            r = self.session.get(
                CLOB_BASE_URL + path,
                headers=self._auth_headers("GET", path),
                timeout=10,
            )
            r.raise_for_status()
            return r.json()
        except Exception as e:
            log.error(f"get_orderbook error: {e}")
            return None

    def get_best_price(self, token_id: str) -> Optional[float]:
        book = self.get_orderbook(token_id)
        if not book:
            return None
        bids = book.get("bids", [])
        if bids:
            return float(bids[0]["price"])
        return None

    def place_market_order(self, token_id: str, side: str, size_usd: float) -> Optional[dict]:
        """
        side: "BUY"
        size_usd: dollar amount
        Returns order response dict or None on failure.
        """
        path = "/order"
        payload = json.dumps({
            "tokenID":   token_id,
            "side":      side,
            "type":      "MARKET",
            "amount":    round(size_usd, 2),
            "timeInForce": "FOK",
        })
        headers = self._auth_headers("POST", path, payload)
        if DRY_RUN:
            log.info(f"[DRY RUN] Would place order: token={token_id} side={side} size=${size_usd:.2f}")
            return {"dry_run": True, "token_id": token_id, "size": size_usd}
        try:
            r = self.session.post(
                CLOB_BASE_URL + path,
                data=payload,
                headers=headers,
                timeout=10,
            )
            r.raise_for_status()
            return r.json()
        except Exception as e:
            log.error(f"place_order error: {e}")
            return None

    def search_markets(self, keyword: str, limit: int = 5) -> list[dict]:
        try:
            r = requests.get(
                GAMMA_BASE_URL + "/markets",
                params={"search": keyword, "limit": limit, "active": True},
                timeout=10,
            )
            r.raise_for_status()
            return r.json().get("data", [])
        except Exception as e:
            log.error(f"search_markets error: {e}")
            return []


# ─── Candle Analysis Engine ────────────────────────────────────────────────────
class CandleAnalyzer:

    def detect_pattern(self, candles: list[Candle]) -> tuple[str, float]:
        """Returns (pattern_name, raw_candle_score)"""
        if len(candles) < 3:
            return "PLAIN", 0.0

        c   = candles[-1]   # latest
        c1  = candles[-2]   # previous
        c2  = candles[-3]   # two back

        body      = abs(c.close - c.open)
        total_rng = c.high - c.low if c.high != c.low else 0.0001
        body_ratio = body / total_rng

        # Engulfing
        bullish_body = c.close - c.open if c.close > c.open else 0
        bearish_body = c1.open - c1.close if c1.open > c1.close else 0
        if bullish_body > 0 and bearish_body > 0 and bullish_body > bearish_body:
            pattern = "BULLISH_ENGULF"
        elif c.open > c.close and (c1.close - c1.open) > 0:
            bull_prev = c1.close - c1.open
            bear_cur  = c.open - c.close
            if bear_cur > bull_prev:
                pattern = "BEARISH_ENGULF"
            else:
                pattern = "PLAIN"
        # Hammer / Shooting Star
        elif c.close > c.open:
            lower_wick = c.open - c.low
            if lower_wick > body * 2:
                pattern = "HAMMER"
            else:
                pattern = "PLAIN"
        elif c.open > c.close:
            upper_wick = c.high - c.open
            if upper_wick > body * 2:
                pattern = "SHOOTING_STAR"
            else:
                pattern = "PLAIN"
        # Doji
        elif body_ratio < 0.1:
            pattern = "DOJI"
        else:
            pattern = "PLAIN"

        # Three consecutive
        if len(candles) >= 4:
            last3 = candles[-3:]
            if all(c.close > c.open for c in last3):
                pattern = "THREE_BULL"
            elif all(c.close < c.open for c in last3):
                pattern = "THREE_BEAR"

        base_score = PATTERN_SCORES.get(pattern, 0.0)

        # Consecutive run score (max 4 candles = 1.0)
        run = 0
        direction = 1 if candles[-1].close > candles[-1].open else -1
        for cv in reversed(candles):
            d = 1 if cv.close > cv.open else -1
            if d == direction:
                run += 1
            else:
                break
        run_score = min(run / 4.0, 1.0)

        candle_score = base_score + body_ratio * 0.30 + run_score * 0.25
        candle_score = max(0.0, min(1.0, candle_score))

        return pattern, candle_score

    def momentum_score(self, candles: list[Candle]) -> tuple[str, float]:
        """
        Returns (direction, score 0-1).
        Uses rate of change over last 3 candles.
        """
        if len(candles) < 4:
            return "FLAT", 0.0
        closes = [c.close for c in candles[-4:]]
        pct = (closes[-1] - closes[0]) / closes[0] if closes[0] != 0 else 0
        score = min(abs(pct) / 0.01, 1.0)   # normalise: 1% move = score 1.0
        direction = "UP" if pct > 0 else "DOWN"
        return direction, round(score, 4)


# ─── Mode 2 Scoring Engine ─────────────────────────────────────────────────────
class Mode2Engine:

    def evaluate(
        self,
        signal:       str,
        yes_price:    float,
        no_price:     float,
        mom_score:    float,
        candle_score: float,
        pattern:      str,
    ) -> Optional['TradeSignal']:

        token_price = yes_price if signal == "UP" else no_price

        # ── Gate 1: 86¢ threshold ──
        if token_price < PRICE_THRESHOLD:
            log.debug(f"  SKIP: {signal} token at {token_price:.2f} < {PRICE_THRESHOLD}")
            return None

        # ── Factor 1: Momentum (40%) ──
        m_score = mom_score * W_MOM

        # ── Factor 2: Candle (35%) ──
        c_score = candle_score * W_CANDLE

        # ── Factor 3: Market confirmation (25%) ──
        price_confirms = (signal == "UP" and yes_price >= PRICE_THRESHOLD) or \
                         (signal == "DOWN" and no_price >= PRICE_THRESHOLD)
        if not price_confirms:
            log.debug(f"  BLOCKED: price doesn't confirm {signal}")
            return None

        excess = token_price - PRICE_THRESHOLD
        market_score = min(1.0, 0.5 + excess / 0.14)
        p_score = market_score * W_MARKET

        total_score = m_score + c_score + p_score

        # ── Gate 2: minimum total score ──
        if total_score < TOTAL_GATE:
            log.debug(f"  SKIP: total_score={total_score:.3f} < {TOTAL_GATE}")
            return None

        # ── Sizing ──
        if total_score < 0.75:
            size = MIN_SIZE + ((total_score - TOTAL_GATE) / 0.35) * 1.0
        else:
            size = 2.0 + ((total_score - 0.75) / 0.25) * 1.0
        size = round(min(MAX_SIZE, max(MIN_SIZE, size)), 2)

        reason = (
            f"mom={mom_score:.2f} | candle={candle_score:.2f}({pattern}) | "
            f"mkt={market_score:.2f}({'YES' if signal=='UP' else 'NO'}@{token_price:.2f}) | "
            f"TOTAL={total_score:.2f} | SIZE=${size}"
        )

        return TradeSignal(
            asset="",
            signal=signal,
            token_id="",
            price=token_price,
            size_usd=size,
            total_score=total_score,
            m_score=m_score,
            c_score=c_score,
            p_score=p_score,
            market_score=market_score,
            reason=reason,
            pattern=pattern,
        )


# ─── Main Bot Loop ─────────────────────────────────────────────────────────────
class Mode2Bot:

    def __init__(self):
        self.feed     = PriceFeed()
        self.analyzer = CandleAnalyzer()
        self.engine   = Mode2Engine()
        self.client   = PolymarketClient()
        self.state    = BotState()

    def scan_asset(self, asset: str, market: MarketInfo) -> Optional[TradeSignal]:
        log.info(f"  Scanning {asset} ...")

        candles = self.feed.get_candles(asset, limit=10)
        if len(candles) < 4:
            log.warning(f"  Not enough candles for {asset}")
            return None

        mom_dir, mom_score = self.analyzer.momentum_score(candles)
        pattern, candle_score = self.analyzer.detect_pattern(candles)

        yes_p = market.yes_price
        no_p  = market.no_price

        log.info(
            f"  {asset}: mom={mom_dir}/{mom_score:.2f} | "
            f"pattern={pattern}/{candle_score:.2f} | "
            f"YES={yes_p:.2f} NO={no_p:.2f}"
        )

        # Determine signal from market prices
        if yes_p >= PRICE_THRESHOLD:
            signal = "UP"
        elif no_p >= PRICE_THRESHOLD:
            signal = "DOWN"
        else:
            log.debug(f"  {asset}: neither token at threshold, skip")
            return None

        # Mode 2 only activates when momentum is weak or misaligned
        if mom_score >= MOM_SCORE_CUTOFF and mom_dir == signal:
            log.info(f"  {asset}: Mode 1 territory (mom confirms), deferring to Mode 1")
            return None

        result = self.engine.evaluate(
            signal=signal,
            yes_price=yes_p,
            no_price=no_p,
            mom_score=mom_score,
            candle_score=candle_score,
            pattern=pattern,
        )
        if result:
            result.asset    = asset
            result.token_id = market.yes_token_id if signal == "UP" else market.no_token_id
        return result

    def fetch_market_prices(self, asset: str, market_id: str) -> Optional[MarketInfo]:
        """Fetch live YES/NO prices from Polymarket CLOB."""
        data = self.client.get_market(market_id)
        if not data:
            return None
        tokens = data.get("tokens", [])
        yes_tok = next((t for t in tokens if t.get("outcome", "").upper() == "YES"), None)
        no_tok  = next((t for t in tokens if t.get("outcome", "").upper() == "NO"),  None)
        if not yes_tok or not no_tok:
            return None

        yes_price = self.client.get_best_price(yes_tok["token_id"]) or 0.0
        no_price  = self.client.get_best_price(no_tok["token_id"])  or 0.0

        return MarketInfo(
            asset=asset,
            condition_id=market_id,
            yes_token_id=yes_tok["token_id"],
            no_token_id=no_tok["token_id"],
            yes_price=yes_price,
            no_price=no_price,
            description=data.get("question", ""),
        )

    def run_cycle(self):
        log.info("=" * 60)
        log.info(f"Cycle start: {datetime.now(timezone.utc).isoformat()} | DRY_RUN={DRY_RUN}")
        log.info(f"Open trades: {len(self.state.open_trades)}/{MAX_OPEN_TRADES}")

        signals: list[TradeSignal] = []

        for asset, market_id in ASSET_MARKET_MAP.items():
            if not market_id:
                log.warning(f"  {asset}: MARKET_ID not set in .env, skipping")
                continue
            if market_id in self.state.open_trades:
                log.info(f"  {asset}: already in open trade, skip")
                continue

            market = self.fetch_market_prices(asset, market_id)
            if not market:
                log.warning(f"  {asset}: could not fetch market data")
                continue

            sig = self.scan_asset(asset, market)
            if sig:
                signals.append(sig)

        # Sort by total_score descending, take best ones up to capacity
        signals.sort(key=lambda s: s.total_score, reverse=True)
        capacity = MAX_OPEN_TRADES - len(self.state.open_trades)
        to_trade = signals[:capacity]

        if not to_trade:
            log.info("No Mode 2 signals this cycle.")
        else:
            for sig in to_trade:
                log.info(f"  SIGNAL: {sig.asset} {sig.signal} ${sig.size_usd:.2f} | {sig.reason}")
                resp = self.client.place_market_order(sig.token_id, "BUY", sig.size_usd)
                if resp:
                    self.state.open_trades.append(sig.asset)
                    self.state.trade_count += 1
                    log.info(f"  ORDER PLACED: {resp}")
                else:
                    log.error(f"  ORDER FAILED for {sig.asset}")

        log.info(f"Cycle done. Total trades so far: {self.state.trade_count}")

    def run(self):
        log.info("Mode 2 Bot started.")
        log.info(f"Assets: {list(ASSET_MARKET_MAP.keys())}")
        log.info(f"Params: threshold={PRICE_THRESHOLD} gate={TOTAL_GATE} "
                 f"weights={W_MOM}/{W_CANDLE}/{W_MARKET} "
                 f"size=${MIN_SIZE}-${MAX_SIZE}")
        while True:
            try:
                self.run_cycle()
            except KeyboardInterrupt:
                log.info("Bot stopped by user.")
                break
            except Exception as e:
                log.error(f"Unhandled error in cycle: {e}", exc_info=True)
            log.info(f"Sleeping {SCAN_INTERVAL}s until next cycle...")
            time.sleep(SCAN_INTERVAL)


# ─── CLI Helpers ───────────────────────────────────────────────────────────────
def list_markets():
    """Helper: search and print active 5-min markets for each asset."""
    client = PolymarketClient()
    for asset in ASSET_MARKET_MAP:
        print(f"\n── {asset} ──")
        results = client.search_markets(f"{asset} 5 minute", limit=5)
        for m in results:
            print(f"  {m.get('conditionId','')} | {m.get('question','')[:80]}")


# ─── Entry Point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    if "--list-markets" in sys.argv:
        list_markets()
    else:
        bot = Mode2Bot()
        bot.run()
