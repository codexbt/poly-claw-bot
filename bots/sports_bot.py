#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CemeterysunReplicant v7.1 — Polymarket Sports Trading Bot
FIXES from v7.0:
  ✅ FIX 1: LLM prompt rewritten — less restrictive, actionable recommendations
  ✅ FIX 2: MIN_EDGE lowered to 0.02 (2%) — realistic for prediction markets
  ✅ FIX 3: LLM_CONF_MIN lowered to 55% — was vetoing everything at 60%
  ✅ FIX 4: stat_estimate fixed — returns proper prob even without ESPN data
   ✅ FIX 5: Game-level grouping fixed — O/U markets no longer blocking ML markets
# Sports bot improvements - 2025-12-04
  ✅ FIX 6: LLM fallback logic — if LLM fails, stat model makes the call
  ✅ FIX 7: Prob extraction fixed — LLM JSON parsing more robust
  ✅ FIX 8: Volume/depth thresholds relaxed — was rejecting valid markets
  ✅ FIX 9: All v7.0 fixes retained (FIX A/B/C/D/E)
  ✅ FIX 10: Debug logging added to show WHY markets are being rejected
"""

import argparse, json, logging, os, re, sqlite3, sys, time, threading
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
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
OPENROUTER_API_KEY        = (os.getenv("OPENROUTER_API_KEY") or
                             os.getenv("OPENROUTER_KEY") or
                             os.getenv("OR_API_KEY") or "")

PAPER_MODE          = os.getenv("PAPER_MODE", "true").lower() in ("true","1","yes")
# FIX 2: Lowered from 0.04 to 0.02 — prediction markets have tight spreads
MIN_EDGE            = float(os.getenv("MIN_EDGE",           "0.02"))
MAX_BET_USD         = float(os.getenv("MAX_BET_USD",        "50"))
MIN_BET_USD         = float(os.getenv("MIN_BET_USD",        "5"))
KELLY_FRACTION      = float(os.getenv("KELLY_FRACTION",     "0.10"))
DAILY_LOSS_LIMIT    = float(os.getenv("DAILY_LOSS_LIMIT",   "150"))
MAX_EXPOSURE_PCT    = float(os.getenv("MAX_EXPOSURE_PCT",   "0.03"))
FIXED_BET_USD       = float(os.getenv("FIXED_BET_USD",      "5"))
SCAN_INTERVAL       = int(os.getenv("SCAN_INTERVAL",        "300"))
BALANCE_UPDATE_INT  = int(os.getenv("BALANCE_UPDATE_INT",   "1"))
INITIAL_BALANCE     = float(os.getenv("INITIAL_BALANCE",    "1000"))
# FIX 3: Lowered from 60 to 55 — was vetoing too many valid bets
LLM_CONF_MIN        = int(os.getenv("LLM_CONFIDENCE_MIN",   "55"))
ADVERSE_MOVE_PCT    = float(os.getenv("ADVERSE_MOVE_PCT",   "0.15"))
PROFIT_TARGET_PCT   = float(os.getenv("PROFIT_TARGET_PCT",  "0.25"))
IN_GAME_MONITOR     = os.getenv("IN_GAME_MONITOR","true").lower() in ("true","1","yes")
IN_GAME_HOURS_START = float(os.getenv("IN_GAME_HOURS_START","1.5"))
ADD_MORE_CONF_MIN   = int(os.getenv("ADD_MORE_CONF_MIN",    "80"))
MIN_VOL_NO_DATE     = float(os.getenv("MIN_VOL_NO_DATE",    "50"))  # FIX 8: was 100

MAX_HOURS_AHEAD     = 2
LIVE_POLL_INTERVAL  = int(os.getenv("LIVE_POLL_INTERVAL", "10"))

MIN_PRICE_THRESHOLD = float(os.getenv("MIN_PRICE_THRESHOLD", "0.05"))  # FIX 8: was 0.10
MAX_PRICE_THRESHOLD = float(os.getenv("MAX_PRICE_THRESHOLD", "0.95"))  # FIX 8: was 0.90

PRIMARY_MODEL  = "deepseek/deepseek-r1"
FALLBACK_MODEL = "deepseek/deepseek-chat"

DB_FILE   = "sports_v7.db"
SIM_FILE  = "sports_v7.json"
LOG_FILE  = "sports_v7.log"
TRADE_LOG = "trades_log_v7.txt"

GAMMA   = "https://gamma-api.polymarket.com"
CLOB    = "https://clob.polymarket.com"
ESPN    = "https://site.api.espn.com/apis/site/v2/sports"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; SportsBot/7.1)", "Cache-Control": "no-cache", "Pragma": "no-cache"}

BOT_START_TIME: datetime = datetime.now(timezone.utc)


# ═══════════════════════════════════════════════════════════════
# COLORS + LOGGING
# ═══════════════════════════════════════════════════════════════
class C:
    R="\033[0m";   B="\033[1m";    DIM="\033[2m"
    RED="\033[91m"; GRN="\033[92m"; YLW="\033[93m"
    CYN="\033[96m"; WHT="\033[97m"; MAG="\033[35m"
    BGRED="\033[41m"; BGBLU="\033[44m"; BGGRN="\033[42m"
    BGYEL="\033[43m"

_ANSI_RE = re.compile(r"\033\[[0-9;]*m")

def strip_ansi(s: str) -> str:
    return _ANSI_RE.sub("", s)

def cp(msg: str):
    try:
        print(msg + C.R)
    except UnicodeEncodeError:
        print((msg + C.R).encode("ascii", errors="replace").decode())

def tlog(msg: str):
    ts  = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[{ts}] {strip_ansi(msg)}"
    try:
        with open(TRADE_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

def _setup_logging():
    fmt  = logging.Formatter("%(asctime)s  %(levelname)-7s  %(message)s",
                             datefmt="%H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setFormatter(fmt); root.addHandler(fh)
    ch = logging.StreamHandler(sys.stdout)
    if hasattr(ch.stream, "reconfigure"):
        try: ch.stream.reconfigure(encoding="utf-8", errors="replace")
        except: pass
    ch.setFormatter(fmt); root.addHandler(ch)

_setup_logging()
log = logging.getLogger("v71")


# ═══════════════════════════════════════════════════════════════
# SPORTS KEYWORD LISTS
# ═══════════════════════════════════════════════════════════════
_NBA = ["CELTICS","LAKERS","WARRIORS","NUGGETS","HEAT","NETS","KNICKS","BULLS",
        "PISTONS","CAVALIERS","BUCKS","PACERS","RAPTORS","MAGIC","HAWKS","76ERS",
        "SIXERS","HORNETS","WIZARDS","THUNDER","ROCKETS","GRIZZLIES","JAZZ",
        "CLIPPERS","SUNS","KINGS","BLAZERS","TRAILBLAZERS","TIMBERWOLVES",
        "SPURS","MAVERICKS","PELICANS"]
_MLB = ["YANKEES","RED SOX","DODGERS","CUBS","METS","BRAVES","ASTROS","PHILLIES",
        "CARDINALS","GIANTS","PADRES","TIGERS","TWINS","ROYALS","RANGERS",
        "ATHLETICS","PIRATES","NATIONALS","ORIOLES","RAYS","MARINERS","ANGELS",
        "ROCKIES","BREWERS","REDS","MARLINS","WHITE SOX"]
_NHL = ["MAPLE LEAFS","BRUINS","PENGUINS","BLACKHAWKS","OILERS","FLAMES","CANUCKS",
        "CAPITALS","LIGHTNING","PANTHERS","HURRICANES","AVALANCHE","GOLDEN KNIGHTS",
        "STARS","BLUES","PREDATORS","WILD","SENATORS","CANADIENS","FLYERS",
        "DEVILS","ISLANDERS","SABRES","DUCKS","SHARKS","JETS","KINGS"]
_IPL = ["IPL","MUMBAI INDIANS","CHENNAI SUPER","ROYAL CHALLENGERS","KOLKATA KNIGHT",
        "SUNRISERS HYDERABAD","RAJASTHAN ROYALS","DELHI CAPITALS","GUJARAT TITANS",
        "LUCKNOW SUPER","PUNJAB KINGS"," MI "," CSK "," RCB "," KKR "," SRH ",
        " RR "," DC "," GT "," LSG "," PBKS "]
_SOC = ["PREMIER LEAGUE","CHAMPIONS LEAGUE","LA LIGA","SERIE A","BUNDESLIGA","MLS",
        "EPL","ARSENAL","CHELSEA","LIVERPOOL","REAL MADRID","BARCELONA","MANCHESTER",
        "TOTTENHAM","ATLETICO","JUVENTUS","MILAN","INTER","PSG","DORTMUND",
        "LEICESTER","EVERTON","ASTON VILLA","NEWCASTLE","WEST HAM","WOLVES",
        "LEVERKUSEN","NAPOLI","PORTO","BENFICA","AJAX","CELTIC","RANGERS"]

_BLOCK_WORDS = [
    "DIPLOMAT","SANCTION","ELECTION","PRESIDENT","MINISTER","TARIFF","TRADE WAR",
    "NUCLEAR","BITCOIN","CRYPTO","ETH","SOL ","DOGE","ANTHROPIC","OPENAI",
    "GPT","CLAUDE","CHATGPT","AI MODEL","MIDTERM","SENATE","CONGRESS","HOUSE ",
    "GOVERNOR","MAYOR","SUPREME COURT","AMENDMENT","REFERENDUM","LEGISLATION",
    "STOCK","NASDAQ","S&P","DOW JONES","INTEREST RATE","FED ","INFLATION",
    "GDP","RECESSION","IPO","MERGER","ACQUISITION","BANKRUPTCY",
    "EXPEL","EXPELLED","ARREST","WAR ","INVASION","ATTACK","MISSILE",
    "IRAN","ISRAEL","UKRAINE","RUSSIA","CHINA ","TAIWAN","NORTH KOREA",
    "OSCAR","GRAMMY","EMMY","GOLDEN GLOBE","ACADEMY AWARD",
    "BOX OFFICE","MOVIE","FILM","ALBUM","SONG","CHART",
    "CELEBRITY","KARDASHIAN","TAYLOR SWIFT","ELON MUSK","TRUMP","BIDEN","HARRIS",
    "SPACEX","TESLA","APPLE ","GOOGLE","META ","AMAZON","MICROSOFT",
    "CLIMATE","HURRICANE","EARTHQUAKE","FLOOD","VOLCANO",
    "VACCINE","COVID","PANDEMIC","WHO ","DRUG APPROVAL",
]

GAME_WORDS = [" vs "," vs. "," v "," @ "," at ","to beat ","beats ",
              "moneyline","to win","will beat","will win"]
FUTURES_WORDS = [
    "win the 2025","win the 2026","nba finals","nba champion",
    "world series","stanley cup","super bowl","world cup winner",
    "mvp","rookie of the year","most valuable","all-star",
    "eastern conference champion","western conference champion",
    "season wins","make the playoffs","win the championship",
    "first overall pick","win the season","qualify for",
    "advance to","promote","relegated","top scorer","golden boot",
    "win the series","win the pennant","win the title",
    "win the cup","nhl title","win the pennant",
    "end of season","finish top","finish last","regular season",
    "by april","by may","by june","by july","by end of",
    "next coach","next manager","transfer","sign with",
]


def is_blocked(title: str) -> bool:
    t = title.upper()
    return any(bw in t for bw in _BLOCK_WORDS)

def detect_sport(title: str) -> str:
    t = " " + title.upper() + " "
    if is_blocked(title):
        return "OTHER"
    if any(k in t for k in _IPL):
        return "CRICKET"
    if any(k in t for k in ["CRICKET","T20","ODI","TEST MATCH"," IPL "]):
        return "CRICKET"
    if any(k in t for k in [" NBA "," BASKETBALL "]):
        return "NBA"
    if any(k in t for k in _NBA):
        return "NBA"
    if any(k in t for k in [" MLB "," BASEBALL "]):
        return "MLB"
    if any(k in t for k in _MLB):
        return "MLB"
    if any(k in t for k in [" NHL "," HOCKEY "]):
        return "NHL"
    if any(k in t for k in _NHL):
        return "NHL"
    if any(k in t for k in [" SOCCER "," FOOTBALL "]):
        if not any(b in t for b in ["SUPER BOWL","NFL ","NCAA "]):
            return "SOCCER"
    if any(k in t for k in _SOC):
        return "SOCCER"
    if any(k in t for k in [" FC "," AFC "," UCL "," UEFA "]):
        return "SOCCER"
    return "OTHER"

def detect_league(title: str, sport: str) -> str:
    t = title.upper()
    if sport == "CRICKET":
        return "IPL" if any(k in t for k in _IPL) else "CRICKET"
    if sport == "SOCCER":
        if any(k in t for k in ["CHAMPIONS LEAGUE","UCL","UEFA"]): return "UCL"
        if any(k in t for k in ["PREMIER LEAGUE","ARSENAL","CHELSEA",
                                 "LIVERPOOL","TOTTENHAM","MANCHESTER",
                                 "LEICESTER","EVERTON","NEWCASTLE"]): return "EPL"
        if any(k in t for k in ["LA LIGA","REAL MADRID","BARCELONA","ATLETICO"]): return "LA_LIGA"
        if any(k in t for k in ["BUNDESLIGA","DORTMUND","LEVERKUSEN"]): return "BUND"
        if any(k in t for k in ["SERIE A","JUVENTUS","MILAN","INTER","NAPOLI"]): return "SERIE_A"
        return "MLS" if "MLS" in t else "SOCCER"
    return sport

def is_game(title: str) -> bool:
    tl = title.lower()
    if any(f in tl for f in FUTURES_WORDS):
        return False
    if is_blocked(title):
        return False
    return any(g in tl for g in GAME_WORDS)

def is_market_ou(title: str) -> bool:
    t = title.upper()
    ou_keywords = [" O/U ", "OVER/UNDER", "OVER ", "UNDER ",
                   "TOTAL RUNS", "RUNS SCORED", "TOTAL POINTS", "TOTAL GOALS",
                   "YELLOW CARDS", "CORNERS", "ACES", "DOUBLE FAULTS"]
    ml_keywords = ["MONEYLINE", "WILL BEAT", "WILL WIN", "TO WIN",
                   "WINNER", "ADVANCE", "QUALIFY", "CHAMPION", "MVP"]
    if any(kw in t for kw in ml_keywords):
        return False
    if any(kw in t for kw in ou_keywords):
        return True
    if re.search(r"O/U\s*\d+\.?\d*|OVER.*UNDER|\d+\.?\d*\s*(RUNS|POINTS|GOALS)", t):
        return True
    return False

def is_ipl(title: str) -> bool:
    t = " " + title.upper() + " "
    return any(k in t for k in _IPL)

def is_valid_sports_market(title: str) -> bool:
    if is_blocked(title):
        return False
    sport = detect_sport(title)
    if sport == "OTHER":
        return False
    if is_ipl(title):
        return True
    if is_game(title):
        return True
    return False

def extract_teams(title: str) -> tuple:
    pats = [
        r"will\s+(.+?)\s+(?:win\s+(?:vs?\.?\s+|against\s+|over\s+)|beat\s+|defeat\s+)(.+?)(?:\?|$|\s+on|\s+\()",
        r"^(.+?)\s+vs\.?\s+(.+?)(?:\?|$|\s+on|\s+[-\u2013\(])",
        r"^(.+?)\s+v\s+(.+?)(?:\?|$|\s+on|\s+[-\u2013\(])",
        r"^(.+?)\s+(?:at|@)\s+(.+?)(?:\?|$|\s+on|\s+[-\u2013\(])",
        r"(.+?)\s+beats?\s+(.+?)(?:\?|$|\s+\()",
    ]
    for pat in pats:
        m = re.search(pat, title, re.I)
        if m:
            a = re.sub(r"\s*(on|for|game|\d{4})\s*.*$","",m.group(1),flags=re.I).strip()
            b = re.sub(r"\s*(on|for|game|\d{4})\s*.*$","",m.group(2),flags=re.I).strip()
            if a and b and len(a)>2 and len(b)>2:
                return a[:45], b[:45]
    w = title.split(); mid = len(w)//2
    return " ".join(w[:mid])[:45], " ".join(w[mid:])[:45]

def normalize_game_date(end_date: str) -> str:
    if not end_date or str(end_date).strip() in ("","None","null","0","null","nan"):
        return "unknown"
    s = str(end_date).strip()
    dt = _parse_datetime(s)
    if dt:
        return dt.date().isoformat()
    m = re.search(r"(\d{4}-\d{2}-\d{2})", s)
    if m:
        return m.group(1)
    return s[:10] if len(s) >= 10 else s


def _parse_datetime(s: str) -> Optional[datetime]:
    s = str(s).strip()
    if not s:
        return None
    try:
        if re.match(r"^\d{10,13}$", s):
            ts = int(s)
            if ts > 1e12: ts //= 1000
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        if s.endswith("Z"):
            return datetime.fromisoformat(s[:-1] + "+00:00")
        if re.search(r"[+-]\d{2}:\d{2}$", s):
            return datetime.fromisoformat(s)
        if s.upper().endswith(" UTC") or s.upper().endswith(" GMT"):
            return datetime.fromisoformat(re.sub(r"\s+(UTC|GMT)$", "+00:00", s, flags=re.I))
        if "T" in s:
            return datetime.fromisoformat(s)
    except ValueError:
        pass

    fmts = [
        "%Y-%m-%dT%H:%M:%S.%f%z","%Y-%m-%dT%H:%M:%S%z","%Y-%m-%d %H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f","%Y-%m-%dT%H:%M:%S","%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d","%b %d %Y %H:%M:%S","%b %d %Y %H:%M",
        "%B %d %Y %H:%M:%S","%B %d %Y %H:%M","%d %b %Y %H:%M:%S",
        "%d %b %Y %H:%M","%b %d, %Y %H:%M:%S","%b %d, %Y %H:%M",
    ]
    for fmt in fmts:
        try:
            dt = datetime.strptime(s[:len(fmt)], fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None


def format_ist(dt_str: str) -> str:
    dt = _parse_datetime(dt_str)
    if dt:
        ist_tz = timezone(timedelta(hours=5, minutes=30))
        ist_dt = dt.astimezone(ist_tz)
        return ist_dt.strftime("%Y-%m-%d %H:%M:%S IST")
    return dt_str


def parse_hours(end_date: str) -> float:
    if not end_date or str(end_date).strip() in ("","None","null","0","null","nan"):
        return 999.0
    dt = _parse_datetime(end_date)
    if not dt:
        return 999.0
    return (dt - datetime.now(timezone.utc)).total_seconds() / 3600


def hours_from_bot_start(end_date: str) -> float:
    if not end_date or str(end_date).strip() in ("","None","null","0","null","nan"):
        return 999.0
    dt = _parse_datetime(end_date)
    if not dt:
        return 999.0
    return (dt - BOT_START_TIME).total_seconds() / 3600


# ═══════════════════════════════════════════════════════════════
# DATA CLASSES
# ═══════════════════════════════════════════════════════════════
@dataclass
class MarketInfo:
    condition_id: str; title: str; sport: str; league: str
    yes_token: str; no_token: str; end_date: str; volume: float
    market_id: str; yes_vol: float = 0.0; no_vol: float = 0.0
    hours_left: float = 999.0
    hours_from_start: float = 999.0

@dataclass
class TradePosition:
    opened_at: str; sport: str; league: str; title: str
    condition_id: str; yes_token: str; no_token: str
    direction: str; entry_price: float; bet_usd: float
    shares: float; model_prob: float; edge: float
    llm_conf: int = 0
    resolved: bool = False; outcome: str = "OPEN"; pnl: float = 0.0
    phase: str = "PRE_GAME"
    last_price: float = 0.0; last_action: str = "NONE"
    monitor_note: str = ""; exited_early: bool = False
    game_time: str = ""

@dataclass
class BotStats:
    balance: float = INITIAL_BALANCE
    trades: int = 0; wins: int = 0; losses: int = 0
    total_pnl: float = 0.0; daily_pnl: float = 0.0
    daily_reset: str = ""; llm_calls: int = 0
    llm_nobet: int = 0; pre_skips: int = 0
    early_exits: int = 0; early_pnl: float = 0.0
    last_balance_update: str = ""

    def save(self): pass

    @property
    def win_rate(self):
        c = self.wins + self.losses
        return self.wins / c * 100 if c else 0.0


# ═══════════════════════════════════════════════════════════════
# TRADE LOGGER
# ═══════════════════════════════════════════════════════════════
def log_trade_open(pos: TradePosition, balance_after: float,
                   edge: float, conf: int, reasoning: str, factors: list):
    sep  = "=" * 70
    msg  = (
        f"\n{sep}\n📈 TRADE OPENED\n"
        f"  Sport   : {pos.sport} | {pos.league}\n"
        f"  Market  : {pos.title}\n"
        f"  Side    : {pos.direction}  Entry={pos.entry_price:.4f}  Shares={pos.shares:.4f}\n"
        f"  Bet     : ${pos.bet_usd:.2f}  |  Balance after: ${balance_after:.2f}\n"
        f"  Edge    : {edge:+.1%}  |  Conf: {conf}%\n"
        f"  Factors : {' | '.join(factors[:3]) or 'n/a'}\n"
        f"  Reasoning: {reasoning}\n"
        f"  Match Opens  : {format_ist(pos.opened_at)}\n"
        f"  Match Closes : {format_ist(pos.game_time)}\n{sep}"
    )
    tlog(msg)
    side_col = C.GRN if pos.direction=="YES" else C.YLW
    cp(f"\n  {C.BGGRN}{C.WHT}{C.B} TRADE OPENED {C.R}  "
       f"[{pos.sport}|{pos.league}]  {side_col}{pos.direction}{C.R}@{pos.entry_price:.4f}"
       f"  ${pos.bet_usd:.2f}  edge={C.GRN}{edge:+.1%}{C.R}  conf={C.GRN}{conf}%{C.R}"
       f"\n   {pos.title[:60]}"
       f"\n   Bal: {C.WHT}${balance_after:.2f}{C.R}")

def log_trade_close(pos: TradePosition, balance_after: float):
    won = pos.outcome in ("WIN","EXIT_PROFIT")
    icon = "✅ WIN" if won else "❌ LOSS"
    pnl_s  = f"+${pos.pnl:.2f}" if pos.pnl >= 0 else f"-${abs(pos.pnl):.2f}"
    sep = "=" * 70
    msg = (
        f"\n{sep}\n{icon}\n"
        f"  Sport   : {pos.sport} | {pos.league}\n"
        f"  Market  : {pos.title}\n"
        f"  Side    : {pos.direction}  Entry={pos.entry_price:.4f}\n"
        f"  Result  : {pos.outcome}  PnL={pnl_s}\n"
        f"  Balance after: ${balance_after:.2f}\n"
        f"  Note    : {pos.monitor_note or 'resolved'}\n{sep}"
    )
    tlog(msg)
    col = C.GRN if won else C.RED
    cp(f"\n  {col}{C.B}{'✅ WIN' if won else '❌ LOSS'}{C.R}"
       f"  [{pos.sport}|{pos.league}] {pos.title[:52]}"
       f"\n   PnL={col}{pnl_s}{C.R}  Bal={C.WHT}${balance_after:.2f}{C.R}"
       f"  Outcome={pos.outcome}")

def log_balance_update(balance: float, source: str):
    msg = f"💰 BALANCE UPDATE [{source}]: ${balance:.2f}"
    tlog(msg)
    cp(f"  {C.CYN}💰 Balance: {C.WHT}${balance:.2f}{C.R}  [{source}]")


def print_live_dashboard(sim: "SimManager", label: str = ""):
    s = sim.stats
    open_p  = [p for p in sim.positions if not p.resolved]
    closed  = [p for p in sim.positions if p.resolved]
    pnl_col = C.GRN if s.total_pnl >= 0 else C.RED
    day_col = C.GRN if s.daily_pnl >= 0 else C.RED
    cp(f"\n{C.CYN}{'═'*65}{C.R}")
    if label:
        cp(f"  {C.BGYEL}{C.B} {label} {C.R}")
    cp(f"  {C.B}💰 Balance:{C.R} {C.WHT}${s.balance:,.2f}{C.R}  "
       f"Total PnL: {pnl_col}{s.total_pnl:+.2f}{C.R}  "
       f"Today: {day_col}{s.daily_pnl:+.2f}{C.R}")
    cp(f"  {C.B}📊 Record:{C.R} {s.trades} trades  "
       f"{C.GRN}✅ W:{s.wins}{C.R} / {C.RED}❌ L:{s.losses}{C.R}  "
       f"WR:{C.GRN}{s.win_rate:.1f}%{C.R}")
    if open_p:
        cp(f"\n  {C.B}📋 OPEN POSITIONS ({len(open_p)}):{C.R}")
        for p in open_p:
            side_col = C.GRN if p.direction == "YES" else C.YLW
            drift_str = ""
            if p.last_price > 0:
                d = (p.last_price - p.entry_price) / p.entry_price * 100
                dc = C.GRN if d >= 0 else C.RED
                drift_str = f"  now={p.last_price:.4f} ({dc}{d:+.1f}%{C.R})"
            cp(f"    [{p.phase}] {side_col}{p.direction}{C.R}@{p.entry_price:.4f}"
               f"  ${p.bet_usd:.0f}  conf={p.llm_conf}%{drift_str}"
               f"\n      {p.title[:60]}")
    else:
        cp(f"  📭 No open positions.")
    if closed:
        last3 = closed[-3:]
        cp(f"\n  {C.B}📜 RECENT CLOSED:{C.R}")
        for p in reversed(last3):
            col = C.GRN if p.pnl >= 0 else C.RED
            cp(f"    {col}{'✅' if p.pnl>=0 else '❌'} {p.direction}"
               f"  {p.title[:45]}  PnL={col}${p.pnl:+.2f}{C.R}")
    cp(f"{C.CYN}{'═'*65}{C.R}\n")
    tlog(f"DASHBOARD [{label}] | bal=${s.balance:.2f} | W{s.wins}/L{s.losses} | open={len(open_p)}")


# ═══════════════════════════════════════════════════════════════
# DATABASE
# ═══════════════════════════════════════════════════════════════
class DB:
    def __init__(self):
        self.conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        self._lock = threading.Lock()
        self._init()

    def _init(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                opened_at TEXT, sport TEXT, league TEXT, title TEXT,
                condition_id TEXT UNIQUE, direction TEXT,
                entry_price REAL, bet_usd REAL, shares REAL,
                model_prob REAL, edge REAL, llm_conf INTEGER DEFAULT 0,
                resolved INTEGER DEFAULT 0, outcome TEXT DEFAULT 'OPEN',
                pnl REAL DEFAULT 0, phase TEXT DEFAULT 'PRE_GAME',
                last_price REAL DEFAULT 0, last_action TEXT DEFAULT 'NONE',
                monitor_note TEXT DEFAULT '', exited_early INTEGER DEFAULT 0,
                game_time TEXT DEFAULT '', reasoning TEXT DEFAULT '',
                factors TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS scans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT, found INTEGER, signals INTEGER, executed INTEGER,
                llm_calls INTEGER DEFAULT 0, balance REAL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS prices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_id TEXT, price REAL, ts TEXT
            );
            CREATE TABLE IF NOT EXISTS monitor_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT, condition_id TEXT, phase TEXT,
                live_price REAL, drift REAL, action TEXT, note TEXT
            );
            CREATE TABLE IF NOT EXISTS balance_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT, balance REAL, source TEXT
            );
        """)
        self.conn.commit()

    def _exec(self, sql, params=()):
        with self._lock:
            self.conn.execute(sql, params)
            self.conn.commit()

    def open_trade(self, pos: TradePosition, reasoning: str = "", factors: str = ""):
        try:
            self._exec(
                "INSERT OR IGNORE INTO trades "
                "(opened_at,sport,league,title,condition_id,direction,"
                "entry_price,bet_usd,shares,model_prob,edge,llm_conf,"
                "game_time,reasoning,factors) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (pos.opened_at,pos.sport,pos.league,pos.title,pos.condition_id,
                 pos.direction,pos.entry_price,pos.bet_usd,pos.shares,
                 pos.model_prob,pos.edge,pos.llm_conf,pos.game_time,
                 reasoning, factors))
        except Exception as e:
            log.warning("DB open_trade: %s", e)

    def already_open(self, cid: str) -> bool:
        with self._lock:
            return bool(self.conn.execute(
                "SELECT id FROM trades WHERE condition_id=? AND resolved=0",(cid,)
            ).fetchone())

    def log_scan(self, found, signals, executed, llm_calls=0, balance=0):
        self._exec(
            "INSERT INTO scans (ts,found,signals,executed,llm_calls,balance) VALUES (?,?,?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(), found, signals, executed, llm_calls, balance))

    def log_balance(self, balance: float, source: str):
        self._exec(
            "INSERT INTO balance_log (ts,balance,source) VALUES (?,?,?)",
            (datetime.now(timezone.utc).isoformat(), balance, source))

    def save_price(self, token_id: str, price: float):
        self._exec(
            "INSERT INTO prices (token_id,price,ts) VALUES (?,?,?)",
            (token_id, price, datetime.now(timezone.utc).isoformat()))

    def price_2h_ago(self, token_id: str) -> Optional[float]:
        ago = (datetime.now(timezone.utc)-timedelta(hours=2)).isoformat()
        with self._lock:
            row = self.conn.execute(
                "SELECT price FROM prices WHERE token_id=? AND ts>=? ORDER BY ts ASC LIMIT 1",
                (token_id, ago)).fetchone()
        return float(row[0]) if row else None

    def update_monitor(self, cid, phase, price, action, note, exited=False):
        self._exec(
            "UPDATE trades SET phase=?,last_price=?,last_action=?,monitor_note=?,exited_early=? "
            "WHERE condition_id=?",
            (phase, price, action, note[:300], int(exited), cid))
        self._exec(
            "INSERT INTO monitor_log (ts,condition_id,phase,live_price,drift,action,note) "
            "VALUES (?,?,?,?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(),cid,phase,price,0,action,note[:300]))

    def resolve_trade(self, cid: str, outcome: str, pnl: float):
        self._exec(
            "UPDATE trades SET resolved=1, outcome=?, pnl=? WHERE condition_id=?",
            (outcome, pnl, cid))

    def get_outcome(self, cid: str) -> Optional[str]:
        try:
            r = requests.get(f"{CLOB}/markets/{cid}", timeout=6, headers=HEADERS)
            data = r.json()
            if data.get("resolved"):
                p = data.get("resolved_payout",[])
                if isinstance(p,list) and len(p)>=2:
                    if float(p[0]) > float(p[1]):
                        return "YES"
                    elif float(p[0]) < float(p[1]):
                        return "NO"
                    else:
                        return "CANCEL"
            for t in data.get("tokens",[]):
                if t.get("winner"):
                    return t.get("outcome","").upper()
        except: pass
        return None


# ═══════════════════════════════════════════════════════════════
# MARKET DISCOVERY
# ═══════════════════════════════════════════════════════════════
class MarketDiscovery:
    def __init__(self):
        self._s = requests.Session()
        self._s.headers.update(HEADERS)
        self._s.headers.update({"Connection": "close"})

    def _get(self, url, params=None, timeout=15) -> Optional[dict]:
        try:
            r = self._s.get(url, params=params, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            log.debug("  HTTP %d: %s", r.status_code, url[:60])
        except Exception as e:
            log.debug("  GET error %s: %s", url[:50], e)
        return None

    def _parse_raw(self, m: dict, src: str) -> Optional[dict]:
        title = (m.get("question") or m.get("title") or "").strip()
        if not title:
            return None
        if not is_valid_sports_market(title):
            return None
        if is_market_ou(title):
            return None

        cid = (m.get("condition_id") or m.get("conditionId") or "")
        if not cid:
            return None

        tids = (m.get("clobTokenIds") or m.get("token_ids") or m.get("tokenIds") or [])
        if isinstance(tids, str):
            try: tids = json.loads(tids)
            except: tids = []
        if not tids:
            tids = [t.get("token_id") or t.get("tokenId","")
                    for t in (m.get("tokens") or m.get("outcomes") or [])]
        if len(tids) < 2 or not tids[0]:
            return None

        yes_tok = no_tok = ""
        yv = nv = 0.0
        for tok in (m.get("tokens") or []):
            nm = (tok.get("outcome") or tok.get("side","")).upper()
            tid = tok.get("token_id") or tok.get("tokenId") or tok.get("id") or ""
            v  = float(tok.get("volume") or tok.get("volumeNum") or 0)
            if nm == "YES":
                yes_tok = tid or yes_tok; yv = v
            elif nm == "NO":
                no_tok = tid or no_tok; nv = v

        if not yes_tok and len(tids) >= 1: yes_tok = tids[0]
        if not no_tok and len(tids) >= 2:  no_tok = tids[1]

        vol = float(m.get("volume") or m.get("volumeNum") or m.get("usdVolume") or 0)
        end_date = (m.get("endDate") or m.get("end_date_iso") or m.get("end_date") or
                    m.get("closeTime") or m.get("close_time") or m.get("eventDate") or
                    m.get("eventStart") or m.get("startDate") or m.get("start_date") or "")
        return {
            "_src": src, "conditionId": cid, "question": title,
            "clobTokenIds": tids, "yes_token": yes_tok, "no_token": no_tok,
            "endDate": str(end_date), "volume": vol,
            "id": m.get("id") or cid, "tokens": m.get("tokens",[]),
            "yes_vol": yv, "no_vol": nv,
        }

    def _l1_clob_simplified(self) -> list:
        out = []; seen = set(); cursor = "MA=="; page = 0
        while page < 30:
            data = self._get(f"{CLOB}/simplified-markets",
                             {"next_cursor": cursor, "active": "true"})
            if not data or isinstance(data, str): break
            items  = data.get("data", []) if isinstance(data, dict) else []
            cursor = data.get("next_cursor","") if isinstance(data, dict) else ""
            for m in items:
                parsed = self._parse_raw(m, "clob_simplified")
                if not parsed: continue
                cid = parsed["conditionId"]
                if cid in seen: continue
                seen.add(cid); out.append(parsed)
            page += 1
            if not cursor or cursor in ("MA==","") or not items: break
            time.sleep(0.15)
        log.info("  [L1-CLOB] TOTAL: %d sports markets", len(out))
        return out

    def _l2_clob_markets(self) -> list:
        out = []; seen = set()
        data = self._get(f"{CLOB}/markets", {"active": "true", "limit":"500"})
        if not data: return out
        items = data if isinstance(data, list) else data.get("data", data.get("markets",[]))
        for m in items:
            parsed = self._parse_raw(m, "clob_markets")
            if not parsed: continue
            cid = parsed["conditionId"]
            if cid in seen: continue
            seen.add(cid); out.append(parsed)
        log.info("  [L2-CLOB-Direct] %d sports markets", len(out))
        return out

    def _l3_gamma_markets(self) -> list:
        out = []; seen = set()
        queries = [
            {"tag_id":"6","limit":"200","order":"volume","ascending":"false","active":"true","closed":"false"},
            {"tag_id":"7","limit":"200","order":"volume","ascending":"false","active":"true","closed":"false"},
            {"tag_id":"8","limit":"200","order":"volume","ascending":"false","active":"true","closed":"false"},
            {"tag_id":"12","limit":"200","order":"volume","ascending":"false","active":"true","closed":"false"},
            {"tag_id":"21","limit":"200","order":"volume","ascending":"false","active":"true","closed":"false"},
            {"q":"vs","active":"true","closed":"false","limit":"100","order":"volume","ascending":"false"},
            {"q":"ipl","active":"true","closed":"false","limit":"100"},
            {"q":"cricket","active":"true","closed":"false","limit":"100"},
            {"q":"nba","active":"true","closed":"false","limit":"100"},
            {"q":"mlb","active":"true","closed":"false","limit":"100"},
            {"q":"nhl","active":"true","closed":"false","limit":"100"},
            {"q":"soccer","active":"true","closed":"false","limit":"100"},
            {"q":"mumbai indians","active":"true","closed":"false","limit":"50"},
            {"q":"chennai super","active":"true","closed":"false","limit":"50"},
            {"q":"rajasthan royals","active":"true","closed":"false","limit":"50"},
            {"q":"kolkata knight","active":"true","closed":"false","limit":"50"},
            {"q":"sunrisers","active":"true","closed":"false","limit":"50"},
            {"q":"royal challengers","active":"true","closed":"false","limit":"50"},
            {"q":"lakers","active":"true","closed":"false","limit":"50"},
            {"q":"celtics","active":"true","closed":"false","limit":"50"},
            {"q":"yankees","active":"true","closed":"false","limit":"50"},
            {"q":"arsenal","active":"true","closed":"false","limit":"50"},
            {"q":"liverpool","active":"true","closed":"false","limit":"50"},
            {"q":"manchester","active":"true","closed":"false","limit":"50"},
        ]
        for p in queries:
            data = self._get(f"{GAMMA}/markets", p, timeout=10)
            if not data: continue
            items = data if isinstance(data,list) else data.get("markets",data.get("data",[]))
            for m in items:
                parsed = self._parse_raw(m, "gamma_markets")
                if not parsed: continue
                cid = parsed["conditionId"]
                if cid in seen: continue
                seen.add(cid); out.append(parsed)
        log.info("  [L3-Gamma] %d unique sports markets", len(out))
        return out

    def _l4_gamma_events(self) -> list:
        out = []; seen = set()
        for p in [
            {"active":"true","closed":"false","limit":"200","order":"volume","ascending":"false"},
            {"active":"true","closed":"false","limit":"200","tag_slug":"sports"},
            {"active":"true","closed":"false","limit":"200","tag_slug":"cricket"},
            {"active":"true","closed":"false","limit":"200","tag_slug":"basketball"},
            {"active":"true","closed":"false","limit":"200","tag_slug":"soccer"},
        ]:
            data = self._get(f"{GAMMA}/events", p, timeout=12)
            if not data: continue
            events = data if isinstance(data,list) else data.get("events",data.get("data",[]))
            for evt in events:
                nested = evt.get("markets") or []
                for m in nested:
                    parsed = self._parse_raw(m, "gamma_events")
                    if not parsed: continue
                    cid = parsed["conditionId"]
                    if cid in seen: continue
                    seen.add(cid); out.append(parsed)
        log.info("  [L4-Gamma-Events] %d sports markets", len(out))
        return out

    def discover(self) -> list:
        log.info("[DISCOVER] Starting 4-layer sports-only scan...")
        all_raw = []
        all_raw.extend(self._l1_clob_simplified())
        all_raw.extend(self._l2_clob_markets())
        all_raw.extend(self._l3_gamma_markets())
        all_raw.extend(self._l4_gamma_events())

        seen = set(); markets = []
        ct_dupe = ct_other = ct_time = ct_price = ct_ok = 0

        for m in all_raw:
            cid   = m["conditionId"]
            title = m["question"]
            if cid in seen:
                ct_dupe += 1; continue
            seen.add(cid)

            sport  = detect_sport(title)
            league = detect_league(title, sport)
            if sport == "OTHER":
                ct_other += 1; continue

            end_date = m.get("endDate","")
            hl       = parse_hours(end_date)
            hfs      = hours_from_bot_start(end_date)

            if hl < -6:
                ct_time += 1
                log.debug("  [SKIP] expired %.0fh ago: %s", -hl, title[:45])
                continue

            if hl == 999.0:
                if m["volume"] < MIN_VOL_NO_DATE:
                    ct_time += 1; continue
            else:
                if hfs > MAX_HOURS_AHEAD:
                    ct_time += 1
                    log.debug("  [SKIP-24H] closes %.1fh after bot start: %s", hfs, title[:45])
                    continue

            # FIX 8: Relaxed price filter — check but don't reject on fetch failure
            yes_tok = str(m.get("yes_token") or m.get("clobTokenIds",[""])[0])
            if yes_tok:
                live_px = self.get_price(yes_tok)
                if live_px is not None:
                    if live_px < MIN_PRICE_THRESHOLD or live_px > MAX_PRICE_THRESHOLD:
                        ct_price += 1
                        log.debug("  [SKIP-PRICE] price=%.4f outside [%.2f-%.2f]: %s",
                                 live_px, MIN_PRICE_THRESHOLD, MAX_PRICE_THRESHOLD, title[:45])
                        continue

            mi = MarketInfo(
                condition_id=cid, title=title, sport=sport, league=league,
                yes_token=yes_tok,
                no_token=str(m.get("no_token") or m.get("clobTokenIds",["",""])[1]),
                end_date=str(end_date), volume=m["volume"],
                market_id=m.get("id") or cid,
                yes_vol=m.get("yes_vol",0.0), no_vol=m.get("no_vol",0.0),
                hours_left=hl, hours_from_start=hfs,
            )
            markets.append(mi); ct_ok += 1
            log.info("  [OK] h_left=%.1f vol=%.0f [%s|%s]: %s",
                     hl, m["volume"], sport, league, title[:55])

        markets.sort(key=lambda mi: mi.hours_left if mi.hours_left != 999.0 else 9999.0)

        log.info("[DISCOVER] DONE: %d valid markets | dupe=%d other=%d time=%d price=%d",
                 ct_ok, ct_dupe, ct_other, ct_time, ct_price)

        if markets:
            cp(f"\n  {C.CYN}[DISCOVER] {ct_ok} markets found — soonest first{C.R}")
            cp(f"  First: {C.YLW}{markets[0].title[:55]}{C.R} h_left={markets[0].hours_left:.1f}h")

        return markets

    def get_price(self, token_id: str) -> Optional[float]:
        import time
        try:
            params = {"token_id": token_id, "_": int(time.time()*1000)}
            r = self._s.get(f"{CLOB}/midpoint",
                            params=params, timeout=6)
            r.raise_for_status()
            v = r.json().get("mid")
            return float(v) if v is not None else None
        except: return None

    def get_depth(self, token_id: str) -> dict:
        empty = {"total": 0, "spread": 1, "bids": 0, "asks": 0}
        try:
            r = self._s.get(f"{CLOB}/book",
                            params={"token_id": token_id}, timeout=6)
            r.raise_for_status()
            data = r.json()
            bids = data.get("bids",[])
            asks = data.get("asks",[])
            bd = sum(float(b.get("size",0)) for b in bids[:5])
            ad = sum(float(a.get("size",0)) for a in asks[:5])
            bb = float(bids[0]["price"]) if bids else 0
            ba = float(asks[0]["price"]) if asks else 1
            return {"total": bd+ad, "spread": round(ba-bb,4), "bids": bd, "asks": ad}
        except: return empty


# ═══════════════════════════════════════════════════════════════
# ESPN DATA
# ═══════════════════════════════════════════════════════════════
class ESPNFetcher:
    _cache: dict = {}
    SMAP = {"NBA":("basketball","nba"),"MLB":("baseball","mlb"),
            "NHL":("hockey","nhl"),"SOCCER":("soccer","eng.1")}

    def _get(self, url, params=None):
        k = url+str(params)
        if k in self._cache: return self._cache[k]
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=8)
            r.raise_for_status()
            d = r.json(); self._cache[k] = d; return d
        except: return None

    def standings(self, sport: str) -> dict:
        if sport not in self.SMAP: return {}
        sk, lg = self.SMAP[sport]
        data = self._get(f"{ESPN}/{sk}/{lg}/teams", {"limit":50})
        if not data: return {}
        out = {}
        for t in (data.get("sports",[{}])[0].get("leagues",[{}])[0].get("teams",[])):
            info = t.get("team",{})
            rec  = info.get("record",{}).get("items",[{}])
            if not rec: continue
            stats = {s["name"]:float(s.get("value",0))
                     for s in rec[0].get("stats",[]) if "name" in s}
            w, l = stats.get("wins",0), stats.get("losses",0)
            wp   = round(w/(w+l),4) if (w+l)>0 else 0.5
            for key in ["displayName","shortDisplayName","name","abbreviation","location"]:
                n = info.get(key,"").strip().lower()
                if n and len(n)>1: out[n] = wp
        return out

    def win_pct(self, std: dict, team: str) -> float:
        if not team or not std: return 0.5
        tl = team.lower().strip()
        if tl in std: return std[tl]
        for k,wp in std.items():
            if tl in k or k in tl: return wp
        words = set(tl.split()); best,bwp = 0,0.5
        for k,wp in std.items():
            ov = len(words & set(k.split()))
            if ov > best: best,bwp = ov,wp
        return bwp if best>0 else 0.5

    def home_team(self, sport: str, a: str, b: str) -> Optional[str]:
        if sport not in self.SMAP: return None
        sk,lg = self.SMAP[sport]
        data = self._get(f"{ESPN}/{sk}/{lg}/scoreboard")
        if not data: return None
        al,bl = a.lower()[:5], b.lower()[:5]
        for ev in data.get("events",[]):
            comp  = ev.get("competitions",[{}])[0]
            comps = comp.get("competitors",[])
            names = " ".join(c.get("team",{}).get("displayName","").lower() for c in comps)
            if al not in names and bl not in names: continue
            for c in comps:
                if c.get("homeAway")=="home":
                    return c.get("team",{}).get("displayName")
        return None

    def recent_form(self, sport: str, team: str) -> str:
        if sport not in self.SMAP: return "N/A"
        sk,lg = self.SMAP[sport]
        std   = self._get(f"{ESPN}/{sk}/{lg}/teams",{"limit":50})
        if not std: return "N/A"
        tid = None; tl = team.lower().strip()
        for t in (std.get("sports",[{}])[0].get("leagues",[{}])[0].get("teams",[])):
            info = t.get("team",{})
            for key in ["displayName","shortDisplayName","name","abbreviation"]:
                n = info.get(key,"").strip().lower()
                if n and (tl in n or n in tl):
                    tid = info.get("id"); break
            if tid: break
        if not tid: return "N/A"
        data = self._get(f"{ESPN}/{sk}/{lg}/teams/{tid}/schedule",
                         {"season": datetime.now().year})
        if not data: return "N/A"
        results = []
        for ev in data.get("events",[])[-10:]:
            comp = ev.get("competitions",[{}])[0]
            for c in comp.get("competitors",[]):
                if str(c.get("team",{}).get("id","")) == str(tid):
                    if c.get("winner") is True: results.append("W")
                    elif c.get("winner") is False: results.append("L")
                    break
        last5 = results[-5:]
        w,l = last5.count("W"), last5.count("L")
        return f"{' '.join(last5)} ({w}-{l})" if last5 else "N/A"

    def injuries(self, sport: str, team: str) -> list:
        if sport not in self.SMAP: return []
        sk,lg = self.SMAP[sport]
        std = self._get(f"{ESPN}/{sk}/{lg}/teams",{"limit":50})
        if not std: return []
        tid = None; tl = team.lower().strip()
        for t in (std.get("sports",[{}])[0].get("leagues",[{}])[0].get("teams",[])):
            info = t.get("team",{})
            for key in ["displayName","shortDisplayName","name","abbreviation"]:
                n = info.get(key,"").strip().lower()
                if n and (tl in n or n in tl):
                    tid = info.get("id"); break
            if tid: break
        if not tid: return []
        data = self._get(f"{ESPN}/{sk}/{lg}/teams/{tid}/injuries")
        if not data: return []
        out = []
        for item in (data.get("injuries") or [])[:5]:
            a  = item.get("athlete",{})
            st = item.get("status","")
            if st.upper() in ("OUT","DOUBTFUL","QUESTIONABLE"):
                nm = a.get("displayName","")
                ps = a.get("position",{}).get("abbreviation","")
                if nm: out.append(f"{nm}({ps})[{st}]")
        return out[:4]

    def mlb_eras(self, a: str, b: str) -> dict:
        data = self._get(f"{ESPN}/baseball/mlb/scoreboard")
        if not data: return {}
        al,bl = a.lower()[:5], b.lower()[:5]
        for ev in data.get("events",[]):
            comp  = ev.get("competitions",[{}])[0]
            names = " ".join(c.get("team",{}).get("displayName","").lower()
                             for c in comp.get("competitors",[]))
            if al not in names and bl not in names: continue
            res = {}
            for c in comp.get("competitors",[]):
                role  = c.get("homeAway","home")
                probs = c.get("probables") or []
                if not probs: continue
                stats = {s.get("name"):s.get("displayValue","4.50")
                         for s in probs[0].get("statistics",[])}
                try: era = float(str(stats.get("ERA","4.50")).replace("-","4.50"))
                except: era = 4.50
                res[f"{role}_era"] = era
                res[f"{role}_pitcher"] = probs[0].get("athlete",{}).get("displayName","?")
            return res
        return {}

    def ipl_data(self, title: str) -> dict:
        out = {"toss_winner":"","toss_choice":"","pitch_type":"balanced","venue":"","venue_avg":175}
        try:
            r = requests.get(
                "https://hs-consumer-api.espncricinfo.com/v1/pages/matches/upcoming",
                params={"lang":"en","series":"8048"}, headers=HEADERS, timeout=8)
            if r.status_code != 200: return out
            data = r.json()
            matches = (data.get("matches") or data.get("content",{}).get("matches",[]))
            tl = title.upper()
            for m in matches:
                t1 = m.get("team1",{}).get("abbreviation","").upper()
                t2 = m.get("team2",{}).get("abbreviation","").upper()
                if t1 in tl or t2 in tl:
                    venue = m.get("ground",{}).get("name","")
                    out["venue"] = venue
                    vl = venue.lower()
                    if any(v in vl for v in ["wankhede","chinnaswamy","eden"]):
                        out["pitch_type"]="batting-friendly"; out["venue_avg"]=185
                    elif any(v in vl for v in ["chepauk","feroz shah","kotla"]):
                        out["pitch_type"]="spin-friendly"; out["venue_avg"]=165
                    break
        except Exception as e:
            log.debug("IPL data: %s", e)
        return out


# ═══════════════════════════════════════════════════════════════
# STAT MODEL — FIX 4: Better fallback probability
# ═══════════════════════════════════════════════════════════════
HOME_ADV = {"NBA":0.058,"MLB":0.040,"NHL":0.050,"SOCCER":0.075,"CRICKET":0.030}

def stat_estimate(sport, wpa, wpb, a_home, h_era=4.5, a_era=4.5) -> float:
    """
    FIX 4: Returns meaningful probability even when ESPN data is unavailable.
    - If both teams are 0.5 (no data), returns 0.5 +/- home advantage
    - Doesn't block betting just because ESPN data is missing
    """
    tot  = wpa + wpb
    if tot == 0:
        base = 0.5
    else:
        base = wpa / tot

    if a_home:
        base += HOME_ADV.get(sport, 0.04)
    if sport == "MLB" and (h_era != 4.5 or a_era != 4.5):
        base += (a_era - h_era) * 0.025
    return round(max(0.05, min(0.95, base)), 4)


def stat_has_real_data(wpa: float, wpb: float, form_a: str, form_b: str) -> bool:
    """Check if we have any real statistical data for the game."""
    has_standings = not (wpa == 0.5 and wpb == 0.5)
    has_form = form_a != "N/A" or form_b != "N/A"
    return has_standings or has_form


# ═══════════════════════════════════════════════════════════════
# LLM — FIX 1: Completely rewritten prompts for more recommendations
# ═══════════════════════════════════════════════════════════════

# FIX 1: New prompt — focused on finding edge, not avoiding bets
GAME_LEVEL_SYSTEM = """You are a sports betting analyst for Polymarket prediction markets.
Your goal is to find ACTIONABLE BETS with positive expected value.

IMPORTANT CONTEXT:
- These are Polymarket prediction markets (binary YES/NO outcomes)
- Market price reflects crowd consensus — your job is to find where crowd is WRONG
- Even small edges (2-5%) are valuable at scale
- If you have ANY reasonable opinion on who will win, you should bet

SPORT ANALYSIS FRAMEWORKS:
NBA  : Recent form > win% > home advantage(+5-6%) > injuries > H2H
MLB  : Starting pitcher ERA > home advantage(+4%) > team win% > bullpen
NHL  : Recent form > goalie stats > home advantage(+5%) > injuries
SOCCER: Home advantage(+7-8%) > league position > recent form > H2H
IPL/CRICKET: Pitch type > toss impact > recent form > H2H at venue > team strength

HOW TO FIND EDGE:
- YES price vs your estimated win probability. If YES=0.45 but you think team wins 55% → YES has +10% edge
- Line movement direction = smart money signal
- Injury news not yet priced in = edge
- Strong home team is often underpriced (public loves away glamour teams)
- Teams on 4+ win streaks often underpriced

BET DECISION RULES:
- If your probability estimate differs from market price by 2%+ → BET
- Confidence can be 55-100. Lower confidence = smaller size (handled by Kelly)
- Data quality LOW means NO ESPN data, but you can STILL BET if you know the teams
- Only say "NO BET" if you genuinely have zero opinion on the outcome

OUTPUT FORMAT — JSON ONLY, no markdown, no preamble:
{
  "game_analysis": "2-3 sentence game overview",
  "recommended_bets": [
    {
      "market_title": "exact market title from input",
      "yes_probability": 0.00,
      "confidence": 0,
      "bet_side": "YES" | "NO",
      "key_factors": ["factor1", "factor2", "factor3"],
      "reasoning": "Why this side has edge over market price"
    }
  ],
  "data_quality": "HIGH" | "MEDIUM" | "LOW"
}

IMPORTANT: recommended_bets must contain at least one bet UNLESS you are genuinely 50/50 on the outcome.
If edge >= 2%, include the bet. Be decisive."""


INGAME_SYSTEM = """You monitor live sports positions on Polymarket.
Decide: HOLD, EXIT_PROFIT, EXIT_PROTECT, or ADD_MORE.

HOLD        : game developing as expected, hold position.
EXIT_PROFIT : price moved 25%+ in your favour OR game clearly won. Take profit.
EXIT_PROTECT: price moved 15%+ against you OR situation reversed.
ADD_MORE    : price moved 5-10% against but thesis STILL strongly valid, confidence >= 80.

OUTPUT JSON ONLY:
{"action":"HOLD"|"EXIT_PROFIT"|"EXIT_PROTECT"|"ADD_MORE",
 "updated_win_probability":0.00,"confidence":0,
 "reasoning":"1-2 sentences","urgency":"LOW"|"MEDIUM"|"HIGH"}"""


def _extract_json_from_text(raw: str) -> Optional[str]:
    if not raw:
        return None
    think_stripped = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    clean = re.sub(r"```(?:json)?|```", "", think_stripped).strip()

    candidates = []
    i = 0
    while i < len(clean):
        if clean[i] == '{':
            depth = 0; in_string = False; escape = False
            for j in range(i, len(clean)):
                ch = clean[j]
                if ch == '"' and not escape: in_string = not in_string
                if in_string and ch == '\\' and not escape: escape = True; continue
                escape = False
                if in_string: continue
                if ch == '{': depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0:
                        candidates.append(clean[i:j+1]); i = j + 1; break
            else: i += 1
        else: i += 1

    for candidate in reversed(candidates):
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict): return candidate
        except json.JSONDecodeError:
            cleaned = re.sub(r",\s*([}\]])", r"\1", candidate)
            try:
                parsed = json.loads(cleaned)
                if isinstance(parsed, dict): return cleaned
            except: continue

    lines = clean.split('\n')
    for i in range(len(lines) - 1, -1, -1):
        line = lines[i].strip()
        if line.startswith('{') and line.endswith('}'):
            try:
                parsed = json.loads(line)
                if isinstance(parsed, dict): return line
            except json.JSONDecodeError: continue

    return None


def _llm_call(system: str, user_msg: str, max_tok=600) -> Optional[str]:
    if not OPENROUTER_API_KEY:
        log.error("    LLM disabled: OPENROUTER_API_KEY missing")
        return None

    for model in [PRIMARY_MODEL, FALLBACK_MODEL]:
        for attempt in range(2):
            try:
                t0 = time.time()
                r = requests.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}",
                             "Content-Type": "application/json"},
                    json={"model": model,
                          "messages":[{"role":"system","content":system},
                                      {"role":"user","content":user_msg}],
                          "max_tokens": max_tok,
                          "temperature": 0.1},
                    timeout=90)
                elapsed = time.time()-t0
                status = getattr(r, "status_code", None)
                log.info("    LLM %.1fs [%s] status=%s", elapsed, model.split("/")[-1], status)

                if status != 200:
                    body = r.text[:300] if hasattr(r, "text") else ""
                    log.warning("    LLM HTTP %s body=%s", status, body)
                    time.sleep(2); continue

                payload = r.json()
                choices = payload.get("choices") or payload.get("output") or []
                if isinstance(choices, dict): choices = [choices]
                if isinstance(choices, str):  choices = [{"content": choices}]
                if not choices:
                    log.warning("    LLM returned no choices")
                    continue

                choice = choices[0]
                msg = choice.get("message", {})
                raw = msg.get("content") or choice.get("content") or choice.get("text")
                if not raw:
                    raw = msg.get("reasoning") or ""
                if not raw:
                    details = msg.get("reasoning_details") or []
                    for d in (details if isinstance(details, list) else []):
                        if isinstance(d, dict) and d.get("text"):
                            raw = d["text"]; break
                if not raw and msg:
                    raw = json.dumps(msg)
                if not raw:
                    log.warning("    LLM empty content")
                    continue

                return raw

            except requests.Timeout:
                log.warning("    LLM timeout [%s] attempt %d", model, attempt+1)
            except Exception as e:
                log.warning("    LLM error [%s]: %s", model, e)
            if attempt == 0: time.sleep(4)

    log.error("    LLM failed on all models/attempts")
    return None


