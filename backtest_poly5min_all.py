#!/usr/bin/env python3
"""
backtest_poly5min_all.py

Approximate the `poly5min_all.py` strategy against historical 5-minute Polymarket crypto windows.
This is a best-effort backtest using Binance 1-minute OHLC data and the same validation logic
where possible. It does not have direct access to historical Polymarket CLOB mid-price snapshots
at 80-120 seconds before market close, so it uses an underlying probability proxy instead.
"""

import argparse
import math
import time
from datetime import datetime, timedelta, timezone

import ccxt
import pytz

# Strategy configuration copied from poly5min_all.py
PRICE_THRESHOLD = 0.86
MOMENTUM_THRESHOLD = 0.02
STRONG_THRESHOLD = 0.05
MIN_TRADE_SIZE = 1.0
BASE_TRADE_SIZE = 2.0
MAX_TRADE_SIZE = 3.0
DAILY_LIMIT = 300.0

SYMBOLS = ["BTC", "ETH", "SOL", "XRP", "DOGE", "BNB"]
PAIR_MAP = {
    "BTC": "BTC/USDT",
    "ETH": "ETH/USDT",
    "SOL": "SOL/USDT",
    "XRP": "XRP/USDT",
    "DOGE": "DOGE/USDT",
    "BNB": "BNB/USDT",
}

ET = pytz.timezone("America/New_York")


def utc_dt_from_ms(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)


def et_dt_from_ms(ms: int) -> datetime:
    return utc_dt_from_ms(ms).astimezone(ET)


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def fetch_ohlcv(exchange: ccxt.binance, symbol: str, since_ms: int) -> list:
    bars = []
    current_since = since_ms
    while True:
        chunk = exchange.fetch_ohlcv(symbol, timeframe="1m", since=current_since, limit=1000)
        if not chunk:
            break
        bars.extend(chunk)
        last_ts = chunk[-1][0]
        if last_ts >= int(time.time() * 1000) - 60_000:
            break
        current_since = last_ts + 60_000
        if len(chunk) < 1000:
            break
        time.sleep(exchange.rateLimit / 1000.0)
    return bars


def build_bar_index(ohlcv: list) -> dict:
    return {bar[0]: bar for bar in ohlcv}


def is_et_five_min_start(ts_ms: int) -> bool:
    dt = et_dt_from_ms(ts_ms)
    return dt.second == 0 and dt.microsecond == 0 and dt.minute % 5 == 0


def get_window_start_times(ohlcv: list, since_ms: int) -> list:
    starts = []
    for bar in ohlcv:
        ts_ms = bar[0]
        if ts_ms < since_ms:
            continue
        if is_et_five_min_start(ts_ms):
            starts.append(ts_ms)
    return sorted(starts)


def proxy_token_probability(start_price: float, current_price: float, signal: str) -> float:
    if start_price <= 0:
        return 0.5
    move_pct = ((current_price - start_price) / start_price * 100) if signal == "UP" else ((start_price - current_price) / start_price * 100)
    proxy = 0.5 + clamp(move_pct / 2.0, -0.5, 0.5)
    return round(proxy, 6)


def calc_momentum_from_prices(prior_price: float, current_price: float, threshold: float = 0.02) -> tuple:
    if prior_price <= 0:
        return None, "NEUTRAL", 0.0
    pct_change = (current_price - prior_price) / prior_price * 100
    if abs(pct_change) < threshold:
        return pct_change, "NEUTRAL", 0.0
    signal = "UP" if pct_change > 0 else "DOWN"
    raw_str = min(1.0, (abs(pct_change) - threshold) / (3.0 * STRONG_THRESHOLD))
    consistency = 1.0
    momentum_score = raw_str * (0.5 + 0.5 * consistency)
    return pct_change, signal, momentum_score


