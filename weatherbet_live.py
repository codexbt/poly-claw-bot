#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WeatherBet Live — Polymarket Weather Trading Bot
=================================================
One-file bot. Real CLOB order execution via py_clob_client.

Trading Logic  : WeatherBet v2 (ECMWF + HRRR + METAR + EV + Kelly)
Trade Execution: Real CLOB buy/sell orders (from Sniper Bot v4 pattern)

Usage:
    python weatherbet_live.py          # run full loop (scan every hour)
    python weatherbet_live.py status   # balance + open positions
    python weatherbet_live.py report   # full resolved market report

.env variables needed:
    PRIVATE_KEY or FUNDING_PRIVATE_KEY
    POLYMARKET_FUNDER_ADDRESS
    CHAIN_ID          (default 137)
    SIGNATURE_TYPE    (default 0)
    DRY_RUN           (true/false — default false)
    STARTING_BALANCE  (optional, e.g. 12 for dry-run start balance)
    RELAYER_URL       (optional)
    RELAYER_API_KEY   (optional)
    RELAYER_API_KEY_ADDRESS (optional)
    VC_KEY            (Visual Crossing API key — for resolution check)
"""

import re
import sys
import json
import math
import time
import os
import csv
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import requests

try:
    from dotenv import load_dotenv
    load_dotenv(override=True)
except Exception:
    pass

# ─────────────────────────────────────────────
# ANSI COLORS
# ─────────────────────────────────────────────
class C:
    RESET    = "\033[0m";  BOLD    = "\033[1m";  DIM    = "\033[2m"
    RED      = "\033[31m"; GREEN   = "\033[32m"; YELLOW = "\033[33m"
    BLUE     = "\033[34m"; MAGENTA = "\033[35m"; CYAN   = "\033[36m"
    BRED     = "\033[91m"; BGREEN  = "\033[92m"; BYELLOW= "\033[93m"
    BBLUE    = "\033[94m"; BMAGENTA= "\033[95m"; BCYAN  = "\033[96m"
    BWHITE   = "\033[97m"
    BG_GREEN = "\033[42m"; BG_RED  = "\033[41m"; BG_BLUE= "\033[44m"
    BG_YELLOW= "\033[43m"; BG_BLACK= "\033[40m"

def cprint(msg):   print(msg + C.RESET)
def ok(msg):       cprint(f"  {C.BGREEN}✅ {msg}")
def warn(msg):     cprint(f"  {C.BYELLOW}⚠️  {msg}")
def info(msg):     cprint(f"  {C.BCYAN}{msg}")
def skip(msg):     cprint(f"  {C.DIM}⏸️  {msg}")
def err(msg):      cprint(f"  {C.BRED}❌ {msg}")
def trade_ok(msg): cprint(f"\n{C.BG_GREEN}{C.BOLD}  🚀 {msg}  {C.RESET}")
def trade_win(msg):cprint(f"\n{C.BG_BLUE}{C.BWHITE}{C.BOLD}  ✅ WIN: {msg}  {C.RESET}")
def trade_loss(msg):cprint(f"\n{C.BG_RED}{C.BWHITE}{C.BOLD}  ❌ LOSS: {msg}  {C.RESET}")

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("weatherbet")

# ─────────────────────────────────────────────
# CONFIG — from env + config.json
# ─────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
DATA_DIR    = BASE_DIR / "data"

_cfg: dict = {}
if CONFIG_PATH.exists():
    with open(CONFIG_PATH, encoding="utf-8") as _f:
        _cfg = json.load(_f)

BALANCE         = float(os.getenv("STARTING_BALANCE", _cfg.get("balance", 100.0)))
MAX_BET         = float(_cfg.get("max_bet", 20.0))
MIN_STAKE       = float(_cfg.get("min_stake", 1.00))  # CLOB minimum order size
MIN_EV          = float(_cfg.get("min_ev", 0.10))
MAX_PRICE       = float(_cfg.get("max_price", 0.45))
MIN_VOLUME      = float(_cfg.get("min_volume", 500))
MIN_HOURS       = float(_cfg.get("min_hours", 2.0))
MAX_HOURS       = float(_cfg.get("max_hours", 72.0))
KELLY_FRACTION  = float(_cfg.get("kelly_fraction", 0.25))
MAX_SLIPPAGE    = float(_cfg.get("max_slippage", 0.03))
SCAN_INTERVAL   = int(_cfg.get("scan_interval", 3600))    # 1 hour
MONITOR_INTERVAL= 600                                       # 10 min
CALIBRATION_MIN = int(_cfg.get("calibration_min", 30))
VC_KEY          = _cfg.get("vc_key", os.getenv("VC_KEY", ""))

SIGMA_F = 2.0   # default forecast sigma for °F cities
SIGMA_C = 1.2   # default forecast sigma for °C cities

# CLOB / execution config
PRIVATE_KEY              = os.getenv("PRIVATE_KEY", "") or os.getenv("FUNDING_PRIVATE_KEY", "")
CHAIN_ID                 = int(os.getenv("CHAIN_ID", "137"))
SIGNATURE_TYPE           = int(os.getenv("SIGNATURE_TYPE", "0"))
POLYMARKET_FUNDER_ADDRESS= os.getenv("POLYMARKET_FUNDER_ADDRESS", "")
RELAYER_URL              = os.getenv("RELAYER_URL", "https://relayer.polymarket.com")
RELAYER_API_KEY          = os.getenv("RELAYER_API_KEY", "")
RELAYER_API_KEY_ADDRESS  = os.getenv("RELAYER_API_KEY_ADDRESS", "")
_dry_run_env             = os.getenv("DRY_RUN")
if _dry_run_env is None or _dry_run_env.strip() == "":
    DRY_RUN = True
else:
    DRY_RUN = _dry_run_env.strip().lower() in ("true", "1", "yes")

DATA_DIR.mkdir(exist_ok=True)
(DATA_DIR / "markets").mkdir(exist_ok=True)
STATE_FILE       = DATA_DIR / "state.json"
MARKETS_DIR      = DATA_DIR / "markets"
CALIBRATION_FILE = DATA_DIR / "calibration.json"
TRADES_CSV       = DATA_DIR / "trades.csv"

# ─────────────────────────────────────────────
# LOCATIONS — airport coordinates (exact Polymarket resolution stations)
# ─────────────────────────────────────────────
LOCATIONS = {
    "nyc":          {"lat": 40.7772,  "lon":  -73.8726, "name": "New York City", "station": "KLGA",  "unit": "F", "region": "us"},
    "chicago":      {"lat": 41.9742,  "lon":  -87.9073, "name": "Chicago",       "station": "KORD",  "unit": "F", "region": "us"},
    "miami":        {"lat": 25.7959,  "lon":  -80.2870, "name": "Miami",         "station": "KMIA",  "unit": "F", "region": "us"},
    "dallas":       {"lat": 32.8471,  "lon":  -96.8518, "name": "Dallas",        "station": "KDAL",  "unit": "F", "region": "us"},
    "seattle":      {"lat": 47.4502,  "lon": -122.3088, "name": "Seattle",       "station": "KSEA",  "unit": "F", "region": "us"},
    "los-angeles":  {"lat": 33.9416,  "lon": -118.4085, "name": "Los Angeles",   "station": "KLAX",  "unit": "F", "region": "us"},
    "atlanta":      {"lat": 33.6407,  "lon":  -84.4277, "name": "Atlanta",       "station": "KATL",  "unit": "F", "region": "us"},
    "london":       {"lat": 51.5048,  "lon":    0.0495, "name": "London",        "station": "EGLC",  "unit": "C", "region": "eu"},
    "paris":        {"lat": 48.9962,  "lon":    2.5979, "name": "Paris",         "station": "LFPG",  "unit": "C", "region": "eu"},
    "munich":       {"lat": 48.3537,  "lon":   11.7750, "name": "Munich",        "station": "EDDM",  "unit": "C", "region": "eu"},
    "ankara":       {"lat": 40.1281,  "lon":   32.9951, "name": "Ankara",        "station": "LTAC",  "unit": "C", "region": "eu"},
    "seoul":        {"lat": 37.4691,  "lon":  126.4505, "name": "Seoul",         "station": "RKSI",  "unit": "C", "region": "asia"},
    "tokyo":        {"lat": 35.7647,  "lon":  140.3864, "name": "Tokyo",         "station": "RJTT",  "unit": "C", "region": "asia"},
    "shanghai":     {"lat": 31.1443,  "lon":  121.8083, "name": "Shanghai",      "station": "ZSPD",  "unit": "C", "region": "asia"},
    "singapore":    {"lat":  1.3502,  "lon":  103.9940, "name": "Singapore",     "station": "WSSS",  "unit": "C", "region": "asia"},
    "lucknow":      {"lat": 26.7606,  "lon":   80.8893, "name": "Lucknow",       "station": "VILK",  "unit": "C", "region": "asia"},
    "tel-aviv":     {"lat": 32.0114,  "lon":   34.8867, "name": "Tel Aviv",      "station": "LLBG",  "unit": "C", "region": "asia"},
    "toronto":      {"lat": 43.6772,  "lon":  -79.6306, "name": "Toronto",       "station": "CYYZ",  "unit": "C", "region": "ca"},
    "sao-paulo":    {"lat": -23.4356, "lon":  -46.4731, "name": "Sao Paulo",     "station": "SBGR",  "unit": "C", "region": "sa"},
    "buenos-aires": {"lat": -34.8222, "lon":  -58.5358, "name": "Buenos Aires",  "station": "SAEZ",  "unit": "C", "region": "sa"},
    "wellington":   {"lat": -41.3272, "lon":  174.8052, "name": "Wellington",    "station": "NZWN",  "unit": "C", "region": "oc"},
}

TIMEZONES = {
    "nyc": "America/New_York", "chicago": "America/Chicago", "miami": "America/New_York",
    "dallas": "America/Chicago", "seattle": "America/Los_Angeles",
    "los-angeles": "America/Los_Angeles", "atlanta": "America/New_York",
    "london": "Europe/London", "paris": "Europe/Paris", "munich": "Europe/Berlin",
    "ankara": "Europe/Istanbul", "seoul": "Asia/Seoul", "tokyo": "Asia/Tokyo",
    "shanghai": "Asia/Shanghai", "singapore": "Asia/Singapore", "lucknow": "Asia/Kolkata",
    "tel-aviv": "Asia/Jerusalem", "toronto": "America/Toronto", "sao-paulo": "America/Sao_Paulo",
    "buenos-aires": "America/Argentina/Buenos_Aires", "wellington": "Pacific/Auckland",
}

MONTHS = ["january","february","march","april","may","june",
          "july","august","september","october","november","december"]

# ─────────────────────────────────────────────
# MATH
# ─────────────────────────────────────────────
def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

def bucket_prob(forecast: float, t_low: float, t_high: float, sigma: float = 2.0) -> float:
    """Probability that forecast falls in bucket, using normal distribution for edge buckets."""
    s = sigma or 2.0
    if t_low == -999:
        return norm_cdf((t_high - forecast) / s)
    if t_high == 999:
        return 1.0 - norm_cdf((t_low - forecast) / s)
    return 1.0 if in_bucket(forecast, t_low, t_high) else 0.0

def calc_ev(p: float, price: float) -> float:
    """Expected value of buying YES at `price` with win probability `p`."""
    if price <= 0 or price >= 1:
        return 0.0
    return round(p * (1.0 / price - 1.0) - (1.0 - p), 4)

def calc_kelly(p: float, price: float) -> float:
    """Fractional Kelly position size (as fraction of balance)."""
    if price <= 0 or price >= 1:
        return 0.0
    b = 1.0 / price - 1.0
    f = (p * b - (1.0 - p)) / b
    return round(min(max(0.0, f) * KELLY_FRACTION, 1.0), 4)

def bet_size(kelly: float, balance: float) -> float:
    """
    Fractional Kelly position size with hard minimum (CLOB requires $1 min).
    Returns 0 if balance is too low to meet minimum.
    """
    stake = min(kelly * balance, MAX_BET)
    if stake < MIN_STAKE:
        return 0.0
    return round(stake, 2)

def in_bucket(forecast: float, t_low: float, t_high: float) -> bool:
    if t_low == t_high:
        return round(float(forecast)) == round(t_low)
    return t_low <= float(forecast) <= t_high

# ─────────────────────────────────────────────
# CALIBRATION
# ─────────────────────────────────────────────
_cal: dict = {}

def load_cal() -> dict:
    if CALIBRATION_FILE.exists():
        return json.loads(CALIBRATION_FILE.read_text(encoding="utf-8"))
    return {}

def get_sigma(city_slug: str, source: str = "ecmwf") -> float:
    key = f"{city_slug}_{source}"
    if key in _cal:
        return _cal[key]["sigma"]
    return SIGMA_F if LOCATIONS[city_slug]["unit"] == "F" else SIGMA_C

def run_calibration(markets: list) -> dict:
    resolved = [m for m in markets if m.get("resolved") and m.get("actual_temp") is not None]
    cal = load_cal()
    updated = []
    for source in ["ecmwf", "hrrr", "metar"]:
        for city in set(m["city"] for m in resolved):
            group = [m for m in resolved if m["city"] == city]
            errors = []
            for m in group:
                snap = next((s for s in reversed(m.get("forecast_snapshots", []))
                             if s.get("source") == source), None)
                if snap and snap.get("temp") is not None:
                    errors.append(abs(snap["temp"] - m["actual_temp"]))
            if len(errors) < CALIBRATION_MIN:
                continue
            mae = sum(errors) / len(errors)
            key = f"{city}_{source}"
            old = cal.get(key, {}).get("sigma", SIGMA_F if LOCATIONS[city]["unit"] == "F" else SIGMA_C)
            cal[key] = {"sigma": round(mae, 3), "n": len(errors),
                        "updated_at": datetime.now(timezone.utc).isoformat()}
            if abs(mae - old) > 0.05:
                updated.append(f"{LOCATIONS[city]['name']} {source}: {old:.2f}->{mae:.2f}")
    CALIBRATION_FILE.write_text(json.dumps(cal, indent=2), encoding="utf-8")
    if updated:
        info(f"[CAL] {', '.join(updated)}")
    return cal

# ─────────────────────────────────────────────
# FORECAST SOURCES
# ─────────────────────────────────────────────
def get_ecmwf(city_slug: str, dates: list) -> dict:
    """ECMWF daily max temperature from Open-Meteo (bias corrected). All cities."""
    loc = LOCATIONS[city_slug]
    unit = loc["unit"]
    temp_unit = "fahrenheit" if unit == "F" else "celsius"
    tz = TIMEZONES.get(city_slug, "UTC")
    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={loc['lat']}&longitude={loc['lon']}"
        f"&daily=temperature_2m_max&temperature_unit={temp_unit}"
        f"&forecast_days=7&timezone={tz}"
        f"&models=ecmwf_ifs025&bias_correction=true"
    )
    result = {}
    for attempt in range(3):
        try:
            data = requests.get(url, timeout=(5, 10)).json()
            if "error" not in data:
                for date, temp in zip(data["daily"]["time"], data["daily"]["temperature_2m_max"]):
                    if date in dates and temp is not None:
                        result[date] = round(temp, 1) if unit == "C" else round(temp)
            break
        except Exception as e:
            if attempt < 2:
                time.sleep(3)
            else:
                warn(f"[ECMWF] {city_slug}: {e}")
    return result

def get_hrrr(city_slug: str, dates: list) -> dict:
    """HRRR/GFS seamless via Open-Meteo. US cities only, D+0 to D+2."""
    loc = LOCATIONS[city_slug]
    if loc["region"] != "us":
        return {}
    tz = TIMEZONES.get(city_slug, "UTC")
    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={loc['lat']}&longitude={loc['lon']}"
        f"&daily=temperature_2m_max&temperature_unit=fahrenheit"
        f"&forecast_days=3&timezone={tz}"
        f"&models=gfs_seamless"
    )
    result = {}
    for attempt in range(3):
        try:
            data = requests.get(url, timeout=(5, 10)).json()
            if "error" not in data:
                for date, temp in zip(data["daily"]["time"], data["daily"]["temperature_2m_max"]):
                    if date in dates and temp is not None:
                        result[date] = round(temp)
            break
        except Exception as e:
            if attempt < 2:
                time.sleep(3)
            else:
                warn(f"[HRRR] {city_slug}: {e}")
    return result

def get_metar(city_slug: str) -> Optional[float]:
    """Current real-time temperature from METAR airport station. D+0 only."""
    loc = LOCATIONS[city_slug]
    station = loc["station"]
    unit = loc["unit"]
    try:
        url = f"https://aviationweather.gov/api/data/metar?ids={station}&format=json"
        data = requests.get(url, timeout=(5, 8)).json()
        if data and isinstance(data, list):
            temp_c = data[0].get("temp")
            if temp_c is not None:
                if unit == "F":
                    return round(float(temp_c) * 9 / 5 + 32)
                return round(float(temp_c), 1)
    except Exception as e:
        warn(f"[METAR] {city_slug}: {e}")
    return None

def get_actual_temp(city_slug: str, date_str: str) -> Optional[float]:
    """Actual historical max temperature via Visual Crossing (for resolution check)."""
    if not VC_KEY:
        return None
    loc = LOCATIONS[city_slug]
    station = loc["station"]
    unit = loc["unit"]
    vc_unit = "us" if unit == "F" else "metric"
    url = (
        f"https://weather.visualcrossing.com/VisualCrossingWebServices/rest/services/timeline"
        f"/{station}/{date_str}/{date_str}"
        f"?unitGroup={vc_unit}&key={VC_KEY}&include=days&elements=tempmax"
    )
    try:
        data = requests.get(url, timeout=(5, 8)).json()
        days = data.get("days", [])
        if days and days[0].get("tempmax") is not None:
            return round(float(days[0]["tempmax"]), 1)
    except Exception as e:
        warn(f"[VC] {city_slug} {date_str}: {e}")
    return None

def take_forecast_snapshot(city_slug: str, dates: list) -> dict:
    """Fetch all sources; return best estimate per date."""
    now_str = datetime.now(timezone.utc).isoformat()
    ecmwf   = get_ecmwf(city_slug, dates)
    hrrr    = get_hrrr(city_slug, dates)
    today   = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    loc     = LOCATIONS[city_slug]

    snapshots = {}
    for date in dates:
        snap = {
            "ts":    now_str,
            "ecmwf": ecmwf.get(date),
            "hrrr":  hrrr.get(date) if date <= (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%d") else None,
            "metar": get_metar(city_slug) if date == today else None,
        }
        if loc["region"] == "us" and snap["hrrr"] is not None:
            snap["best"] = snap["hrrr"]
            snap["best_source"] = "hrrr"
        elif snap["ecmwf"] is not None:
            snap["best"] = snap["ecmwf"]
            snap["best_source"] = "ecmwf"
        else:
            snap["best"] = None
            snap["best_source"] = None
        snapshots[date] = snap
    return snapshots

# ─────────────────────────────────────────────
# POLYMARKET GAMMA API
# ─────────────────────────────────────────────
def get_polymarket_event(city_slug: str, month: str, day: int, year: int) -> Optional[dict]:
    slug = f"highest-temperature-in-{city_slug}-on-{month}-{day}-{year}"
    try:
        r = requests.get(f"https://clob.polymarket.com/events?slug={slug}", timeout=(5, 8))
        data = r.json()
        if data and isinstance(data, list) and len(data) > 0:
            return data[0]
    except Exception:
        pass
    return None

def get_market_bestask(market_id: str) -> Optional[float]:
    try:
        r = requests.get(f"https://clob.polymarket.com/markets/{market_id}", timeout=(3, 5))
        data = r.json()
        v = data.get("bestAsk")
        return float(v) if v is not None else None
    except Exception:
        return None

def get_market_bestbid(market_id: str) -> Optional[float]:
    try:
        r = requests.get(f"https://clob.polymarket.com/markets/{market_id}", timeout=(3, 5))
        data = r.json()
        v = data.get("bestBid")
        return float(v) if v is not None else None
    except Exception:
        return None

def check_market_resolved(market_id: str) -> Optional[bool]:
    """
    Returns True  = YES won (our bet was correct)
             False = NO won (we lost)
             None  = not yet resolved
    """
    try:
        r = requests.get(f"https://clob.polymarket.com/markets/{market_id}", timeout=10)
        data = r.json()
        if not data.get("resolved", False):
            return None
        payouts = data.get("resolved_payout", [])
        if isinstance(payouts, list) and len(payouts) >= 2:
            if payouts[0] != payouts[1]:
                return float(payouts[0]) > float(payouts[1])
        # Fallback to winner check
        yes_winner = no_winner = False
        for token in data.get("tokens", []):
            outcome = token.get("outcome", "").upper()
            if token.get("winner"):
                if outcome == "YES":
                    yes_winner = True
                elif outcome == "NO":
                    no_winner = True
        if yes_winner and not no_winner:
            return True
        if no_winner and not yes_winner:
            return False
        return None
    except Exception as e:
        warn(f"[RESOLVE] {market_id}: {e}")
    return None

def parse_temp_range(question: str) -> Optional[tuple]:
    """Extract (low, high) temperature range from a market question string."""
    if not question:
        return None
    num = r'(-?\d+(?:\.\d+)?)'
    if re.search(r'or below', question, re.IGNORECASE):
        m = re.search(num + r'[°]?[FC] or below', question, re.IGNORECASE)
        if m:
            return (-999.0, float(m.group(1)))
    if re.search(r'or higher', question, re.IGNORECASE):
        m = re.search(num + r'[°]?[FC] or higher', question, re.IGNORECASE)
        if m:
            return (float(m.group(1)), 999.0)
    m = re.search(r'between ' + num + r'-' + num + r'[°]?[FC]', question, re.IGNORECASE)
    if m:
        return (float(m.group(1)), float(m.group(2)))
    m = re.search(r'be ' + num + r'[°]?[FC] on', question, re.IGNORECASE)
    if m:
        v = float(m.group(1))
        return (v, v)
    return None

def hours_to_resolution(end_date_str: str) -> float:
    try:
        end = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
        return max(0.0, (end - datetime.now(timezone.utc)).total_seconds() / 3600)
    except Exception:
        return 999.0

# ─────────────────────────────────────────────
# MARKET DATA STORAGE
# ─────────────────────────────────────────────
def market_path(city_slug: str, date_str: str) -> Path:
    return MARKETS_DIR / f"{city_slug}_{date_str}.json"

def load_market(city_slug: str, date_str: str) -> Optional[dict]:
    p = market_path(city_slug, date_str)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return None

def save_market(market: dict):
    p = market_path(market["city"], market["date"])
    p.write_text(json.dumps(market, indent=2, ensure_ascii=False), encoding="utf-8")

def load_all_markets() -> list:
    markets = []
    for f in MARKETS_DIR.glob("*.json"):
        try:
            markets.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            pass
    return markets

def new_market(city_slug: str, date_str: str, event: dict, hours: float) -> dict:
    loc = LOCATIONS[city_slug]
    return {
        "city":               city_slug,
        "city_name":          loc["name"],
        "date":               date_str,
        "unit":               loc["unit"],
        "station":            loc["station"],
        "event_end_date":     event.get("endDate", ""),
        "hours_at_discovery": round(hours, 1),
        "status":             "open",
        "position":           None,
        "actual_temp":        None,
        "resolved_outcome":   None,
        "pnl":                None,
        "forecast_snapshots": [],
        "market_snapshots":   [],
        "all_outcomes":       [],
        "created_at":         datetime.now(timezone.utc).isoformat(),
    }

# ─────────────────────────────────────────────
# STATE
# ─────────────────────────────────────────────
def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {
        "balance":          BALANCE,
        "starting_balance": BALANCE,
        "total_trades":     0,
        "wins":             0,
        "losses":           0,
        "peak_balance":     BALANCE,
    }

def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")

# ─────────────────────────────────────────────
# TRADE CSV LOG
# ─────────────────────────────────────────────
_CSV_FIELDS = ["timestamp","city","date","direction","bucket","entry_price","shares",
               "cost","ev","kelly","forecast_temp","forecast_src","outcome","pnl","reason"]

def _ensure_csv():
    if not TRADES_CSV.exists():
        with open(TRADES_CSV, "w", newline="") as f:
            csv.DictWriter(f, fieldnames=_CSV_FIELDS).writeheader()

def log_trade_csv(row: dict):
    _ensure_csv()
    with open(TRADES_CSV, "a", newline="") as f:
        csv.DictWriter(f, fieldnames=_CSV_FIELDS, extrasaction="ignore").writerow(row)

# ─────────────────────────────────────────────
# CLOB EXECUTOR  (real on-chain orders)
# ─────────────────────────────────────────────
class ClobExecutor:
    """
    Wraps py_clob_client for real BUY and SELL market orders.
    Falls back to simulation if no private key or DRY_RUN=true.
    """

    def __init__(self):
        self.client          = None
        self.relayer_enabled = False
        self.live            = False

        if DRY_RUN:
            cprint(f"  {C.BBLUE}🔵 DRY_RUN mode — CLOB disabled (simulation only){C.RESET}")
            return
        if not PRIVATE_KEY:
            warn("No PRIVATE_KEY set — running in simulation mode")
            return

        try:
            from py_clob_client.client import ClobClient
            from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
            self._ClobClient = ClobClient
            self._BalanceAllowanceParams = BalanceAllowanceParams
            self._AssetType = AssetType

            self.client = ClobClient(
                host="https://clob.polymarket.com",
                key=PRIVATE_KEY,
                chain_id=CHAIN_ID,
                signature_type=SIGNATURE_TYPE,
                funder=POLYMARKET_FUNDER_ADDRESS,
            )
            creds = self.client.create_or_derive_api_creds()
            self.client.set_api_creds(creds)
            self.relayer_enabled = bool(RELAYER_API_KEY and RELAYER_API_KEY_ADDRESS)
            self.live = True
            ok(f"CLOB initialized | relayer={'yes' if self.relayer_enabled else 'no'} | dry_run=False")
        except ImportError:
            warn("py_clob_client not installed — pip install py-clob-client")
            self.client = None
        except Exception as e:
            warn(f"ClobClient init failed: {e} — simulation mode")
            self.client = None

    def get_balance(self) -> float:
        """Fetch real USDC balance from Polymarket CLOB."""
        if self.client is None:
            return BALANCE
        try:
            from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
            resp = self.client.get_balance_allowance(
                BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            )
            return round(int(resp.get("balance", 0)) / 1e6, 2)
        except Exception as e:
            warn(f"get_balance error: {e}")
            return BALANCE

    def _submit_relayer(self, signed_payload: dict) -> Optional[dict]:
        try:
            r = requests.post(
                f"{RELAYER_URL}/order",
                headers={
                    "Content-Type":           "application/json",
                    "RELAYER_API_KEY":         RELAYER_API_KEY,
                    "RELAYER_API_KEY_ADDRESS": RELAYER_API_KEY_ADDRESS,
                },
                json=signed_payload,
                timeout=15,
            )
            r.raise_for_status()
            return r.json()
        except Exception as e:
            warn(f"Relayer submit failed: {e}")
            return None

    def buy(self, token_id: str, amount_usd: float, label: str = "") -> Optional[dict]:
        """
        Place a market BUY order (FOK) for `amount_usd` USDC of `token_id`.
        Returns response dict or None on failure.
        """
        if self.client is None:
            info(f"[SIM-BUY] {label} ${amount_usd:.2f}")
            return {"sim": True, "amount": amount_usd}

        from py_clob_client.clob_types import MarketOrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY as BUY_SIDE

        for retry in range(3):
            try:
                log.info("  [CLOB-BUY%s] %s $%.2f token=%s",
                         f" R{retry}" if retry else "", label, amount_usd, token_id[:12])
                order  = MarketOrderArgs(token_id=token_id, amount=amount_usd,
                                         side=BUY_SIDE, order_type=OrderType.FOK)
                signed = self.client.create_market_order(order)
                resp   = None
                if self.relayer_enabled:
                    resp = self._submit_relayer(signed)
                if resp is None:
                    resp = self.client.post_order(signed, OrderType.FOK)
                if resp and (resp.get("orderID") or resp.get("id")):
                    ok(f"BUY order confirmed: {resp.get('orderID') or resp.get('id')}")
                    return resp
                warn(f"BUY order failed resp={resp}")
                if retry < 2:
                    time.sleep(1 + retry)
            except Exception as e:
                warn(f"BUY error (retry {retry}): {e}")
                if retry < 2:
                    time.sleep(1 + retry)
        return None

    def sell(self, token_id: str, shares: float, label: str = "") -> Optional[dict]:
        """
        Place a market SELL order (FOK) to exit `shares` of `token_id`.
        Returns response dict or None on failure.
        """
        if self.client is None:
            info(f"[SIM-SELL] {label} {shares:.4f} shares")
            return {"sim": True, "shares": shares}

        from py_clob_client.clob_types import MarketOrderArgs, OrderType
        from py_clob_client.order_builder.constants import SELL as SELL_SIDE

        for retry in range(3):
            try:
                log.info("  [CLOB-SELL%s] %s %.4f shares token=%s",
                         f" R{retry}" if retry else "", label, shares, token_id[:12])
                order  = MarketOrderArgs(token_id=token_id, amount=shares,
                                         side=SELL_SIDE, order_type=OrderType.FOK)
                signed = self.client.create_market_order(order)
                resp   = None
                if self.relayer_enabled:
                    resp = self._submit_relayer(signed)
                if resp is None:
                    resp = self.client.post_order(signed, OrderType.FOK)
                if resp and (resp.get("orderID") or resp.get("id")):
                    ok(f"SELL order confirmed: {resp.get('orderID') or resp.get('id')}")
                    return resp
                warn(f"SELL order failed resp={resp}")
                if retry < 2:
                    time.sleep(1 + retry)
            except Exception as e:
                warn(f"SELL error (retry {retry}): {e}")
                if retry < 2:
                    time.sleep(1 + retry)
        return None

    def get_yes_token(self, market_id: str) -> Optional[str]:
        """Fetch the YES token_id (CLOB token) for a given Polymarket market_id."""
        try:
            r = requests.get(
                f"https://clob.polymarket.com/markets/{market_id}", timeout=(5, 8)
            )
            data = r.json()
            clob_ids = data.get("clobTokenIds")
            if clob_ids:
                try:
                    token_ids = json.loads(clob_ids) if isinstance(clob_ids, str) else clob_ids
                    if token_ids:
                        return str(token_ids[0])
                except Exception:
                    pass
            tokens = data.get("tokens", [])
            for t in tokens:
                if t.get("outcome", "").upper() == "YES":
                    return t.get("token_id")
            if tokens:
                return tokens[0].get("token_id")
        except Exception as e:
            warn(f"get_yes_token {market_id}: {e}")
        return None

# ─────────────────────────────────────────────
# POSITION MONITORING (stop-loss + trailing)
# ─────────────────────────────────────────────
def monitor_positions(executor: ClobExecutor) -> int:
    markets  = load_all_markets()
    open_pos = [m for m in markets if m.get("position") and m["position"].get("status") == "open"]
    if not open_pos:
        return 0

    state   = load_state()
    balance = state["balance"]
    closed  = 0

    for mkt in open_pos:
        pos = mkt["position"]
        mid = pos["market_id"]
        current_price = None
        for o in mkt.get("all_outcomes", []):
            if o["market_id"] == pos["market_id"]:
                current_price = o.get("bid", o.get("price"))
                break

        if current_price is None:
            current_price = get_market_bestbid(mid)

        if current_price is None:
            continue

        entry = pos["entry_price"]
        stop  = pos.get("stop_price", round(entry * 0.80, 4))
        end_date   = mkt.get("event_end_date", "")
        hours_left = hours_to_resolution(end_date) if end_date else 999.0

        if current_price >= entry * 1.20 and stop < entry:
            pos["stop_price"] = entry
            pos["trailing_activated"] = True
            info(f"[TRAILING] {mkt['city_name']} {mkt['date']} — stop moved to breakeven ${entry:.3f}")

        if hours_left < 24:
            take_profit = None
        elif hours_left < 48:
            take_profit = 0.85
        else:
            take_profit = 0.75

        take_triggered = take_profit is not None and current_price >= take_profit
        stop_triggered = current_price <= stop

        if take_triggered or stop_triggered:
            token_id = pos.get("token_id")
            if token_id and not DRY_RUN:
                executor.sell(token_id, pos["shares"],
                              label=f"{mkt['city_name']} {mkt['date']}")
            pnl    = round((current_price - entry) * pos["shares"], 2)
            balance += pos["cost"] + pnl
            pos["exit_price"]   = current_price
            pos["pnl"]          = pnl
            pos["status"]       = "closed"
            pos["closed_at"]    = datetime.now(timezone.utc).isoformat()

            if take_triggered:
                reason = "TAKE-PROFIT"
                pos["close_reason"] = "take_profit"
            elif current_price < entry:
                reason = "STOP-LOSS"
                pos["close_reason"] = "stop_loss"
            else:
                reason = "TRAILING-BE"
                pos["close_reason"] = "trailing_stop"

            mkt["pnl"] = pnl
            closed += 1

            pnl_str = f"{'' if pnl>=0 else ''}{pnl:.2f}"
            if pnl >= 0:
                ok(f"[{reason}] {mkt['city_name']} {mkt['date']} | entry ${entry:.3f} exit ${current_price:.3f} | PnL: {pnl_str}")
            else:
                err(f"[{reason}] {mkt['city_name']} {mkt['date']} | entry ${entry:.3f} exit ${current_price:.3f} | PnL: {pnl_str}")

            log_trade_csv({
                "timestamp": datetime.now().isoformat(),
                "city": mkt["city"],
                "date": mkt["date"],
                "direction": pos.get("direction", "BUY_YES"),
                "bucket": f"{pos.get('bucket_low')}-{pos.get('bucket_high')}",
                "entry_price": entry,
                "shares": pos["shares"],
                "cost": pos["cost"],
                "ev": pos.get("ev", ""),
                "kelly": pos.get("kelly", ""),
                "forecast_temp": pos.get("forecast_temp", ""),
                "forecast_src": pos.get("forecast_src", ""),
                "outcome": "closed",
                "pnl": pnl,
                "reason": reason,
            })
            save_market(mkt)

    if closed:
        state["balance"] = round(balance, 2)
        save_state(state)
    return closed

# ─────────────────────────────────────────────
# MAIN SCAN + TRADE LOGIC
# ─────────────────────────────────────────────
def scan_and_update(executor: ClobExecutor) -> tuple:
    global _cal
    now      = datetime.now(timezone.utc)
    state    = load_state()
    balance  = state["balance"]
    new_pos  = 0
    closed   = 0
    resolved = 0

    cprint(f"\n{C.BG_BLUE}{C.BWHITE}{C.BOLD}  🌤  FULL SCAN — {now.strftime('%Y-%m-%d %H:%M UTC')}  {C.RESET}")
    mode_label = 'DRY-RUN' if not executor.live else 'LIVE'
    cprint(f"  {C.BYELLOW}Balance: ${balance:.2f} | Mode: {mode_label}  {C.RESET}\n")

    for city_slug, loc in LOCATIONS.items():
        unit     = loc["unit"]
        unit_sym = "F" if unit == "F" else "C"
        print(f"  {C.CYAN}→ {loc['name']}...{C.RESET}", end=" ", flush=True)

        try:
            dates     = [(now + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(4)]
            snapshots = take_forecast_snapshot(city_slug, dates)
            time.sleep(0.3)
        except Exception as e:
            print(f"{C.BRED}skipped ({e}){C.RESET}")
            continue

        for i, date in enumerate(dates):
            dt    = datetime.strptime(date, "%Y-%m-%d")
            event = get_polymarket_event(city_slug, MONTHS[dt.month - 1], dt.day, dt.year)
            if not event:
                continue

            end_date = event.get("endDate", "")
            hours    = hours_to_resolution(end_date) if end_date else 0
            horizon  = f"D+{i}"

            mkt = load_market(city_slug, date)
            if mkt is None:
                if hours < MIN_HOURS or hours > MAX_HOURS:
                    continue
                mkt = new_market(city_slug, date, event, hours)

            if mkt["status"] == "resolved":
                continue

            outcomes = []
            for market in event.get("markets", []):
                question = market.get("question", "")
                mid      = str(market.get("id", ""))
                volume   = float(market.get("volume", 0))
                rng      = parse_temp_range(question)
                if not rng:
                    continue
                try:
                    prices = json.loads(market.get("outcomePrices", "[0.5,0.5]"))
                    yes_price = float(prices[0])
                except Exception:
                    continue
                bid = yes_price
                ask = yes_price
                outcomes.append({
                    "question":  question,
                    "market_id": mid,
                    "range":     rng,
                    "bid":       round(bid, 4),
                    "ask":       round(ask, 4),
                    "price":     round(yes_price, 4),
                    "spread":    0.0,
                    "volume":    round(volume, 0),
                })
            outcomes.sort(key=lambda x: x["range"][0])
            mkt["all_outcomes"] = outcomes

            snap = snapshots.get(date, {})
            mkt["forecast_snapshots"].append({
                "ts":          snap.get("ts"),
                "horizon":     horizon,
                "hours_left":  round(hours, 1),
                "ecmwf":       snap.get("ecmwf"),
                "hrrr":        snap.get("hrrr"),
                "metar":       snap.get("metar"),
                "best":        snap.get("best"),
                "best_source": snap.get("best_source"),
            })

            forecast_temp = snap.get("best")
            best_source   = snap.get("best_source")

            if mkt.get("position") and mkt["position"].get("status") == "open":
                pos = mkt["position"]
                current_price = None
                for o in outcomes:
                    if o["market_id"] == pos["market_id"]:
                        current_price = o.get("bid", o.get("price"))
                        break
                if current_price is not None:
                    entry = pos["entry_price"]
                    stop  = pos.get("stop_price", round(entry * 0.80, 4))

                    if current_price >= entry * 1.20 and stop < entry:
                        pos["stop_price"] = entry
                        pos["trailing_activated"] = True

                    if current_price <= stop:
                        token_id = pos.get("token_id")
                        if token_id and not DRY_RUN:
                            executor.sell(token_id, pos["shares"],
                                          label=f"{loc['name']} {date}")
                        pnl = round((current_price - entry) * pos["shares"], 2)
                        balance += pos["cost"] + pnl
                        pos["closed_at"]    = snap.get("ts")
                        pos["close_reason"] = "stop_loss" if current_price < entry else "trailing_stop"
                        pos["exit_price"]   = current_price
                        pos["pnl"]          = pnl
                        pos["status"]       = "closed"
                        mkt["pnl"]          = pnl
                        closed += 1
                        reason  = "STOP" if current_price < entry else "TRAILING-BE"
                        pnl_str = f"{'+'if pnl>=0 else ''}{pnl:.2f}"
                        if pnl >= 0:
                            ok(f"[{reason}] {loc['name']} {date} exit ${current_price:.3f} | PnL {pnl_str}")
                        else:
                            err(f"[{reason}] {loc['name']} {date} exit ${current_price:.3f} | PnL {pnl_str}")

            if (mkt.get("position") and forecast_temp is not None
                    and mkt["position"].get("status") == "open"):
                pos = mkt["position"]
                bl, bh = pos.get("bucket_low", -999), pos.get("bucket_high", 999)
                buffer = 2.0 if unit == "F" else 1.0
                mid_b  = (bl + bh) / 2 if bl != -999 and bh != 999 else forecast_temp
                if not in_bucket(forecast_temp, bl, bh) and abs(forecast_temp - mid_b) > (abs(mid_b - bl) + buffer):
                    current_price = None
                    for o in outcomes:
                        if o["market_id"] == pos["market_id"]:
                            current_price = o.get("bid", o.get("price"))
                            break
                    if current_price is not None:
                        token_id = pos.get("token_id")
                        if token_id and not DRY_RUN:
                            executor.sell(token_id, pos["shares"],
                                          label=f"{loc['name']} {date} (forecast shifted)")
                        pnl = round((current_price - pos["entry_price"]) * pos["shares"], 2)
                        balance += pos["cost"] + pnl
                        pos["closed_at"]    = snap.get("ts")
                        pos["close_reason"] = "forecast_changed"
                        pos["exit_price"]   = current_price
                        pos["pnl"]          = pnl
                        pos["status"]       = "closed"
                        mkt["pnl"]          = pnl
                        closed += 1
                        warn(f"[CLOSE] {loc['name']} {date} — forecast shifted to {forecast_temp}{unit_sym} | PnL {'+'if pnl>=0 else ''}{pnl:.2f}")

            if (not mkt.get("position") and forecast_temp is not None
                    and hours >= MIN_HOURS):
                sigma = get_sigma(city_slug, best_source or "ecmwf")

                matched = None
                for o in outcomes:
                    t_low, t_high = o["range"]
                    if in_bucket(forecast_temp, t_low, t_high):
                        matched = o
                        break

                if matched:
                    t_low   = matched["range"][0]
                    t_high  = matched["range"][1]
                    volume  = matched["volume"]
                    bid     = matched.get("bid", matched["price"])
                    ask     = matched.get("ask", matched["price"])
                    real_ask = get_market_bestask(matched["market_id"]) or ask
                    real_bid = get_market_bestbid(matched["market_id"]) or bid
                    real_spread = round(real_ask - real_bid, 4) if real_ask is not None and real_bid is not None else None

                    if volume < MIN_VOLUME:
                        skip(f"{loc['name']} {date} — forecast {forecast_temp}{unit_sym} in {t_low}-{t_high}{unit_sym} — volume {volume:.0f} < {MIN_VOLUME}")
                    elif real_spread is not None and real_spread > MAX_SLIPPAGE:
                        skip(f"{loc['name']} {date} — forecast {forecast_temp}{unit_sym} in {t_low}-{t_high}{unit_sym} — spread ${real_spread:.3f} too wide")
                    else:
                        if real_ask is not None:
                            ask = real_ask
                        if real_bid is not None:
                            bid = real_bid
                        p  = bucket_prob(forecast_temp, t_low, t_high, sigma)
                        ev = calc_ev(p, ask)

                        if ev < MIN_EV:
                            skip(f"{loc['name']} {date} — forecast {forecast_temp}{unit_sym} in {t_low}-{t_high}{unit_sym} — EV {ev:+.2f} < {MIN_EV}")
                        elif ask >= MAX_PRICE:
                            skip(f"{loc['name']} {date} — forecast {forecast_temp}{unit_sym} in {t_low}-{t_high}{unit_sym} — ask ${ask:.3f} >= max ${MAX_PRICE}")
                        else:
                            kelly  = calc_kelly(p, ask)
                            size   = bet_size(kelly, balance)

                            if size < MIN_STAKE or size == 0.0:
                                skip(f"{loc['name']} {date} — forecast {forecast_temp}{unit_sym} in {t_low}-{t_high}{unit_sym} — size ${size:.2f} below minimum ${MIN_STAKE}")
                            else:
                                info(f"  [SIZE] {loc['name']} {date} {t_low}-{t_high}{unit_sym} | forecast {forecast_temp}{unit_sym} | p={p:.3f} | ask=${ask:.3f} | Kelly={kelly:.2%} | stake=${size:.2f}")
                                real_ask = get_market_bestask(matched["market_id"]) or ask
                                real_bid = get_market_bestbid(matched["market_id"]) or bid
                                real_spread = round(real_ask - real_bid, 4)

                                if real_spread > MAX_SLIPPAGE or real_ask >= MAX_PRICE:
                                    skip(f"{loc['name']} {date} — forecast {forecast_temp}{unit_sym} in {t_low}-{t_high}{unit_sym} — real ask ${real_ask:.3f} spread ${real_spread:.3f} rejected")
                                else:
                                    ask    = real_ask
                                    bid    = real_bid
                                    shares = round(size / ask, 2)
                                    ev     = round(calc_ev(p, ask), 4)

                                    token_id = executor.get_yes_token(matched["market_id"])
                                    if not token_id:
                                        err(f"Missing CLOB YES token for {loc['name']} {date} {t_low}-{t_high}{unit_sym}")
                                        buy_resp = None
                                    else:
                                        buy_resp = executor.buy(
                                            token_id,
                                            size,
                                            label=f"{loc['name']} {date} {t_low}-{t_high}{unit_sym}",
                                        )

                                    if buy_resp is not None:
                                        balance -= size
                                        bucket_label = f"{t_low}-{t_high}{unit_sym}"
                                        mkt["position"] = {
                                            "market_id":          matched["market_id"],
                                            "token_id":           token_id,
                                            "direction":          "BUY_YES",
                                            "question":           matched["question"],
                                            "bucket_low":         t_low,
                                            "bucket_high":        t_high,
                                            "entry_price":        ask,
                                            "bid_at_entry":       bid,
                                            "spread":             real_spread,
                                            "shares":             shares,
                                            "cost":               size,
                                            "stop_price":         round(ask * 0.80, 4),
                                            "trailing_activated": False,
                                            "p":                  round(p, 4),
                                            "ev":                 ev,
                                            "kelly":              round(kelly, 4),
                                            "forecast_temp":      forecast_temp,
                                            "forecast_src":       best_source,
                                            "sigma":              sigma,
                                            "opened_at":          snap.get("ts"),
                                            "status":             "open",
                                            "pnl":                None,
                                            "exit_price":         None,
                                            "close_reason":       None,
                                            "closed_at":          None,
                                        }
                                        state["total_trades"] += 1
                                        new_pos += 1

                                        trade_ok(
                                            f"BUY_YES  {loc['name']} {horizon} {date} | "
                                            f"{bucket_label} | ask ${ask:.3f} | "
                                            f"EV {ev:+.2f} | Kelly {kelly:.2%} | "
                                            f"${size:.2f} ({best_source.upper() if best_source else '?'})"
                                        )
                                        log_trade_csv({
                                            "timestamp":    datetime.now().isoformat(),
                                            "city":         city_slug,
                                            "date":         date,
                                            "direction":    "BUY_YES",
                                            "bucket":       bucket_label,
                                            "entry_price":  ask,
                                            "shares":       shares,
                                            "cost":         size,
                                            "ev":           ev,
                                            "kelly":        round(kelly, 4),
                                            "forecast_temp":forecast_temp,
                                            "forecast_src": best_source,
                                            "outcome":      "open",
                                            "pnl":          "",
                                            "reason":       "entry",
                                        })
                                    else:
                                        err(f"BUY order failed for {loc['name']} {date}")

            if hours < 0.5 and mkt["status"] == "open":
                mkt["status"] = "closed"

            save_market(mkt)
            time.sleep(0.1)

        print(f"{C.BGREEN}ok{C.RESET}")

    cprint(f"\n  {C.BYELLOW}🔍 Checking resolutions...{C.RESET}")
    for mkt in load_all_markets():
        if mkt["status"] == "resolved":
            continue
        pos = mkt.get("position")
        if not pos or pos.get("status") != "open":
            continue
        market_id = pos.get("market_id")
        if not market_id:
            continue

        won = check_market_resolved(market_id)
        if won is None:
            continue

        price  = pos["entry_price"]
        size   = pos["cost"]
        shares = pos["shares"]
        pnl    = round(shares * (1 - price), 2) if won else round(-size, 2)

        balance += size + pnl
        pos["exit_price"]   = 1.0 if won else 0.0
        pos["pnl"]          = pnl
        pos["close_reason"] = "resolved"
        pos["closed_at"]    = now.isoformat()
        pos["status"]       = "closed"
        mkt["pnl"]          = pnl
        mkt["status"]       = "resolved"
        mkt["resolved_outcome"] = "win" if won else "loss"

        if won:
            state["wins"] += 1
            trade_win(f"{mkt['city_name']} {mkt['date']} | PnL: +{pnl:.2f}")
        else:
            state["losses"] += 1
            trade_loss(f"{mkt['city_name']} {mkt['date']} | PnL: {pnl:.2f}")

        log_trade_csv({
            "timestamp":    datetime.now().isoformat(),
            "city":         mkt["city"],
            "date":         mkt["date"],
            "direction":    pos.get("direction", "BUY_YES"),
            "bucket":       f"{pos.get('bucket_low')}-{pos.get('bucket_high')}",
            "entry_price":  price,
            "shares":       shares,
            "cost":         size,
            "ev":           pos.get("ev", ""),
            "kelly":        pos.get("kelly", ""),
            "forecast_temp":pos.get("forecast_temp", ""),
            "forecast_src": pos.get("forecast_src", ""),
            "outcome":      "win" if won else "loss",
            "pnl":          pnl,
            "reason":       "resolved",
        })
        resolved += 1
        save_market(mkt)
        time.sleep(0.3)

    state["balance"]      = round(balance, 2)
    state["peak_balance"] = max(state.get("peak_balance", balance), balance)
    save_state(state)

    all_mkts = load_all_markets()
    resolved_count = len([m for m in all_mkts if m["status"] == "resolved"])
    if resolved_count >= CALIBRATION_MIN:
        _cal = run_calibration(all_mkts)

    _print_window_summary(state, new_pos, closed, resolved)
    return new_pos, closed, resolved

# ─────────────────────────────────────────────
# COLORFUL SUMMARIES
# ─────────────────────────────────────────────
def _print_window_summary(state: dict, new_pos: int, closed: int, resolved: int):
    bal   = state["balance"]
    start = state["starting_balance"]
    delta = bal - start
    w     = state["wins"]
    l     = state["losses"]
    total = w + l
    wr    = f"{w/total:.0%}" if total else "—"
    delta_color = C.BGREEN if delta >= 0 else C.BRED
    bar = "━" * 58

    cprint(f"\n{C.BCYAN}{bar}{C.RESET}")
    cprint(f"  {C.BG_BLUE}{C.BWHITE}{C.BOLD}  📊 SCAN COMPLETE — {datetime.now().strftime('%H:%M:%S')}  {C.RESET}")
    cprint(f"  {C.BYELLOW}Balance:{C.RESET}  {C.BWHITE}${bal:.2f}{C.RESET}  (start ${start:.2f}  Δ {delta_color}${delta:+.2f}{C.RESET})")
    cprint(f"  {C.BYELLOW}Trades:{C.RESET}   {C.BWHITE}{total}{C.RESET} resolved  {C.BGREEN}W:{w}{C.RESET}  {C.BRED}L:{l}{C.RESET}  WR:{C.BWHITE}{wr}{C.RESET}")
    cprint(f"  {C.BYELLOW}This scan:{C.RESET} {C.BGREEN}+{new_pos} opened{C.RESET}  {C.YELLOW}{closed} stopped{C.RESET}  {C.CYAN}{resolved} resolved{C.RESET}")
    cprint(f"{C.BCYAN}{bar}{C.RESET}\n")

def print_status():
    state    = load_state()
    markets  = load_all_markets()
    open_pos = [m for m in markets if m.get("position") and m["position"].get("status") == "open"]
    resolved = [m for m in markets if m["status"] == "resolved" and m.get("pnl") is not None]

    bal     = state["balance"]
    start   = state["starting_balance"]
    ret_pct = (bal - start) / start * 100
    w       = state["wins"]
    l       = state["losses"]
    total   = w + l
    bar     = "=" * 55
    delta_c = C.BGREEN if ret_pct >= 0 else C.BRED

    cprint(f"\n{C.BCYAN}{bar}{C.RESET}")
    cprint(f"  {C.BOLD}{C.BYELLOW}🌤  WEATHERBET — STATUS{C.RESET}")
    cprint(f"{C.BCYAN}{bar}{C.RESET}")
    cprint(f"  {C.BYELLOW}Balance:{C.RESET}  {C.BWHITE}${bal:.2f}{C.RESET}  (start ${start:.2f}  {delta_c}{'+'if ret_pct>=0 else ''}{ret_pct:.1f}%{C.RESET})")
    if total:
        cprint(f"  {C.BYELLOW}Trades:{C.RESET}   {C.BWHITE}{total}{C.RESET}  {C.BGREEN}W:{w}{C.RESET}  {C.BRED}L:{l}{C.RESET}  WR:{C.BWHITE}{w/total:.0%}{C.RESET}")
    else:
        cprint(f"  No trades yet.")
    cprint(f"  {C.BYELLOW}Open:{C.RESET}     {C.BWHITE}{len(open_pos)}{C.RESET}    {C.BYELLOW}Resolved:{C.RESET} {C.BWHITE}{len(resolved)}{C.RESET}")

    if open_pos:
        cprint(f"\n  {C.BMAGENTA}Open positions:{C.RESET}")
        for m in open_pos:
            pos      = m["position"]
            unit_sym = "F" if m["unit"] == "F" else "C"
            label    = f"{pos['bucket_low']}-{pos['bucket_high']}{unit_sym}"
            entry    = pos["entry_price"]
            cur      = get_market_bestbid(pos["market_id"]) or entry
            unreal   = round((cur - entry) * pos["shares"], 2)
            unr_c    = C.BGREEN if unreal >= 0 else C.BRED
            trail    = f" {C.BYELLOW}[TRAILING]{C.RESET}" if pos.get("trailing_activated") else ""
            cprint(
                f"    {C.BMAGENTA}▸{C.RESET} {C.BOLD}{m['city_name']:<16}{C.RESET} {m['date']}  "
                f"{C.BCYAN}{label:<14}{C.RESET}  entry ${entry:.3f} → ${cur:.3f}  "
                f"PnL: {unr_c}${unreal:+.2f}{C.RESET}{trail}"
            )
    cprint(f"{C.BCYAN}{bar}{C.RESET}\n")

def print_report():
    markets  = load_all_markets()
    resolved = [m for m in markets if m["status"] == "resolved" and m.get("pnl") is not None]
    bar = "=" * 55

    cprint(f"\n{C.BCYAN}{bar}{C.RESET}")
    cprint(f"  {C.BOLD}{C.BYELLOW}🌤  WEATHERBET — FULL REPORT{C.RESET}")
    cprint(f"{C.BCYAN}{bar}{C.RESET}")

    if not resolved:
        warn("No resolved markets yet.")
        return

    total_pnl = sum(m["pnl"] for m in resolved)
    wins      = [m for m in resolved if m["resolved_outcome"] == "win"]
    losses    = [m for m in resolved if m["resolved_outcome"] == "loss"]
    pnl_c     = C.BGREEN if total_pnl >= 0 else C.BRED

    cprint(f"\n  Resolved: {C.BWHITE}{len(resolved)}{C.RESET}  "
           f"{C.BGREEN}W:{len(wins)}{C.RESET}  {C.BRED}L:{len(losses)}{C.RESET}  "
           f"WR:{C.BWHITE}{len(wins)/len(resolved):.0%}{C.RESET}")
    cprint(f"  Total PnL: {pnl_c}${total_pnl:+.2f}{C.RESET}")

    cprint(f"\n  {C.BYELLOW}By city:{C.RESET}")
    for city in sorted(set(m["city"] for m in resolved)):
        group = [m for m in resolved if m["city"] == city]
        w     = len([m for m in group if m["resolved_outcome"] == "win"])
        pnl   = sum(m["pnl"] for m in group)
        name  = LOCATIONS[city]["name"]
        pc    = C.BGREEN if pnl >= 0 else C.BRED
        cprint(f"    {C.BOLD}{name:<16}{C.RESET} {w}/{len(group)} ({w/len(group):.0%})  "
               f"PnL: {pc}${pnl:+.2f}{C.RESET}")

    cprint(f"\n  {C.BYELLOW}Market details:{C.RESET}")
    for m in sorted(resolved, key=lambda x: x["date"]):
        pos     = m.get("position", {})
        unit_sym = "F" if m["unit"] == "F" else "C"
        label   = f"{pos.get('bucket_low')}-{pos.get('bucket_high')}{unit_sym}" if pos else "—"
        pnl_c   = C.BGREEN if m["pnl"] >= 0 else C.BRED
        pnl_str = f"{'+'if m['pnl']>=0 else ''}{m['pnl']:.2f}"
        result  = m["resolved_outcome"].upper()
        actual  = f"actual {m['actual_temp']}{unit_sym}" if m.get("actual_temp") else ""
        cprint(
            f"    {C.BOLD}{m['city_name']:<16}{C.RESET} {m['date']} | "
            f"{C.BCYAN}{label:<14}{C.RESET} | {result} {pnl_c}{pnl_str}{C.RESET} {C.DIM}{actual}{C.RESET}"
        )
    cprint(f"{C.BCYAN}{bar}{C.RESET}\n")

# ─────────────────────────────────────────────
# MAIN LOOP
# ─────────────────────────────────────────────
def run_loop():
    global _cal
    _cal = load_cal()
    executor = ClobExecutor()

    state = load_state()
    if DRY_RUN and state.get("starting_balance") != BALANCE:
        info(f"DRY_RUN starting balance set to ${BALANCE:.2f} (was ${state.get('balance', 0):.2f})")
        state["starting_balance"] = BALANCE
        state["balance"] = BALANCE
        state["peak_balance"] = max(state.get("peak_balance", BALANCE), BALANCE)
        save_state(state)

    if state["balance"] < 10:
        warn(f"Balance is low (${state['balance']:.2f}); Kelly sizing may produce tiny trade sizes and fewer entries.")
    elif state["balance"] < MAX_BET:
        warn(f"Balance ${state['balance']:.2f} is below MAX_BET ${MAX_BET:.2f}; stake will be capped by current balance.")

    if not DRY_RUN and executor.client is not None:
        real_bal = executor.get_balance()
        if real_bal != BALANCE:
            state["balance"] = real_bal
            if state["starting_balance"] == BALANCE:
                state["starting_balance"] = real_bal
            save_state(state)
            info(f"Real CLOB balance synced: ${real_bal:.2f}")

    cprint(f"\n{C.BG_BLACK}{C.BCYAN}{C.BOLD}")
    cprint("  ┌─────────────────────────────────────────────────────┐")
    cprint("  │  🌤  WEATHERBET LIVE  —  Polymarket Weather Bot     │")
    cprint("  │  Real CLOB execution  |  EV + Kelly + Stop-Loss     │")
    cprint(f"  │  Cities: {len(LOCATIONS):<3}  |  Max bet: ${MAX_BET:<6}  |  EV min: {MIN_EV}       │")
    cprint("  └─────────────────────────────────────────────────────┘")
    cprint(f"  Mode: {'DRY-RUN (simulation)' if not executor.live else C.BGREEN + 'LIVE — real orders' + C.RESET}")
    cprint(f"  Scan every {SCAN_INTERVAL//60}min  |  Monitor every {MONITOR_INTERVAL//60}min  |  Ctrl+C to stop\n")

    last_full_scan = 0

    # Before entering the loop, reconcile any existing open positions first.
    try:
        stopped = monitor_positions(executor)
        if stopped:
            state = load_state()
            cprint(f"  {C.BYELLOW}Recovered {stopped} open position(s) before starting scan — balance: ${state['balance']:.2f}{C.RESET}")
    except Exception as e:
        warn(f"Initial monitor error: {e}")

    while True:
        now_ts  = time.time()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if now_ts - last_full_scan >= SCAN_INTERVAL:
            cprint(f"\n{C.DIM}[{now_str}] running full scan...{C.RESET}")
            try:
                stopped = monitor_positions(executor)
                if stopped:
                    state = load_state()
                    cprint(f"  {C.BYELLOW}Recovered {stopped} open position(s) before scan — balance: ${state['balance']:.2f}{C.RESET}")
                new_pos, cl, res = scan_and_update(executor)
                state = load_state()
                cprint(
                    f"  {C.BCYAN}Scan done:{C.RESET} balance=${C.BWHITE}{state['balance']:.2f}{C.RESET}  "
                    f"new={C.BGREEN}{new_pos}{C.RESET}  closed={C.YELLOW}{cl}{C.RESET}  "
                    f"resolved={C.CYAN}{res}{C.RESET}"
                )
                last_full_scan = time.time()
            except KeyboardInterrupt:
                cprint(f"\n{C.BRED}Stopping — saving state...{C.RESET}")
                save_state(load_state())
                break
            except requests.exceptions.ConnectionError:
                warn("Connection lost — waiting 60s")
                time.sleep(60)
                continue
            except Exception as e:
                err(f"Scan error: {e} — waiting 60s")
                time.sleep(60)
                continue
        else:
            next_scan = int(SCAN_INTERVAL - (now_ts - last_full_scan))
            cprint(f"{C.DIM}[{now_str}] monitoring positions... (next full scan in {next_scan//60}m {next_scan%60}s){C.RESET}")
            try:
                stopped = monitor_positions(executor)
                if stopped:
                    state = load_state()
                    cprint(f"  {C.BYELLOW}Stopped {stopped} position(s) — balance: ${state['balance']:.2f}{C.RESET}")
            except Exception as e:
                warn(f"Monitor error: {e}")

        try:
            time.sleep(MONITOR_INTERVAL)
        except KeyboardInterrupt:
            cprint(f"\n{C.BRED}Stopping — saving state...{C.RESET}")
            save_state(load_state())
            break

# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────
if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        run_loop()
    elif cmd == "status":
        _cal = load_cal()
        print_status()
    elif cmd == "report":
        _cal = load_cal()
        print_report()
    else:
        print("Usage: python weatherbet_live.py [run|status|report]")