def _parse_json(raw: str) -> Optional[dict]:
    candidate = _extract_json_from_text(raw)
    if not candidate:
        log.warning("    JSON: no valid JSON found (length=%d)", len(raw) if raw else 0)
        return None
    try:
        return json.loads(candidate)
    except Exception as e:
        log.warning("    JSON parse error: %s", e)
        return None


def group_by_game(markets: list) -> dict:
    """
    FIX 5: Better game grouping — uses team names extracted from title
    to group related markets (moneyline + 1H) together.
    """
    games = {}
    for mi in markets:
        date_key = normalize_game_date(mi.end_date)
        ta, tb = extract_teams(mi.title)
        # Normalize team names for grouping
        ta_norm = re.sub(r'\s+', ' ', ta.strip().lower())[:20]
        tb_norm = re.sub(r'\s+', ' ', tb.strip().lower())[:20]
        game_key = tuple(sorted([ta_norm, tb_norm])) + (date_key,)
        if game_key not in games:
            games[game_key] = []
        games[game_key].append(mi)
    return games


# ═══════════════════════════════════════════════════════════════
# FIX 6: Stat-model fallback when LLM fails or returns no bets
# ═══════════════════════════════════════════════════════════════
def stat_model_fallback(mi: MarketInfo, yes_px: float, wpa: float, wpb: float,
                        a_home: bool, form_a: str, form_b: str,
                        inj_a: list, inj_b: list,
                        h_era: float = 4.5, a_era: float = 4.5) -> Optional[dict]:
    """
    FIX 6: Pure stat-model bet when LLM is unavailable or returns nothing.
    Only bets when edge is clear from the statistical model.
    """
    sp = stat_estimate(mi.sport, wpa, wpb, a_home, h_era, a_era)

    # Build factors list
    factors = []
    if wpa != 0.5 or wpb != 0.5:
        factors.append(f"WinPct A={wpa:.3f} B={wpb:.3f}")
    if a_home:
        factors.append(f"Home adv +{HOME_ADV.get(mi.sport,0.04):.1%}")
    if form_a != "N/A":
        factors.append(f"Form A: {form_a}")
    if form_b != "N/A":
        factors.append(f"Form B: {form_b}")
    if inj_a:
        factors.append(f"A injuries: {', '.join(inj_a[:2])}")
    if inj_b:
        factors.append(f"B injuries: {', '.join(inj_b[:2])}")

    edge_yes = sp - yes_px
    edge_no  = (1 - sp) - (1 - yes_px)

    if abs(edge_yes) < MIN_EDGE and abs(edge_no) < MIN_EDGE:
        log.info("    [STAT-FALLBACK] Edge too small: YES_edge=%.3f NO_edge=%.3f model=%.4f price=%.4f",
                 edge_yes, edge_no, sp, yes_px)
        return None

    if edge_yes >= MIN_EDGE:
        direction = "YES"; edge = edge_yes; prob = sp
        conf = min(75, 50 + int(edge_yes * 200))
    else:
        direction = "NO"; edge = edge_no; prob = 1 - sp
        conf = min(75, 50 + int(edge_no * 200))

    if conf < LLM_CONF_MIN:
        return None

    reasoning = f"Stat model: {mi.sport} prob={sp:.3f} vs market={yes_px:.3f}, edge={edge:+.3f}"

    log.info("    [STAT-FALLBACK] %s %s edge=%.3f conf=%d prob=%.4f",
             direction, mi.title[:40], edge, conf, prob)

    return {
        "market": mi, "yes_probability": sp,
        "confidence": conf, "bet_side": direction,
        "key_factors": factors or ["Stat model estimate"],
        "reasoning": reasoning, "data_quality": "MEDIUM"
    }


