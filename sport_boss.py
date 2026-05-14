#!/usr/bin/env python3
"""
SPORT BOSS v1.0 — Polymarket Sports Bot with Markov Chain Price Mispricing
============================================================================

Core Logic (CemeterySun-style):
- Scan active sports markets every 30-60 seconds via Gamma/CLOB API
- For each market: get YES price (midpoint), NO price = 1 - YES price
- Discretize price into 10 bins [0-0.1, 0.1-0.2, ..., 0.9-1.0]
- Maintain 3x3 or 5x5 transition matrix from last N price changes
- Compute stationary distribution OR diagonal persistence probability
- Fair_NO_prob = Markov model probability
- Edge = fair_NO_prob - current_NO_price
- ONLY bet when: Edge > 0.08, Diagonal persistence >= 0.87,
  Volume > $50K, Price between 0.10-0.90

Features:
- Color-coded signals display
- Trade logging to file
- Single-file, under 300 lines
"""

import os
import sys
import time
import json
import logging
import requests
import threading
from datetime import datetime, timezone
from collections import defaultdict, deque
from typing import Dict, List, Optional, Tuple
import numpy as np

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ═══════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════

PRIVATE_KEY = os.getenv("PRIVATE_KEY", "")
POLYMARKET_FUNDER_ADDRESS = os.getenv("POLYMARKET_FUNDER_ADDRESS", "")
CHAIN_ID = int(os.getenv("CHAIN_ID", "137"))
SIGNATURE_TYPE = int(os.getenv("SIGNATURE_TYPE", "0"))

MIN_EDGE = 0.08
DIAGONAL_PERSISTENCE_MIN = 0.87
MIN_VOLUME = 50000
MIN_PRICE = 0.10
MAX_PRICE = 0.90

SCAN_INTERVAL = 45  # seconds
HISTORY_LENGTH = 50  # price history points
MATRIX_SIZE = 5     # 5x5 transition matrix
TRADE_SIZE_USD = 50.0

LOG_FILE = "sport_boss_trades.log"
STATE_FILE = "sport_boss_state.json"

# APIs
GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"

# ═══════════════════════════════════════════════════════════════
# COLORS
# ═══════════════════════════════════════════════════════════════

class C:
    R = "\033[0m"
    B = "\033[1m"
    RED = "\033[91m"
    GRN = "\033[92m"
    YLW = "\033[93m"
    CYN = "\033[96m"
    WHT = "\033[97m"
    BGRED = "\033[41m"
    BGGRN = "\033[42m"
    BGBLU = "\033[44m"

def cp(msg: str):
    print(msg + C.R)

def log_trade(msg: str):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[{ts}] {msg}"
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    cp(msg)

# ═══════════════════════════════════════════════════════════════
# MARKOV CHAIN LOGIC
# ═══════════════════════════════════════════════════════════════

class MarkovChain:
    def __init__(self, matrix_size: int = 5):
        self.matrix_size = matrix_size
        self.bins = np.linspace(0, 1, matrix_size + 1)
        self.transition_matrix = np.ones((matrix_size, matrix_size)) / matrix_size  # Uniform prior
        self.price_history: deque = deque(maxlen=HISTORY_LENGTH)

    def discretize_price(self, price: float) -> int:
        """Discretize price into bins [0, matrix_size)"""
        if price <= 0:
            return 0
        if price >= 1:
            return self.matrix_size - 1
        for i in range(self.matrix_size):
            if self.bins[i] <= price < self.bins[i + 1]:
                return i
        return self.matrix_size - 1

    def update_transition_matrix(self, price: float):
        """Update transition matrix with new price"""
        state = self.discretize_price(price)
        self.price_history.append(state)

        if len(self.price_history) >= 2:
            prev_state = self.price_history[-2]
            self.transition_matrix[prev_state, state] += 1

            # Normalize rows
            for i in range(self.matrix_size):
                row_sum = np.sum(self.transition_matrix[i])
                if row_sum > 0:
                    self.transition_matrix[i] /= row_sum

    def get_stationary_distribution(self) -> np.ndarray:
        """Compute stationary distribution via power iteration"""
        if len(self.price_history) < 10:
            return np.ones(self.matrix_size) / self.matrix_size

        # Power iteration
        pi = np.ones(self.matrix_size) / self.matrix_size
        for _ in range(100):
            pi = pi @ self.transition_matrix
            pi /= np.sum(pi)
        return pi

    def get_diagonal_persistence(self) -> float:
        """Average diagonal elements (probability of staying in same state)"""
        return np.mean(np.diag(self.transition_matrix))

    def get_fair_probability(self, current_price: float) -> float:
        """Get fair NO probability from Markov model"""
        current_state = self.discretize_price(current_price)
        stationary = self.get_stationary_distribution()

        # Fair prob is weighted average of stationary distribution
        # Higher states (higher prices) favor YES, lower favor NO
        fair_yes_prob = np.sum(stationary * np.linspace(0, 1, self.matrix_size))
        return 1 - fair_yes_prob

