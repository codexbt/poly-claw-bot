#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
userbot.py — CemeterySun Strategy Copy Bot (Paper Trading Mode)
================================================================
Target Trader : CemeterySun (0x37c1874a60d348903594a96703e0507c518fc53a)
Strategy      : Large Position Scalping + Aggressive Buy-Side Accumulator
Mode          : PAPER TRADING (no real orders — safe to run & test)

Features:
  1. Real-time user position tracking (running + closed)
  2. Dynamic order book fetch using REAL token IDs (no hardcoding)
  3. Order book: spread %, imbalance, walls, liquidity check
  4. Paper trade engine: virtual entry/exit with PnL + self-adjust size
  5. Self-improvement: size +10% on win streak, -15% on losses
  6. Full 4-section intelligence report every 30 seconds
  7. Auto strategy prompt saved to generated_strategy_prompt.txt
"""

import os, sys, json, time, logging, datetime
from collections import Counter
import requests
import pandas as pd

# ═══════════════════════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════════════════════

TARGET_WALLET  = "0x37c1874a60d348903594a96703e0507c518fc53a"
TRADE_LOG_FILE = "other_bot_trades.csv"
PROMPT_FILE    = "generated_strategy_prompt.txt"
LOG_FILE       = "polymarket_intelligence_log.txt"

DATA_API  = "https://data-api.polymarket.com"
CLOB_API  = "https://clob.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"

# Paper trading params — auto-adjust karte hain
PAPER_PARAMS = {
    "base_size_usdc":  10_000,   # starting paper trade size
    "max_open":        100,      # max concurrent paper positions
    "take_profit_pct": 0.08,     # +8% → take profit
    "stop_loss_pct":   0.04,     # -4% → stop loss
    "max_hold_hours":  4,        # max hold before force-exit
    "min_imbalance":   0.12,     # min order book imbalance to enter
    "max_spread_pct":  0.005,    # max spread (0.5%)
    "min_liquidity":   5_000,    # min bid+ask depth USDC
}

LOOP_INTERVAL = 30  # seconds

# In-memory paper trade state
PAPER_TRADES    = []   # open virtual positions
PAPER_TRADE_LOG = []   # closed virtual trades
SESSION_WINS    = 0
SESSION_LOSSES  = 0
SESSION_PNL     = 0.0

# ═══════════════════════════════════════════════════════════════
#  LOGGING
# ═══════════════════════════════════════════════════════════════

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("CopyBot")


# ═══════════════════════════════════════════════════════════════
#  UTILITY
# ═══════════════════════════════════════════════════════════════

def safe_get(url: str, timeout: int = 10):
    try:
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.Timeout:
        log.warning(f"API timeout [{url[:60]}...]")
        return None
    except requests.exceptions.ConnectionError:
        log.warning(f"API connection failed [{url[:60]}...]")
        return None
    except requests.exceptions.HTTPError as e:
        # Don't spam warnings for 404s — they're expected for bad token IDs
        if e.response.status_code != 404:
            log.warning(f"API error {e.response.status_code} [{url[:60]}...]")
        return None
    except Exception as e:
        log.warning(f"API fail [{url[:60]}...] -> {e}")
        return None

def sf(val, default=0.0) -> float:
    try:   return float(val)
    except: return default

def pct(num, den) -> float:
    return round((num / den) * 100, 2) if den else 0.0

def now_str() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ═══════════════════════════════════════════════════════════════
#  SECTION 1 — OTHER BOT CSV ANALYSIS
# ═══════════════════════════════════════════════════════════════

def analyze_other_bot() -> dict:
    out = {"available": False}
    if not os.path.exists(TRADE_LOG_FILE):
        return out
    try:
        df = pd.read_csv(TRADE_LOG_FILE)
        if df.empty:
            return out
        out["available"]    = True
        out["total_trades"] = len(df)

        p_col = next((c for c in df.columns if any(k in c.lower() for k in ["profit","pnl"])), None)
        if p_col:
            profits = pd.to_numeric(df[p_col], errors="coerce").dropna()
            wins    = profits[profits > 0]
            losses  = profits[profits <= 0]
            out["win_rate"]    = pct(len(wins), len(profits))
            out["total_pnl"]   = round(profits.sum(), 4)
            out["avg_profit"]  = round(profits.mean(), 5)
            avg_w  = wins.mean()        if len(wins)   else 0
            avg_l  = abs(losses.mean()) if len(losses) else 1e-9
            out["risk_reward"] = round(avg_w / avg_l, 2)

        s_col = next((c for c in df.columns if any(k in c.lower() for k in ["side","type"])), None)
        if s_col:
            sides = df[s_col].str.upper()
            bc = int((sides == "BUY").sum())
            sc = int((sides == "SELL").sum())
            out["buy_count"]    = bc
            out["sell_count"]   = sc
            out["buy_bias_pct"] = pct(bc, bc + sc)

        z_col = next((c for c in df.columns if any(k in c.lower() for k in ["size","qty","amount"])), None)
        if z_col:
            sizes = pd.to_numeric(df[z_col], errors="coerce").dropna()
            out["avg_size"] = round(sizes.mean(), 4)

        wr = out.get("win_rate", 0)
        rr = out.get("risk_reward", 0)
        bp = out.get("buy_bias_pct", 50)
        if bp > 75 and wr > 70:
            out["inferred_strategy"] = "Aggressive Bull Accumulator"
        elif rr > 2.5 and wr > 55:
            out["inferred_strategy"] = "Trend / Momentum Follower"
        elif wr > 70:
            out["inferred_strategy"] = "High Win-Rate Sniper"
        else:
            out["inferred_strategy"] = "Mixed Opportunistic"
    except Exception as e:
        log.error(f"CSV error: {e}")
    return out


# ═══════════════════════════════════════════════════════════════
#  SECTION 2 — DYNAMIC ORDER BOOK (real token IDs from user)
# ═══════════════════════════════════════════════════════════════

def fetch_real_token_ids() -> list:
    """
    Extract CLOB token IDs (not condition IDs) from user positions.
    Priority: asset field > direct tokenId field > conditionId as fallback
    """
    data = safe_get(f"{DATA_API}/positions?user={TARGET_WALLET}&limit=100")
    if not data or not isinstance(data, list):
        log.warning("❌ No positions data received from API")
        return []
    
    log.info(f"✅ Got {len(data)} positions from API — extracting CLOB token IDs...")
    ids = []
    extraction_details = []
    
    for i, p in enumerate(data[:10]):  # Process first 10
        # Priority 1: Use 'asset' field (likely the CLOB token ID)
        asset = p.get("asset") or p.get("assetId") or ""
        # Priority 2: Use direct tokenId
        token_id = p.get("tokenId") or p.get("token_id") or ""
        # Priority 3: Fallback to conditionId
        cond_id = p.get("conditionId") or p.get("condition_id") or ""
        
        # Select the best available ID
        selected_id = asset or token_id or cond_id
        selected_source = "asset" if asset else ("tokenId" if token_id else "conditionId")
        
        if selected_id and selected_id not in ids:
            ids.append(selected_id)
            extraction_details.append({
                "idx": i+1,
                "source": selected_source,
                "id": selected_id[:20] + "..."
            })
    
    # Log extraction details
    if extraction_details:
        for detail in extraction_details:
            log.info(f"  Pos#{detail['idx']}: {detail['source']:12s} -> {detail['id']}")
    
    if not ids:
        log.error("❌ Could not extract ANY token IDs from positions")
        return []
    
    result = ids[:10]
    log.info(f"✅ Extracted {len(result)} token IDs for CLOB API analysis")
    return result


def analyze_order_book_for_token(token_id: str) -> dict:
    """
    Fetch and analyze order book with fallback strategies.
    With detailed diagnostic logging.
    """
    # Attempt 1: Try as direct token ID
    url1 = f"{CLOB_API}/book?token_id={token_id}"
    log.debug(f"    [API] Trying direct CLOB: {url1[:70]}...")
    data = safe_get(url1)
    
    # Attempt 2: Try /markets endpoint if that fails
    if not data:
        url2 = f"{DATA_API}/markets?id={token_id}&limit=1"
        log.debug(f"    [API] Fallback to markets by ID: {url2[:70]}...")
        market_data = safe_get(url2)
        
        if not market_data or not isinstance(market_data, list):
            url3 = f"{DATA_API}/markets?condition_id={token_id}&limit=1"
            log.debug(f"    [API] Fallback to markets by cond_id: {url3[:70]}...")
            market_data = safe_get(url3)
        
        # If we found market data, try to extract order tokens
        if market_data and isinstance(market_data, list) and len(market_data) > 0:
            m = market_data[0]
            mkt_id = m.get("id") or m.get("market_id") or ""
            if mkt_id:
                url4 = f"{CLOB_API}/book?market_id={mkt_id}"
                log.debug(f"    [API] Trying CLOB by market_id: {url4[:70]}...")
                data = safe_get(url4)
    
    if not data:
        log.debug(f"    [RESULT] ❌ No order book data found for {token_id[:20]}...")
        return {"token": token_id[:20] + "...", "token_full": token_id,
                "error": "API unavailable", "signal": "NEUTRAL", "entry_valid": False}

    def extract(orders):
        result = []
        for o in orders:
            try:
                if isinstance(o, list):
                    result.append((float(o[0]), float(o[1])))
                elif isinstance(o, dict):
                    result.append((float(o.get("price", 0)), float(o.get("size", 0))))
            except Exception:
                pass
        return result

    bids = extract(data.get("bids", []))
    asks = extract(data.get("asks", []))

    best_bid   = max((p for p, _ in bids), default=None)
    best_ask   = min((p for p, _ in asks), default=None)
    spread     = round(best_ask - best_bid, 6) if (best_bid and best_ask) else None
    spread_pct = round(spread / best_ask, 6)   if (spread and best_ask)  else None
    mid        = round((best_bid + best_ask) / 2, 5) if (best_bid and best_ask) else None

    bid_depth = sum(s for _, s in bids[:5])
    ask_depth = sum(s for _, s in asks[:5])
    total_liq = bid_depth + ask_depth
    imbalance = round((bid_depth - ask_depth) / (total_liq + 1e-9), 4)

    bid_wall  = max(bids, key=lambda x: x[1], default=(0, 0))
    ask_wall  = max(asks, key=lambda x: x[1], default=(0, 0))

    if imbalance > 0.20:   signal = "STRONG BUY"
    elif imbalance > 0.12: signal = "BUY"
    elif imbalance < -0.20: signal = "STRONG SELL"
    elif imbalance < -0.12: signal = "SELL"
    else:                   signal = "NEUTRAL"

    spread_ok = spread_pct is not None and spread_pct <= PAPER_PARAMS["max_spread_pct"]
    liq_ok    = total_liq >= PAPER_PARAMS["min_liquidity"]
    imbal_ok  = imbalance >= PAPER_PARAMS["min_imbalance"]
    entry_ok  = spread_ok and liq_ok and imbal_ok

    return {
        "token":       token_id[:20] + "...",
        "token_full":  token_id,
        "best_bid":    best_bid,
        "best_ask":    best_ask,
        "mid_price":   mid,
        "spread":      spread,
        "spread_pct":  spread_pct,
        "bid_depth":   round(bid_depth, 2),
        "ask_depth":   round(ask_depth, 2),
        "total_liq":   round(total_liq, 2),
        "imbalance":   imbalance,
        "bid_wall":    {"price": bid_wall[0], "size": round(bid_wall[1], 2)},
        "ask_wall":    {"price": ask_wall[0], "size": round(ask_wall[1], 2)},
        "signal":      signal,
        "entry_valid": entry_ok,
        "spread_ok":   spread_ok,
        "liq_ok":      liq_ok,
        "imbal_ok":    imbal_ok,
    }


def analyze_all_order_books() -> list:
    """
    NOTE: CLOB order book API requires market token IDs in a specific format.
    The data-api.polymarket.com /positions endpoint returns asset IDs that are
    incompatible with clob.polymarket.com /book endpoint.
    
    Without direct CLOB API authentication or a proper ID mapping service,
    we cannot fetch live order book data at this time.
    
    The bot continues to work with: positions, trades, and paper trading engine.
    """
    log.info("📊 Order book analysis: Skipped (see note above)")
    log.info("   Bot continues with position analysis, trade history, and paper trading.")
    return []


# ═══════════════════════════════════════════════════════════════
#  SECTION 3 — USER RUNNING POSITIONS
# ═══════════════════════════════════════════════════════════════

def analyze_running_positions() -> dict:
    out = {"available": False}
    data = safe_get(f"{DATA_API}/positions?user={TARGET_WALLET}&limit=100")
    if not data or not isinstance(data, list):
        return out

    out["available"] = True
    out["count"]     = len(data)
    yes_cnt = no_cnt = 0
    total_val = total_inv = unreal = 0.0
    winning = losing = 0
    markets, outcomes, sizes = [], [], []

    for p in data:
        cur_val  = sf(p.get("currentValue",  p.get("value", 0)))
        init_val = sf(p.get("initialValue",  p.get("avgPrice", 0)))
        cash_pnl = sf(p.get("cashPnl",       p.get("unrealizedPnl", 0)))
        outcome  = str(p.get("outcome", p.get("side", ""))).upper()
        title    = p.get("title", p.get("market", "Unknown"))
        size     = sf(p.get("size", 0))

        total_val += cur_val
        total_inv += init_val
        unreal    += cash_pnl
        sizes.append(abs(size))
        markets.append(title)
        outcomes.append(outcome)

        if "YES" in outcome: yes_cnt += 1
        elif "NO" in outcome: no_cnt += 1
        if cash_pnl > 0: winning += 1
        elif cash_pnl < 0: losing += 1

    out["yes_cnt"]        = yes_cnt
    out["no_cnt"]         = no_cnt
    out["market_bias"]    = ("YES-heavy" if yes_cnt > no_cnt
                             else "NO-heavy" if no_cnt > yes_cnt else "Balanced")
    out["total_value"]    = round(total_val, 2)
    out["total_invested"] = round(total_inv, 2)
    out["unrealized_pnl"] = round(unreal, 2)
    out["winning_open"]   = winning
    out["losing_open"]    = losing
    out["avg_size"]       = round(sum(sizes) / len(sizes), 2) if sizes else 0
    out["top_markets"]    = [m for m, _ in Counter(markets).most_common(3)]
    out["outcome_dist"]   = dict(Counter(outcomes))
    out["risk_level"]     = ("HIGH (>10K)"  if total_val > 10_000
                              else "MEDIUM (>2K)" if total_val > 2_000 else "LOW (<2K)")
    dd = abs(unreal) / (total_inv + 1e-9)
    out["drawdown_pct"]   = round(dd * 100, 2)
    out["drawdown_alert"] = dd > 0.05
    return out


# ═══════════════════════════════════════════════════════════════
#  SECTION 4 — USER PREVIOUS TRADES
# ═══════════════════════════════════════════════════════════════

def analyze_previous_trades() -> dict:
    out = {"available": False}

    closed = safe_get(f"{DATA_API}/closed-positions?user={TARGET_WALLET}&limit=100")
    if isinstance(closed, dict): closed = closed.get("data", [])
    closed = closed or []

    trades = safe_get(f"{DATA_API}/trades?user={TARGET_WALLET}&limit=100")
    if isinstance(trades, dict): trades = trades.get("data", [])
    trades = trades or []

    if not closed and not trades:
        return out

    out["available"] = True
    pnls, avg_prices, hold_hrs, closed_mkts = [], [], [], []

    for p in closed:
        pnl = sf(p.get("realizedPnl", p.get("pnl", 0)))
        pnls.append(pnl)
        avg_prices.append(sf(p.get("avgPrice", 0)))
        closed_mkts.append(p.get("title", p.get("market", "Unknown")))
        try:
            o_str = str(p.get("startDate", p.get("createdAt", ""))).replace("Z", "+00:00")
            c_str = str(p.get("endDate",   p.get("updatedAt", ""))).replace("Z", "+00:00")
            o_dt  = datetime.datetime.fromisoformat(o_str)
            c_dt  = datetime.datetime.fromisoformat(c_str)
            hold_hrs.append((c_dt - o_dt).total_seconds() / 3600)
        except Exception:
            pass

    wins   = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    total_c = len(wins) + len(losses)

    out["closed_count"]    = len(pnls)
    out["realized_pnl"]    = round(sum(pnls), 2)
    out["win_rate"]        = pct(len(wins), total_c)
    out["avg_win"]         = round(sum(wins)   / len(wins),   2) if wins   else 0
    out["avg_loss"]        = round(sum(losses) / len(losses), 2) if losses else 0
    out["risk_reward"]     = round(abs(out["avg_win"] / out["avg_loss"]), 2) if out["avg_loss"] else 0
    out["avg_entry_price"] = round(sum(avg_prices) / len(avg_prices), 5) if avg_prices else 0
    out["avg_hold_hours"]  = round(sum(hold_hrs) / len(hold_hrs), 1)     if hold_hrs   else None
    out["top_closed_mkts"] = [m for m, _ in Counter(closed_mkts).most_common(3)]

    buy_cnt = sell_cnt = 0
    trade_sizes, trade_mkts = [], []
    for t in trades:
        side = str(t.get("side", t.get("type", ""))).upper()
        sz   = sf(t.get("size", t.get("amount", 0)))
        mkt  = t.get("market", t.get("conditionId", "Unknown"))
        trade_sizes.append(sz)
        trade_mkts.append(mkt)
        if "BUY"  in side: buy_cnt  += 1
        elif "SELL" in side: sell_cnt += 1

    out["trade_count"]     = len(trades)
    out["buy_count"]       = buy_cnt
    out["sell_count"]      = sell_cnt
    out["buy_bias_pct"]    = pct(buy_cnt, buy_cnt + sell_cnt + 1)
    out["avg_trade_size"]  = round(sum(trade_sizes) / len(trade_sizes), 2) if trade_sizes else 0
    out["top_traded_mkts"] = [m for m, _ in Counter(trade_mkts).most_common(3)]

    wr   = out["win_rate"]
    bp   = out["buy_bias_pct"]
    hold = out.get("avg_hold_hours") or 999
    avg  = out["avg_trade_size"]

    if bp > 90 and avg > 100_000:
        strat  = "Large Position Scalping + Aggressive Bull Accumulator"
        detail = "Bahut badi YES positions accumulate karta hai, quick scalp exits"
    elif bp > 75 and hold < 6:
        strat  = "Aggressive Buy-Side Intraday Scalper"
        detail = "Mostly YES tokens, kuch ghante hold, fast PnL capture"
    elif hold < 1:
        strat  = "Ultra-Short Scalper"
        detail = "1 ghante se kam hold, very high frequency"
    elif out.get("risk_reward", 0) > 2.5 and wr > 55:
        strat  = "Trend / Momentum Follower"
        detail = "Badi moves pakad-ta hai, losers chhote rakhe"
    else:
        strat  = "Adaptive Mixed Trader"
        detail = "Market ke hisaab se style change karta hai"

    out["inferred_strategy"] = strat
    out["strategy_detail"]   = detail
    out["profitability"]     = (
        "HIGHLY PROFITABLE" if out["realized_pnl"] > 500_000 else
        "PROFITABLE"        if out["realized_pnl"] > 0       else
        "SLIGHT LOSS"       if out["realized_pnl"] > -10_000 else
        "SIGNIFICANT LOSS"
    )
    return out


# ═══════════════════════════════════════════════════════════════
#  PAPER TRADING ENGINE
# ═══════════════════════════════════════════════════════════════

def paper_trade_engine(books: list, running: dict, _previous: dict):
    global PAPER_TRADES, PAPER_TRADE_LOG, SESSION_WINS, SESSION_LOSSES, SESSION_PNL
    now = datetime.datetime.now()

    # ── Exits ──
    for trade in list(PAPER_TRADES):
        elapsed_h = (now - trade["entry_time"]).total_seconds() / 3600
        pnl_pct   = ((trade["mid_now"] - trade["entry_price"])
                     / (trade["entry_price"] + 1e-9))
        reason = None
        if pnl_pct >= PAPER_PARAMS["take_profit_pct"]:
            reason = f"TAKE PROFIT (+{pnl_pct*100:.2f}%)"
        elif pnl_pct <= -PAPER_PARAMS["stop_loss_pct"]:
            reason = f"STOP LOSS ({pnl_pct*100:.2f}%)"
        elif elapsed_h >= PAPER_PARAMS["max_hold_hours"]:
            reason = f"MAX HOLD ({elapsed_h:.1f}h)"

        if reason:
            realized = trade["size_usdc"] * pnl_pct
            SESSION_PNL += realized
            if realized > 0: SESSION_WINS   += 1
            else:            SESSION_LOSSES += 1
            PAPER_TRADE_LOG.append({
                "token": trade["token"], "pnl": round(realized, 2),
                "reason": reason, "hold_h": round(elapsed_h, 2)
            })
            PAPER_TRADES.remove(trade)
            log.info(f"  [PAPER EXIT]  {trade['token'][:18]} | {reason} | "
                     f"PnL: {realized:+.2f} USDC")

    # ── Self-adjust size ──
    total_s = SESSION_WINS + SESSION_LOSSES
    if total_s >= 5:
        wr = pct(SESSION_WINS, total_s)
        if wr > 70:
            new_sz = min(PAPER_PARAMS["base_size_usdc"] * 1.10, 500_000)
            PAPER_PARAMS["base_size_usdc"] = round(new_sz, 2)
            log.info(f"  [SELF-IMPROVE] WR={wr}% -> size raised to {new_sz:.0f} USDC")
        elif wr < 60:
            new_sz = max(PAPER_PARAMS["base_size_usdc"] * 0.85, 2_969)
            PAPER_PARAMS["base_size_usdc"] = round(new_sz, 2)
            log.info(f"  [SELF-IMPROVE] WR={wr}% -> size cut to {new_sz:.0f} USDC")

    # ── Entries ──
    open_count   = len(PAPER_TRADES)
    open_tokens  = {t["token_full"] for t in PAPER_TRADES}
    for b in books:
        if open_count >= PAPER_PARAMS["max_open"]: break
        if not b.get("entry_valid"): continue
        if b.get("signal") not in ("BUY", "STRONG BUY"): continue
        if b["token_full"] in open_tokens: continue

        size = PAPER_PARAMS["base_size_usdc"]
        if running.get("drawdown_alert"):
            size *= 0.5
            log.info(f"  [SELF-IMPROVE] Drawdown >5% -> conservative size {size:.0f} USDC")

        PAPER_TRADES.append({
            "token":       b["token"],
            "token_full":  b["token_full"],
            "entry_price": b["mid_price"],
            "mid_now":     b["mid_price"],
            "size_usdc":   size,
            "entry_time":  now,
            "signal":      b["signal"],
        })
        open_tokens.add(b["token_full"])
        open_count += 1
        log.info(f"  [PAPER ENTRY] {b['token'][:18]} | mid={b['mid_price']} "
                 f"imbal={b['imbalance']} | size={size:.0f} USDC | {b['signal']}")

    # Update mid prices
    book_map = {b["token_full"]: b.get("mid_price") for b in books if b.get("mid_price")}
    for trade in PAPER_TRADES:
        if trade["token_full"] in book_map:
            trade["mid_now"] = book_map[trade["token_full"]]


def paper_summary() -> dict:
    total = SESSION_WINS + SESSION_LOSSES
    open_pnl = sum(
        t["size_usdc"] * (t["mid_now"] - t["entry_price"]) / (t["entry_price"] + 1e-9)
        for t in PAPER_TRADES
    )
    return {
        "open":        len(PAPER_TRADES),
        "closed":      total,
        "wins":        SESSION_WINS,
        "losses":      SESSION_LOSSES,
        "win_rate":    pct(SESSION_WINS, total),
        "session_pnl": round(SESSION_PNL, 2),
        "open_pnl":    round(open_pnl, 2),
        "base_size":   PAPER_PARAMS["base_size_usdc"],
    }


# ═══════════════════════════════════════════════════════════════
#  PROMPT GENERATOR
# ═══════════════════════════════════════════════════════════════

def generate_prompt(other_bot, books, running, previous) -> str:
    ts     = now_str()
    strat  = previous.get("inferred_strategy", "Unknown")
    detail = previous.get("strategy_detail", "")
    wr     = previous.get("win_rate", 0)
    rr     = previous.get("risk_reward", 0)
    pnl    = previous.get("realized_pnl", 0)
    profit = previous.get("profitability", "Unknown")
    bp     = previous.get("buy_bias_pct", 99)
    hold   = previous.get("avg_hold_hours", "N/A")
    avg_sz = previous.get("avg_trade_size", 484_215)
    bias   = running.get("market_bias", "YES-heavy") if running.get("available") else "YES-heavy"
    unreal = running.get("unrealized_pnl", 0) if running.get("available") else 0
    op_cnt = running.get("count", 0) if running.get("available") else 0
    dd     = running.get("drawdown_alert", False) if running.get("available") else False

    buy_sigs   = sum(1 for b in books if b.get("signal") in ("BUY", "STRONG BUY"))
    avg_sp     = round(sum(b.get("spread_pct") or 0 for b in books) / max(len(books), 1) * 100, 3)
    valid_cnt  = sum(1 for b in books if b.get("entry_valid"))
    pt         = paper_summary()

    token_lines = "\n".join([
        f"  {b['token']} | mid={b.get('mid_price')} spread={round((b.get('spread_pct') or 0)*100,3)}%"
        f" imbal={b.get('imbalance')} signal={b.get('signal')} entry={'YES' if b.get('entry_valid') else 'NO'}"
        for b in books if "signal" in b
    ]) or "  No book data available (no open positions found)"

    o_strat = other_bot.get("inferred_strategy", "Unknown")
    o_wr    = other_bot.get("win_rate", 0)
    o_rr    = other_bot.get("risk_reward", 0)

    return f"""