def llm_game_level(game_markets: list, disc, db, espn_fetcher, standings_cache: dict) -> list:
    if not game_markets:
        return []

    mi = game_markets[0]
    ta, tb = extract_teams(mi.title)

    # FIX 8: Relaxed depth requirement (was 5, now 0 — just needs a price)
    markets_data = []
    for market in game_markets:
        yes_px = disc.get_price(market.yes_token)
        if yes_px is None:
            log.info("    [SKIP] No price for: %s", market.title[:40])
            continue
        if yes_px < MIN_PRICE_THRESHOLD or yes_px > MAX_PRICE_THRESHOLD:
            log.info("    [SKIP-PRICE] price=%.4f for %s", yes_px, market.title[:40])
            continue
        no_px = round(1-yes_px, 4)
        depth = disc.get_depth(market.yes_token)

        db.save_price(market.yes_token, yes_px)
        old_px = db.price_2h_ago(market.yes_token)
        mv_pct = (yes_px-old_px) if old_px else 0.0
        mv_dir = "UP" if mv_pct>0.005 else "DOWN" if mv_pct<-0.005 else "STABLE"

        tot = market.yes_vol + market.no_vol
        yv_pct = market.yes_vol/tot if tot>0 else yes_px

        markets_data.append({
            'market': market, 'title': market.title,
            'yes_price': yes_px, 'no_price': no_px,
            'volume': market.volume, 'yes_vol_pct': yv_pct,
            'depth': depth, 'line_move': mv_dir, 'line_pct': mv_pct,
            'hours_left': market.hours_left
        })

    if not markets_data:
        log.info("    [GAME-SKIP] No priced markets for %s vs %s", ta, tb)
        return []

    std = standings_cache.get(mi.sport, {})
    wpa = espn_fetcher.win_pct(std, ta)
    wpb = espn_fetcher.win_pct(std, tb)
    ht = espn_fetcher.home_team(mi.sport, ta, tb)
    a_home = bool(ht) and (ht or "").lower()[:5] in ta.lower()
    form_a = espn_fetcher.recent_form(mi.sport, ta)
    form_b = espn_fetcher.recent_form(mi.sport, tb)
    inj_a = espn_fetcher.injuries(mi.sport, ta)
    inj_b = espn_fetcher.injuries(mi.sport, tb)

    h_era = a_era = 4.5
    if mi.sport == "MLB":
        eras = espn_fetcher.mlb_eras(ta, tb)
        h_era = eras.get("home_era", 4.5)
        a_era = eras.get("away_era", 4.5)

    toss = pitch = ""
    venue_avg = 175
    if mi.league == "IPL":
        ipl = espn_fetcher.ipl_data(mi.title)
        toss = ipl.get("toss_winner", "")
        pitch = ipl.get("pitch_type", "")
        venue_avg = ipl.get("venue_avg", 175)

    sp = stat_estimate(mi.sport, wpa, wpb, a_home, h_era, a_era)
    has_data = stat_has_real_data(wpa, wpb, form_a, form_b)

    msg = "\n".join([
        f"=== GAME: {ta} vs {tb} ===",
        f"Sport: {mi.sport} | League: {mi.league}",
        f"Team A ({ta}): win%={wpa:.3f}  form_last5={form_a}",
        f"Team B ({tb}): win%={wpb:.3f}  form_last5={form_b}",
        f"Home team: {ht or 'unknown'} | A_is_home: {a_home}",
        f"Injuries A: {', '.join(inj_a) or 'none'}",
        f"Injuries B: {', '.join(inj_b) or 'none'}",
        f"Statistical model says: {sp:.4f} probability Team A wins",
        f"Data availability: {'GOOD (has standings/form)' if has_data else 'LIMITED (using defaults)'}",
    ])
    if mi.sport == "MLB" and (h_era != 4.5 or a_era != 4.5):
        msg += f"\nHome starter ERA: {h_era:.2f}  Away starter ERA: {a_era:.2f}"
    if mi.league == "IPL":
        msg += f"\nToss: {toss or 'not decided'}  Pitch: {pitch}  Venue avg score: {venue_avg:.0f}"

    msg += "\n\n=== MARKETS TO ANALYZE ==="
    for i, md in enumerate(markets_data):
        msg += f"\n[{i+1}] Title: {md['title']}"
        msg += f"\n    YES price: {md['yes_price']:.4f}  |  NO price: {md['no_price']:.4f}"
        msg += f"\n    Volume: ${md['volume']:.0f}  |  YES_vol%: {md['yes_vol_pct']:.1%}"
        msg += f"\n    Line move (2h): {md['line_move']} ({md['line_pct']:+.2%})"
        msg += f"\n    Hours to close: {md['hours_left']:.1f}h"
        msg += f"\n    Your stat model edge: YES={sp - md['yes_price']:+.4f}"

    msg += "\n\nFor each market: does the stat model + any other info you know give edge? Bet if diff >= 2%."

    raw = _llm_call(GAME_LEVEL_SYSTEM, msg, max_tok=800)
    parsed = _parse_json(raw)

    recommendations = []

    if parsed:
        title_to_market_data = {md['title'].strip().lower(): md for md in markets_data}

        for bet in parsed.get("recommended_bets", []):
            market_title = bet.get("market_title", "")
            desired = re.sub(r"\s+", " ", market_title.strip().lower())

            # FIX 7: Better market matching
            matching_md = title_to_market_data.get(desired)
            if not matching_md:
                # Fuzzy match
                for t, md_candidate in title_to_market_data.items():
                    if desired in t or t in desired or \
                       (len(desired) > 10 and desired[:15] in t):
                        matching_md = md_candidate; break
            if not matching_md and len(markets_data) == 1:
                matching_md = markets_data[0]

            if not matching_md:
                log.info("    [LLM] Could not match market title: %s", market_title[:40])
                continue

            side = bet.get("bet_side", "NO BET")
            if side == "NO BET":
                log.info("    [LLM] NO BET for: %s", market_title[:40])
                continue

            prob = float(bet.get("yes_probability", 0.5))
            conf = int(bet.get("confidence", 50))

            # FIX 7: Clamp values to valid range
            prob = max(0.01, min(0.99, prob))
            conf = max(40, min(99, conf))

            log.info("    [LLM-REC] %s | side=%s | prob=%.4f | conf=%d | price=%.4f",
                     matching_md['title'][:40], side, prob, conf, matching_md['yes_price'])

            recommendations.append({
                "market": matching_md['market'],
                "yes_probability": prob,
                "confidence": conf,
                "bet_side": side,
                "key_factors": bet.get("key_factors", []),
                "reasoning": bet.get("reasoning", ""),
                "data_quality": parsed.get("data_quality", "MEDIUM")
            })

    # FIX 6: If LLM gave no recommendations, try stat model fallback
    if not recommendations:
        log.info("    [FALLBACK] LLM gave 0 recs, trying stat model for %s vs %s", ta, tb)
        for md in markets_data:
            fallback = stat_model_fallback(
                md['market'], md['yes_price'], wpa, wpb, a_home,
                form_a, form_b, inj_a, inj_b, h_era, a_era)
            if fallback:
                recommendations.append(fallback)
                log.info("    [FALLBACK] Added stat-model bet: %s %s",
                         fallback['bet_side'], md['title'][:40])

    log.info("    [LLM-TOTAL] %d final recommendations for %s vs %s",
             len(recommendations), ta, tb)
    return recommendations


