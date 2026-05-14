#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CemeterysunReplicant v4.5 — Polymarket Sports Trading Bot
══════════════════════════════════════════════════════════

TARGET: 80%+ Win Rate — LLM Final Decision Maker + In-Game Monitor

HOW IT WORKS (v4.5 Architecture):

  ┌─────────────────────────────────────────────────────────┐
  │  PRE-GAME FLOW (new markets)                            │
  └─────────────────────────────────────────────────────────┘
  STEP 1 — Market Discovery (3-layer: Gamma + CLOB)
  STEP 2 — Price & Liquidity check
  STEP 3 — Rich Data Gathering (ALL before LLM):
      A. Season win%, home/away
      B. Recent form (last 5 games)
      C. Head-to-head record
      D. Injury report (key players)
      E. Line movement (2h price change)
      F. Market sentiment (YES/NO volume ratio)
      G. MLB pitcher ERA
      H. IPL: toss winner, pitch type, venue avg score
  STEP 4 — Pre-filter (obvious zero-edge skip)
  STEP 5 — LLM final decision (DeepSeek R1)
      • ONLY bets if confidence >= 72%
      • LLM has full veto ("NO BET")
  STEP 6 — Kelly sizing → Execute

  ┌─────────────────────────────────────────────────────────┐
  │  IN-GAME MONITORING (open positions — runs every scan)  │
  └─────────────────────────────────────────────────────────┘
  For each open position:
    A. Detect game phase: PRE_GAME / IN_GAME / CLOSING
    B. Fetch live price (CLOB midpoint)
    C. Calculate price drift from entry
    D. Fetch live game data (score, run rate, quarter, etc.)
    E. LLM IN-GAME call with live context:
       Outputs: HOLD / EXIT_PROFIT / EXIT_PROTECT / ADD_MORE
       + updated_probability + reasoning
    F. Act on LLM decision:
       HOLD        → do nothing, log
       EXIT_PROFIT → sell position (lock profit)
       EXIT_PROTECT→ sell position (stop loss / situation reversed)
       ADD_MORE    → increase position size (if confidence >= 80)

  PHASE DETECTION logic:
    • hours_to_close > 1.5h  → PRE_GAME   (don't monitor yet)
    • 0h < hours_to_close <= 1.5h → IN_GAME  (monitor actively)
    • -2h < hours_to_close <= 0h  → CLOSING  (final check)
    • hours_to_close < -2h  → EXPIRED   (await settlement)

  IPL specific IN_GAME signals:
    • After toss: update toss_winner → LLM re-evaluates
    • Powerplay score (if available): run rate vs par
    • Death overs run rate anomaly
    • Key wickets fallen (LLM assesses impact)

  EXIT triggers (LLM decides, bot executes):
    • Price moved against us > ADVERSE_MOVE_PCT (default 15%)
    • Live score strongly suggests our bet is losing
    • Game situation reversed (chasing team collapsing, etc.)
    • Profit target hit: price moved in our favour > PROFIT_TARGET_PCT (25%)

Usage:
  pip install requests python-dotenv py-clob-client
  python sports_bot_v4.py              # one-shot scan + monitor
  python sports_bot_v4.py --live       # real trades
  python sports_bot_v4.py --positions  # portfolio + live status
  python sports_bot_v4.py --daemon     # scan+monitor every 75s
  python sports_bot_v4.py --reset      # balance reset

.env:
  OPENROUTER_API_KEY=sk-or-...  (required)
  PRIVATE_KEY=0x...             (only for --live)
  POLYMARKET_FUNDER_ADDRESS=0x. (only for --live)
  PAPER_MODE=true
  MIN_EDGE=0.04
  MAX_BET_USD=200
  DAILY_LOSS_LIMIT=150
  LLM_CONFIDENCE_MIN=72
  ADVERSE_MOVE_PCT=0.15         (exit if price moves 15% against us)
  PROFIT_TARGET_PCT=0.25        (consider exit/lock at 25% profit move)
  IN_GAME_MONITOR=true          (enable in-game monitoring, default true)
"""

import argparse
import json
import logging
import os
import re
import sqlite3
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
import requests

try:
    from dotenv import load_dotenv
    load_dotenv(override=True)
except ImportError:
    pass

# ═══════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════
PRIVATE_KEY               = os.getenv("PRIVATE_KEY", "")
POLYMARKET_FUNDER_ADDRESS = os.getenv("POLYMARKET_FUNDER_ADDRESS", "")
CHAIN_ID                  = int(os.getenv("CHAIN_ID", "137"))
SIGNATURE_TYPE            = int(os.getenv("SIGNATURE_TYPE", "0"))
OPENROUTER_API_KEY        = (
    os.getenv("OPENROUTER_API_KEY")
    or os.getenv("OPENROUTER_KEY")
    or os.getenv("OR_API_KEY") or ""
)

PAPER_MODE          = os.getenv("PAPER_MODE", "true").lower() in ("true", "1", "yes")
MIN_EDGE            = float(os.getenv("MIN_EDGE",            "0.04"))
MAX_BET_USD         = float(os.getenv("MAX_BET_USD",         "200"))
MIN_BET_USD         = float(os.getenv("MIN_BET_USD",         "5"))
KELLY_FRACTION      = float(os.getenv("KELLY_FRACTION",      "0.20"))
DAILY_LOSS_LIMIT    = float(os.getenv("DAILY_LOSS_LIMIT",    "150"))
MAX_EXPOSURE_PCT    = float(os.getenv("MAX_EXPOSURE_PCT",    "0.05"))
SCAN_INTERVAL       = int(os.getenv("SCAN_INTERVAL",         "75"))
INITIAL_BALANCE     = float(os.getenv("INITIAL_BALANCE",     "1000"))
LLM_CONFIDENCE_MIN  = int(os.getenv("LLM_CONFIDENCE_MIN",   "72"))   # v4: raised bar
ADVERSE_MOVE_PCT    = float(os.getenv("ADVERSE_MOVE_PCT",    "0.15"))  # exit if price moves 15% against us
PROFIT_TARGET_PCT   = float(os.getenv("PROFIT_TARGET_PCT",   "0.25"))  # consider exit/lock at +25% price move
IN_GAME_MONITOR     = os.getenv("IN_GAME_MONITOR", "true").lower() in ("true","1","yes")
IN_GAME_HOURS_START = float(os.getenv("IN_GAME_HOURS_START", "1.5"))   # monitor when < this many hours left
ADD_MORE_CONF_MIN   = int(os.getenv("ADD_MORE_CONF_MIN",     "80"))    # min LLM confidence to add more shares

PRIMARY_MODEL  = "deepseek/deepseek-r1"
FALLBACK_MODEL = "deepseek/deepseek-chat"

DB_FILE  = "sports_trades_v4.db"
SIM_FILE = "sports_sim_v4.json"
LOG_FILE = "sports_bot_v4.log"

ESPN_API = "https://site.api.espn.com/apis/site/v2/sports"
GAMMA    = "https://gamma-api.polymarket.com"
CLOB     = "https://clob.polymarket.com"
HEADERS  = {"User-Agent": "Mozilla/5.0 (compatible; SportsBot/4.0)"}

# ═══════════════════════════════════════════════════
# COLORS
# ═══════════════════════════════════════════════════
class C:
    R="\033[0m"; B="\033[1m"; DIM="\033[2m"
    BRED="\033[91m"; BGRN="\033[92m"; BYLW="\033[93m"
    BCYN="\033[96m"; BWHT="\033[97m"; MAG="\033[35m"
    BG_RED="\033[41m"; BG_BLU="\033[44m"; BG_GRN="\033[42m"

def cp(msg): print(msg + C.R)

# Windows Unicode fix + UTF-8 logging
def _setup_logging():
    fmt = logging.Formatter(
        fmt="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    # File handler — always UTF-8
    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)

    # Console handler — reconfigure to UTF-8 on Windows
    ch = logging.StreamHandler(sys.stdout)
    if hasattr(ch.stream, "reconfigure"):
        try:
            ch.stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ch.setFormatter(fmt)
    root.addHandler(ch)

_setup_logging()
log = logging.getLogger("sportsbot_v4")


# ═══════════════════════════════════════════════════
# DATA CLASSES
# ═══════════════════════════════════════════════════
@dataclass
class GameData:
    """All data gathered about a game BEFORE calling LLM."""
    title: str
    sport: str
    league: str
    team_a: str
    team_b: str
    yes_price: float
    no_price: float
    volume: float
    depth: dict
    hours_to_close: float

    # ESPN stats
    wp_a: float = 0.500
    wp_b: float = 0.500
    home_team: str = ""
    a_is_home: bool = False

    # Recent form (last 5 games)
    form_a: str = "N/A"      # e.g. "W W L W W (4-1)"
    form_b: str = "N/A"

    # Head to head
    h2h: str = "N/A"         # e.g. "A won 2 of last 3 vs B"

    # Injuries
    injuries_a: list = field(default_factory=list)
    injuries_b: list = field(default_factory=list)

    # MLB specific
    home_era: float = 4.50
    away_era: float = 4.50

    # Line movement
    line_movement: str = "STABLE"  # UP / DOWN / STABLE
    movement_pct: float = 0.0

    # Market sentiment
    yes_vol_pct: float = 0.50  # YES volume / total volume
    open_interest: float = 0.0

    # IPL specific
    toss_winner: str = ""
    pitch_type: str = ""
    venue_avg_score: float = 0.0

    # Stat model estimate (pre-LLM quick estimate)
    stat_prob: float = 0.500

    # Market IDs
    condition_id: str = ""
    market_id: str = ""
    yes_token: str = ""
    no_token: str = ""
    game_time: str = ""


@dataclass
class SportSignal:
    market_id: str; condition_id: str; yes_token: str; no_token: str
    title: str; sport: str; league: str; direction: str
    market_price: float; model_prob: float; edge: float
    kelly_f: float; bet_usd: float; llm_confidence: int
    home_team: str; away_team: str; game_time: str; factors: list


@dataclass
class TradePosition:
    opened_at: str; sport: str; league: str; title: str
    condition_id: str; yes_token: str; no_token: str
    direction: str; entry_price: float; bet_usd: float
    shares: float; model_prob: float; edge: float
    llm_confidence: int = 0
    resolved: bool = False; outcome: str = "OPEN"; pnl: float = 0.0

    # In-game monitoring fields (v4.5)
    # phase: PRE_GAME → IN_GAME → CLOSING → EXPIRED → settled
    phase: str = "PRE_GAME"
    last_monitored_at: str = ""        # ISO timestamp of last LLM in-game check
    last_live_price: float = 0.0       # most recent market price seen
    last_monitor_action: str = "NONE"  # HOLD / EXIT_PROFIT / EXIT_PROTECT / ADD_MORE
    monitor_notes: str = ""            # last LLM reasoning snippet
    exited_early: bool = False         # True if we sold before resolution


@dataclass
class BotStats:
    balance: float = INITIAL_BALANCE
    total_trades: int = 0; wins: int = 0; losses: int = 0
    total_pnl: float = 0.0; daily_pnl: float = 0.0; daily_reset: str = ""
    llm_calls: int = 0; llm_no_bet: int = 0; pre_filter_skips: int = 0
    early_exits: int = 0; early_exit_pnl: float = 0.0

    @property
    def win_rate(self):
        c = self.wins + self.losses
        return (self.wins / c * 100) if c else 0.0


# ═══════════════════════════════════════════════════
# DATABASE
# ═══════════════════════════════════════════════════
class DB:
    def __init__(self, path=DB_FILE):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self._init()

    def _init(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                opened_at TEXT, sport TEXT, league TEXT, title TEXT,
                condition_id TEXT UNIQUE, direction TEXT,
                entry_price REAL, bet_usd REAL, shares REAL,
                model_prob REAL, edge REAL, llm_confidence INTEGER DEFAULT 0,
                resolved INTEGER DEFAULT 0,
                outcome TEXT DEFAULT 'OPEN', pnl REAL DEFAULT 0,
                phase TEXT DEFAULT 'PRE_GAME',
                last_monitored_at TEXT DEFAULT '',
                last_live_price REAL DEFAULT 0,
                last_monitor_action TEXT DEFAULT 'NONE',
                monitor_notes TEXT DEFAULT '',
                exited_early INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS scans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scanned_at TEXT, markets INTEGER, signals INTEGER,
                executed INTEGER, llm_calls INTEGER, pre_skips INTEGER
            );
            CREATE TABLE IF NOT EXISTS price_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_id TEXT, price REAL, recorded_at TEXT
            );
            CREATE TABLE IF NOT EXISTS monitor_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                logged_at TEXT, condition_id TEXT, phase TEXT,
                live_price REAL, entry_price REAL, drift REAL,
                llm_action TEXT, reasoning TEXT
            );
        """)
        # Migrate existing DB — add columns if missing
        try:
            self.conn.execute("ALTER TABLE trades ADD COLUMN phase TEXT DEFAULT 'PRE_GAME'")
        except: pass
        try:
            self.conn.execute("ALTER TABLE trades ADD COLUMN last_monitored_at TEXT DEFAULT ''")
        except: pass
        try:
            self.conn.execute("ALTER TABLE trades ADD COLUMN last_live_price REAL DEFAULT 0")
        except: pass
        try:
            self.conn.execute("ALTER TABLE trades ADD COLUMN last_monitor_action TEXT DEFAULT 'NONE'")
        except: pass
        try:
            self.conn.execute("ALTER TABLE trades ADD COLUMN monitor_notes TEXT DEFAULT ''")
        except: pass
        try:
            self.conn.execute("ALTER TABLE trades ADD COLUMN exited_early INTEGER DEFAULT 0")
        except: pass
        self.conn.commit()

    def insert_trade(self, pos: TradePosition):
        try:
            self.conn.execute(
                "INSERT OR IGNORE INTO trades "
                "(opened_at,sport,league,title,condition_id,direction,"
                "entry_price,bet_usd,shares,model_prob,edge,llm_confidence) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (pos.opened_at, pos.sport, pos.league, pos.title,
                 pos.condition_id, pos.direction, pos.entry_price,
                 pos.bet_usd, pos.shares, pos.model_prob, pos.edge,
                 pos.llm_confidence)
            )
            self.conn.commit()
        except Exception as e:
            log.warning("DB insert: %s", e)

    def log_scan(self, markets, signals, executed, llm_calls=0, pre_skips=0):
        self.conn.execute(
            "INSERT INTO scans (scanned_at,markets,signals,executed,llm_calls,pre_skips) "
            "VALUES (?,?,?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(), markets, signals, executed,
             llm_calls, pre_skips)
        )
        self.conn.commit()

    def already_open(self, cid: str) -> bool:
        return bool(self.conn.execute(
            "SELECT id FROM trades WHERE condition_id=? AND resolved=0", (cid,)
        ).fetchone())

    def record_price(self, token_id: str, price: float):
        self.conn.execute(
            "INSERT INTO price_history (token_id,price,recorded_at) VALUES (?,?,?)",
            (token_id, price, datetime.now(timezone.utc).isoformat())
        )
        self.conn.commit()

    def get_price_2h_ago(self, token_id: str) -> Optional[float]:
        two_h_ago = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        row = self.conn.execute(
            "SELECT price FROM price_history WHERE token_id=? AND recorded_at >= ? "
            "ORDER BY recorded_at ASC LIMIT 1",
            (token_id, two_h_ago)
        ).fetchone()
        return float(row[0]) if row else None

    def log_monitor(self, cid: str, phase: str, live_price: float,
                    entry_price: float, drift: float,
                    action: str, reasoning: str):
        try:
            self.conn.execute(
                "INSERT INTO monitor_log "
                "(logged_at,condition_id,phase,live_price,entry_price,drift,llm_action,reasoning) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (datetime.now(timezone.utc).isoformat(), cid, phase,
                 live_price, entry_price, drift, action, reasoning[:300])
            )
            self.conn.commit()
        except Exception as e:
            log.debug("DB log_monitor: %s", e)

    def update_position_monitor(self, cid: str, phase: str, live_price: float,
                                 action: str, notes: str, exited: bool = False):
        try:
            self.conn.execute(
                "UPDATE trades SET phase=?, last_monitored_at=?, last_live_price=?, "
                "last_monitor_action=?, monitor_notes=?, exited_early=? "
                "WHERE condition_id=?",
                (phase,
                 datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 live_price, action, notes[:200], int(exited), cid)
            )
            self.conn.commit()
        except Exception as e:
            log.debug("DB update_position_monitor: %s", e)