def analyse_minute_candles(last_bars: list) -> tuple:
    if len(last_bars) < 3:
        return 0.0, "INSUFFICIENT_DATA"
    current = last_bars[-1]
    prev = last_bars[-2]
    pprev = last_bars[-3] if len(last_bars) >= 3 else None
    o, h, l, c = current[1], current[2], current[3], current[4]
    body = abs(c - o)
    range_ = max(h - l, 1e-9)
    body_ratio = body / range_
    score = body_ratio * 0.3
    bullish_run = sum(1 for bar in last_bars[-4:] if bar[4] >= bar[1])
    bearish_run = sum(1 for bar in last_bars[-4:] if bar[4] < bar[1])
    score += max(bullish_run, bearish_run) / 4 * 0.25
    labels = []
    if pprev and body > abs(prev[4] - prev[1]) * 1.5:
        if c >= o and prev[4] < prev[1]:
            labels.append("BULLISH_ENGULF")
            score += 0.25
        elif c < o and prev[4] >= prev[1]:
            labels.append("BEARISH_ENGULF")
            score += 0.25
    if body_ratio < 0.15:
        labels.append("DOJI")
        score -= 0.15
    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l
    if c >= o and lower_wick > body * 2:
        labels.append("HAMMER")
        score += 0.20
    if c < o and upper_wick > body * 2:
        labels.append("SHOOTING_STAR")
        score += 0.20
    if len(last_bars) >= 3:
        c1, c2, c3 = last_bars[-3], last_bars[-2], last_bars[-1]
        if c1[4] >= c1[1] and c2[4] >= c2[1] and c3[4] >= c3[1]:
            labels.append("THREE_BULL")
            score += 0.15
        elif c1[4] < c1[1] and c2[4] < c2[1] and c3[4] < c3[1]:
            labels.append("THREE_BEAR")
            score += 0.15
    return clamp(score, 0.0, 1.0), ",".join(labels) if labels else "PLAIN"


def check_volatility_from_bars(bar1: list, bar2: list, min_pct: float = 0.03) -> tuple:
    if not bar1 or not bar2 or bar1[1] <= 0 or bar2[1] <= 0:
        return False, 0.0, "Insufficient bar data"
    highs = [bar1[2], bar2[2]]
    lows = [bar1[3], bar2[3]]
    high = max(highs)
    low = min(lows)
    range_pct = ((high - low) / low * 100) if low > 0 else 0.0
    if range_pct >= min_pct:
        return True, range_pct, f"Movement: {range_pct:.4f}%"
    return False, range_pct, f"Insufficient movement: {range_pct:.4f}% (need {min_pct}%)"


def check_open_price_momentum_from_bar(bar: list, signal: str, min_move_pct: float = 0.10) -> tuple:
    if not bar:
        return False, "No bar"
    open_price = bar[1]
    close_price = bar[4]
    if open_price <= 0:
        return False, "Invalid open price"
    change_pct = (close_price - open_price) / open_price * 100
    if signal == "UP" and change_pct >= min_move_pct:
        return True, f"Open moved +{change_pct:.2f}% from candle open"
    if signal == "DOWN" and change_pct <= -min_move_pct:
        return True, f"Open moved {change_pct:.2f}% from candle open"
    return False, f"Open move {change_pct:.2f}% insufficient"


def check_86_threshold_crossing(yes_price: float, no_price: float, signal: str,
                                seconds_left: int = None,
                                last_yes: float = None,
                                last_no: float = None,
                                current_bar: list = None) -> tuple:
    threshold = PRICE_THRESHOLD
    low_threshold = 0.80
    special_threshold = 0.85

    if signal == "UP" and yes_price is not None:
        if seconds_left == 120 and yes_price >= special_threshold:
            return True, f"YES {yes_price:.3f} at 120s left (85%+ entry)"
        if current_bar is not None:
            sharp, msg = check_open_price_momentum_from_bar(current_bar, signal)
            if sharp and yes_price >= low_threshold:
                return True, f"YES {yes_price:.3f} with open move: {msg}"
        if yes_price >= threshold:
            if last_yes and last_yes < threshold:
                return True, f"YES crossed threshold: {last_yes:.3f} → {yes_price:.3f}"
            elif last_yes is None:
                return True, f"YES confirmed above threshold: {yes_price:.3f}"
            else:
                time_since_cross = abs(yes_price - threshold) / (threshold * 0.1)
                if time_since_cross < 0.05:
                    return True, f"YES above threshold (recent): {yes_price:.3f}"
        return False, f"YES below threshold: {yes_price:.3f}"

    if signal == "DOWN" and no_price is not None:
        if seconds_left == 120 and no_price >= special_threshold:
            return True, f"NO {no_price:.3f} at 120s left (85%+ entry)"
        if current_bar is not None:
            sharp, msg = check_open_price_momentum_from_bar(current_bar, signal)
            if sharp and no_price >= low_threshold:
                return True, f"NO {no_price:.3f} with open move: {msg}"
        if no_price >= threshold:
            if last_no and last_no < threshold:
                return True, f"NO crossed threshold: {last_no:.3f} → {no_price:.3f}"
            elif last_no is None:
                return True, f"NO confirmed above threshold: {no_price:.3f}"
            else:
                time_since_cross = abs(no_price - threshold) / (threshold * 0.1)
                if time_since_cross < 0.05:
                    return True, f"NO above threshold (recent): {no_price:.3f}"
        return False, f"NO below threshold: {no_price:.3f}"

    return False, "Invalid signal or price"