def llm_ingame(pos: TradePosition, live: dict,
               cur_price: float, hours_left: float) -> Optional[dict]:
    entry = pos.entry_price
    drift = (cur_price-entry)/entry if entry>0 else 0.0
    if pos.direction == "NO":
        drift = (entry-cur_price)/entry if entry>0 else 0.0

    msg = "\n".join([
        f"ACTIVE POSITION: {pos.title}",
        f"Side: {pos.direction} @ entry={entry:.4f}  Shares={pos.shares:.4f}  Staked=${pos.bet_usd:.2f}",
        f"Original conf: {pos.llm_conf}%  Model prob: {pos.model_prob:.4f}",
        f"Current YES price: {cur_price:.4f}  Drift: {drift:+.2%} ({'GOOD' if drift>=0 else 'BAD'})",
        f"Hours left: {hours_left:.1f}h  Phase: {pos.phase}",
        f"Game status: {live.get('status','?')}  Score A: {live.get('score_a','?')}  Score B: {live.get('score_b','?')}",
        f"Situation: {live.get('situation','no live data')}",
    ])
    raw = _llm_call(INGAME_SYSTEM, msg, max_tok=200)
    parsed = _parse_json(raw)
    if not parsed:
        if drift <= -ADVERSE_MOVE_PCT:
            return {"action":"EXIT_PROTECT","updated_win_probability":pos.model_prob,
                    "confidence":60,"reasoning":"Auto stop-loss: adverse drift","urgency":"HIGH"}
        if drift >= PROFIT_TARGET_PCT:
            return {"action":"EXIT_PROFIT","updated_win_probability":pos.model_prob,
                    "confidence":60,"reasoning":"Auto profit exit","urgency":"MEDIUM"}
        return {"action":"HOLD","updated_win_probability":pos.model_prob,
                "confidence":55,"reasoning":"LLM unavailable — HOLD","urgency":"LOW"}
    act = str(parsed.get("action","HOLD")).upper()
    if act not in ("HOLD","EXIT_PROFIT","EXIT_PROTECT","ADD_MORE"): act = "HOLD"
    return {"action":act,
            "prob":max(0.02,min(0.98,float(parsed.get("updated_win_probability",pos.model_prob)))),
            "conf":max(40,min(99,int(parsed.get("confidence",55)))),
            "reasoning":str(parsed.get("reasoning",""))[:300],
            "urgency":str(parsed.get("urgency","LOW")).upper()}