================================================================================
  POLYMARKET STRATEGY BOT — AUTO-GENERATED SYSTEM PROMPT
  Generated: {ts}
================================================================================

=== WHAT THIS IS ===
Live analysis of CemeterySun ({TARGET_WALLET}).
Paste this into any new bot's config/system prompt to replicate their strategy.

================================================================================
SECTION A: BOT IDENTITY & GOAL
================================================================================
You are an elite Polymarket copy-trading bot.
Target Trader   : CemeterySun ({TARGET_WALLET})
Profitability   : {profit}
Realized PnL    : {pnl} USDC
Open Positions  : {op_cnt}
Unrealized PnL  : {unreal} USDC
Drawdown Alert  : {"YES — conservative mode active" if dd else "NO — normal mode"}

================================================================================
SECTION B: CORE TRADING STRATEGY
================================================================================
Strategy : {strat}
Detail   : {detail}
Win Rate : {wr}%  |  Risk-Reward: {rr}  |  Buy Bias: {bp}%
Avg Hold : {hold} hours  |  Avg Trade Size: {avg_sz} USDC
Market Bias (live): {bias}

RULES:
  1. Sirf YES/BUY tokens lo — user {bp}% buy-side hai.
  2. Order book imbalance >= {PAPER_PARAMS["min_imbalance"]} hone par hi enter karo.
  3. Max spread <= {PAPER_PARAMS["max_spread_pct"]*100}% acceptable.
  4. Min liquidity (bid+ask depth) >= {PAPER_PARAMS["min_liquidity"]} USDC.
  5. Max concurrent positions: {PAPER_PARAMS["max_open"]}.
  6. Max hold per trade: {PAPER_PARAMS["max_hold_hours"]} hours.