def score_signal(signal: str, momentum_score: float, candle_score: float, yes_price: float, no_price: float) -> tuple:
    if signal == "NEUTRAL":
        return 0.0, 0.0, "NO_SIGNAL"
    m_score = momentum_score * 0.40
    p_score = 0.0
    if signal == "UP":
        if yes_price >= PRICE_THRESHOLD:
            excess = yes_price - PRICE_THRESHOLD
            market_score = clamp(0.5 + excess / 0.14, 0.0, 1.0)
            price_check = f"YES@{yes_price:.3f}"
        else:
            market_score = 0.0
            price_check = f"YES_LOW@{yes_price:.3f}"
    else:
        if no_price >= PRICE_THRESHOLD:
            excess = no_price - PRICE_THRESHOLD
            market_score = clamp(0.5 + excess / 0.14, 0.0, 1.0)
            price_check = f"NO@{no_price:.3f}"
        else:
            market_score = 0.0
            price_check = f"NO_LOW@{no_price:.3f}"
    p_score = market_score * 0.25
    total = m_score + candle_score * 0.35 + p_score
    if market_score == 0.0:
        reason = f"BLOCKED({price_check})"
        return 0.0, 0.0, reason
    size = 0.0
    if total < 0.40:
        size = 0.0
    elif total < 0.60:
        size = MIN_TRADE_SIZE
    elif total < 0.75:
        size = BASE_TRADE_SIZE
    else:
        size = MAX_TRADE_SIZE
    if 0.40 <= total < 0.75:
        size = 1.0 + ((total - 0.40) / 0.35) * 1.0
    elif total >= 0.75:
        size = 2.0 + ((total - 0.75) / 0.25) * 1.0
    reason = f"mom={momentum_score:.2f}|mkt={market_score:.2f}({price_check})|TOTAL={total:.2f}|SIZE=${size:.2f}"
    return total, size, reason


def run_backtest(symbol: str, days: int, verbose: bool = False) -> dict:
    pair = PAIR_MAP[symbol]
    exchange = ccxt.binance({"enableRateLimit": True})
    now_ms = int(time.time() * 1000)
    since_ms = now_ms - days * 24 * 60 * 60 * 1000
    print(f"Fetching {symbol} 1m history since {datetime.fromtimestamp(since_ms/1000, tz=timezone.utc)} UTC")
    bars = fetch_ohlcv(exchange, pair, since_ms)
    bars_by_ts = build_bar_index(bars)
    window_starts = get_window_start_times(bars, since_ms)
    results = {
        "symbol": symbol,
        "total_windows": 0,
        "signals": 0,
        "wins": 0,
        "losses": 0,
        "pnl": 0.0,
        "trade_sizes": [],
        "details": [],
    }
    for window_ts in window_starts:
        required = [window_ts, window_ts + 120_000, window_ts + 180_000, window_ts + 240_000]
        if any(ts not in bars_by_ts for ts in required):
            continue
        results["total_windows"] += 1
        bar_start = bars_by_ts[window_ts]
        bar_2m = bars_by_ts[window_ts + 120_000]
        bar_3m = bars_by_ts[window_ts + 180_000]
        bar_4m = bars_by_ts[window_ts + 240_000]
        start_price = bar_start[1]
        signal_price = (bar_3m[1] + bar_3m[4]) / 2.0
        reference_price = bar_2m[4]
        momentum_pct, signal, mom_score = calc_momentum_from_prices(reference_price, signal_price, 0.02)
        if signal == "NEUTRAL":
            continue
        vol_ok, vol_pct, vol_msg = check_volatility_from_bars(bar_2m, bar_3m)
        # REMOVED volatility check as per request
        # if not vol_ok:
        #     continue
        last_yes = proxy_token_probability(start_price, bar_2m[4], signal)
        last_no = proxy_token_probability(start_price, bar_2m[4], "DOWN")
        yes_price = proxy_token_probability(start_price, signal_price, "UP")
        no_price = proxy_token_probability(start_price, signal_price, "DOWN")
        threshold_ok, threshold_msg = check_86_threshold_crossing(
            yes_price, no_price, signal,
            seconds_left=120,
            last_yes=last_yes,
            last_no=last_no,
            current_bar=bar_3m,
        )
        if not threshold_ok:
            continue
        candle_score, pattern = analyse_minute_candles([bar_start, bar_2m, bar_3m])
        total, size, reason = score_signal(signal, mom_score, candle_score, yes_price, no_price)
        
        # Determine mode
        if mom_score >= 0.3:
            size = 3.0  # Mode 1: Technical
            mode = "MODE1"
        else:
            # Mode 2: Threshold, use scored size
            mode = "MODE2"
            if size <= 0:
                continue  # Skip if score too low for mode 2
        
        results["signals"] += 1
        resolution = "UP" if bar_4m[4] >= start_price else "DOWN"
        correct = signal == resolution
        pnl = size if correct else -size
        results["pnl"] += pnl
        results["trade_sizes"].append(size)
        if correct:
            results["wins"] += 1
        else:
            results["losses"] += 1
        if verbose:
            ts = et_dt_from_ms(window_ts)
            results["details"].append({
                "window": ts.isoformat(),
                "signal": signal,
                "result": resolution,
                "current_price": signal_price,
                "yes_price": yes_price,
                "score": total,
                "size": size,
                "pnl": pnl,
                "reason": reason,
            })
    return results