# ═══════════════════════════════════════════════════════════════
# LIVE SCORE FETCHER
# ═══════════════════════════════════════════════════════════════
class LiveScore:
    _cache: dict = {}
    SMAP = {"NBA":("basketball","nba"),"MLB":("baseball","mlb"),
            "NHL":("hockey","nhl"),"SOCCER":("soccer","eng.1")}

    def _get(self, url, params=None, ttl=40):
        k = url+str(params)
        e = self._cache.get(k)
        if e and time.time()-e["ts"]<ttl: return e["d"]
        try:
            r = requests.get(url,params=params,headers=HEADERS,timeout=8)
            r.raise_for_status()
            d = r.json(); self._cache[k]={"d":d,"ts":time.time()}; return d
        except: return None

    def get(self, sport: str, a: str, b: str) -> dict:
        empty = {"status":"UNKNOWN","period":"","clock":"",
                 "score_a":"","score_b":"","situation":"no data","extra":{}}
        if sport not in self.SMAP:
            return self._ipl(a,b)
        sk,lg = self.SMAP[sport]
        data = self._get(f"{ESPN}/{sk}/{lg}/scoreboard")
        if not data: return empty
        al,bl = a.lower()[:5], b.lower()[:5]
        for ev in data.get("events",[]):
            comp  = ev.get("competitions",[{}])[0]
            comps = comp.get("competitors",[])
            names = " ".join(c.get("team",{}).get("displayName","").lower() for c in comps)
            if al not in names and bl not in names: continue
            st_obj = comp.get("status",{})
            status = st_obj.get("type",{}).get("name","UNKNOWN").upper()
            period = str(st_obj.get("period",""))
            clock  = str(st_obj.get("displayClock",""))
            sa=sb="?"
            for c in comps:
                nm = c.get("team",{}).get("displayName","").lower()
                sc = c.get("score","?")
                if al in nm: sa=sc
                elif bl in nm: sb=sc
            return {"status":status,"period":period,"clock":clock,
                    "score_a":sa,"score_b":sb,
                    "situation":f"P{period} T{clock} A:{sa} B:{sb}","extra":{}}
        return empty

    def _ipl(self, a: str, b: str) -> dict:
        empty = {"status":"UNKNOWN","period":"","clock":"",
                 "score_a":"","score_b":"","situation":"no IPL live data","extra":{}}
        try:
            r = requests.get(
                "https://hs-consumer-api.espncricinfo.com/v1/pages/matches/live",
                params={"lang":"en"}, headers=HEADERS, timeout=8)
            if r.status_code!=200: return empty
            matches = r.json().get("matches") or []
            al,bl = a.lower()[:4], b.lower()[:4]
            for m in matches:
                t1=m.get("team1",{}).get("shortName","").lower()
                t2=m.get("team2",{}).get("shortName","").lower()
                if (al in t1 or bl in t1) and (al in t2 or bl in t2):
                    innings = m.get("innings",[])
                    scores  = [f"{i.get('team',{}).get('shortName','?')}: "
                               f"{i.get('runs','?')}/{i.get('wickets','?')} "
                               f"({i.get('overs','?')}ov)" for i in innings]
                    toss = m.get("toss",{})
                    status = ("IN_PROGRESS" if m.get("isLive") else
                              "FINAL" if m.get("isComplete") else "SCHEDULED")
                    return {
                        "status":status,"period":"IPL","clock":m.get("currentInnings",""),
                        "score_a":scores[0] if scores else "?",
                        "score_b":scores[1] if len(scores)>1 else "?",
                        "situation":" | ".join(scores),
                        "extra":{"toss_winner":toss.get("winner",{}).get("shortName",""),
                                 "toss_decision":toss.get("decision","")}}
        except Exception as e:
            log.debug("IPL live: %s", e)
        return empty