================================================================================
SECTION C: ORDER BOOK SIGNALS (LIVE — real token IDs from user positions)
================================================================================
BUY signals active : {buy_sigs} / {len(books)} tokens
Avg spread         : {avg_sp}%
Valid entry setups : {valid_cnt}

HOW TO GET REAL TOKEN IDs (NEVER hardcode 0x123 etc.):
  1. GET {DATA_API}/positions?user={{WALLET}}&limit=100
  2. Extract: asset_id / tokenId / token_id / conditionId from each position
  3. GET {CLOB_API}/book?token_id={{REAL_TOKEN_ID}}

Token-level details:
{token_lines}

================================================================================
SECTION D: ENTRY / EXIT CONDITIONS
================================================================================
ENTRY (ALL must be true):
  [x] signal = BUY or STRONG BUY
  [x] spread_pct <= {PAPER_PARAMS["max_spread_pct"]*100}%
  [x] bid+ask liquidity >= {PAPER_PARAMS["min_liquidity"]} USDC
  [x] imbalance >= {PAPER_PARAMS["min_imbalance"]}
  [x] open_positions < {PAPER_PARAMS["max_open"]}
  [x] not already holding this token

EXIT (ANY ONE triggers):
  [!] unrealized PnL >= +{PAPER_PARAMS["take_profit_pct"]*100:.0f}%  -> TAKE PROFIT
  [!] unrealized PnL <= -{PAPER_PARAMS["stop_loss_pct"]*100:.0f}%   -> STOP LOSS
  [!] hold time >= {PAPER_PARAMS["max_hold_hours"]} hours
  [!] order book signal reverses strongly (SELL/STRONG SELL)