# ═══════════════════════════════════════════════════
# POLYMARKET FETCHER — v5 (FIXED market discovery)
# ═══════════════════════════════════════════════════
#
# ROOT CAUSE OF "5 markets found" BUG:
#   1. Gamma /events endpoint returns event-level objects
#      which DON'T have conditionId directly — need to
#      extract nested markets array from each event
#   2. Time filter was using endDate which is often NULL
#      for active in-play/today markets
#   3. CLOB /simplified-markets is the most reliable source
#      but Layer 3 only ran as fallback — now runs ALWAYS
#   4. hours>72 filter was killing today's markets that
#      Polymarket listed with end_date = tomorrow midnight
#
# FIX:
#   - Layer 1: Gamma /events → extract nested markets[]
#   - Layer 2: Gamma /markets with broader params + tag_id
#   - Layer 3: CLOB /markets (not simplified) with proper
#              active=true filter — ALWAYS runs, not fallback
#   - Layer 4: Direct CLOB /markets search by sport tags
#   - Time filter: relaxed to -6h .. +96h; if no endDate
#     but volume >1000, accept it (active in-play market)
#   - Volume floor: reduced to 100 (was 500)
# ═══════════════════════════════════════════════════
class PolymarketFetcher:

    # v5: broader game detection
    GAME_INDICATORS = [
        " vs ", " vs. ", " v ", " @ ", " at ",
        "to beat", "moneyline", "beats ", "wins vs",
        "to win", "will win", "will beat",
        "win tonight", "win today",
    ]

    FUTURES_SKIP = [
        "win the 2025", "win the 2026", "nba finals", "nba champion",
        "world series", "stanley cup", "super bowl", "world cup winner",
        "mvp", "rookie of the year", "most valuable", "all-star",
        "eastern conference champion", "western conference champion",
        "season wins", "make the playoffs", "win the championship",
        "first overall pick", "win the season", "qualify for",
        "advance to", "promote", "relegated", "top scorer", "golden boot",
        "serie a title", "la liga title", "premier league title",
        "bundesliga title", "ligue 1 title", "win the series",
        "win the pennant",
    ]

    IPL_TEAMS = [
        "MUMBAI INDIANS", "MI ", "CHENNAI SUPER KINGS", "CSK",
        "ROYAL CHALLENGERS", "RCB", "KOLKATA KNIGHT RIDERS", "KKR",
        "SUNRISERS HYDERABAD", "SRH", "RAJASTHAN ROYALS", "RR",
        "DELHI CAPITALS", "DC ", "GUJARAT TITANS", "GT ",
        "LUCKNOW SUPER GIANTS", "LSG", "PUNJAB KINGS", "PBKS",
    ]

    def __init__(self):
        self._sess = requests.Session()
        self._sess.headers.update(HEADERS)

    def _is_game_market(self, title: str) -> bool:
        tl = title.lower()
        if any(skip in tl for skip in self.FUTURES_SKIP):
            return False
        return any(ind in tl for ind in self.GAME_INDICATORS)

    def _is_ipl_market(self, title: str) -> bool:
        t = title.upper()
        return any(team in t for team in self.IPL_TEAMS) or "IPL" in t

    def _is_valid_game(self, title: str) -> bool:
        return self._is_game_market(title) or self._is_ipl_market(title)

    # ── Normalize any market dict to standard form ─
    @staticmethod
    def _norm(m: dict) -> dict:
        """Normalize varied API response shapes to one standard dict."""
        return {
            "conditionId":  (m.get("conditionId") or m.get("condition_id") or
                             m.get("id", "")),
            "question":     (m.get("question") or m.get("title") or
                             m.get("market_slug", "")),
            "clobTokenIds": (m.get("clobTokenIds") or m.get("token_ids") or
                             m.get("tokenIds") or []),
            "tokens":       m.get("tokens") or m.get("outcomes") or [],
            "endDate":      (m.get("endDate") or m.get("end_date_iso") or
                             m.get("end_date") or m.get("closeTime") or
                             m.get("close_time") or ""),
            "volume":       float(m.get("volume") or m.get("volumeNum") or
                                  m.get("usdVolume") or 0),
            "id":           m.get("id", ""),
            "yes_vol":      0.0,
            "no_vol":       0.0,
            "_raw":         m,
        }

    # ── Layer 1: Gamma /events with nested markets ─
    def _layer1_gamma_events(self) -> list:
        """
        Gamma /events returns event objects that contain a 'markets' array.
        We must unpack them. Common mistake: treating event-level object
        as a market (it has no tokenIds).
        """
        out  = []
        seen = set()

        event_params_list = [
            {"active": "true", "closed": "false", "limit": "200",
             "order": "volume", "ascending": "false"},
            {"active": "true", "closed": "false", "limit": "200",
             "tag_slug": "sports"},
            {"active": "true", "closed": "false", "limit": "200",
             "tag_slug": "cricket"},
            {"active": "true", "closed": "false", "limit": "200",
             "tag_slug": "basketball"},
            {"active": "true", "closed": "false", "limit": "200",
             "tag_slug": "baseball"},
            {"active": "true", "closed": "false", "limit": "200",
             "tag_slug": "soccer"},
        ]

        for params in event_params_list:
            for endpoint in [f"{GAMMA}/events", f"{GAMMA}/markets"]:
                try:
                    r = self._sess.get(endpoint, params=params, timeout=15)
                    if r.status_code != 200:
                        continue
                    data = r.json()

                    # Handle both list and dict responses
                    items = []
                    if isinstance(data, list):
                        items = data
                    elif isinstance(data, dict):
                        items = (data.get("events") or data.get("markets") or
                                 data.get("data") or [])

                    for item in items:
                        # Events have nested markets — unpack them
                        nested = item.get("markets") or item.get("events") or []
                        if nested:
                            for nm in nested:
                                cid = (nm.get("conditionId") or
                                       nm.get("condition_id") or "")
                                if cid and cid not in seen:
                                    seen.add(cid)
                                    out.append(nm)
                        else:
                            # Direct market object
                            cid = (item.get("conditionId") or
                                   item.get("condition_id") or "")
                            if cid and cid not in seen:
                                seen.add(cid)
                                out.append(item)
                except Exception as e:
                    log.debug("Layer1 %s: %s", endpoint, e)

        log.info("  [L1-Gamma-Events] %d items", len(out))
        return out

    # ── Layer 2: Gamma /markets broad search ───────
    def _layer2_gamma_markets(self) -> list:
        """
        Direct /markets search with many queries + tag IDs.
        Polymarket tag IDs for sports: 6=sports, 7=basketball,
        8=baseball, 12=soccer, 21=cricket
        """
        out  = []
        seen = set()

        # Both text queries AND tag-based fetches
        text_queries = [
            "vs", " v ", "tonight", "today", "moneyline",
            "will win", "to beat", "at home",
            "nba", "mlb", "nhl", "soccer", "cricket", "ipl",
            "lakers", "celtics", "warriors", "nuggets", "heat",
            "yankees", "dodgers", "mets", "cubs", "braves",
            "mumbai", "chennai", "kolkata", "sunrisers",
            "rajasthan", "delhi capitals", "gujarat", "lucknow",
            "punjab", "royal challengers bangalore",
            "arsenal", "chelsea", "liverpool", "manchester",
            "real madrid", "barcelona",
        ]

        tag_ids = ["6", "7", "8", "9", "12", "21", "1349", "1350"]

        # Text queries
        for q in text_queries:
            try:
                r = self._sess.get(
                    f"{GAMMA}/markets",
                    params={"active": "true", "closed": "false",
                            "limit": "100", "q": q,
                            "order": "volume", "ascending": "false"},
                    timeout=12,
                )
                if r.status_code != 200:
                    continue
                data  = r.json()
                items = data if isinstance(data, list) else (
                    data.get("markets") or data.get("data") or []
                )
                for m in items:
                    cid = (m.get("conditionId") or m.get("condition_id") or "")
                    if cid and cid not in seen:
                        seen.add(cid)
                        out.append(m)
            except Exception as e:
                log.debug("Layer2-text '%s': %s", q, e)

        # Tag ID fetches
        for tid in tag_ids:
            try:
                r = self._sess.get(
                    f"{GAMMA}/markets",
                    params={"active": "true", "closed": "false",
                            "limit": "200", "tag_id": tid,
                            "order": "volume", "ascending": "false"},
                    timeout=12,
                )
                if r.status_code != 200:
                    continue
                data  = r.json()
                items = data if isinstance(data, list) else (
                    data.get("markets") or data.get("data") or []
                )
                for m in items:
                    cid = (m.get("conditionId") or m.get("condition_id") or "")
                    if cid and cid not in seen:
                        seen.add(cid)
                        out.append(m)
            except Exception as e:
                log.debug("Layer2-tag %s: %s", tid, e)

        log.info("  [L2-Gamma-Markets] %d unique markets", len(out))
        return out

    # ── Layer 3: CLOB /markets (ALWAYS runs) ───────
    def _layer3_clob_markets(self) -> list:
        """
        CLOB /markets endpoint — most reliable for active markets.
        Uses pagination. ALWAYS runs (not just fallback).
        """
        out    = []
        seen   = set()
        cursor = "MA=="

        for page in range(20):  # up to 2000 markets
            try:
                r = self._sess.get(
                    f"{CLOB}/simplified-markets",
                    params={"next_cursor": cursor, "active": "true"},
                    timeout=15,
                )
                if r.status_code != 200:
                    log.warning("  [L3-CLOB] HTTP %d on page %d",
                                r.status_code, page)
                    break
                raw = r.json()
                if isinstance(raw, str):
                    break

                items  = raw.get("data", []) if isinstance(raw, dict) else raw
                cursor = raw.get("next_cursor", "") if isinstance(raw, dict) else ""

                if not items:
                    break

                for m in items:
                    title = (m.get("question") or m.get("title") or "")
                    if not self._is_valid_game(title):
                        continue
                    # Normalize token IDs from CLOB format
                    if "tokens" in m and not m.get("clobTokenIds"):
                        m["clobTokenIds"] = [
                            t.get("token_id", "") for t in m.get("tokens", [])
                        ]
                    cid = (m.get("condition_id") or m.get("conditionId") or "")
                    m["conditionId"] = cid
                    if cid and cid not in seen:
                        seen.add(cid)
                        out.append(m)

                log.debug("  [L3-CLOB] page %d: +%d game markets (cursor=%s)",
                          page, len(out), cursor[:8] if cursor else "END")

                if not cursor or cursor in ("MA==", ""):
                    break
                time.sleep(0.2)

            except Exception as e:
                log.warning("  [L3-CLOB] page %d error: %s", page, e)
                break

        log.info("  [L3-CLOB] %d game markets total", len(out))
        return out

    # ── Layer 4: CLOB /markets by condition IDs ────
    def _layer4_clob_sports_search(self) -> list:
        """
        Fetch CLOB /markets directly — different endpoint than
        simplified-markets. Returns richer data including volume splits.
        """
        out  = []
        seen = set()
        # CLOB has a /markets endpoint with query params
        for params in [
            {"active": "true", "limit": "500"},
        ]:
            try:
                r = self._sess.get(f"{CLOB}/markets",
                                   params=params, timeout=15)
                if r.status_code != 200:
                    continue
                data  = r.json()
                items = data if isinstance(data, list) else (
                    data.get("data") or data.get("markets") or []
                )
                for m in items:
                    title = (m.get("question") or m.get("market_slug") or "")
                    cid   = (m.get("condition_id") or m.get("conditionId") or "")
                    if self._is_valid_game(title) and cid and cid not in seen:
                        seen.add(cid)
                        m["conditionId"] = cid
                        out.append(m)
            except Exception as e:
                log.debug("Layer4-CLOB-markets: %s", e)

        log.info("  [L4-CLOB-Direct] %d game markets", len(out))
        return out

    def get_game_markets(self) -> list:
        """Run all 4 layers, deduplicate, return game markets."""
        log.info("[DISCOVERY] Starting 4-layer market scan...")

        all_raw = []
        all_raw.extend(self._layer1_gamma_events())
        all_raw.extend(self._layer2_gamma_markets())
        all_raw.extend(self._layer3_clob_markets())   # always runs now
        all_raw.extend(self._layer4_clob_sports_search())

        seen_cids    = set()
        game_markets = []
        skipped_dupe = 0
        skipped_nogame = 0
        skipped_notok  = 0

        for m in all_raw:
            norm  = self._norm(m)
            cid   = norm["conditionId"]
            title = norm["question"].strip()

            if not cid or not title:
                continue
            if cid in seen_cids:
                skipped_dupe += 1
                continue
            if not self._is_valid_game(title):
                skipped_nogame += 1
                continue

            sport = self.detect_sport(title)
            if sport == "OTHER":
                # Still include if has strong game indicators
                if not self._is_game_market(title):
                    continue

            seen_cids.add(cid)
            # Attach normalized data back for parse_market
            m["conditionId"] = cid
            m["_title_clean"] = title
            game_markets.append(m)
            log.debug("  [OK] [%s] %s", sport, title[:65])

        log.info("[DISCOVERY] Found %d game markets | dupes=%d no-game=%d",
                 len(game_markets), skipped_dupe, skipped_nogame)
        return game_markets

    @staticmethod
    def parse_market(m: dict) -> Optional[dict]:
        title = (m.get("_title_clean") or m.get("question") or
                 m.get("title") or "").strip()
        cid   = (m.get("conditionId") or m.get("condition_id") or "")
        if not title or not cid:
            return None

        # Token IDs — multiple possible locations
        tids = (m.get("clobTokenIds") or m.get("token_ids") or
                m.get("tokenIds") or [])
        if isinstance(tids, str):
            try:    tids = json.loads(tids)
            except: tids = []
        if len(tids) < 2:
            tokens = m.get("tokens") or m.get("outcomes") or []
            if isinstance(tokens, list) and len(tokens) >= 2:
                tids = [
                    t.get("token_id") or t.get("tokenId") or
                    t.get("id", "")
                    for t in tokens
                ]
        if len(tids) < 2 or not tids[0]:
            return None

        # End date — try multiple fields
        end_date = (m.get("endDate") or m.get("end_date_iso") or
                    m.get("end_date") or m.get("closeTime") or
                    m.get("close_time") or "")

        try:
            vol = float(m.get("volume") or m.get("volumeNum") or
                        m.get("usdVolume") or 0)
        except:
            vol = 0.0

        # Volume split for sentiment
        yes_vol, no_vol = 0.0, 0.0
        tokens_list = m.get("tokens") or m.get("outcomes") or []
        for tok in tokens_list:
            name = (tok.get("outcome") or tok.get("side") or "").upper()
            v    = float(tok.get("volume") or tok.get("volumeNum") or 0)
            if name == "YES":
                yes_vol = v
            elif name == "NO":
                no_vol = v

        return {
            "condition_id": cid,
            "title":        title,
            "yes_token":    str(tids[0]),
            "no_token":     str(tids[1]),
            "end_date":     str(end_date),
            "volume":       vol,
            "market_id":    m.get("id") or cid,
            "yes_vol":      yes_vol,
            "no_vol":       no_vol,
        }

    @staticmethod
    def detect_sport(title: str) -> str:
        t = title.upper()
        NBA_TEAMS = [
            "CELTICS","LAKERS","WARRIORS","NUGGETS","HEAT","NETS","KNICKS",
            "BULLS","PISTONS","CAVALIERS","BUCKS","PACERS","RAPTORS","MAGIC",
            "HAWKS","76ERS","SIXERS","HORNETS","WIZARDS","THUNDER","ROCKETS",
            "GRIZZLIES","JAZZ","CLIPPERS","SUNS","KINGS","BLAZERS","TRAILBLAZERS",
            "TIMBERWOLVES","SPURS","MAVERICKS","PELICANS",
        ]
        MLB_TEAMS = [
            "YANKEES","RED SOX","DODGERS","CUBS","METS","BRAVES","ASTROS",
            "PHILLIES","CARDINALS","GIANTS","PADRES","TIGERS","TWINS","ROYALS",
            "RANGERS","ATHLETICS","PIRATES","NATIONALS","ORIOLES","RAYS",
            "MARINERS","ANGELS","ROCKIES","BREWERS","REDS","MARLINS","WHITE SOX",
        ]
        NHL_TEAMS = [
            "MAPLE LEAFS","BRUINS","PENGUINS","BLACKHAWKS","OILERS","FLAMES",
            "CANUCKS","CAPITALS","LIGHTNING","PANTHERS","HURRICANES","AVALANCHE",
            "GOLDEN KNIGHTS","STARS","BLUES","PREDATORS","WILD","SENATORS",
            "CANADIENS","FLYERS","DEVILS","ISLANDERS","SABRES","DUCKS","SHARKS",
            "JETS","KINGS",
        ]
        IPL_KEYS = [
            "IPL","MUMBAI INDIANS","CHENNAI SUPER","ROYAL CHALLENGERS",
            "KOLKATA KNIGHT","SUNRISERS","RAJASTHAN ROYALS","DELHI CAPITALS",
            "GUJARAT TITANS","LUCKNOW SUPER","PUNJAB KINGS",
            " MI "," CSK "," RCB "," KKR "," SRH "," RR "," DC "," GT ",
            " LSG "," PBKS ",
        ]
        if any(k in t for k in IPL_KEYS):
            return "CRICKET"
        if any(k in t for k in ["NBA","BASKETBALL"] + NBA_TEAMS):
            return "NBA"
        if any(k in t for k in ["MLB","BASEBALL"] + MLB_TEAMS):
            return "MLB"
        if any(k in t for k in ["NHL","HOCKEY"] + NHL_TEAMS):
            return "NHL"
        if any(k in t for k in [
            "PREMIER LEAGUE","CHAMPIONS LEAGUE","LA LIGA","SERIE A",
            "BUNDESLIGA","MLS","EPL","ARSENAL","CHELSEA","LIVERPOOL",
            "REAL MADRID","BARCELONA","MANCHESTER","TOTTENHAM","ATLETICO",
            "JUVENTUS","MILAN","INTER","PSG","SOCCER","FC ","AFC ",
        ]):
            return "SOCCER"
        if any(k in t for k in ["CRICKET","T20","ODI","TEST MATCH"]):
            return "CRICKET"
        return "OTHER"

    @staticmethod
    def detect_league(title: str, sport: str) -> str:
        t = title.upper()
        if sport == "CRICKET":
            if any(k in t for k in [
                "IPL","MUMBAI INDIANS","CHENNAI","KOLKATA",
                "SUNRISERS","RAJASTHAN","DELHI CAPITAL","GUJARAT TITAN",
                "LUCKNOW","PUNJAB KINGS","ROYAL CHALLENGERS",
                " MI "," CSK "," RCB "," KKR "," SRH ",
            ]):
                return "IPL"
            return "CRICKET"
        if sport == "SOCCER":
            if any(k in t for k in ["CHAMPIONS LEAGUE","UCL"]): return "UCL"
            if any(k in t for k in ["PREMIER LEAGUE","ARSENAL","CHELSEA",
                                     "LIVERPOOL","TOTTENHAM","MANCHESTER"]): return "EPL"
            if any(k in t for k in ["LA LIGA","REAL MADRID","BARCELONA",
                                     "ATLETICO"]): return "LA_LIGA"
            if any(k in t for k in ["BUNDESLIGA","DORTMUND","LEVERKUSEN"]): return "BUND"
            if any(k in t for k in ["SERIE A","JUVENTUS","MILAN","INTER"]): return "SERIE_A"
            if "MLS" in t: return "MLS"
            return "SOCCER"
        return sport

    @staticmethod
    def extract_teams(title: str) -> tuple:
        patterns = [
            r"will\s+(.+?)\s+(?:win\s+(?:vs?\.?\s+|against\s+|over\s+)|beat\s+|defeat\s+)(.+?)(?:\?|$|\s+on|\s+\()",
            r"^(.+?)\s+vs\.?\s+(.+?)(?:\?|$|\s+on|\s+[-\u2013\(])",
            r"^(.+?)\s+v\s+(.+?)(?:\?|$|\s+on|\s+[-\u2013\(])",
            r"^(.+?)\s+(?:at|@)\s+(.+?)(?:\?|$|\s+on|\s+[-\u2013\(])",
            r"(.+?)\s+beats?\s+(.+?)(?:\?|$|\s+\()",
        ]
        for pat in patterns:
            m = re.search(pat, title, re.I)
            if m:
                a = re.sub(r"\s*(on|for|game|\d{4})\s*.*$", "", m.group(1),
                           flags=re.I).strip()
                b = re.sub(r"\s*(on|for|game|\d{4})\s*.*$", "", m.group(2),
                           flags=re.I).strip()
                if a and b and len(a) > 2 and len(b) > 2:
                    return a[:45], b[:45]
        words = title.split()
        mid   = len(words) // 2
        return " ".join(words[:mid])[:45], " ".join(words[mid:])[:45]

    @staticmethod
    def hours_to_resolution(end_date: str) -> float:
        """
        Parse end_date and return hours until resolution.
        Returns 999.0 if date is missing/unparseable.
        Negative = already past.
        """
        if not end_date or str(end_date).strip() in ("None", "", "null", "0"):
            return 999.0
        s = str(end_date).strip()
        # Remove trailing Z, handle microseconds
        fmts = [
            "%Y-%m-%dT%H:%M:%S.%fZ",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
        ]
        # Handle epoch timestamps (Polymarket sometimes sends unix ms)
        if s.isdigit() and len(s) >= 10:
            ts = int(s)
            if len(s) == 13:  # milliseconds
                ts = ts / 1000
            try:
                dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                return (dt - datetime.now(timezone.utc)).total_seconds() / 3600
            except:
                pass
        for fmt in fmts:
            try:
                clean = s[:len(fmt)+4]  # slight over-read is OK
                dt = datetime.strptime(clean[:len(fmt)], fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return (dt - datetime.now(timezone.utc)).total_seconds() / 3600
            except ValueError:
                continue
        log.debug("  hours_to_resolution: unparseable date '%s'", s[:30])
        return 999.0

    def get_midpoint(self, token_id: str) -> Optional[float]:
        if not token_id:
            return None
        try:
            r = self._sess.get(f"{CLOB}/midpoint",
                               params={"token_id": token_id}, timeout=6)
            r.raise_for_status()
            val = r.json().get("mid")
            return float(val) if val is not None else None
        except:
            return None

    def get_order_book_depth(self, token_id: str) -> dict:
        empty = {"total_depth": 0, "spread": 1, "bid_depth": 0, "ask_depth": 0}
        if not token_id:
            return empty
        try:
            r = self._sess.get(f"{CLOB}/book",
                               params={"token_id": token_id}, timeout=6)
            r.raise_for_status()
            data = r.json()
            bids = data.get("bids", [])
            asks = data.get("asks", [])
            bd   = sum(float(b.get("size", 0)) for b in bids[:5])
            ad   = sum(float(a.get("size", 0)) for a in asks[:5])
            bb   = float(bids[0]["price"]) if bids else 0
            ba   = float(asks[0]["price"]) if asks else 1
            return {"bid_depth": bd, "ask_depth": ad,
                    "total_depth": bd + ad, "spread": round(ba - bb, 4)}
        except:
            return empty

    def get_market_outcome(self, condition_id: str) -> Optional[bool]:
        try:
            r = self._sess.get(f"{CLOB}/markets/{condition_id}", timeout=6)
            r.raise_for_status()
            data = r.json()
            if data.get("resolved"):
                payouts = data.get("resolved_payout", [])
                if isinstance(payouts, list) and len(payouts) >= 2:
                    p0, p1 = float(payouts[0]), float(payouts[1])
                    if p0 != p1:
                        return p0 > p1
            for token in data.get("tokens", []):
                if token.get("winner"):
                    return token.get("outcome", "").upper() == "YES"
        except:
            pass
        return None


# ═══════════════════════════════════════════════════
# ESPN DATA FETCHER — v4 (richer data)
# ═══════════════════════════════════════════════════
class ESPNFetcher:
    _cache: dict = {}

    SPORT_MAP = {
        "NBA":    ("basketball", "nba"),
        "MLB":    ("baseball",   "mlb"),
        "NHL":    ("hockey",     "nhl"),
        "SOCCER": ("soccer",     "eng.1"),
    }

    # IPL — Cricinfo/ESPN Cricinfo API
    CRICINFO_BASE = "https://www.espncricinfo.com"

    def _get(self, url, params=None):
        key = url + str(params)
        if key in self._cache:
            return self._cache[key]
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=10)
            r.raise_for_status()
            data = r.json()
            self._cache[key] = data
            return data
        except:
            return None

    # ── Season win% ────────────────────────────────
    def get_standings(self, sport: str) -> dict:
        if sport not in self.SPORT_MAP:
            return {}
        sk, league = self.SPORT_MAP[sport]
        data = self._get(f"{ESPN_API}/{sk}/{league}/teams", {"limit": 50})
        if not data:
            return {}
        result = {}
        for t in (data.get("sports", [{}])[0]
                  .get("leagues", [{}])[0]
                  .get("teams", [])):
            info   = t.get("team", {})
            record = info.get("record", {}).get("items", [{}])
            if not record:
                continue
            stats = {s["name"]: float(s.get("value", 0))
                     for s in record[0].get("stats", []) if "name" in s}
            wins   = stats.get("wins",   0)
            losses = stats.get("losses", 0)
            total  = wins + losses
            wp     = round(wins / total, 4) if total > 0 else 0.5
            for key in ["displayName","shortDisplayName","name",
                        "abbreviation","location"]:
                n = info.get(key, "").strip().lower()
                if n and len(n) > 1:
                    result[n] = wp
        return result

    def find_win_pct(self, standings: dict, team: str) -> float:
        if not team or not standings:
            return 0.5
        tl = team.lower().strip()
        if tl in standings:
            return standings[tl]
        for k, wp in standings.items():
            if tl in k or k in tl:
                return wp
        words = set(tl.split())
        best, bwp = 0, 0.5
        for k, wp in standings.items():
            ov = len(words & set(k.split()))
            if ov > best:
                best, bwp = ov, wp
        return bwp if best > 0 else 0.5

    # ── Home team detection ────────────────────────
    def get_home_team(self, sport: str, a: str, b: str) -> Optional[str]:
        if sport not in self.SPORT_MAP:
            return None
        sk, league = self.SPORT_MAP[sport]
        data = self._get(f"{ESPN_API}/{sk}/{league}/scoreboard")
        if not data:
            return None
        al, bl = a.lower()[:5], b.lower()[:5]
        for event in data.get("events", []):
            comp  = event.get("competitions", [{}])[0]
            comps = comp.get("competitors", [])
            names = " ".join(c.get("team", {}).get("displayName", "").lower()
                             for c in comps)
            if al not in names and bl not in names:
                continue
            for c in comps:
                if c.get("homeAway") == "home":
                    return c.get("team", {}).get("displayName")
        return None

    # ── Recent form (last 5 games) ─────────────────
    def get_recent_form(self, sport: str, team: str) -> str:
        """Returns string like 'W W L W W (4-1)' or 'N/A'"""
        if sport not in self.SPORT_MAP:
            return "N/A"
        sk, league = self.SPORT_MAP[sport]
        # Get team ID first
        standings = self._get(f"{ESPN_API}/{sk}/{league}/teams", {"limit": 50})
        if not standings:
            return "N/A"
        team_id = None
        tl = team.lower().strip()
        for t in (standings.get("sports", [{}])[0]
                  .get("leagues", [{}])[0]
                  .get("teams", [])):
            info = t.get("team", {})
            for key in ["displayName","shortDisplayName","name","abbreviation","location"]:
                n = info.get(key, "").strip().lower()
                if n and (tl in n or n in tl):
                    team_id = info.get("id")
                    break
            if team_id:
                break
        if not team_id:
            return "N/A"
        data = self._get(f"{ESPN_API}/{sk}/{league}/teams/{team_id}/schedule",
                         {"season": datetime.now().year})
        if not data:
            return "N/A"
        results = []
        for event in data.get("events", [])[-10:]:
            comp = event.get("competitions", [{}])[0]
            for c in comp.get("competitors", []):
                if str(c.get("team", {}).get("id", "")) == str(team_id):
                    if c.get("winner"):
                        results.append("W")
                    elif c.get("winner") is False:
                        results.append("L")
                    break
        last5 = results[-5:] if len(results) >= 5 else results
        w = last5.count("W"); l = last5.count("L")
        return f"{' '.join(last5)} ({w}-{l})" if last5 else "N/A"

    # ── Head-to-head ───────────────────────────────
    def get_h2h(self, sport: str, a: str, b: str) -> str:
        """Returns string like 'A won 2 of last 3 vs B' or 'N/A'"""
        if sport not in self.SPORT_MAP:
            return "N/A"
        sk, league = self.SPORT_MAP[sport]
        data = self._get(f"{ESPN_API}/{sk}/{league}/scoreboard")
        if not data:
            return "N/A"
        al, bl = a.lower()[:5], b.lower()[:5]
        for event in data.get("events", []):
            comp  = event.get("competitions", [{}])[0]
            comps = comp.get("competitors", [])
            names = " ".join(c.get("team", {}).get("displayName", "").lower()
                             for c in comps)
            if al in names and bl in names:
                records = {}
                for c in comps:
                    nm = c.get("team", {}).get("displayName", "")
                    records[nm] = {
                        "wins":   c.get("records", [{}])[0].get("summary", "0-0"),
                    }
                parts = [f"{k}: {v['wins']}" for k, v in records.items()]
                return " | ".join(parts) if parts else "N/A"
        return "N/A"

    # ── Injury report ──────────────────────────────
    def get_injuries(self, sport: str, team: str) -> list:
        """Returns list of injured player names (key players only)."""
        if sport not in self.SPORT_MAP:
            return []
        sk, league = self.SPORT_MAP[sport]
        standings = self._get(f"{ESPN_API}/{sk}/{league}/teams", {"limit": 50})
        if not standings:
            return []
        team_id = None
        tl = team.lower().strip()
        for t in (standings.get("sports", [{}])[0]
                  .get("leagues", [{}])[0]
                  .get("teams", [])):
            info = t.get("team", {})
            for key in ["displayName","shortDisplayName","name","abbreviation"]:
                n = info.get(key, "").strip().lower()
                if n and (tl in n or n in tl):
                    team_id = info.get("id")
                    break
            if team_id:
                break
        if not team_id:
            return []
        data = self._get(f"{ESPN_API}/{sk}/{league}/teams/{team_id}/injuries")
        if not data:
            return []
        injured = []
        for item in (data.get("injuries") or [])[:5]:
            athlete = item.get("athlete", {})
            status  = item.get("status", "")
            if status.upper() in ("OUT", "DOUBTFUL", "QUESTIONABLE"):
                name = athlete.get("displayName", "")
                pos  = athlete.get("position", {}).get("abbreviation", "")
                if name:
                    injured.append(f"{name} ({pos}) [{status}]")
        return injured[:4]

    # ── MLB pitcher ERA ────────────────────────────
    def get_mlb_eras(self, a: str, b: str) -> dict:
        data = self._get(f"{ESPN_API}/baseball/mlb/scoreboard")
        if not data:
            return {}
        al, bl = a.lower()[:5], b.lower()[:5]
        for event in data.get("events", []):
            comp  = event.get("competitions", [{}])[0]
            comps = comp.get("competitors", [])
            names = " ".join(c.get("team", {}).get("displayName", "").lower()
                             for c in comps)
            if al not in names and bl not in names:
                continue
            result = {}
            for c in comps:
                role  = c.get("homeAway", "home")
                probs = c.get("probables") or []
                if not probs:
                    continue
                stats = {s.get("name"): s.get("displayValue", "4.50")
                         for s in probs[0].get("statistics", [])}
                try:
                    era = float(str(stats.get("ERA", "4.50")).replace("-", "4.50"))
                except:
                    era = 4.50
                result[f"{role}_era"] = era
                pitcher = probs[0].get("athlete", {}).get("displayName", "?")
                result[f"{role}_pitcher"] = pitcher
            return result
        return {}

    # ── IPL specific data ──────────────────────────
    def get_ipl_data(self, title: str) -> dict:
        """
        Try to get IPL toss/venue data from ESPN cricinfo.
        Returns dict with toss_winner, pitch_type, venue.
        """
        result = {
            "toss_winner": "", "toss_choice": "",
            "pitch_type": "unknown", "venue": "",
            "avg_1st_innings_score": 0,
        }
        try:
            # ESPN cricinfo upcoming matches
            r = requests.get(
                "https://hs-consumer-api.espncricinfo.com/v1/pages/matches/upcoming",
                params={"lang": "en", "series": "8048"},  # IPL 2025 series ID
                headers=HEADERS, timeout=8
            )
            if r.status_code != 200:
                return result
            data    = r.json()
            matches = data.get("matches") or data.get("content", {}).get("matches", [])
            tl      = title.upper()
            for m in matches:
                teams = " ".join([
                    m.get("team1", {}).get("abbreviation", ""),
                    m.get("team2", {}).get("abbreviation", ""),
                    m.get("team1", {}).get("shortName", ""),
                    m.get("team2", {}).get("shortName", ""),
                ]).upper()
                # Basic match check
                if any(t in tl for t in teams.split() if len(t) > 1):
                    result["venue"] = m.get("ground", {}).get("name", "")
                    # Pitch/venue type heuristic
                    venue_name = result["venue"].lower()
                    if any(v in venue_name for v in ["wankhede","chinnaswamy","eden"]):
                        result["pitch_type"] = "batting-friendly"
                        result["avg_1st_innings_score"] = 185
                    elif any(v in venue_name for v in ["chepauk","feroz shah","kotla"]):
                        result["pitch_type"] = "spin-friendly"
                        result["avg_1st_innings_score"] = 165
                    else:
                        result["pitch_type"] = "balanced"
                        result["avg_1st_innings_score"] = 175
                    break
        except Exception as e:
            log.debug("IPL data fetch: %s", e)
        return result