# ═══════════════════════════════════════════════════════════════
# KELLY + SIZING
# ═══════════════════════════════════════════════════════════════
def kelly(prob: float, price: float) -> float:
    if price<=0 or price>=1: return 0.0
    b = 1.0/price - 1.0
    f = max(0.0, (b*prob-(1-prob))/b)
    return round(f*KELLY_FRACTION, 5)

def size_bet(kf: float, balance: float, edge: float, conf: int) -> float:
    if FIXED_BET_USD > 0:
        raw = FIXED_BET_USD
    else:
        raw = kf * balance
        if edge > 0.12 and conf >= 80: raw *= 1.35
        elif edge > 0.08 and conf >= 75: raw *= 1.20
        elif conf >= 75: raw *= 1.10
        elif conf < 60: raw *= 0.80
    cap = min(raw, MAX_EXPOSURE_PCT*balance, MAX_BET_USD, balance)
    return round(cap,2) if cap>=MIN_BET_USD else 0.0


# ═══════════════════════════════════════════════════════════════
# CLOB EXECUTOR
# ═══════════════════════════════════════════════════════════════
class CLOBExec:
    def __init__(self):
        self.client = None
        if PAPER_MODE or not PRIVATE_KEY:
            log.info("[PAPER] Paper mode active — no real orders")
            return
        try:
            from py_clob_client.client import ClobClient
            self.client = ClobClient(
                host=CLOB, key=PRIVATE_KEY, chain_id=CHAIN_ID,
                signature_type=SIGNATURE_TYPE, funder=POLYMARKET_FUNDER_ADDRESS)
            self.client.set_api_creds(self.client.create_or_derive_api_creds())
            log.info("[LIVE] CLOB client initialized successfully")
        except ImportError:
            log.error("[CLOB] py_clob_client not installed. Run: pip install py-clob-client")
        except Exception as e:
            log.error("[CLOB] Init failed: %s", e)

    def balance(self) -> float:
        if not self.client: return INITIAL_BALANCE
        try:
            from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
            r = self.client.get_balance_allowance(
                BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
            return round(int(r.get("balance", 0)) / 1_000_000, 2)
        except Exception as e:
            log.warning("[CLOB] balance() error: %s", e)
            return 0.0

    def get_best_ask(self, token_id: str) -> Optional[float]:
        try:
            r = requests.get(f"{CLOB}/book", params={"token_id": token_id},
                             headers=HEADERS, timeout=6)
            r.raise_for_status()
            asks = r.json().get("asks", [])
            if asks: return float(asks[0]["price"])
        except Exception as e:
            log.warning("[CLOB] get_best_ask error: %s", e)
        return None

    def buy(self, token_id: str, amount_usd: float, direction: str = "YES") -> Optional[dict]:
        if not self.client:
            log.info("    [PAPER-BUY] token=%s amount=$%.2f dir=%s",
                     token_id[:16], amount_usd, direction)
            return {"paper": True, "token_id": token_id, "amount": amount_usd, "direction": direction}
        try:
            from py_clob_client.clob_types import MarketOrderArgs, OrderType
            from py_clob_client.order_builder.constants import BUY
            best_ask = self.get_best_ask(token_id)
            if best_ask is None: return None
            order_args = MarketOrderArgs(token_id=token_id, amount=amount_usd,
                                         side=BUY, order_type=OrderType.FOK)
            signed_order = self.client.create_market_order(order_args)
            resp = self.client.post_order(signed_order, OrderType.FOK)
            if resp:
                order_id = resp.get("orderID") or resp.get("id") or "?"
                log.info("    [CLOB-BUY] ✅ id=%s", order_id)
                return resp
            return None
        except Exception as e:
            log.error("    [CLOB-BUY] ❌ Exception: %s", e)
            return None

    def sell(self, token_id: str, shares: float) -> Optional[dict]:
        if not self.client:
            log.info("    [PAPER-SELL] token=%s shares=%.4f", token_id[:16], shares)
            return {"paper": True, "token_id": token_id, "shares": shares}
        try:
            from py_clob_client.clob_types import MarketOrderArgs, OrderType
            from py_clob_client.order_builder.constants import SELL
            order_args = MarketOrderArgs(token_id=token_id, amount=shares,
                                         side=SELL, order_type=OrderType.FOK)
            signed_order = self.client.create_market_order(order_args)
            resp = self.client.post_order(signed_order, OrderType.FOK)
            if resp:
                log.info("    [CLOB-SELL] ✅ id=%s", resp.get("orderID","?"))
                return resp
            return None
        except Exception as e:
            log.error("    [CLOB-SELL] ❌ Exception: %s", e)
            return None

    def verify_fill(self, order_id: str) -> dict:
        if not self.client: return {"filled": True, "paper": True}
        try:
            r = requests.get(f"{CLOB}/orders/{order_id}", headers=HEADERS, timeout=6)
            if r.status_code == 200:
                data = r.json()
                status = data.get("status","").upper()
                filled = status in ("MATCHED","FILLED","COMPLETED")
                return {"filled": filled, "status": status,
                        "size_matched": float(data.get("sizeMatched", 0))}
        except Exception as e:
            log.warning("[CLOB] verify_fill error: %s", e)
        return {"filled": False, "status": "UNKNOWN"}


# ═══════════════════════════════════════════════════════════════
# SIMULATION MANAGER
# ═══════════════════════════════════════════════════════════════
class SimManager:
    def __init__(self):
        self._load()

    def _load(self):
        if os.path.exists(SIM_FILE):
            try:
                d = json.load(open(SIM_FILE))
                self.stats = BotStats(**{k:v for k,v in d.items()
                                         if k!="positions" and
                                         k in BotStats.__dataclass_fields__})
                self.positions = [TradePosition(**p) for p in d.get("positions",[])]
                self.game_exposure = defaultdict(float, d.get("game_exposure", {}))
                return
            except Exception as e:
                log.warning("SimManager load: %s", e)
        self.stats = BotStats(); self.positions = []
        self.game_exposure = defaultdict(float)

    def save(self):
        d = asdict(self.stats)
        d["positions"] = [asdict(p) for p in self.positions]
        d["game_exposure"] = dict(self.game_exposure)
        json.dump(d, open(SIM_FILE,"w"), indent=2)
        self.stats.save = self.save

    def reset_daily(self):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self.stats.daily_reset != today:
            self.stats.daily_pnl = 0.0; self.stats.daily_reset = today; self.save()

    def limit_hit(self) -> bool:
        return self.stats.daily_pnl < -DAILY_LOSS_LIMIT

    def settle_all(self, db: DB):
        changed = False
        for pos in self.positions:
            if pos.resolved: continue
            outcome = db.get_outcome(pos.condition_id)
            if outcome is None: continue
            if outcome == "CANCEL":
                pos.pnl = 0.0
                pos.outcome = "CANCEL"
                self.stats.balance = round(self.stats.balance + pos.bet_usd, 4)
            else:
                won = outcome == pos.direction
                if won:
                    pos.pnl = round(pos.shares - pos.bet_usd, 4)
                    pos.outcome = "WIN"; self.stats.wins += 1
                    self.stats.balance = round(self.stats.balance + pos.shares, 4)
                else:
                    pos.pnl = -pos.bet_usd
                    pos.outcome = "LOSS"; self.stats.losses += 1
            pos.resolved = True
            self.stats.total_pnl = round(self.stats.total_pnl + pos.pnl, 4)
            self.stats.daily_pnl = round(self.stats.daily_pnl + pos.pnl, 4)
            game_key = pos.title[:50]
            self.game_exposure[game_key] -= pos.bet_usd
            if self.game_exposure[game_key] <= 0:
                del self.game_exposure[game_key]
            db.resolve_trade(pos.condition_id, pos.outcome, pos.pnl)
            log_trade_close(pos, self.stats.balance)
            db.log_balance(self.stats.balance, "settle")
            changed = True
        if changed:
            self.save(); print_live_dashboard(self, "SETTLEMENT")

    def open_pos(self, mi: MarketInfo, direction: str, price: float,
                 bet: float, model_prob: float, edge: float, conf: int,
                 reasoning: str = "", factors: list = None,
                 db: DB = None) -> TradePosition:
        open_count = len([p for p in self.positions if not p.resolved])
        if open_count >= 2:
            log.info("Skipping trade, max 2 open positions")
            return None
        game_key = mi.title[:50]
        if self.game_exposure[game_key] + bet > 150:
            log.info("Skipping trade, per game cap 150$ exceeded")
            return None
        pos = TradePosition(
            opened_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            sport=mi.sport, league=mi.league, title=mi.title,
            condition_id=mi.condition_id, yes_token=mi.yes_token,
            no_token=mi.no_token, direction=direction,
            entry_price=price, bet_usd=bet,
            shares=round(bet/price,4), model_prob=model_prob,
            edge=edge, llm_conf=conf, game_time=mi.end_date,
        )
        self.positions.append(pos)
        self.game_exposure[game_key] += bet
        self.stats.balance = round(self.stats.balance-bet, 4)
        self.stats.trades += 1
        if db: db.log_balance(self.stats.balance, "open_trade")
        self.save()
        log_trade_open(pos, self.stats.balance, edge, conf, reasoning, factors or [])
        print_live_dashboard(self, "TRADE OPENED")
        return pos

    def early_exit(self, pos: TradePosition, yes_price: float, reason: str, db: DB):
        if pos.direction=="YES":
            proceeds = round(pos.shares*yes_price, 4)
        else:
            proceeds = round(pos.shares*(1-yes_price), 4)
        pnl = round(proceeds-pos.bet_usd, 4)
        pos.pnl = pnl
        pos.outcome = "EXIT_PROFIT" if pnl>0 else "EXIT_PROTECT"
        pos.resolved = True; pos.exited_early = True; pos.monitor_note = reason[:200]
        self.stats.balance    = round(self.stats.balance+proceeds, 4)
        self.stats.total_pnl  = round(self.stats.total_pnl+pnl, 4)
        self.stats.daily_pnl  = round(self.stats.daily_pnl+pnl, 4)
        self.stats.early_exits += 1; self.stats.early_pnl = round(self.stats.early_pnl+pnl, 4)
        if pnl>0: self.stats.wins += 1
        else:     self.stats.losses += 1
        db.resolve_trade(pos.condition_id, pos.outcome, pnl)
        db.update_monitor(pos.condition_id, pos.phase, yes_price, pos.outcome, reason, exited=True)
        db.log_balance(self.stats.balance, "early_exit")
        log_trade_close(pos, self.stats.balance)
        log_balance_update(self.stats.balance, "early_exit")
        print_live_dashboard(self, f"EARLY EXIT [{pos.outcome}]")
        self.save()

    def print_summary(self):
        s = self.stats
        wc = C.GRN if s.win_rate>=70 else (C.YLW if s.win_rate>=50 else C.RED)
        pc = C.GRN if s.total_pnl>=0 else C.RED
        dc = C.GRN if s.daily_pnl>=0 else C.RED
        bar = "═" * 68
        cp(f"\n{C.CYN}{bar}{C.R}")
        cp(f"  {C.BGBLU}{C.WHT}{C.B} CemeterysunReplicant v7.1 ── PORTFOLIO {C.R}")
        cp(f"  💰 Balance : {C.WHT}${s.balance:,.2f}{C.R}  "
           f"Total PnL={pc}${s.total_pnl:+.2f}{C.R}  Today={dc}${s.daily_pnl:+.2f}{C.R}")
        cp(f"  📊 Record  : {s.trades} trades  "
           f"{C.GRN}✅ W:{s.wins}{C.R} / {C.RED}❌ L:{s.losses}{C.R}  "
           f"WR:{wc}{s.win_rate:.1f}%{C.R}")
        if s.llm_calls>0:
            nb_rate = s.llm_nobet/s.llm_calls*100 if s.llm_calls>0 else 0
            cp(f"  🤖 LLM     : {s.llm_calls} calls  NO_BET={s.llm_nobet}({nb_rate:.0f}%)")
        if s.early_exits>0:
            ec = C.GRN if s.early_pnl>=0 else C.RED
            cp(f"  ⚡ EarlyExit: {s.early_exits}  pnl={ec}${s.early_pnl:+.2f}{C.R}")
        open_p = [p for p in self.positions if not p.resolved]
        if open_p:
            exp = sum(p.bet_usd for p in open_p)
            cp(f"\n  📋 Open ({len(open_p)}) exposure=${exp:.2f}:")
            for p in open_p:
                ec = C.GRN if p.edge>0.04 else C.YLW
                side_col = C.GRN if p.direction=="YES" else C.YLW
                cp(f"    [{p.phase}] [{p.sport}|{p.league}] "
                   f"{side_col}{p.direction}{C.R}@{p.entry_price:.4f}"
                   f"  ${p.bet_usd:.0f}  edge={ec}{p.edge:+.1%}{C.R}  conf={p.llm_conf}%"
                   f"\n      {p.title[:55]}")
        else:
            cp(f"  📭 No open positions.")
        cp(f"{C.CYN}{bar}{C.R}\n")

    def reset(self):
        self.stats = BotStats(); self.positions = []; self.save()
        cp(f"  {C.YLW}Reset complete. Starting balance: ${INITIAL_BALANCE:.2f}{C.R}")
        log.info("Reset to $%.2f", INITIAL_BALANCE)


# ═══════════════════════════════════════════════════════════════
# LIVE PRICE POLLER (FIX D retained)
# ═══════════════════════════════════════════════════════════════
class LivePricePoller:
    def __init__(self, disc: MarketDiscovery):
        self.disc    = disc
        self._sim: Optional[SimManager] = None
        self._stop   = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="LivePoller")

    def attach(self, sim: SimManager):
        self._sim = sim

    def start(self):
        if not self._thread.is_alive():
            self._thread.start()
            log.info("[POLLER] Live price poller started (every %ds)", LIVE_POLL_INTERVAL)

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.is_set():
            try: self._poll_once()
            except Exception as e: log.debug("[POLLER] error: %s", e)
            self._stop.wait(LIVE_POLL_INTERVAL)

    def _poll_once(self):
        if self._sim is None: return
        open_p = [p for p in self._sim.positions if not p.resolved]
        if not open_p: return

        now_str = datetime.now(timezone.utc).strftime("%H:%M:%S")
        updates = []
        for pos in open_p:
            yes_px = self.disc.get_price(pos.yes_token)
            if yes_px is None: continue
            pos.last_price = yes_px
            if pos.direction == "YES":
                current_px = yes_px
                drift = (yes_px - pos.entry_price) / pos.entry_price * 100
                est_pnl = round(pos.shares * yes_px - pos.bet_usd, 2)
            else:
                no_px = 1 - yes_px
                current_px = no_px
                drift = (no_px - pos.entry_price) / pos.entry_price * 100
                est_pnl = round(pos.shares * no_px - pos.bet_usd, 2)
            dc = C.GRN if drift >= 0 else C.RED
            pc = C.GRN if est_pnl >= 0 else C.RED
            updates.append(
                f"    [{pos.sport}] {pos.direction}@{pos.entry_price:.4f}"
                f" → {current_px:.4f} {dc}{drift:+.1f}%{C.R}"
                f" estPnL={pc}${est_pnl:+.2f}{C.R} | {pos.title[:35]}"
            )
        if updates:
            cp(f"\n  {C.DIM}[TICK {now_str}]{C.R}")
            for u in updates: cp(u)