def print_summary(results: dict) -> None:
    total = results["total_windows"]
    signals = results["signals"]
    wins = results["wins"]
    losses = results["losses"]
    pnl = results["pnl"]
    win_rate = (wins / signals * 100) if signals else 0.0
    avg_size = sum(results["trade_sizes"]) / len(results["trade_sizes"]) if results["trade_sizes"] else 0.0
    print(f"\n=== {results['symbol']} BACKTEST SUMMARY ===")
    print(f"Total 5-min windows evaluated: {total}")
    print(f"Signals generated: {signals}")
    print(f"Wins: {wins} | Losses: {losses} | Win rate: {win_rate:.2f}%")
    print(f"Net P/L (proxy): ${pnl:.2f}")
    print(f"Average trade size: ${avg_size:.2f}")
    print("Note: This backtest approximates CLOB token prices using underlying price movement and does not use actual historical Polymarket mid-price snapshots.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest poly5min_all.py strategy over historical crypto windows.")
    parser.add_argument("--symbols", default=','.join(SYMBOLS), help="Comma-separated symbols to backtest")
    parser.add_argument("--days", type=int, default=90, help="History depth in days")
    parser.add_argument("--verbose", action="store_true", help="Save detailed signal records")
    args = parser.parse_args()
    symbols = [s.strip().upper() for s in args.symbols.split(',') if s.strip()]
    summary = []
    for symbol in symbols:
        if symbol not in PAIR_MAP:
            print(f"Skipping unsupported symbol: {symbol}")
            continue
        result = run_backtest(symbol, args.days, verbose=args.verbose)
        print_summary(result)
        summary.append(result)
    if summary:
        total_windows = sum(r["total_windows"] for r in summary)
        total_signals = sum(r["signals"] for r in summary)
        total_wins = sum(r["wins"] for r in summary)
        total_losses = sum(r["losses"] for r in summary)
        total_pnl = sum(r["pnl"] for r in summary)
        avg_size = sum(sum(r["trade_sizes"]) for r in summary) / sum(len(r["trade_sizes"]) for r in summary) if sum(len(r["trade_sizes"]) for r in summary) else 0.0
        win_rate = (total_wins / total_signals * 100) if total_signals else 0.0
        print("\n=== AGGREGATE BACKTEST SUMMARY ===")
        print(f"Symbols: {', '.join([r['symbol'] for r in summary])}")
        print(f"Total windows: {total_windows}")
        print(f"Total signals: {total_signals}")
        print(f"Total wins: {total_wins} | Total losses: {total_losses} | Aggregate win rate: {win_rate:.2f}%")
        print(f"Aggregate net P/L (proxy): ${total_pnl:.2f}")
        print(f"Aggregate average trade size: ${avg_size:.2f}")


if __name__ == "__main__":
    main()