# ═══════════════════════════════════════════════════
# STAT MODEL (quick pre-filter only)
# ═══════════════════════════════════════════════════
class StatModel:
    HOME_ADV = {
        "NBA": 0.058, "MLB": 0.040, "NHL": 0.050,
        "SOCCER": 0.075, "CRICKET": 0.030,
    }

    def estimate(self, sport, wp_a, wp_b, a_is_home,
                 home_era=4.5, away_era=4.5) -> float:
        total = wp_a + wp_b
        base  = (wp_a / total) if total > 0 else 0.5
        if a_is_home:
            base += self.HOME_ADV.get(sport, 0.04)
        if sport == "MLB" and (home_era != 4.5 or away_era != 4.5):
            base += (away_era - home_era) * 0.025
        return round(max(0.05, min(0.95, base)), 4)


# ═══════════════════════════════════════════════════
# LLM MODEL — v4 (FINAL DECISION MAKER)
# ═══════════════════════════════════════════════════
class LLMModel:

    SYSTEM = """You are an elite sports betting analyst with a strict mandate:
ONLY recommend bets you are highly confident will WIN (80%+ win rate target).

You analyze all available data and output a FINAL BETTING DECISION.

YOUR FRAMEWORKS BY SPORT:

NBA (weights):
  - Season win% ratio (30%) + Home advantage +5.8% (20%)
  - Recent form last 5 games (25%) — form > season record
  - Key injuries (20%) — star player OUT is massive edge
  - H2H record (5%)

MLB (weights):
  - Starting pitcher ERA matchup (35%) — most important factor
  - Season win% (25%) + Home advantage +4% (15%)
  - Recent form (15%) + Bullpen ERA if known (10%)

NHL (weights):
  - Goalie save% and recent form (30%)
  - Season win% (25%) + Home advantage +5% (20%)
  - Injuries (15%) + Line movement (10%)

SOCCER (weights):
  - Home advantage +7.5% (25%) — biggest in soccer
  - Season table position / win% (25%)
  - Recent form (25%) — last 5 games critical
  - H2H record at venue (15%) + Injuries (10%)

CRICKET/IPL (weights):
  - Toss winner (batting/bowling choice) on this pitch: +8% (20%)
  - Head-to-head record (20%)
  - Recent form last 5 games (25%)
  - Home/neutral venue advantage (10%)
  - Pitch type (batting/spin friendly) (15%)
  - Key player injuries (10%)

LINE MOVEMENT RULES:
  - Price UP (toward YES) = sharp money on YES → supports YES bet
  - Price DOWN (from YES) = sharp money on NO → supports NO bet
  - Stable line = no strong signal

MARKET SENTIMENT:
  - YES volume >65% = strong public lean YES (slight fade consideration)
  - YES volume <35% = strong public lean NO (slight fade consideration)
  - 45-55% = balanced market

STRICT RULES:
  1. If confidence < 72, output "NO BET" (protect win rate)
  2. Never bet on games with insufficient data (both teams wp=0.500 AND no form)
  3. For injuries: if a star player (top scorer, ace pitcher, star goalkeeper) is OUT → adjust probability ±15%
  4. Line movement opposing your thesis? Reduce confidence by 10 points
  5. If market price already reflects true probability (edge < 4%) → NO BET

OUTPUT FORMAT (JSON only, no other text):
{
  "yes_probability": 0.00,
  "confidence": 0,
  "bet_side": "YES" | "NO" | "NO BET",
  "key_factors": ["factor1", "factor2", "factor3"],
  "reasoning": "2-3 sentence explanation",
  "data_quality": "HIGH" | "MEDIUM" | "LOW"
}

Rules: yes_probability 0.05-0.95, confidence 50-95, be SELECTIVE not trigger-happy."""

    def estimate(self, gd: GameData) -> Optional[dict]:
        """Takes a GameData object and returns LLM decision."""

        # Build rich prompt
        msg_parts = [
            f"=== GAME ANALYSIS REQUEST ===",
            f"Market: {gd.title}",
            f"Sport: {gd.sport} | League: {gd.league}",
            f"",
            f"--- TEAMS ---",
            f"Team A (YES = Team A wins): {gd.team_a}",
            f"Team B (NO = Team B wins):  {gd.team_b}",
            f"",
            f"--- WIN PROBABILITIES (Season) ---",
            f"Team A season win%: {gd.wp_a:.3f}",
            f"Team B season win%: {gd.wp_b:.3f}",
            f"Home team: {gd.home_team or 'unknown'} | Team A is home: {gd.a_is_home}",
            f"",
            f"--- RECENT FORM (last 5 games) ---",
            f"Team A form: {gd.form_a}",
            f"Team B form: {gd.form_b}",
            f"",
            f"--- HEAD TO HEAD ---",
            f"H2H record: {gd.h2h}",
            f"",
            f"--- INJURIES ---",
            f"Team A injuries: {', '.join(gd.injuries_a) if gd.injuries_a else 'None reported'}",
            f"Team B injuries: {', '.join(gd.injuries_b) if gd.injuries_b else 'None reported'}",
            f"",
            f"--- MARKET DATA ---",
            f"Current YES price: {gd.yes_price:.3f} (implied prob: {gd.yes_price:.1%})",
            f"Current NO price:  {gd.no_price:.3f}  (implied prob: {gd.no_price:.1%})",
            f"Market volume: ${gd.volume:,.0f}",
            f"YES volume %: {gd.yes_vol_pct:.1%}",
            f"Line movement (2h): {gd.line_movement} ({gd.movement_pct:+.1%})",
            f"Order book depth: ${gd.depth.get('total_depth',0):,.0f} | Spread: {gd.depth.get('spread',1):.4f}",
            f"Hours to close: {gd.hours_to_close:.1f}h",
            f"",
            f"--- STAT MODEL ESTIMATE ---",
            f"Quick stat estimate (reference only): {gd.stat_prob:.3f}",
        ]

        # MLB pitcher data
        if gd.sport == "MLB" and (gd.home_era != 4.5 or gd.away_era != 4.5):
            msg_parts += [
                f"",
                f"--- MLB PITCHING ---",
                f"Home pitcher ERA: {gd.home_era:.2f}",
                f"Away pitcher ERA: {gd.away_era:.2f}",
            ]

        # IPL specific
        if gd.league == "IPL":
            msg_parts += [
                f"",
                f"--- IPL SPECIFIC ---",
                f"Toss winner: {gd.toss_winner or 'not yet'}",
                f"Pitch type: {gd.pitch_type}",
                f"Venue avg 1st innings: {gd.venue_avg_score:.0f}",
            ]

        msg_parts.append(f"\nProvide your final betting analysis in JSON format:")
        msg = "\n".join(msg_parts)

        for model in [PRIMARY_MODEL, FALLBACK_MODEL]:
            for attempt in range(2):
                try:
                    t0 = time.time()
                    r  = requests.post(
                        "https://openrouter.ai/api/v1/chat/completions",
                        headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}",
                                 "Content-Type": "application/json"},
                        json={"model": model,
                              "messages": [
                                  {"role": "system", "content": self.SYSTEM},
                                  {"role": "user",   "content": msg},
                              ],
                              "max_tokens": 400, "temperature": 0.05},
                        timeout=50,
                    )
                    elapsed = time.time() - t0
                    log.info("  LLM %.1fs [%s]", elapsed, model.split("/")[-1])
                    if r.status_code != 200:
                        log.warning("  LLM HTTP %d", r.status_code)
                        time.sleep(2)
                        continue
                    raw = (r.json()
                           .get("choices", [{}])[0]
                           .get("message", {})
                           .get("content", ""))
                    if not raw:
                        continue
                    raw_clean = re.sub(r"```(?:json)?|```", "", raw).strip()
                    # Handle DeepSeek R1 <think> tags
                    raw_clean = re.sub(r"<think>.*?</think>", "", raw_clean,
                                       flags=re.DOTALL).strip()
                    m_json    = re.search(r"\{[^{}]+\}", raw_clean, re.DOTALL)
                    if not m_json:
                        log.warning("  LLM no JSON found in: %s", raw_clean[:100])
                        continue
                    parsed = json.loads(m_json.group(0))
                    prob   = max(0.05, min(0.95, float(parsed.get("yes_probability", 0.5))))
                    conf   = max(40,   min(95,   int(parsed.get("confidence", 55))))
                    side   = str(parsed.get("bet_side", "NO BET")).upper().strip()
                    dq     = str(parsed.get("data_quality", "MEDIUM")).upper()
                    return {
                        "prob":         prob,
                        "confidence":   conf,
                        "bet_side":     side,
                        "factors":      parsed.get("key_factors", []),
                        "reasoning":    parsed.get("reasoning", ""),
                        "data_quality": dq,
                        "model":        model,
                    }
                except json.JSONDecodeError as e:
                    log.warning("  LLM JSON parse error: %s", e)
                except requests.Timeout:
                    log.warning("  LLM timeout [%s]", model)
                except Exception as e:
                    log.warning("  LLM error: %s", e)
                if attempt == 0:
                    time.sleep(3)
        return None