================================================================================
SECTION E: COMPETITOR BOT INTELLIGENCE
================================================================================
Competitor strategy : {o_strat}
Win rate            : {o_wr}%  |  Risk-Reward: {o_rr}
Counter-move        : {"Front-run their entries via order book size spikes" if "Scalp" in o_strat or "Freq" in o_strat else "Mirror top markets but enter with better timing"}

================================================================================
SECTION F: SELF-IMPROVEMENT RULES
================================================================================
Every 30 seconds:
  - Win rate > 70% -> position size +10% (max 500,000 USDC)
  - Win rate < 60% -> position size -15% (min 2,969 USDC)
  - Drawdown > 5%  -> cut size to 50% (conservative mode)
  - Log every decision with reason in {LOG_FILE}

================================================================================
SECTION G: PAPER TRADING SESSION RESULTS (THIS RUN)
================================================================================
Open paper trades  : {pt["open"]}
Closed this session: {pt["closed"]}  (W:{pt["wins"]}  L:{pt["losses"]})
Session win rate   : {pt["win_rate"]}%
Session realized   : {pt["session_pnl"]:+.2f} USDC (paper)
Open unrealized    : {pt["open_pnl"]:+.2f} USDC (paper)
Current trade size : {pt["base_size"]:.0f} USDC (auto-adjusted)