# ═══════════════════════════════════════════════════════════════
# IN-GAME MONITOR
# ═══════════════════════════════════════════════════════════════
class Monitor:
    def __init__(self, disc: MarketDiscovery, live: LiveScore, clob: CLOBExec, db: DB):
        self.disc=disc; self.live=live; self.clob=clob; self.db=db

    def _phase(self, hours_left: float) -> str:
        if hours_left > IN_GAME_HOURS_START: return "PRE_GAME"
        if hours_left > 0:                   return "IN_GAME"
        if hours_left > -3:                  return "CLOSING"
        return "EXPIRED"

    def run(self, sim: SimManager, live_mode=False):
        if not IN_GAME_MONITOR: return
        open_p = [p for p in sim.positions if not p.resolved]
        if not open_p: return

        cp(f"\n  {C.CYN}[MONITOR] Checking {len(open_p)} open position(s)...{C.R}")
        for pos in open_p:
            hl    = parse_hours(pos.game_time)
            phase = self._phase(hl)
            pos.phase = phase
            if phase == "PRE_GAME": continue
            if phase == "EXPIRED":  continue

            yes_px = self.disc.get_price(pos.yes_token)
            if yes_px is None: continue
            cur_px = yes_px if pos.direction=="YES" else round(1-yes_px,4)
            pos.last_price = cur_px
            drift = (cur_px-pos.entry_price)/pos.entry_price
            if pos.direction=="NO": drift = -drift

            if drift <= -(ADVERSE_MOVE_PCT*1.5):
                reason = f"Auto STOP-LOSS: drift={drift:.1%}"
                if live_mode and self.clob.client:
                    token = pos.yes_token if pos.direction=="YES" else pos.no_token
                    self.clob.sell(token, pos.shares)
                sim.early_exit(pos, yes_px, reason, self.db); continue

            ta, tb = extract_teams(pos.title)
            live_d = self.live.get(pos.sport, ta, tb)
            log.info("  [LIVE] %s  YES=%.4f drift=%+.1f%%  game=%s",
                     pos.title[:38], yes_px, drift*100, live_d.get("status","?"))

            if live_d.get("status") in ("SCHEDULED","UNKNOWN") and hl>0.3:
                self.db.update_monitor(pos.condition_id, phase, cur_px, "HOLD","Game not yet started")
                continue

            res = llm_ingame(pos, live_d, cur_px, hl)
            if not res: continue
            act = res["action"]; note = res["reasoning"]
            pos.last_action = act; pos.monitor_note = note
            self.db.update_monitor(pos.condition_id, phase, cur_px, act, note,
                                   exited=act in("EXIT_PROFIT","EXIT_PROTECT"))

            if act in ("EXIT_PROFIT","EXIT_PROTECT"):
                token = pos.yes_token if pos.direction=="YES" else pos.no_token
                if live_mode and self.clob.client:
                    self.clob.sell(token, pos.shares)
                sim.early_exit(pos, yes_px, f"{act}: {note}", self.db)
            elif act == "ADD_MORE" and res.get("conf",0) >= ADD_MORE_CONF_MIN:
                add = min(pos.bet_usd*0.25, MAX_BET_USD*0.25, sim.stats.balance*0.02)
                add = round(add,2)
                if add >= MIN_BET_USD:
                    token = pos.yes_token if pos.direction=="YES" else pos.no_token
                    if live_mode and self.clob.client:
                        self.clob.buy(token, add, pos.direction)
                    pos.bet_usd = round(pos.bet_usd+add,4)
                    pos.shares  = round(pos.shares+add/cur_px,4)
                    sim.stats.balance = round(sim.stats.balance-add,4)
                    self.db.log_balance(sim.stats.balance, "add_more")
                    print_live_dashboard(sim, "ADD_MORE")
            else:
                cp(f"  {C.DIM}[HOLD] {pos.title[:48]}  drift={drift:+.1%}{C.R}")
        sim.save()


# ═══════════════════════════════════════════════════════════════
# SIGNAL GENERATOR
# ═══════════════════════════════════════════════════════════════
class SignalGen:
    def __init__(self):
        self.disc  = MarketDiscovery()
        self.espn  = ESPNFetcher()
        self._std  : dict = {}

    def _standings(self, sport):
        if sport not in self._std:
            self._std[sport] = self.espn.standings(sport)
            log.info("  ESPN standings: %d teams [%s]", len(self._std[sport]), sport)
        return self._std[sport]

    def scan(self, balance: float, db: DB, stats: BotStats):
        signals = []; llm_calls = 0; llm_nobet = 0
        seen_sig = set()
        self._std = {}

        markets = self.disc.discover()
        log.info("[SCAN] %d sports markets (soonest-first, 24h window)...", len(markets))

        if not markets:
            cp(f"  {C.YLW}[SCAN] No valid sports markets in 24h window.{C.R}")
            return [], 0

        games = group_by_game(markets)
        log.info("[SCAN] Grouped into %d unique games", len(games))

        # Sort games by soonest-closing market
        ordered_games = sorted(
            games.items(),
            key=lambda kv: min(mi.hours_left for mi in kv[1])
        )

        for game_key, game_markets in ordered_games:
            filtered = [mi for mi in game_markets if not db.already_open(mi.condition_id)]
            if not filtered:
                log.info("  [SKIP-GAME] All markets already open: %s", game_key)
                continue
            filtered.sort(key=lambda mi: mi.hours_left if mi.hours_left != 999.0 else 9999.0)

            ta, tb = extract_teams(filtered[0].title)
            h_left = filtered[0].hours_left
            h_str  = f"{h_left:.1f}h" if h_left != 999.0 else "live"
            log.info("  [GAME] %s vs %s (%d markets | closes in %s)", ta, tb, len(filtered), h_str)

            # Preload standings
            sport = filtered[0].sport
            self._standings(sport)

            recommendations = llm_game_level(filtered, self.disc, db, self.espn, self._std)
            llm_calls += 1
            stats.llm_calls += 1

            if not recommendations:
                log.info("    [NO-RECS] 0 recommendations for %s vs %s", ta, tb)
                llm_nobet += 1; stats.llm_nobet += 1
                continue

            log.info("    [LLM] %d recommendations total", len(recommendations))

            for rec in recommendations:
                mi    = rec["market"]
                prob  = rec.get("yes_probability", 0.5)
                conf  = rec.get("confidence", 50)
                side  = rec.get("bet_side", "NO BET")
                factors   = rec.get("key_factors", [])
                reasoning = rec.get("reasoning", "")
                dq    = rec.get("data_quality", "MEDIUM")

                if side == "NO BET":
                    llm_nobet += 1; stats.llm_nobet += 1
                    log.info("    [VETO-NOBET] explicit NO BET for: %s", mi.title[:40])
                    continue

                if conf < LLM_CONF_MIN:
                    log.info("    [VETO-CONF] conf=%d < min=%d for: %s", conf, LLM_CONF_MIN, mi.title[:40])
                    llm_nobet += 1; stats.llm_nobet += 1
                    continue

                if not mi.yes_token:
                    log.info("    [VETO-TOKEN] No yes_token for: %s", mi.title[:40])
                    continue

                yes_px = self.disc.get_price(mi.yes_token)
                if yes_px is None:
                    log.info("    [VETO-PRICE] No price for: %s", mi.title[:40])
                    continue
                if yes_px < MIN_PRICE_THRESHOLD or yes_px > MAX_PRICE_THRESHOLD:
                    log.info("    [VETO-RESOLVED] price=%.4f outside [%.2f-%.2f]: %s",
                             yes_px, MIN_PRICE_THRESHOLD, MAX_PRICE_THRESHOLD, mi.title[:40])
                    continue

                no_px = round(1 - yes_px, 4)

                if side == "YES":
                    direction = "YES"; eprice = yes_px
                    dprob = prob; edge = prob - yes_px
                elif side == "NO":
                    direction = "NO"; eprice = no_px
                    dprob = 1 - prob; edge = dprob - no_px
                else:
                    continue

                if edge < MIN_EDGE:
                    log.info("    [VETO-EDGE] edge=%.2f%% < min=%.2f%% for: %s",
                             edge*100, MIN_EDGE*100, mi.title[:40])
                    continue

                kf  = kelly(dprob, eprice)
                bet = size_bet(kf, balance, edge, conf)
                if bet <= 0:
                    log.info("    [VETO-SIZE] bet=$%.2f too small for: %s", bet, mi.title[:40])
                    continue

                sig_key = (mi.market_id, direction)
                if sig_key in seen_sig:
                    log.info("    [VETO-DUPE] duplicate signal: %s", mi.title[:40])
                    continue
                seen_sig.add(sig_key)

                signals.append({
                    "mi": mi, "direction": direction, "price": eprice,
                    "model_prob": dprob, "edge": edge, "kelly": kf,
                    "bet": bet, "conf": conf, "factors": factors,
                    "reasoning": reasoning,
                })

                side_col = C.GRN if direction=="YES" else C.YLW
                ta2, tb2 = extract_teams(mi.title)
                team_pred = f"{ta2} WINS" if direction=="YES" else f"{tb2} WINS"

                cp(f"\n  {C.BGGRN}{C.WHT}{C.B} ✅ SIGNAL {C.R}  "
                   f"[{mi.sport}|{mi.league}] {side_col}{team_pred}{C.R} "
                   f"entry={eprice:.4f} edge={C.GRN}{edge:+.2%}{C.R} "
                   f"conf={C.GRN}{conf}%{C.R} bet=${bet:.0f} closes_in={h_str}"
                   f"\n   → {mi.title[:60]}")
                if reasoning:
                    cp(f"     💭 {C.DIM}{reasoning[:120]}{C.R}")

        signals.sort(key=lambda s: s["mi"].hours_left if s["mi"].hours_left != 999.0 else 9999.0)

        log.info("[SCAN] Done: %d signals | LLM=%d | NO_BET=%d", len(signals), llm_calls, llm_nobet)
        return signals, llm_calls