# ═══════════════════════════════════════════════════
# KELLY + SIZING
# ═══════════════════════════════════════════════════
def calc_kelly(prob: float, price: float) -> float:
    if price <= 0 or price >= 1:
        return 0.0
    b = (1.0 / price) - 1.0
    f = max(0.0, (b * prob - (1 - prob)) / b)
    return round(f * KELLY_FRACTION, 5)

def size_bet(kf: float, balance: float, edge: float,
             confidence: int) -> float:
    raw = kf * balance
    # Scale up slightly for high confidence + high edge
    if edge > 0.12 and confidence >= 80:
        raw *= 1.30
    elif edge > 0.08 and confidence >= 75:
        raw *= 1.15
    capped = min(raw, MAX_EXPOSURE_PCT * balance, MAX_BET_USD)
    return round(capped, 2) if capped >= MIN_BET_USD else 0.0


# ═══════════════════════════════════════════════════
# CLOB EXECUTOR
# ═══════════════════════════════════════════════════
class CLOBExecutor:
    def __init__(self):
        self.client = None
        if PAPER_MODE or not PRIVATE_KEY:
            log.info("📄 Paper mode active")
            return
        try:
            from py_clob_client.client import ClobClient
            self.client = ClobClient(
                host=CLOB, key=PRIVATE_KEY, chain_id=CHAIN_ID,
                signature_type=SIGNATURE_TYPE, funder=POLYMARKET_FUNDER_ADDRESS,
            )
            self.client.set_api_creds(self.client.create_or_derive_api_creds())
            log.info("✅ CLOB LIVE ready")
        except Exception as e:
            log.warning("CLOB init failed: %s", e)

    def get_balance(self) -> float:
        if not self.client:
            return INITIAL_BALANCE
        try:
            from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
            r = self.client.get_balance_allowance(
                BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
            return round(int(r.get("balance", 0)) / 1_000_000, 2)
        except:
            return 0.0

    def buy(self, token_id: str, amount: float) -> Optional[dict]:
        if not self.client:
            return {"paper": True}
        try:
            from py_clob_client.clob_types import MarketOrderArgs, OrderType
            from py_clob_client.order_builder.constants import BUY
            order  = MarketOrderArgs(token_id=token_id, amount=amount,
                                     side=BUY, order_type=OrderType.FOK)
            signed = self.client.create_market_order(order)
            return self.client.post_order(signed, OrderType.FOK)
        except Exception as e:
            log.error("CLOB buy: %s", e)
            return None

    def sell(self, token_id: str, shares: float) -> Optional[dict]:
        """Sell / exit a position by placing a limit-market sell order."""
        if not self.client:
            return {"paper": True}
        try:
            from py_clob_client.clob_types import MarketOrderArgs, OrderType
            from py_clob_client.order_builder.constants import SELL
            order  = MarketOrderArgs(token_id=token_id, amount=shares,
                                     side=SELL, order_type=OrderType.FOK)
            signed = self.client.create_market_order(order)
            return self.client.post_order(signed, OrderType.FOK)
        except Exception as e:
            log.error("CLOB sell: %s", e)
            return None


# ═══════════════════════════════════════════════════
# SIMULATION MANAGER
# ═══════════════════════════════════════════════════
class SimManager:
    def __init__(self, path=SIM_FILE):
        self.path = path
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path) as f:
                    d = json.load(f)
                self.stats = BotStats(
                    balance=d.get("balance", INITIAL_BALANCE),
                    total_trades=d.get("total_trades", 0),
                    wins=d.get("wins", 0), losses=d.get("losses", 0),
                    total_pnl=d.get("total_pnl", 0.0),
                    daily_pnl=d.get("daily_pnl", 0.0),
                    daily_reset=d.get("daily_reset", ""),
                    llm_calls=d.get("llm_calls", 0),
                    llm_no_bet=d.get("llm_no_bet", 0),
                    pre_filter_skips=d.get("pre_filter_skips", 0),
                )
                self.positions = [TradePosition(**p) for p in d.get("positions", [])]
                return
            except Exception as e:
                log.warning("SimManager load: %s", e)
        self.stats     = BotStats()
        self.positions = []

    def save(self):
        d = asdict(self.stats)
        d["positions"] = [asdict(p) for p in self.positions]
        with open(self.path, "w") as f:
            json.dump(d, f, indent=2)

    def reset_daily(self):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self.stats.daily_reset != today:
            self.stats.daily_pnl   = 0.0
            self.stats.daily_reset = today
            self.save()

    def daily_limit_hit(self) -> bool:
        return self.stats.daily_pnl < -DAILY_LOSS_LIMIT

    def open_position(self, sig: SportSignal) -> TradePosition:
        pos = TradePosition(
            opened_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            sport=sig.sport, league=sig.league, title=sig.title,
            condition_id=sig.condition_id, yes_token=sig.yes_token,
            no_token=sig.no_token, direction=sig.direction,
            entry_price=sig.market_price, bet_usd=sig.bet_usd,
            shares=round(sig.bet_usd / sig.market_price, 4),
            model_prob=sig.model_prob, edge=sig.edge,
            llm_confidence=sig.llm_confidence,
        )
        self.positions.append(pos)
        self.stats.balance       = round(self.stats.balance - sig.bet_usd, 4)
        self.stats.total_trades += 1
        self.save()
        return pos

    def settle(self, poly: PolymarketFetcher):
        changed = False
        for pos in self.positions:
            if pos.resolved:
                continue
            outcome = poly.get_market_outcome(pos.condition_id)
            if outcome is None:
                continue
            won = (pos.direction == "YES" and outcome) or \
                  (pos.direction == "NO"  and not outcome)
            if won:
                pos.pnl     = round(pos.shares - pos.bet_usd, 4)
                pos.outcome = "WIN"
                self.stats.wins   += 1
                self.stats.balance = round(self.stats.balance + pos.shares, 4)
            else:
                pos.pnl     = -pos.bet_usd
                pos.outcome = "LOSS"
                self.stats.losses += 1
            pos.resolved         = True
            self.stats.total_pnl = round(self.stats.total_pnl + pos.pnl, 4)
            self.stats.daily_pnl = round(self.stats.daily_pnl + pos.pnl, 4)
            icon = f"{C.BGRN}WIN" if won else f"{C.BRED}LOSS"
            cp(f"\n✅ {icon}{C.R}  [{pos.sport}|{pos.league}] "
               f"{pos.title[:55]}  pnl=${pos.pnl:+.2f}  "
               f"conf={pos.llm_confidence}%")
            changed = True
        if changed:
            self.save()

    def print_summary(self):
        s  = self.stats
        wc = C.BGRN if s.win_rate >= 60 else (C.BYLW if s.win_rate >= 50 else C.BRED)
        pc = C.BGRN if s.total_pnl >= 0 else C.BRED
        dc = C.BGRN if s.daily_pnl >= 0 else C.BRED
        bar = "━" * 70
        cp(f"\n{C.BCYN}{C.B}{bar}{C.R}")
        cp(f"  {C.BG_BLU}{C.BWHT}{C.B}  📊 CemeterysunReplicant v4.5 — PORTFOLIO SUMMARY  {C.R}")
        cp(f"{C.BCYN}{bar}{C.R}")
        cp(f"  {C.BYLW}💰 Balance  :{C.R} {C.BWHT}${s.balance:,.2f}{C.R}"
           f"  (start: ${INITIAL_BALANCE:,.2f}  Δ={pc}${s.balance-INITIAL_BALANCE:+.2f}{C.R})")
        cp(f"  {C.BYLW}📈 Total PnL:{C.R} {pc}${s.total_pnl:+,.2f}{C.R}"
           f"  Daily: {dc}${s.daily_pnl:+,.2f}{C.R}")
        wr_bar = f"{C.BG_GRN}" if s.win_rate >= 70 else ""
        cp(f"  {C.BYLW}🎯 Record   :{C.R} {s.total_trades} trades  "
           f"{C.BGRN}W:{s.wins}{C.R}/{C.BRED}L:{s.losses}{C.R}  "
           f"WR:{wr_bar}{wc}{s.win_rate:.1f}%{C.R}")
        if s.llm_calls > 0:
            cp(f"  {C.BYLW}🤖 LLM Stats:{C.R} {s.llm_calls} calls  "
               f"NO BET: {s.llm_no_bet} ({s.llm_no_bet/s.llm_calls*100:.0f}%  protected)  "
               f"Pre-skip: {s.pre_filter_skips}")
        # Early exit stats
        ee = self.stats.early_exits
        if ee > 0:
            ee_pnl = getattr(self.stats, "early_exit_pnl", 0.0)
            eec = C.BGRN if ee_pnl >= 0 else C.BRED
            cp(f"  {C.BYLW}🚪 Early Exits:{C.R} {ee} trades  pnl={eec}${ee_pnl:+.2f}{C.R}")

        open_pos = [p for p in self.positions if not p.resolved]
        if open_pos:
            exp = sum(p.bet_usd for p in open_pos)
            cp(f"\n  {C.BYLW}⏳ Open ({len(open_pos)})  exposure=${exp:.2f}:{C.R}")
            phase_icon = {
                "PRE_GAME": "🕐", "IN_GAME": "⚽", "CLOSING": "⏰", "EXPIRED": "✅"
            }
            for p in open_pos:
                ec  = C.BGRN if p.edge > 0.08 else C.BYLW
                cc  = C.BGRN if p.llm_confidence >= 80 else C.BYLW
                phi = phase_icon.get(p.phase, "❓")
                # Last monitor action color
                ma  = p.last_monitor_action
                mac = C.BGRN if ma == "HOLD" else (
                      C.BRED if "EXIT" in ma else
                      C.MAG  if ma == "ADD_MORE" else C.DIM)
                live_drift = ""
                if p.last_live_price > 0:
                    drift = (p.last_live_price - p.entry_price) / p.entry_price * 100
                    dc    = C.BGRN if drift >= 0 else C.BRED
                    live_drift = f"  now={p.last_live_price:.3f}({dc}{drift:+.1f}%{C.R})"
                cp(f"    {phi} [{p.sport}|{p.league}] {p.title[:45]}"
                   f"  {p.direction}@{p.entry_price:.3f}"
                   f"  ${p.bet_usd:.0f}"
                   f"  edge={ec}{p.edge:+.1%}{C.R}"
                   f"  conf={cc}{p.llm_confidence}%{C.R}"
                   f"{live_drift}"
                   f"  {mac}[{ma}]{C.R}")
                if p.monitor_notes:
                    cp(f"      {C.DIM}↳ {p.monitor_notes[:65]}{C.R}")
        else:
            cp(f"  {C.DIM}No open positions.{C.R}")
        cp(f"{C.BCYN}{bar}{C.R}\n")

    def reset(self):
        self.stats     = BotStats()
        self.positions = []
        self.save()
        log.info("Reset to $%.2f", INITIAL_BALANCE)