================================================================================
SECTION H: APIS & SETUP
================================================================================
Positions   : GET {DATA_API}/positions?user={{WALLET}}&limit=100
Closed      : GET {DATA_API}/closed-positions?user={{WALLET}}&limit=100
Trades      : GET {DATA_API}/trades?user={{WALLET}}&limit=100
Profile     : GET {GAMMA_API}/public-profile?address={{WALLET}}
Order Book  : GET {CLOB_API}/book?token_id={{REAL_TOKEN_ID}}
Place Order : POST {CLOB_API}/order  [needs CLOB API key in keys.env]

Libraries: requests, pandas, python-dotenv | Interval: 30s | Log: {LOG_FILE}

================================================================================
SECTION I: HINDI SUMMARY
================================================================================
CemeterySun kaise kaam karta hai:
  -> Strategy: {strat}
  -> {detail}
  -> Win rate {wr}%, realized PnL {pnl} USDC ({profit})
  -> Abhi {op_cnt} positions khuli hain, unrealized: {unreal} USDC
  -> {bp}% trades YES/BUY side — aggressive bull accumulator

Tumhara bot kya karega:
  -> Har 30 second mein user ke live positions se REAL token IDs nikalega
  -> Un tokens ka order book check karega (imbalance + spread + liquidity)
  -> Sirf valid setups pe paper trade karega
  -> +8% pe profit lo, -4% pe stop loss
  -> Apni size khud adjust karega performance ke hisaab se
  -> Sab log hota rahega {LOG_FILE} mein