# ═══════════════════════════════════════════════════════════════
# MAIN SCAN
# ═══════════════════════════════════════════════════════════════
_last_balance_log: float = 0.0
_poller: Optional[LivePricePoller] = None


def run_scan(live: bool = False, poller: Optional[LivePricePoller] = None):
    global _last_balance_log
    sim  = SimManager()
    clob = CLOBExec()
    gen  = SignalGen()
    db   = DB()
    ls   = LiveScore()
    mon  = Monitor(gen.disc, ls, clob, db)

    if poller:
        poller.attach(sim)

    sim.reset_daily()
    if sim.limit_hit():
        cp(f"\n{C.BGRED}{C.B} ⛔ DAILY LOSS LIMIT ${DAILY_LOSS_LIMIT:.0f} HIT {C.R}")
        sim.settle_all(db); sim.print_summary(); return

    sim.settle_all(db)

    open_n = sum(1 for p in sim.positions if not p.resolved)
    if open_n > 0:
        mon.run(sim, live_mode=live)
    sim.save()

    now = time.time()
    if now - _last_balance_log >= BALANCE_UPDATE_INT:
        balance = clob.balance() if (live and clob.client) else sim.stats.balance
        log_balance_update(balance, "periodic")
        db.log_balance(balance, "periodic")
        _last_balance_log = now

    balance = clob.balance() if (live and clob.client) else sim.stats.balance
    mode_tag = "[LIVE]" if live else "[PAPER]"
    log.info("Balance=$%.2f %s | edge>=%.0f%% | conf>=%d%%",
             balance, mode_tag, MIN_EDGE*100, LLM_CONF_MIN)

    signals, llm_calls = gen.scan(balance, db, sim.stats)
    db.log_scan(0, len(signals), 0, llm_calls, balance)

    if not signals:
        cp(f"\n  {C.YLW}No qualifying signals this scan.{C.R}")
        sim.print_summary(); return

    # Print signal table
    bar = "═" * 70
    cp(f"\n{C.CYN}{bar}{C.R}")
    cp(f"  {C.B}{C.YLW} {len(signals)} SIGNAL(S) — soonest first {C.R}")
    cp(f"{C.CYN}{bar}{C.R}")
    sc_map = {"NBA":C.GRN,"MLB":C.RED,"NHL":C.CYN,"SOCCER":C.YLW,"CRICKET":C.MAG}
    for i,s in enumerate(signals,1):
        mi  = s["mi"]
        sc  = sc_map.get(mi.sport,C.WHT)
        ec  = C.GRN if s["edge"]>0.04 else C.YLW
        pay = round(1/s["price"],2) if s["price"]>0 else 0
        sd  = C.GRN if s["direction"]=="YES" else C.YLW
        h_s = f"{mi.hours_left:.1f}h" if mi.hours_left!=999.0 else "live"
        cp(f"\n  {i}. {sc}[{mi.sport}|{mi.league}]{C.R}  {C.B}{mi.title[:58]}{C.R}")
        cp(f"     {sd}{s['direction']}{C.R}  entry={s['price']:.4f}  "
           f"model={s['model_prob']:.4f}  edge={ec}{s['edge']:+.2%}{C.R}  "
           f"conf={C.GRN}{s['conf']}%{C.R}  payoff={pay:.1f}x  bet=${s['bet']:.0f}")
        cp(f"     closes_in={h_s}  at={mi.end_date[:16]}")
        cp(f"     📌 {C.DIM}{' | '.join(s['factors'][:3]) or 'n/a'}{C.R}")
        if s["reasoning"]:
            cp(f"     💬 {C.DIM}{s['reasoning'][:100]}{C.R}")
    cp(f"\n{C.CYN}{bar}{C.R}\n")

    # Execute trades
    open_conditions = {p.condition_id for p in sim.positions if not p.resolved}
    executed = 0
    for s in signals:
        if sim.limit_hit():
            log.warning("[EXECUTE] Daily loss limit — stopping")
            break
        mi = s["mi"]
        if db.already_open(mi.condition_id) or mi.condition_id in open_conditions:
            log.info("    [SKIP] Already open: %s", mi.title[:40])
            continue

        bal_before = sim.stats.balance
        direction  = s["direction"]
        bet_amt    = s["bet"]
        token = mi.yes_token if direction == "YES" else mi.no_token

        if live and clob.client:
            resp = clob.buy(token, bet_amt, direction)
            if resp and not resp.get("paper"):
                order_id = resp.get("orderID") or resp.get("id", "?")
                time.sleep(1.5)
                fill = clob.verify_fill(order_id)
                if fill.get("filled") or fill.get("status") in ("MATCHED","FILLED"):
                    pos = sim.open_pos(
                        mi, direction, s["price"], bet_amt,
                        s["model_prob"], s["edge"], s["conf"],
                        reasoning=s["reasoning"], factors=s["factors"], db=db)
                    db.open_trade(pos, s["reasoning"], " | ".join(s["factors"][:3]))
                    open_conditions.add(mi.condition_id)
                    executed += 1
                else:
                    cp(f"  {C.RED}[ORDER NOT FILLED]{C.R} id={order_id} | {mi.title[:40]}")
            else:
                cp(f"  {C.RED}[ORDER FAIL]{C.R} {mi.title[:40]}")
        else:
            cp(f"  {C.BGBLU}{C.WHT} PAPER {C.R}  "
               f"{direction} ${bet_amt:.0f} @ {s['price']:.4f}  {mi.title[:50]}")
            pos = sim.open_pos(
                mi, direction, s["price"], bet_amt,
                s["model_prob"], s["edge"], s["conf"],
                reasoning=s["reasoning"], factors=s["factors"], db=db)
            db.open_trade(pos, s["reasoning"], " | ".join(s["factors"][:3]))
            open_conditions.add(mi.condition_id)
            bal_after = sim.stats.balance
            log.info("    [PAPER] Bet=$%.2f | Bal: $%.2f→$%.2f",
                     bet_amt, bal_before, bal_after)
            executed += 1

    db.log_scan(0, len(signals), executed, llm_calls, sim.stats.balance)
    log.info("[SCAN] Executed %d/%d | Final Bal=$%.2f",
             executed, len(signals), sim.stats.balance)
    sim.print_summary()

    if poller:
        poller.attach(sim)


# ═══════════════════════════════════════════════════════════════
# ENTRYPOINT
# ═══════════════════════════════════════════════════════════════
def main():
    global BOT_START_TIME, _poller
    BOT_START_TIME = datetime.now(timezone.utc)

    p = argparse.ArgumentParser(description="CemeterysunReplicant v7.1")
    p.add_argument("--live",      action="store_true", help="Live trading")
    p.add_argument("--positions", action="store_true", help="Show portfolio")
    p.add_argument("--reset",     action="store_true", help="Reset balance")
    p.add_argument("--daemon",    action="store_true", help="Loop every 5 min")
    p.add_argument("--discover",  action="store_true", help="Show markets (no LLM)")
    args = p.parse_args()

    tlog(f"\n{'='*70}\nBOT v7.1 STARTED | mode={'LIVE' if args.live else 'PAPER'} | "
         f"conf>={LLM_CONF_MIN}% | edge>={MIN_EDGE:.0%} | "
         f"24h from {BOT_START_TIME.strftime('%Y-%m-%dT%H:%M:%SZ')}")

    cp(f"\n{C.BGBLU}{C.WHT}{C.B} CemeterysunReplicant v7.1 ── Polymarket Sports Bot {C.R}")
    cp(f"  Models : {PRIMARY_MODEL.split('/')[-1]} → {FALLBACK_MODEL.split('/')[-1]}")
    cp(f"  Sports : NBA | MLB | NHL | SOCCER | IPL/CRICKET")
    cp(f"  Window : {C.YLW}24h from bot start{C.R}")
    cp(f"  Price  : [{MIN_PRICE_THRESHOLD:.2f} - {MAX_PRICE_THRESHOLD:.2f}]")
    cp(f"  Edge   : {C.GRN}>= {MIN_EDGE:.0%}{C.R}  (lowered from 4% to make more bets)")
    cp(f"  Conf   : {C.GRN}>= {LLM_CONF_MIN}%{C.R}  (lowered from 60% to 55%)")
    cp(f"  Fallback: {C.GRN}Stat model bets if LLM gives 0 recommendations{C.R}")
    cp(f"  Mode   : {'🔴 [LIVE]' if args.live else '🔵 [PAPER]'}  max=${MAX_BET_USD:.0f}")

    if args.reset:
        SimManager().reset(); return

    if args.positions:
        sim = SimManager(); sim.settle_all(DB()); sim.print_summary(); return

    if args.discover:
        cp(f"\n{C.YLW}[DISCOVER MODE] Scanning sports markets...{C.R}")
        disc = MarketDiscovery()
        markets = disc.discover()
        cp(f"\n{C.CYN}Found {len(markets)} valid sports markets:{C.R}")
        sc_map = {"NBA":C.GRN,"MLB":C.RED,"NHL":C.CYN,"SOCCER":C.YLW,"CRICKET":C.MAG}
        for i,mi in enumerate(markets, 1):
            yes_px = disc.get_price(mi.yes_token)
            px_s   = f"YES={yes_px:.4f}" if yes_px else "price=N/A"
            h_s    = f"{mi.hours_left:.1f}h" if mi.hours_left!=999.0 else "live"
            sc     = sc_map.get(mi.sport, C.WHT)
            cp(f"  {i:3d}. {sc}[{mi.sport}|{mi.league}]{C.R}  {mi.title[:62]}"
               f"\n       vol=${mi.volume:.0f}  {px_s}  closes_in={h_s}")
        return

    _poller = LivePricePoller(MarketDiscovery())
    _poller.start()

    if args.daemon:
        log.info("Daemon mode: every %ds. Ctrl+C to stop.", SCAN_INTERVAL)
        scan_num = 0
        while True:
            scan_num += 1
            cp(f"\n{C.DIM}{'─'*50} Scan #{scan_num} "
               f"{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')} {'─'*10}{C.R}")
            try:
                run_scan(live=args.live, poller=_poller)
            except KeyboardInterrupt:
                cp(f"\n{C.RED}Stopped by user.{C.R}")
                _poller.stop(); break
            except Exception as e:
                log.exception("Scan #%d error: %s", scan_num, e)
            log.info("Next scan in %ds...", SCAN_INTERVAL)
            try:
                time.sleep(SCAN_INTERVAL)
            except KeyboardInterrupt:
                cp(f"\n{C.RED}Stopped.{C.R}"); _poller.stop(); break
    else:
        try:
            run_scan(live=args.live, poller=_poller)
        finally:
            _poller.stop()


if __name__ == "__main__":
    if not OPENROUTER_API_KEY:
        cp(f"{C.BGRED}{C.WHT} ERROR: OPENROUTER_API_KEY missing in .env {C.R}")
        sys.exit(1)
    main()
# Updated 2026-01-17: Refine documentation details
# Updated 2026-02-01: Update LLM validation note
# Updated 2026-02-05: Strengthen orderbook imbalance comment