# ═══════════════════════════════════════════════════
# LIVE DATA FETCHER — in-game score / status
# ═══════════════════════════════════════════════════
class LiveDataFetcher:
    """
    Fetches live in-game data for open positions.
    Used by LiveMonitor every scan when game is IN_GAME or CLOSING.
    """
    SPORT_MAP = {
        "NBA":    ("basketball", "nba"),
        "MLB":    ("baseball",   "mlb"),
        "NHL":    ("hockey",     "nhl"),
        "SOCCER": ("soccer",     "eng.1"),
    }
    _cache: dict = {}

    def _get(self, url, params=None, ttl=45):
        """Short-TTL cache for live data (45s)."""
        key = url + str(params)
        entry = self._cache.get(key)
        if entry and (time.time() - entry["ts"]) < ttl:
            return entry["data"]
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=8)
            r.raise_for_status()
            data = r.json()
            self._cache[key] = {"data": data, "ts": time.time()}
            return data
        except:
            return None

    def get_live_score(self, sport: str, team_a: str, team_b: str) -> dict:
        """
        Returns a dict with live game context.
        Keys: status, period, clock, score_a, score_b, situation
        status: SCHEDULED / IN_PROGRESS / FINAL / UNKNOWN
        """
        empty = {
            "status": "UNKNOWN", "period": "", "clock": "",
            "score_a": "", "score_b": "", "situation": "No live data",
            "extra": {}
        }
        if sport not in self.SPORT_MAP:
            return self._get_ipl_live(team_a, team_b)

        sk, league = self.SPORT_MAP[sport]
        data = self._get(f"{ESPN_API}/{sk}/{league}/scoreboard")
        if not data:
            return empty

        al = team_a.lower()[:5]
        bl = team_b.lower()[:5]

        for event in data.get("events", []):
            comp       = event.get("competitions", [{}])[0]
            competitors = comp.get("competitors", [])
            names      = " ".join(
                c.get("team", {}).get("displayName", "").lower()
                for c in competitors
            )
            if al not in names and bl not in names:
                continue

            # Status
            status_obj = comp.get("status", {}).get("type", {})
            status     = status_obj.get("name", "UNKNOWN").upper()  # IN_PROGRESS, FINAL, etc.
            period     = str(comp.get("status", {}).get("period", ""))
            clock      = str(comp.get("status", {}).get("displayClock", ""))

            scores = {}
            for c in competitors:
                nm = c.get("team", {}).get("displayName", "")
                scores[nm] = c.get("score", "?")

            # Build situation string
            situation_parts = []
            if period:
                situation_parts.append(f"Period/Qtr: {period}")
            if clock:
                situation_parts.append(f"Clock: {clock}")
            for nm, sc in scores.items():
                situation_parts.append(f"{nm}: {sc}")

            # Sport-specific extra info
            extra = {}
            if sport == "MLB":
                inning = comp.get("status", {}).get("period", "")
                outs   = ""
                for sit in comp.get("situation", {}).items():
                    extra[sit[0]] = sit[1]
                extra["inning"] = inning

            # Find score_a and score_b
            score_a, score_b = "?", "?"
            for c in competitors:
                nm = c.get("team", {}).get("displayName", "").lower()
                sc = c.get("score", "?")
                if al in nm:
                    score_a = sc
                elif bl in nm:
                    score_b = sc

            return {
                "status": status,
                "period": period,
                "clock": clock,
                "score_a": score_a,
                "score_b": score_b,
                "situation": " | ".join(situation_parts) or "No detail",
                "extra": extra,
            }

        return empty

    def _get_ipl_live(self, team_a: str, team_b: str) -> dict:
        """Fetch IPL live score via ESPN Cricinfo."""
        empty = {
            "status": "UNKNOWN", "period": "", "clock": "",
            "score_a": "", "score_b": "",
            "situation": "No IPL live data", "extra": {}
        }
        try:
            r = requests.get(
                "https://hs-consumer-api.espncricinfo.com/v1/pages/matches/live",
                params={"lang": "en"}, headers=HEADERS, timeout=8
            )
            if r.status_code != 200:
                return empty
            data    = r.json()
            matches = data.get("matches") or data.get("content", {}).get("matches", [])
            al, bl  = team_a.lower()[:4], team_b.lower()[:4]

            for m in matches:
                t1 = m.get("team1", {}).get("shortName", "").lower()
                t2 = m.get("team2", {}).get("shortName", "").lower()
                if (al in t1 or bl in t1) and (al in t2 or bl in t2):
                    innings = m.get("innings", [])
                    scores  = []
                    for inn in innings:
                        team  = inn.get("team", {}).get("shortName", "?")
                        runs  = inn.get("runs", "?")
                        wkts  = inn.get("wickets", "?")
                        ovs   = inn.get("overs", "?")
                        scores.append(f"{team}: {runs}/{wkts} ({ovs}ov)")

                    # Toss info (available after toss)
                    toss = m.get("toss", {})
                    toss_str = ""
                    if toss:
                        tw = toss.get("winner", {}).get("shortName", "")
                        tc = toss.get("decision", "")
                        toss_str = f"Toss: {tw} chose to {tc}"

                    situation = " | ".join(scores)
                    if toss_str:
                        situation = toss_str + " | " + situation

                    status = "IN_PROGRESS" if m.get("isLive") else                              ("FINAL" if m.get("isComplete") else "SCHEDULED")

                    return {
                        "status": status,
                        "period": "IPL",
                        "clock": m.get("currentInnings", ""),
                        "score_a": scores[0] if scores else "?",
                        "score_b": scores[1] if len(scores) > 1 else "?",
                        "situation": situation or "Match not started",
                        "extra": {
                            "toss_winner": toss.get("winner", {}).get("shortName", ""),
                            "toss_decision": toss.get("decision", ""),
                            "required_rate": m.get("requiredRunRate", ""),
                            "current_rate":  m.get("currentRunRate", ""),
                        },
                    }
        except Exception as e:
            log.debug("IPL live fetch: %s", e)
        return empty