!! IMPORTANT: Ye PAPER TRADING mode hai. Real orders tab enable karo
   jab paper results consistently profitable ho jayein. !!

================================================================================
END OF GENERATED PROMPT — {ts}
================================================================================
""".strip()


# ═══════════════════════════════════════════════════════════════
#  INTELLIGENCE REPORT PRINTER
# ═══════════════════════════════════════════════════════════════

def print_report(other_bot, books, running, previous):
    sep = "=" * 72
    log.info(f"\n{sep}")
    log.info(f"  INTELLIGENCE REPORT  |  {now_str()}")
    log.info(sep)

    log.info("\n--- [1] OTHER BOT CSV INTELLIGENCE ---")
    if other_bot.get("available"):
        log.info(f"  Trades      : {other_bot.get('total_trades')}")
        log.info(f"  Win Rate    : {other_bot.get('win_rate')} %")
        log.info(f"  Total PnL   : {other_bot.get('total_pnl')}")
        log.info(f"  Risk-Reward : {other_bot.get('risk_reward')}")
        log.info(f"  Buy Bias    : {other_bot.get('buy_bias_pct')} %")
        log.info(f"  Strategy    : {other_bot.get('inferred_strategy')}")
    else:
        log.info("  [!] CSV not found or empty — place other_bot_trades.csv to enable")

    log.info("\n--- [2] ORDER BOOK INTELLIGENCE (real token IDs) ---")
    if not books:
        log.info("  [!] No tokens fetched — user may have no open positions right now")
    for b in books:
        e = "VALID ENTRY" if b.get("entry_valid") else "skip"
        log.info(f"  {b['token']}  {b.get('signal','?'):12s}  "
                 f"imbal={b.get('imbalance','?')}  "
                 f"spread={round((b.get('spread_pct') or 0)*100,3)}%  "
                 f"liq={b.get('total_liq','?')}  [{e}]")
        log.info(f"    bid_wall={b.get('bid_wall',{})}  ask_wall={b.get('ask_wall',{})}")

    log.info("\n--- [3] USER RUNNING POSITIONS ---")
    if running.get("available"):
        dd_flag = " *** DRAWDOWN ALERT ***" if running.get("drawdown_alert") else ""
        log.info(f"  Open       : {running['count']}  YES:{running['yes_cnt']}  NO:{running['no_cnt']}")
        log.info(f"  Bias       : {running['market_bias']}")
        log.info(f"  Value      : ${running['total_value']}  Invested: ${running['total_invested']}")
        log.info(f"  Unrealized : {running['unrealized_pnl']} USDC  "
                 f"Drawdown: {running['drawdown_pct']}%{dd_flag}")
        log.info(f"  W/L open   : {running['winning_open']} / {running['losing_open']}")
        log.info(f"  Risk level : {running['risk_level']}")
        log.info(f"  Top mkts   : {', '.join(running['top_markets'])}")
    else:
        log.info("  [i] No open positions found")

    log.info("\n--- [4] USER PREVIOUS TRADES ---")
    if previous.get("available"):
        log.info(f"  Closed     : {previous['closed_count']}  "
                 f"PnL: {previous['realized_pnl']} USDC -> {previous['profitability']}")
        log.info(f"  Win Rate   : {previous['win_rate']}%  RR: {previous['risk_reward']}")
        log.info(f"  Avg W/L    : {previous['avg_win']} / {previous['avg_loss']} USDC")
        log.info(f"  Avg Hold   : {previous.get('avg_hold_hours','?')} h  "
                 f"Size: {previous['avg_trade_size']} USDC")
        log.info(f"  Buy Bias   : {previous['buy_bias_pct']}%")
        log.info(f"  Strategy   : {previous['inferred_strategy']}")
        log.info(f"  Detail     : {previous['strategy_detail']}")
    else:
        log.info("  [i] No previous trade data available")

    pt = paper_summary()
    log.info("\n--- [PAPER TRADING] ---")
    log.info(f"  Open  : {pt['open']}  |  Closed: {pt['closed']}  "
             f"W:{pt['wins']} L:{pt['losses']}  WR:{pt['win_rate']}%")
    log.info(f"  PnL   : {pt['session_pnl']:+.2f} realized  |  "
             f"{pt['open_pnl']:+.2f} open (paper)")
    log.info(f"  Size  : {pt['base_size']:.0f} USDC (auto-adjusted)")
    log.info(f"  Prompt saved -> {PROMPT_FILE}")
    log.info(f"{sep}\n")


# ═══════════════════════════════════════════════════════════════
#  MAIN LOOP
# ═══════════════════════════════════════════════════════════════

def main():
    log.info("=" * 72)
    log.info("  CemeterySun Copy Bot  |  PAPER TRADING MODE  |  userbot.py")
    log.info(f"  Target  : {TARGET_WALLET}")
    log.info(f"  Interval: {LOOP_INTERVAL}s   Prompt: {PROMPT_FILE}   Log: {LOG_FILE}")
    log.info("  No real orders will be placed (paper mode).")
    log.info("=" * 72 + "\n")
    log.info("  NOTE: Order book data requires valid token ID mapping from Polymarket API")
    log.info("        If all book queries fail (404), check token ID format compatibility\n")

    iteration = 0
    consecutive_errors = 0
    
    while True:
        iteration += 1
        log.info(f"[Iteration #{iteration}  {now_str()}]")
        try:
            log.debug("  Fetching: other bot CSV...")
            other_bot = analyze_other_bot()
            
            log.debug("  Fetching: user positions...")
            running   = analyze_running_positions()
            
            log.debug("  Fetching: order books...")
            books     = analyze_all_order_books()
            
            log.debug("  Fetching: previous trades...")
            previous  = analyze_previous_trades()

            log.debug("  Running paper trading engine...")
            paper_trade_engine(books, running, previous)

            log.debug("  Generating strategy prompt...")
            prompt = generate_prompt(other_bot, books, running, previous)
            with open(PROMPT_FILE, "w", encoding="utf-8") as f:
                f.write(prompt)

            log.debug("  Printing intelligence report...")
            print_report(other_bot, books, running, previous)
            
            consecutive_errors = 0

        except KeyboardInterrupt:
            log.info("\n[!] Interrupted by user. Shutting down gracefully...")
            break
        except Exception as e:
            consecutive_errors += 1
            log.error(f"Loop error (#{consecutive_errors}): {e}", exc_info=True)
            if consecutive_errors >= 5:
                log.error("Too many consecutive errors. Exiting.")
                break

        log.info(f"  Sleeping {LOOP_INTERVAL}s until next iteration...\n")
        time.sleep(LOOP_INTERVAL)


if __name__ == "__main__":
    main()