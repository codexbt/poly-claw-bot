#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SportCap Bot — Fixed Version
Polymarket NO-side sports betting bot
"""

import argparse, json, logging, os, sys, time
from typing import Optional
import requests

try:
    from dotenv import load_dotenv
    load_dotenv(override=True)
except ImportError:
    pass

# ═══════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════
PRIVATE_KEY               = os.getenv("PRIVATE_KEY", "")
POLYMARKET_FUNDER_ADDRESS = os.getenv("POLYMARKET_FUNDER_ADDRESS", "")
CHAIN_ID                  = int(os.getenv("CHAIN_ID", "137"))
SIGNATURE_TYPE            = int(os.getenv("SIGNATURE_TYPE", "0"))

PAPER_MODE      = os.getenv("PAPER_MODE", "true").lower() in ("true", "1", "yes")
MAX_BET_USD     = float(os.getenv("MAX_BET_USD", "200"))
KELLY_FRACTION  = float(os.getenv("KELLY_FRACTION", "0.25"))   # reduced from 0.50
MIN_BET_USD     = float(os.getenv("MIN_BET_USD", "5"))
INITIAL_BALANCE = float(os.getenv("INITIAL_BALANCE", "1000"))
SLEEP_SECONDS   = int(os.getenv("SLEEP_SECONDS", "60"))

# ✅ FIXED: Correct endpoints
GAMMA_API = "https://gamma-api.polymarket.com"   # market metadata
CLOB_API  = "https://clob.polymarket.com"        # order placement

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; SportCap/1.0)"}

# ═══════════════════════════════════════════════════════════════
# COLORS + LOGGING
# ═══════════════════════════════════════════════════════════════
class C:
    R   = "\033[0m"
    B   = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[91m"
    GRN = "\033[92m"
    YLW = "\033[93m"
    CYN = "\033[96m"

def cp(msg: str):
    try:
        print(msg + C.R)
    except UnicodeEncodeError:
        print((msg + C.R).encode("ascii", errors="replace").decode())

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)
log = logging.getLogger("sport_cap")

# ═══════════════════════════════════════════════════════════════
# MARKET DISCOVERY  ✅ FIXED
# ═══════════════════════════════════════════════════════════════
SPORTS_KEYWORDS = [
    " vs ", " v ", "match", "game", "series", "final",
    "nba", "nfl", "mlb", "nhl", "epl", "ufc", "premier league",
    "cricket", "football", "basketball", "baseball", "hockey",
    "tennis", "golf", "soccer", "championship"
]

def is_sports_market(question: str) -> bool:
    """Check if market looks like a sports matchup."""
    q = question.lower()
    return any(kw in q for kw in SPORTS_KEYWORDS)

def parse_probability(market: dict) -> Optional[float]:
    """
    ✅ FIXED: Polymarket uses outcomePrices (list of strings), not 'probability'.
    outcomePrices[0] = YES price ≈ probability, outcomePrices[1] = NO price
    """
    # Try outcomePrices first (Gamma API format)
    outcome_prices = market.get("outcomePrices")
    if outcome_prices and len(outcome_prices) >= 2:
        try:
            yes_prob = float(outcome_prices[0])
            return yes_prob
        except (ValueError, TypeError):
            pass

    # Fallback: tokens list with price
    tokens = market.get("tokens", [])
    for token in tokens:
        if token.get("outcome", "").upper() == "YES":
            price = token.get("price")
            if price is not None:
                try:
                    return float(price)
                except (ValueError, TypeError):
                    pass

    # Last fallback: direct probability field
    prob = market.get("probability")
    if prob is not None:
        try:
            return float(prob)
        except (ValueError, TypeError):
            pass

    return None

def get_clob_token_ids(market: dict) -> tuple[Optional[str], Optional[str]]:
    """
    ✅ FIXED: Extract YES and NO token IDs correctly.
    Returns (yes_token_id, no_token_id)
    """
    # Format 1: clobTokenIds as list [yes_id, no_id]
    clob_ids = market.get("clobTokenIds")
    if clob_ids and len(clob_ids) >= 2:
        return str(clob_ids[0]), str(clob_ids[1])

    # Format 2: tokens list with tokenId field
    tokens = market.get("tokens", [])
    yes_id = no_id = None
    for token in tokens:
        outcome = token.get("outcome", "").upper()
        token_id = token.get("token_id") or token.get("tokenId") or token.get("id")
        if outcome == "YES":
            yes_id = str(token_id) if token_id else None
        elif outcome == "NO":
            no_id = str(token_id) if token_id else None

    return yes_id, no_id

def get_active_markets(limit: int = 100) -> list:
    """
    ✅ FIXED: Use correct Gamma API endpoint with proper params.
    """
    url = f"{GAMMA_API}/markets"
    params = {
        "active":   "true",
        "closed":   "false",
        "limit":    limit,
        "order":    "volume",
        "ascending":"false",
    }
    try:
        resp = requests.get(url, params=params, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        # Gamma API returns a list directly
        if isinstance(data, list):
            markets = data
        elif isinstance(data, dict):
            markets = data.get("markets", data.get("data", []))
        else:
            markets = []

        cp(f"{C.CYN}Fetched {len(markets)} active markets")
        return markets

    except requests.exceptions.RequestException as e:
        log.error(f"Market fetch failed: {e}")
        return []
    except json.JSONDecodeError as e:
        log.error(f"Market JSON parse failed: {e}")
        return []

# ═══════════════════════════════════════════════════════════════
# BETTING LOGIC  ✅ FIXED
# ═══════════════════════════════════════════════════════════════
def calculate_kelly_bet(yes_prob: float, bankroll: float) -> float:
    """
    ✅ FIXED: Kelly for binary Polymarket (price = probability, payout = 1/price).
    We bet NO, so our edge is: we think YES prob is overestimated.
    
    For NO bet:
      - NO price (cost)  = 1 - yes_prob
      - Payout if NO wins = $1 per share
      - Our edge (b) = (1 / no_price) - 1 = yes_prob / (1 - yes_prob)
      - Kelly % = (p_no * b - p_yes) / b  where p_no = 1 - yes_prob, p_yes = yes_prob
    
    Simplified for binary: kelly_pct = p_no - (p_yes / b)
    """
    no_prob  = 1.0 - yes_prob
    no_price = 1.0 - yes_prob   # cost per NO share on Polymarket

    if no_price <= 0 or no_price >= 1:
        return 0.0

    # Net odds for NO bet (profit per $1 risked)
    b = (1.0 / no_price) - 1.0  # e.g. if no_price=0.4, b=1.5

    kelly_pct = (no_prob * b - yes_prob) / b
    if kelly_pct <= 0:
        return 0.0

    fractional_kelly = kelly_pct * KELLY_FRACTION
    bet = fractional_kelly * bankroll
    return round(min(bet, MAX_BET_USD), 2)

# ═══════════════════════════════════════════════════════════════
# ORDER PLACEMENT
# ═══════════════════════════════════════════════════════════════
def place_bet(market: dict, yes_token_id: str, no_token_id: str,
              amount: float) -> bool:
    question = market.get("question", "Unknown market")

    if PAPER_MODE:
        cp(f"{C.YLW}[PAPER] Would bet ${amount:.2f} NO → {question}")
        return True

    # ── Live mode ──────────────────────────────────────────────
    try:
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import MarketOrderArgs
        from py_clob_client.order_builder.constants import BUY

        if not PRIVATE_KEY:
            log.error("PRIVATE_KEY not set — cannot place live order")
            return False

        client = ClobClient(
            host=CLOB_API,
            key=PRIVATE_KEY,
            chain_id=CHAIN_ID,
            signature_type=SIGNATURE_TYPE,
            funder=POLYMARKET_FUNDER_ADDRESS or None,
        )

        # Buying NO token = betting NO
        order_args = MarketOrderArgs(
            token_id=no_token_id,
            amount=amount,
        )

        signed_order = client.create_market_order(order_args)
        resp = client.post_order(signed_order, orderType=None)
        cp(f"{C.GRN}[LIVE] Placed ${amount:.2f} NO order in: {question}")
        log.info(f"Order response: {resp}")
        return True

    except ImportError:
        log.error("py-clob-client not installed. Run: pip install py-clob-client")
        return False
    except Exception as e:
        log.error(f"Live bet failed for '{question}': {e}")
        return False

# ═══════════════════════════════════════════════════════════════
# MAIN LOOP
# ═══════════════════════════════════════════════════════════════
def print_banner(paper: bool):
    mode_str = f"{C.YLW}PAPER MODE" if paper else f"{C.RED}LIVE MODE ⚠"
    cp(f"""
{C.B}╔══════════════════════════════════════╗
║       SportCap Betting Bot           ║
║  Mode : {mode_str}{C.B}
║  Kelly: {KELLY_FRACTION:.0%} fraction              ║
║  Max  : ${MAX_BET_USD:.0f} per bet                ║
╚══════════════════════════════════════╝""")

def main():
    global PAPER_MODE

    parser = argparse.ArgumentParser(description="SportCap — Polymarket sports bot")
    parser.add_argument("--live",  action="store_true", help="Enable live trading")
    parser.add_argument("--paper", action="store_true", help="Force paper mode")
    parser.add_argument("--once",  action="store_true", help="Run one cycle then exit")
    args = parser.parse_args()

    if args.live:
        PAPER_MODE = False
    elif args.paper:
        PAPER_MODE = True

    if not PAPER_MODE and not PRIVATE_KEY:
        cp(f"{C.RED}ERROR: PRIVATE_KEY not set. Use --paper or set PRIVATE_KEY env var.")
        sys.exit(1)

    print_banner(PAPER_MODE)

    balance = INITIAL_BALANCE
    cycle   = 0

    while True:
        cycle += 1
        cp(f"\n{C.B}── Cycle {cycle} | Balance: ${balance:.2f} ──────────────────")

        markets = get_active_markets()

        if not markets:
            cp(f"{C.RED}No markets returned — check network / API. Retrying in {SLEEP_SECONDS}s...")
            time.sleep(SLEEP_SECONDS)
            continue

        bets_this_cycle = 0

        for market in markets:
            question = market.get("question", "")
            if not question:
                continue

            # ── Filter: sports only ──────────────────────────
            if not is_sports_market(question):
                continue

            # ── Parse probability ────────────────────────────
            yes_prob = parse_probability(market)
            if yes_prob is None:
                log.debug(f"No probability for: {question}")
                continue

            # Clamp to valid range
            yes_prob = max(0.01, min(0.99, yes_prob))
            no_prob  = 1.0 - yes_prob

            # ── Only bet NO when YES is overpriced (YES prob > 0.5) ──
            if yes_prob <= 0.5:
                continue   # NO isn't favored enough to have edge

            # ── Calculate bet ────────────────────────────────
            bet_amount = calculate_kelly_bet(yes_prob, balance)
            if bet_amount < MIN_BET_USD:
                log.debug(f"Bet too small (${bet_amount:.2f}) for: {question}")
                continue

            # ── Get token IDs ────────────────────────────────
            yes_token, no_token = get_clob_token_ids(market)
            if not no_token:
                log.warning(f"No token ID found for: {question}")
                continue

            # ── Display opportunity ──────────────────────────
            cp(f"\n{C.CYN}Market   : {question}")
            cp(f"{C.CYN}YES Prob : {yes_prob:.1%}  |  NO Prob: {no_prob:.1%}")
            cp(f"{C.CYN}Bet Size : ${bet_amount:.2f} on NO")

            # ── Place bet ────────────────────────────────────
            success = place_bet(market, yes_token, no_token, bet_amount)

            if success:
                bets_this_cycle += 1
                if not PAPER_MODE:
                    balance -= bet_amount
                    cp(f"{C.GRN}Balance after bet: ${balance:.2f}")

        cp(f"\n{C.DIM}Cycle {cycle} done — {bets_this_cycle} bet(s) placed. "
           f"Sleeping {SLEEP_SECONDS}s...")

        if args.once:
            cp(f"{C.B}--once flag set, exiting.")
            break

        time.sleep(SLEEP_SECONDS)

if __name__ == "__main__":
    main()