# ═══════════════════════════════════════════════════
# IN-GAME LLM — monitors open positions
# ═══════════════════════════════════════════════════
class InGameLLM:
    """
    Called for each open position that is IN_GAME or CLOSING.
    Receives: entry context + live score + current price drift
    Returns: HOLD / EXIT_PROFIT / EXIT_PROTECT / ADD_MORE
    """

    SYSTEM = """You are monitoring an ACTIVE sports bet position on Polymarket.
Your job: decide whether to HOLD, EXIT for profit, EXIT to protect capital, or ADD MORE.

POSITION CONTEXT you will receive:
- Original bet: direction (YES/NO), entry price, bet amount, original confidence
- Current market price + drift from entry
- Live game status: score, period/over, situation
- Hours remaining until market closes

YOUR DECISION FRAMEWORK:

HOLD — stay in position:
  • Game in early stages, situation matches original thesis
  • Price drift < 10% adverse, no major situational change
  • IPL: first innings in progress, toss/pitch still favors your bet

EXIT_PROFIT — take profit now:
  • Price moved 25%+ in your favour (e.g. entry 0.55, now 0.80)
  • Game situation STRONGLY confirms your bet is winning
  • Market close < 20 minutes away and you are ahead

EXIT_PROTECT — exit to stop/protect (avoid bigger loss):
  • Price moved 15%+ AGAINST you (adverse drift)
  • Live score strongly contradicts your original thesis
  • Key event happened: injury to star player, red card, early wicket collapse
  • IPL: chasing team needs 15+ RPO in last 5 overs (practically lost)
  • IPL: batting team has lost 6+ wickets in first 10 overs (very difficult to recover)
  • Situation REVERSED from what you expected

ADD_MORE — increase position (RARE — only highest confidence):
  • Price moved slightly against you (5-10%) but situation is STILL strongly in your favour
  • Live score context confirms thesis is correct
  • Confidence >= 80% after seeing live data
  • Only recommend if original confidence was >= 75

IPL SPECIFIC RULES:
  • After toss revealed: batting-friendly pitch + batting first team = slight YES boost
  • After powerplay: if your team is batting and score is below par (< 50 in 6 overs on batting pitch) → reassess
  • If required run rate exceeds 14 RPO → chasing team unlikely to win
  • If top 3 wickets fall cheaply (< 30 runs) → batting team in serious trouble

OUTPUT FORMAT (JSON only, no other text):
{
  "action": "HOLD" | "EXIT_PROFIT" | "EXIT_PROTECT" | "ADD_MORE",
  "updated_win_probability": 0.00,
  "confidence": 0,
  "reasoning": "1-2 sentences max",
  "urgency": "LOW" | "MEDIUM" | "HIGH"
}

Rules: be DECISIVE. If situation is ambiguous → HOLD. Only EXIT if clearly warranted."""

    def evaluate(self, pos: TradePosition, live: dict,
                 current_price: float, hours_left: float) -> Optional[dict]:
        """
        pos          — the open TradePosition
        live         — dict from LiveDataFetcher.get_live_score()
        current_price— current CLOB midpoint for the bet token
        hours_left   — hours until market closes (can be negative if past close)
        """
        # Drift from entry (positive = price moved in our favour)
        entry = pos.entry_price
        if pos.direction == "YES":
            drift = current_price - entry       # positive = price went up = good for YES
        else:
            drift = entry - current_price       # positive = price went down = good for NO
        drift_pct = drift / entry if entry > 0 else 0.0

        msg = f"""=== IN-GAME POSITION REVIEW ===

ORIGINAL BET:
  Market: {pos.title}
  Sport: {pos.sport} | League: {pos.league}
  Direction: {pos.direction} | Entry price: {entry:.3f}
  Bet amount: ${pos.bet_usd:.2f} | Shares: {pos.shares:.4f}
  Original confidence: {pos.llm_confidence}%
  Original model prob: {pos.model_prob:.3f}

CURRENT MARKET:
  Current price: {current_price:.3f}
  Drift from entry: {drift_pct:+.1%} ({'FAVOURABLE' if drift_pct > 0 else 'ADVERSE'})
  Hours to close: {hours_left:.1f}h
  Phase: {pos.phase}

LIVE GAME STATUS:
  Status: {live.get('status', 'UNKNOWN')}
  Period/Over: {live.get('period', 'N/A')}
  Clock: {live.get('clock', 'N/A')}
  Score A: {live.get('score_a', 'N/A')}
  Score B: {live.get('score_b', 'N/A')}
  Situation: {live.get('situation', 'No data')}"""

        # Add IPL extra data if present
        extra = live.get("extra", {})
        if extra.get("toss_winner"):
            tw = extra.get("toss_winner", "")
            td = extra.get("toss_decision", "?")
            msg += f"\n  Toss: {tw} chose to {td}"
        if extra.get("required_rate"):
            msg += f"\n  Required run rate: {extra.get('required_rate')}"
        if extra.get("current_rate"):
            msg += f"\n  Current run rate: {extra.get('current_rate')}"

        msg += "\n\nProvide your position management decision in JSON format:"



        for model in [PRIMARY_MODEL, FALLBACK_MODEL]:
            for attempt in range(2):
                try:
                    t0 = time.time()
                    r  = requests.post(
                        "https://openrouter.ai/api/v1/chat/completions",
                        headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}",
                                 "Content-Type": "application/json"},
                        json={"model": model,
                              "messages": [
                                  {"role": "system", "content": self.SYSTEM},
                                  {"role": "user",   "content": msg},
                              ],
                              "max_tokens": 200, "temperature": 0.05},
                        timeout=40,
                    )
                    log.info("    InGameLLM %.1fs [%s]", time.time()-t0,
                             model.split("/")[-1])
                    if r.status_code != 200:
                        time.sleep(2); continue
                    raw = (r.json()
                           .get("choices", [{}])[0]
                           .get("message", {})
                           .get("content", ""))
                    if not raw:
                        continue
                    raw = re.sub(r"```(?:json)?|```", "", raw).strip()
                    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
                    m   = re.search(r"\{[^{}]+\}", raw, re.DOTALL)
                    if not m:
                        continue
                    parsed = json.loads(m.group(0))
                    action = str(parsed.get("action", "HOLD")).upper().strip()
                    if action not in ("HOLD","EXIT_PROFIT","EXIT_PROTECT","ADD_MORE"):
                        action = "HOLD"
                    return {
                        "action":       action,
                        "prob":         max(0.02, min(0.98, float(parsed.get("updated_win_probability", pos.model_prob)))),
                        "confidence":   max(40, min(95, int(parsed.get("confidence", 55)))),
                        "reasoning":    str(parsed.get("reasoning", ""))[:200],
                        "urgency":      str(parsed.get("urgency", "LOW")).upper(),
                        "drift_pct":    drift_pct,
                    }
                except json.JSONDecodeError:
                    pass
                except requests.Timeout:
                    log.warning("    InGameLLM timeout [%s]", model)
                except Exception as e:
                    log.warning("    InGameLLM error: %s", e)
                if attempt == 0:
                    time.sleep(2)
        # Fallback: use simple rules if LLM fails
        if drift_pct <= -ADVERSE_MOVE_PCT:
            return {"action": "EXIT_PROTECT", "prob": pos.model_prob,
                    "confidence": 60, "reasoning": "Auto: adverse move threshold hit",
                    "urgency": "HIGH", "drift_pct": drift_pct}
        if drift_pct >= PROFIT_TARGET_PCT:
            return {"action": "EXIT_PROFIT", "prob": pos.model_prob,
                    "confidence": 60, "reasoning": "Auto: profit target hit",
                    "urgency": "MEDIUM", "drift_pct": drift_pct}
        return {"action": "HOLD", "prob": pos.model_prob,
                "confidence": 55, "reasoning": "LLM unavailable — HOLD default",
                "urgency": "LOW", "drift_pct": drift_pct}