# ═══════════════════════════════════════════════════════════════
# MARKET DATA
# ═══════════════════════════════════════════════════════════════

class MarketData:
    def __init__(self, market_info: dict):
        self.condition_id = market_info.get("conditionId", "")
        self.title = market_info.get("title", "")
        self.volume = float(market_info.get("volume", 0))
        self.end_date = market_info.get("endDate", "")
        self.markov = MarkovChain(MATRIX_SIZE)
        self.last_price = 0.0
        self.price_history: deque = deque(maxlen=HISTORY_LENGTH)

    def update_price(self, yes_price: float):
        """Update price and Markov chain"""
        if yes_price != self.last_price:
            self.price_history.append(yes_price)
            self.markov.update_transition_matrix(yes_price)
            self.last_price = yes_price

    def get_edge(self) -> Tuple[float, float, float]:
        """Get edge, diagonal persistence, and fair NO prob"""
        if not self.price_history:
            return 0.0, 0.0, 0.5

        current_yes = self.price_history[-1]
        current_no = 1 - current_yes

        fair_no_prob = self.markov.get_fair_probability(current_yes)
        edge = fair_no_prob - current_no
        diagonal_persistence = self.markov.get_diagonal_persistence()

        return edge, diagonal_persistence, fair_no_prob

# ═══════════════════════════════════════════════════════════════
# API FUNCTIONS
# ═══════════════════════════════════════════════════════════════

def get_active_sports_markets() -> List[dict]:
    """Fetch active sports markets from Gamma API"""
    try:
        url = f"{GAMMA_API}/markets?closed=false&limit=200"
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, headers=headers, timeout=10)

        if response.status_code != 200:
            return []

        markets = response.json()
        sports_markets = []

        for market in markets:
            title = market.get("title", "").lower()
            # Filter for sports markets
            if any(sport in title for sport in ["nba", "nfl", "mlb", "nhl", "soccer", "football", "basketball", "baseball", "hockey", "championship", "finals", "playoffs", "win", "beat", "defeat"]):
                sports_markets.append(market)

        return sports_markets[:50]  # Limit to 50 markets

    except Exception as e:
        cp(f"{C.RED}Error fetching markets: {e}{C.R}")
        return []

def get_market_price(condition_id: str) -> Optional[float]:
    """Get midpoint YES price for a market"""
    try:
        # Try to get tokens first
        market_url = f"{GAMMA_API}/markets/{condition_id}"
        response = requests.get(market_url, timeout=5)

        if response.status_code != 200:
            return None

        market_data = response.json()
        tokens = market_data.get("tokens", [])

        if len(tokens) < 2:
            return None

        yes_token = tokens[0]["tokenId"]

        # Get price from CLOB
        price_url = f"{CLOB_API}/midpoint?token_id={yes_token}"
        price_response = requests.get(price_url, timeout=5)

        if price_response.status_code == 200:
            price_data = price_response.json()
            return float(price_data.get("mid", 0))

    except Exception:
        pass

    return None

# ═══════════════════════════════════════════════════════════════
# TRADING LOGIC
# ═══════════════════════════════════════════════════════════════