# ═══════════════════════════════════════════════════
# LIVE MONITOR — runs every scan on open positions
# ═══════════════════════════════════════════════════
class LiveMonitor:
    """
    Checks all open TradePositions each scan.
    Determines phase, fetches live data, calls InGameLLM,
    executes exits or logs decisions.
    """

    def __init__(self, poly: PolymarketFetcher, clob_exec,
                 live_fetcher: LiveDataFetcher,
                 in_game_llm: InGameLLM, db: DB):
        self.poly       = poly
        self.clob       = clob_exec
        self.live       = live_fetcher
        self.llm        = in_game_llm
        self.db         = db

    def _update_phase(self, pos: TradePosition, hours_left: float) -> str:
        if hours_left > IN_GAME_HOURS_START:
            return "PRE_GAME"
        elif hours_left > 0:
            return "IN_GAME"
        elif hours_left > -2:
            return "CLOSING"
        else:
            return "EXPIRED"

    def _extract_teams(self, pos: TradePosition) -> tuple:
        return PolymarketFetcher.extract_teams(pos.title)

    def _paper_exit(self, pos: TradePosition, current_price: float,
                    reason: str, sim: "SimManager"):
        """Simulate selling position at current market price."""
        if pos.direction == "YES":
            proceeds = round(pos.shares * current_price, 4)
        else:
            # NO token: value = shares * (1 - current_yes_price)
            no_price = round(1.0 - current_price, 4)
            proceeds = round(pos.shares * no_price, 4)

        pnl = round(proceeds - pos.bet_usd, 4)
        pos.pnl          = pnl
        pos.outcome      = "EXIT_PROFIT" if pnl > 0 else "EXIT_PROTECT"
        pos.resolved     = True
        pos.exited_early = True

        sim.stats.balance    = round(sim.stats.balance + proceeds, 4)
        sim.stats.total_pnl  = round(sim.stats.total_pnl + pnl, 4)
        sim.stats.daily_pnl  = round(sim.stats.daily_pnl + pnl, 4)
        sim.stats.early_exits += 1
        sim.stats.early_exit_pnl = round(
            getattr(sim.stats, "early_exit_pnl", 0.0) + pnl, 4)
        if pnl > 0:
            sim.stats.wins += 1
        else:
            sim.stats.losses += 1

        col = C.BGRN if pnl > 0 else C.BRED
        cp(
            f"  [EXIT] [{pos.sport}] {pos.title[:50]}\n"
            f"     Reason: {reason}\n"
            f"     {pos.direction} entry={pos.entry_price:.3f}"
            f" -> exit={current_price:.3f}\n"
            f"     Proceeds=${proceeds:.2f}  PnL={col}${pnl:+.2f}{C.R}"
        )


    def monitor_all(self, positions: list, sim: "SimManager",
                    live_mode: bool = False):
        """
        Main entry point — called once per scan.
        Iterates over open positions, checks phase, monitors, acts.
        """
        if not IN_GAME_MONITOR:
            return

        open_positions = [p for p in positions if not p.resolved]
        if not open_positions:
            return

        changed = False
        for pos in open_positions:
            team_a, team_b = self._extract_teams(pos)
            hours_left     = PolymarketFetcher.hours_to_resolution(pos.game_time + ":00Z"
                                if len(pos.game_time) == 16 else pos.game_time)
            new_phase = self._update_phase(pos, hours_left)
            pos.phase = new_phase

            # Only actively monitor IN_GAME and CLOSING
            if new_phase == "PRE_GAME":
                continue
            if new_phase == "EXPIRED":
                # Will be settled by main settle() call
                continue

            # ── Fetch current price ───────────────
            token     = pos.yes_token if pos.direction == "YES" else pos.no_token
            yes_price = self.poly.get_midpoint(pos.yes_token)
            if yes_price is None:
                log.debug("  Monitor: no price for %s", pos.title[:40])
                continue
            current_price = yes_price if pos.direction == "YES" else round(1.0 - yes_price, 4)
            pos.last_live_price = current_price

            # ── Quick rule-based pre-check ────────
            entry = pos.entry_price
            drift_pct = (current_price - entry) / entry if entry > 0 else 0.0

            # Hard stops — no LLM call needed
            if drift_pct <= -(ADVERSE_MOVE_PCT * 1.5):
                # Extreme adverse move: auto exit without LLM
                reason = f"Auto stop-loss: price moved {drift_pct:.1%} adverse"
                log.info("  🛑 Auto stop-loss %s — drift %.1f%%",
                         pos.title[:40], drift_pct*100)
                if live_mode and self.clob.client:
                    self.clob.sell(token, pos.shares)
                self._paper_exit(pos, yes_price, reason, sim)
                self.db.update_position_monitor(
                    pos.condition_id, new_phase, current_price,
                    "EXIT_PROTECT", reason, exited=True)
                changed = True
                continue

            # ── Fetch live game data ──────────────
            live_data = self.live.get_live_score(pos.sport, team_a, team_b)
            log.info("  📡 [%s|%s] %s  price=%.3f(drift%+.1f%%)  game=%s",
                     pos.sport, new_phase, pos.title[:38],
                     current_price, drift_pct*100,
                     live_data.get("status","?"))

            # If game not yet started — skip LLM, just update phase
            if live_data.get("status") in ("SCHEDULED", "UNKNOWN")                and new_phase == "IN_GAME" and hours_left > 0.3:
                log.info("    Game not started yet — holding")
                pos.last_monitor_action = "HOLD"
                self.db.update_position_monitor(
                    pos.condition_id, new_phase, current_price, "HOLD",
                    "Game not started yet")
                continue

            # ── LLM in-game evaluation ────────────
            log.info("    Calling InGameLLM...")
            result = self.llm.evaluate(pos, live_data, current_price, hours_left)
            if result is None:
                log.warning("    InGameLLM returned None — HOLD")
                continue

            action    = result["action"]
            reasoning = result["reasoning"]
            urgency   = result["urgency"]
            log.info("    InGameLLM → %s (urgency=%s) | %s",
                     action, urgency, reasoning[:80])

            # Update position tracking
            pos.last_monitor_action = action
            pos.monitor_notes       = reasoning[:150]

            self.db.log_monitor(
                pos.condition_id, new_phase, current_price,
                entry, result["drift_pct"], action, reasoning)

            # ── Act on LLM decision ───────────────
            if action == "HOLD":
                self.db.update_position_monitor(
                    pos.condition_id, new_phase, current_price,
                    "HOLD", reasoning)
                cp(f"  ✋ HOLD [{pos.sport}] {pos.title[:45]}"
                   f"  price={current_price:.3f}  drift={drift_pct:+.1%}"
                   f"  {C.DIM}{reasoning[:60]}{C.R}")

            elif action in ("EXIT_PROFIT", "EXIT_PROTECT"):
                if live_mode and self.clob.client:
                    resp = self.clob.sell(token, pos.shares)
                    if resp and not resp.get("paper"):
                        log.info("    LIVE SELL executed: %s", resp.get("orderID","?"))
                reason_label = "PROFIT LOCK 🎯" if action == "EXIT_PROFIT" else "PROTECT 🛡"
                self._paper_exit(pos, yes_price, f"{reason_label}: {reasoning}", sim)
                self.db.update_position_monitor(
                    pos.condition_id, new_phase, current_price,
                    action, reasoning, exited=True)
                changed = True

            elif action == "ADD_MORE":
                # Only add if confidence is high enough
                if result.get("confidence", 0) >= ADD_MORE_CONF_MIN:
                    # Size: 25% of original bet, capped
                    add_amount = min(pos.bet_usd * 0.25, MAX_BET_USD * 0.25,
                                     sim.stats.balance * 0.02)
                    add_amount = round(add_amount, 2)
                    if add_amount >= MIN_BET_USD:
                        if live_mode and self.clob.client:
                            self.clob.buy(token, add_amount)
                        pos.bet_usd  = round(pos.bet_usd + add_amount, 4)
                        pos.shares   = round(pos.shares + add_amount / current_price, 4)
                        sim.stats.balance = round(sim.stats.balance - add_amount, 4)
                        cp(f"  ➕ ADD_MORE [{pos.sport}] {pos.title[:40]}"
                           f"  +${add_amount:.0f} @ {current_price:.3f}"
                           f"  conf={result['confidence']}%"
                           f"  {C.DIM}{reasoning[:50]}{C.R}")
                        self.db.update_position_monitor(
                            pos.condition_id, new_phase, current_price,
                            "ADD_MORE", f"Added ${add_amount:.0f}: {reasoning}")
                        changed = True
                    else:
                        log.info("    ADD_MORE amount too small (%.2f) — HOLD", add_amount)
                        self.db.update_position_monitor(
                            pos.condition_id, new_phase, current_price,
                            "ADD_MORE_SKIPPED", "Amount too small")
                else:
                    log.info("    ADD_MORE confidence %d < %d — HOLD",
                             result.get("confidence",0), ADD_MORE_CONF_MIN)
                    self.db.update_position_monitor(
                        pos.condition_id, new_phase, current_price,
                        "ADD_MORE_SKIPPED", f"Conf {result.get('confidence',0)} too low")

        if changed:
            sim.save()


# ═══════════════════════════════════════════════════
# DATA GATHERER — collects ALL data before LLM
# ═══════════════════════════════════════════════════
class DataGatherer:
    """
    Gathers ALL data for a market candidate BEFORE calling LLM.
    This ensures LLM has maximum context for accurate decisions.
    """
    def __init__(self, poly: PolymarketFetcher, espn: ESPNFetcher,
                 stat: StatModel, db: DB):
        self.poly    = poly
        self.espn    = espn
        self.stat    = stat
        self.db      = db
        self._standings_cache: dict = {}

    def _standings(self, sport):
        if sport not in self._standings_cache:
            self._standings_cache[sport] = self.espn.get_standings(sport)
            log.info("  ESPN standings: %d teams [%s]",
                     len(self._standings_cache[sport]), sport)
        return self._standings_cache[sport]

    def gather(self, parsed: dict) -> Optional[GameData]:
        """Gather all data for a parsed market. Returns GameData or None."""
        title  = parsed["title"]
        cid    = parsed["condition_id"]
        sport  = PolymarketFetcher.detect_sport(title)
        league = PolymarketFetcher.detect_league(title, sport)
        team_a, team_b = PolymarketFetcher.extract_teams(title)
        hours  = PolymarketFetcher.hours_to_resolution(parsed["end_date"])

        # ── Price & liquidity ──────────────────────
        yes_price = self.poly.get_midpoint(parsed["yes_token"])
        if yes_price is None or yes_price < 0.02 or yes_price > 0.98:
            log.info("  Skip bad price=%.3f: %s", yes_price or 0, title[:45])
            return None
        no_price = round(1.0 - yes_price, 4)

        ob = self.poly.get_order_book_depth(parsed["yes_token"])
        if ob["total_depth"] < 20:
            log.info("  Skip low depth=%.0f: %s", ob["total_depth"], title[:45])
            return None

        # Record price for line movement tracking
        self.db.record_price(parsed["yes_token"], yes_price)
        old_price = self.db.get_price_2h_ago(parsed["yes_token"])
        if old_price and abs(yes_price - old_price) > 0.001:
            mv_pct  = yes_price - old_price
            mv_dir  = "UP" if mv_pct > 0 else "DOWN"
        else:
            mv_pct, mv_dir = 0.0, "STABLE"

        # Market sentiment (YES vol %)
        yes_vol = parsed.get("yes_vol", 0)
        no_vol  = parsed.get("no_vol", 0)
        total_vol = yes_vol + no_vol
        yes_vol_pct = (yes_vol / total_vol) if total_vol > 0 else 0.50

        log.info("  → [%s|%s] %s", sport, league, title[:55])
        log.info("    YES=%.3f  vol=$%.0f  depth=$%.0f  h=%.1f  mv=%s",
                 yes_price, parsed["volume"], ob["total_depth"], hours, mv_dir)

        # ── ESPN data ──────────────────────────────
        standings = self._standings(sport)
        wp_a      = self.espn.find_win_pct(standings, team_a)
        wp_b      = self.espn.find_win_pct(standings, team_b)
        home_team = self.espn.get_home_team(sport, team_a, team_b)
        a_is_home = bool(home_team) and \
                    (home_team or "").lower()[:5] in team_a.lower()

        # Recent form
        form_a = self.espn.get_recent_form(sport, team_a)
        form_b = self.espn.get_recent_form(sport, team_b)

        # H2H
        h2h = self.espn.get_h2h(sport, team_a, team_b)

        # Injuries
        injuries_a = self.espn.get_injuries(sport, team_a)
        injuries_b = self.espn.get_injuries(sport, team_b)

        # MLB ERA
        home_era, away_era = 4.50, 4.50
        if sport == "MLB":
            eras = self.espn.get_mlb_eras(team_a, team_b)
            home_era = eras.get("home_era", 4.50)
            away_era = eras.get("away_era", 4.50)

        # IPL data
        toss_winner, pitch_type, venue_avg = "", "unknown", 0.0
        if league == "IPL":
            ipl = self.espn.get_ipl_data(title)
            toss_winner = ipl.get("toss_winner", "")
            pitch_type  = ipl.get("pitch_type", "unknown")
            venue_avg   = float(ipl.get("avg_1st_innings_score", 0))

        # Stat model estimate
        stat_prob = self.stat.estimate(sport, wp_a, wp_b, a_is_home,
                                       home_era, away_era)

        log.info("    %s wp=%.3f form=%s | %s wp=%.3f form=%s",
                 team_a[:10], wp_a, form_a[:7],
                 team_b[:10], wp_b, form_b[:7])
        if injuries_a:
            log.info("    Injuries A: %s", "; ".join(injuries_a[:2]))
        if injuries_b:
            log.info("    Injuries B: %s", "; ".join(injuries_b[:2]))

        return GameData(
            title=title, sport=sport, league=league,
            team_a=team_a, team_b=team_b,
            yes_price=yes_price, no_price=no_price,
            volume=parsed["volume"], depth=ob,
            hours_to_close=hours,
            wp_a=wp_a, wp_b=wp_b,
            home_team=home_team or "",
            a_is_home=a_is_home,
            form_a=form_a, form_b=form_b,
            h2h=h2h,
            injuries_a=injuries_a, injuries_b=injuries_b,
            home_era=home_era, away_era=away_era,
            line_movement=mv_dir, movement_pct=mv_pct,
            yes_vol_pct=yes_vol_pct,
            open_interest=parsed["volume"],
            toss_winner=toss_winner, pitch_type=pitch_type,
            venue_avg_score=venue_avg,
            stat_prob=stat_prob,
            condition_id=cid,
            market_id=parsed["market_id"],
            yes_token=parsed["yes_token"],
            no_token=parsed["no_token"],
            game_time=str(parsed["end_date"])[:16],
        )


# ═══════════════════════════════════════════════════
# SIGNAL GENERATOR — v4
# ═══════════════════════════════════════════════════
class SignalGenerator:
    def __init__(self):
        self.poly     = PolymarketFetcher()
        self.espn     = ESPNFetcher()
        self.stat     = StatModel()
        self.llm      = LLMModel()

    def scan(self, balance: float, db: DB, sim_stats: BotStats) -> list:
        signals       = []
        seen_cids     = set()
        seen_sig_keys = set()
        llm_calls     = 0
        llm_no_bets   = 0
        pre_skips     = 0

        gatherer     = DataGatherer(self.poly, self.espn, self.stat, db)
        raw_markets  = self.poly.get_game_markets()
        log.info("Evaluating %d candidate markets...", len(raw_markets))

        for raw in raw_markets:
            parsed = PolymarketFetcher.parse_market(raw)
            if not parsed:
                continue
            cid = parsed["condition_id"]
            if cid in seen_cids:
                continue
            seen_cids.add(cid)

            if db.already_open(cid):
                log.debug("  Skip already open: %s", parsed["title"][:45])
                continue

            title = parsed["title"]
            sport = PolymarketFetcher.detect_sport(title)

            # ── Time filter (v5: relaxed + better logging) ────
            hours = PolymarketFetcher.hours_to_resolution(parsed["end_date"])
            if hours == 999.0:
                # No end_date — accept if volume >= 500 (likely active today)
                if parsed["volume"] < 500:
                    log.info("  [SKIP-NODATE] vol=%.0f < 500: %s",
                             parsed["volume"], title[:45])
                    continue
                log.info("  [OK-NODATE] vol=%.0f accepted: %s",
                         parsed["volume"], title[:45])
            elif hours < -6:
                # Already resolved more than 6h ago
                log.info("  [SKIP-EXPIRED] %.0fh ago: %s", -hours, title[:40])
                continue
            elif hours > 96:
                # More than 4 days away — too far future
                log.info("  [SKIP-FUTURE] %.0fh away: %s", hours, title[:40])
                continue
            else:
                log.info("  [TIME-OK] %.1fh to close | %s", hours, title[:50])

            # ── Volume (v5: lowered to 100) ────────
            if parsed["volume"] < 100:
                log.info("  [SKIP-VOL] vol=%.0f < 100: %s",
                         parsed["volume"], title[:40])
                continue

            # ── Gather ALL data ────────────────────
            gd = gatherer.gather(parsed)
            if gd is None:
                continue

            # ── Pre-filter (rough stat check only) ─
            # Only skip if stat model shows ZERO edge AND price isn't unusual
            stat_ey = abs(gd.stat_prob - gd.yes_price)
            stat_en = abs((1.0 - gd.stat_prob) - gd.no_price)
            max_stat_edge = max(stat_ey, stat_en)

            # Skip if both teams are essentially equal AND market is balanced
            # (very low information game)
            both_default = (gd.wp_a == 0.5 and gd.wp_b == 0.5)
            no_form      = (gd.form_a == "N/A" and gd.form_b == "N/A")
            no_injuries  = (not gd.injuries_a and not gd.injuries_b)
            price_balanced = abs(gd.yes_price - 0.5) < 0.03

            if both_default and no_form and no_injuries and price_balanced \
               and max_stat_edge < MIN_EDGE * 0.25:
                log.info("  Pre-filter: zero data + balanced price: %s", title[:40])
                pre_skips += 1
                sim_stats.pre_filter_skips += 1
                continue

            # ── LLM — FINAL DECISION MAKER ─────────
            log.info("  Calling LLM for: %s", title[:55])
            llm_result = self.llm.estimate(gd)
            llm_calls += 1
            sim_stats.llm_calls += 1

            if llm_result is None:
                log.warning("  LLM failed completely — skipping %s", title[:40])
                continue

            bet_side   = llm_result["bet_side"]
            model_prob = llm_result["prob"]
            conf       = llm_result["confidence"]
            factors    = llm_result.get("factors", [])
            reasoning  = llm_result.get("reasoning", "")
            dq         = llm_result.get("data_quality", "MEDIUM")

            log.info("  LLM: side=%s prob=%.3f conf=%d%% dq=%s",
                     bet_side, model_prob, conf, dq)
            if reasoning:
                log.info("  Reasoning: %s", reasoning[:120])

            # ── LLM veto power ─────────────────────
            if bet_side == "NO BET":
                log.info("  LLM says NO BET — respecting veto: %s", title[:40])
                llm_no_bets += 1
                sim_stats.llm_no_bet += 1
                continue

            # ── Confidence gate (v4 raised to 72) ──
            if conf < LLM_CONFIDENCE_MIN:
                log.info("  Skip low conf=%d (need %d): %s",
                         conf, LLM_CONFIDENCE_MIN, title[:40])
                llm_no_bets += 1
                sim_stats.llm_no_bet += 1
                continue

            # ── Data quality filter ─────────────────
            if dq == "LOW":
                log.info("  Skip LOW data quality: %s", title[:40])
                llm_no_bets += 1
                sim_stats.llm_no_bet += 1
                continue

            # ── Edge + direction ───────────────────
            if bet_side == "YES":
                direction  = "YES"
                entry_price = gd.yes_price
                dir_prob    = model_prob
                edge        = model_prob - gd.yes_price
            elif bet_side == "NO":
                direction  = "NO"
                entry_price = gd.no_price
                dir_prob    = 1.0 - model_prob
                edge        = (1.0 - model_prob) - gd.no_price
            else:
                log.info("  Unknown bet_side=%s: %s", bet_side, title[:40])
                continue

            if edge < MIN_EDGE:
                log.info("  Edge %.3f < MIN %.3f (LLM said %s but edge too small): %s",
                         edge, MIN_EDGE, bet_side, title[:40])
                continue

            # ── Kelly sizing ───────────────────────
            kf  = calc_kelly(dir_prob, entry_price)
            bet = size_bet(kf, balance, edge, conf)
            if bet <= 0:
                log.info("  Bet too small — skip")
                continue

            signal_key = (gd.market_id, direction)
            if signal_key in seen_sig_keys:
                continue
            seen_sig_keys.add(signal_key)

            signals.append(SportSignal(
                market_id=gd.market_id, condition_id=gd.condition_id,
                yes_token=gd.yes_token, no_token=gd.no_token,
                title=title, sport=sport, league=gd.league,
                direction=direction, market_price=entry_price,
                model_prob=dir_prob, edge=edge, kelly_f=kf, bet_usd=bet,
                llm_confidence=conf,
                home_team=gd.home_team or team_a,
                away_team=gd.team_b,
                game_time=gd.game_time, factors=factors,
            ))
            cp(f"  {C.BG_GRN}{C.BWHT} ✅ SIGNAL {C.R}  "
               f"{direction}  model={dir_prob:.3f}  mkt={entry_price:.3f}  "
               f"edge={C.BGRN}{edge:+.1%}{C.R}  conf={C.BGRN}{conf}%{C.R}  "
               f"bet=${bet:.0f}  [{sport}] {title[:45]}")

        sim_stats.save()  # Save updated LLM call stats
        signals.sort(key=lambda s: (s.llm_confidence, s.edge), reverse=True)
        log.info(
            "Scan done: %d signals | LLM calls: %d | NO BET: %d | Pre-skips: %d",
            len(signals), llm_calls, llm_no_bets, pre_skips
        )
        return signals, llm_calls, llm_no_bets, pre_skips


# ═══════════════════════════════════════════════════
# MAIN SCAN
# ═══════════════════════════════════════════════════
def run_scan(live: bool = False):
    sim       = SimManager()
    clob      = CLOBExecutor()
    gen       = SignalGenerator()
    db        = DB()
    live_data = LiveDataFetcher()
    ig_llm    = InGameLLM()
    monitor   = LiveMonitor(gen.poly, clob, live_data, ig_llm, db)

    sim.reset_daily()
    if sim.daily_limit_hit():
        cp(f"\n{C.BG_RED}{C.B}  🚨 DAILY LIMIT ${DAILY_LOSS_LIMIT:.0f} HIT — paused  {C.R}")
        sim.settle(gen.poly)
        sim.print_summary()
        return

    log.info("Checking resolved positions...")
    sim.settle(gen.poly)

    # ── IN-GAME MONITOR: check open positions first ───
    open_count = sum(1 for p in sim.positions if not p.resolved)
    if open_count > 0:
        cp(f"\n{C.BCYN}📡 Monitoring {open_count} open position(s)...{C.R}")
        monitor.monitor_all(sim.positions, sim, live_mode=live)

    sim.save()

    balance = clob.get_balance() if (live and clob.client) else sim.stats.balance
    log.info(
        "Balance=$%.2f | %s | edge≥%.0f%% | llm_conf≥%d%% | kelly=%.0f%% | max=$%.0f",
        balance, "🔴 LIVE" if live else "📄 PAPER",
        MIN_EDGE*100, LLM_CONFIDENCE_MIN, KELLY_FRACTION*100, MAX_BET_USD
    )

    result = gen.scan(balance, db, sim.stats)
    signals, llm_calls, llm_no_bets, pre_skips = result

    db.log_scan(0, len(signals), 0, llm_calls, pre_skips)

    if not signals:
        log.info("No qualifying signals found this scan.")
        sim.print_summary()
        return

    bar = "═" * 72
    cp(f"\n{C.BCYN}{bar}{C.R}")
    cp(f"  {C.B}{C.BYLW}🎯  {len(signals)} HIGH-CONFIDENCE SIGNAL(S) — sorted by confidence × edge{C.R}")
    cp(f"{C.BCYN}{bar}{C.R}")

    sport_col = {
        "NBA":     C.BGRN, "MLB": C.BRED, "NHL": C.BCYN,
        "SOCCER":  C.BYLW, "CRICKET": C.MAG,
    }
    for i, s in enumerate(signals, 1):
        sc  = sport_col.get(s.sport, C.BWHT)
        ec  = C.BGRN if s.edge > 0.08 else C.BYLW
        cc  = C.BGRN if s.llm_confidence >= 80 else C.BYLW
        pay = round(1 / s.market_price, 2) if s.market_price > 0 else 0
        cp(f"\n  {C.B}{i}.{C.R} {sc}[{s.sport}|{s.league}]{C.R}  "
           f"{C.B}{s.title[:55]}{C.R}\n"
           f"     {C.B}{s.direction}{C.R}  entry={s.market_price:.3f}  "
           f"model={s.model_prob:.3f}  edge={ec}{s.edge:+.1%}{C.R}  "
           f"conf={cc}{s.llm_confidence}%{C.R}  payoff={pay:.1f}x  "
           f"bet=${s.bet_usd:.0f}\n"
           f"     Kelly={s.kelly_f:.4f}  closes={s.game_time}\n"
           f"     {C.DIM}Factors: {' | '.join(s.factors[:3]) or 'n/a'}{C.R}")
    cp(f"\n{C.BCYN}{bar}{C.R}\n")

    executed = 0
    for sig in signals:
        if sim.daily_limit_hit():
            cp(f"\n{C.BRED}Daily loss limit hit — stopping execution.{C.R}")
            break
        if db.already_open(sig.condition_id):
            continue
        token = sig.yes_token if sig.direction == "YES" else sig.no_token
        if live and clob.client:
            resp = clob.buy(token, sig.bet_usd)
            if resp and not resp.get("paper"):
                oid = resp.get("orderID") or resp.get("id", "?")
                cp(f"  {C.BGRN}✅ LIVE ORDER: {oid}{C.R}")
                db.insert_trade(sim.open_position(sig))
                executed += 1
            else:
                cp(f"  {C.BRED}❌ Order failed: {sig.title[:40]}{C.R}")
        else:
            cp(f"  {C.BCYN}[PAPER]{C.R} {sig.direction} ${sig.bet_usd:.0f} "
               f"@ {sig.market_price:.3f} — {sig.title[:48]}")
            db.insert_trade(sim.open_position(sig))
            executed += 1

    db.log_scan(0, len(signals), executed, llm_calls, pre_skips)
    log.info("Executed %d/%d signals", executed, len(signals))
    sim.print_summary()


# ═══════════════════════════════════════════════════
# ENTRYPOINT
# ═══════════════════════════════════════════════════
def main():
    p = argparse.ArgumentParser(description="CemeterysunReplicant v4")
    p.add_argument("--live",      action="store_true",  help="Live trading mode")
    p.add_argument("--positions", action="store_true",  help="Show portfolio")
    p.add_argument("--reset",     action="store_true",  help="Reset paper balance")
    p.add_argument("--daemon",    action="store_true",  help="Continuous scan loop")
    p.add_argument("--discover",  action="store_true",  help="Debug: just show found markets, no LLM")
    args = p.parse_args()

    cp(f"\n{C.BG_BLU}{C.BWHT}{C.B}"
       f"  🏆 CemeterysunReplicant v4.5 — Polymarket Sports Bot  {C.R}")
    cp(f"  {C.BYLW}Models: {PRIMARY_MODEL} → {FALLBACK_MODEL.split('/')[-1]}{C.R}")
    cp(f"  {C.BYLW}Sports: NBA | MLB | NHL | SOCCER | IPL/CRICKET{C.R}")
    cp(f"  {C.BYLW}Data: Win% + Form + H2H + Injuries + Line Movement + Sentiment{C.R}")
    cp(f"  {C.BYLW}edge≥{MIN_EDGE:.0%}  llm_conf≥{LLM_CONFIDENCE_MIN}%  "
       f"kelly={KELLY_FRACTION:.0%}  max=${MAX_BET_USD:.0f}  "
       f"limit=${DAILY_LOSS_LIMIT:.0f}  mode={'🔴 LIVE' if args.live else '📄 PAPER'}{C.R}")
    cp(f"  {C.BG_GRN}{C.BWHT} TARGET: 80%+ WIN RATE {C.R}\n")

    if args.reset:
        SimManager().reset()
        return
    if args.positions:
        sim = SimManager()
        sim.settle(PolymarketFetcher())
        sim.print_summary()
        return
    if args.discover:
        # Debug mode: just show what markets are found, no LLM calls
        cp(f"\n{C.BYLW}[DISCOVER MODE] Scanning markets, no LLM calls...{C.R}")
        poly = PolymarketFetcher()
        raw  = poly.get_game_markets()
        cp(f"\n{C.BCYN}Found {len(raw)} raw game markets:{C.R}")
        for i, m in enumerate(raw, 1):
            parsed = PolymarketFetcher.parse_market(m)
            if not parsed:
                continue
            hours  = PolymarketFetcher.hours_to_resolution(parsed["end_date"])
            sport  = PolymarketFetcher.detect_sport(parsed["title"])
            hr_str = f"{hours:.1f}h" if hours != 999.0 else "no-date"
            yes_px = poly.get_midpoint(parsed["yes_token"])
            px_str = f"YES={yes_px:.3f}" if yes_px else "price=N/A"
            cp(f"  {i:3d}. [{sport}] {parsed['title'][:60]}"
               f"\n       vol=${parsed['volume']:,.0f}  {px_str}  "
               f"close={hr_str}  cid={parsed['condition_id'][:12]}...")
        return

    if args.daemon:
        log.info("Daemon mode — scanning every %ds. Ctrl+C to stop.", SCAN_INTERVAL)
        while True:
            try:
                run_scan(live=args.live)
            except KeyboardInterrupt:
                cp(f"\n{C.BRED}Stopped.{C.R}")
                break
            except Exception as e:
                log.exception("Scan error: %s", e)
            log.info("Next scan in %ds...", SCAN_INTERVAL)
            time.sleep(SCAN_INTERVAL)
    else:
        run_scan(live=args.live)


if __name__ == "__main__":
    if not OPENROUTER_API_KEY:
        cp(f"{C.BRED}⚠  OPENROUTER_API_KEY missing in .env — set karo aur retry karo{C.R}")
        sys.exit(1)
    main()