def should_trade(market: MarketData, yes_price: float) -> Tuple[bool, str]:
    """Check if we should trade this market"""
    if yes_price < MIN_PRICE or yes_price > MAX_PRICE:
        return False, f"Price {yes_price:.3f} outside {MIN_PRICE}-{MAX_PRICE}"

    if market.volume < MIN_VOLUME:
        return False, f"Volume ${market.volume:,.0f} < ${MIN_VOLUME:,}"

    edge, diagonal_persistence, fair_no_prob = market.get_edge()

    if edge <= MIN_EDGE:
        return False, f"Edge {edge:.3f} <= {MIN_EDGE}"

    if diagonal_persistence < DIAGONAL_PERSISTENCE_MIN:
        return False, f"Persistence {diagonal_persistence:.3f} < {DIAGONAL_PERSISTENCE_MIN}"

    return True, f"Edge: {edge:.3f}, Persistence: {diagonal_persistence:.3f}"

def execute_trade(market: MarketData, direction: str, amount: float):
    """Execute a trade (placeholder - implement actual trading)"""
    log_trade(f"{C.BGGRN}{C.WHT}{C.B} TRADE EXECUTED {C.R} {direction} ${amount:.2f} on {market.title[:50]}")

# ═══════════════════════════════════════════════════════════════
# MAIN BOT LOOP
# ═══════════════════════════════════════════════════════════════

def main():
    cp(f"{C.B}{C.CYN}SPORT BOSS v1.0 - Markov Chain Sports Bot{C.R}")
    cp(f"Scanning every {SCAN_INTERVAL}s | Min Edge: {MIN_EDGE} | Min Persistence: {DIAGONAL_PERSISTENCE_MIN}")
    cp(f"Min Volume: ${MIN_VOLUME:,} | Price Range: {MIN_PRICE}-{MAX_PRICE}")
    cp("=" * 80)

    market_cache: Dict[str, MarketData] = {}

    while True:
        try:
            # Fetch active markets
            markets = get_active_sports_markets()
            cp(f"\n{C.B}📊 Found {len(markets)} active sports markets{C.R}")

            signals_found = 0

            for market_info in markets:
                condition_id = market_info.get("conditionId", "")
                if not condition_id:
                    continue

                title = market_info.get("title", "")[:60]

                # Get or create market data
                if condition_id not in market_cache:
                    market_cache[condition_id] = MarketData(market_info)

                market = market_cache[condition_id]

                # Get current price
                yes_price = get_market_price(condition_id)
                if yes_price is None:
                    continue

                # Update market data
                market.update_price(yes_price)
                no_price = 1 - yes_price

                # Check trading conditions
                should_trade_flag, reason = should_trade(market, yes_price)

                if should_trade_flag:
                    signals_found += 1
                    edge, persistence, fair_no = market.get_edge()

                    # Determine direction (bet against mispricing)
                    if edge > 0:  # Fair NO > Current NO, so bet NO
                        direction = "NO"
                        color = C.GRN
                    else:  # Bet YES
                        direction = "YES"
                        color = C.YLW

                    cp(f"{color}🎯 SIGNAL: {title}")
                    cp(f"   YES: {yes_price:.3f} | NO: {no_price:.3f}")
                    cp(f"   Edge: {edge:+.3f} | Persistence: {persistence:.3f}")
                    cp(f"   Direction: {direction} | Volume: ${market.volume:,.0f}{C.R}")

                    # Execute trade
                    execute_trade(market, direction, TRADE_SIZE_USD)

                elif "Edge" in reason or "Persistence" in reason:
                    # Show potential signals that don't meet criteria
                    edge, persistence, _ = market.get_edge()
                    cp(f"{C.DIM}⚪ {title[:40]} | Edge: {edge:.3f} | Pers: {persistence:.3f} | {reason}{C.R}")

            if signals_found == 0:
                cp(f"{C.DIM}No valid signals found this scan{C.R}")

            # Wait for next scan
            cp(f"\n{C.DIM}Waiting {SCAN_INTERVAL}s for next scan...{C.R}")
            time.sleep(SCAN_INTERVAL)

        except KeyboardInterrupt:
            cp(f"\n{C.B}Shutting down SPORT BOSS...{C.R}")
            break
        except Exception as e:
            cp(f"{C.RED}Error in main loop: {e}{C.R}")
            time.sleep(5)

if __name__ == "__main__":
    main()</content>
<parameter name="filePath">d:\btcupdownclaudebot\sport_boss.py