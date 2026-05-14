#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════╗
║         WeatherBet — Polymarket Weather Bot          ║
║         Full CLOB Integration — Fixed & Final        ║
╚══════════════════════════════════════════════════════╝

Single-file bot. No SDK black box. Pure Python + py_clob_client.

FIXES vs previous version:
  ✅ Correct token_id (clobTokenIds) — not Gamma numeric market ID
  ✅ BUY/SELL use py_clob_client constants — not raw strings
  ✅ GTC + FOK fallback order strategy — works on low-volume markets
  ✅ Order success check uses orderID — not absence of "error" key
  ✅ DRY_RUN only from CLI flag — .env DRY_RUN ignored for safety
  ✅ CLOB client init validated at startup with clear error messages
  ✅ Correct position sizing via USDC amount (not share count)
  ✅ monitor_positions() passes dry_run flag through correctly
  ✅ Rate limiting respected throughout

Usage:
    python weatherbet_final.py              # live trading
    python weatherbet_final.py status       # balance + open positions
    python weatherbet_final.py report       # resolved market breakdown
    python weatherbet_final.py --dry-run    # paper scan, no orders placed
    python weatherbet_final.py --reset      # reset state.json
"""

import re
import sys
import json
import math
import time
import logging
import argparse
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path
import os

from dotenv import load_dotenv, find_dotenv

# =============================================================================
# ENV LOADING — must happen before anything else
# =============================================================================

BASE_DIR      = Path(__file__).resolve().parent
_env_path     = BASE_DIR / ".env"
if not _env_path.exists():
    _env_path = find_dotenv()
load_dotenv(_env_path)

# =============================================================================
# CLOB CLIENT IMPORTS
# =============================================================================

try:
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import (
        MarketOrderArgs,
        OrderArgs,
        OrderType,
    )
    from py_clob_client.order_builder.constants import BUY, SELL
    CLOB_AVAILABLE = True
except ImportError as _clob_import_err:
    CLOB_AVAILABLE = False
    BUY  = "BUY"
    SELL = "SELL"
    print(f"  [WARN] py_clob_client not installed: {_clob_import_err}")
    print(f"  [WARN] Run: pip install py-clob-client")
    print(f"  [WARN] Live trading disabled — dry-run only.\n")

# =============================================================================
# CONFIG
# =============================================================================

CONFIG_PATH = BASE_DIR / "config.json"
DATA_DIR    = BASE_DIR / "data"

_DEFAULTS = {
    "balance":         100.0,
    "max_bet":         10.0,
    "min_ev":          0.10,
    "max_price":       0.45,
    "min_volume":      500,
    "min_hours":       2.0,
    "max_hours":       72.0,
    "kelly_fraction":  0.25,
    "scan_interval":   3600,
    "calibration_min": 30,
    "vc_key":          "",
    "max_slippage":    0.03,
}

def _load_config() -> dict:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
        for k, v in _DEFAULTS.items():
            cfg.setdefault(k, v)
        return cfg
    print("  [WARN] config.json not found — using defaults.")
    return dict(_DEFAULTS)

_cfg = _load_config()

BALANCE         = float(_cfg["balance"])
MAX_BET         = float(_cfg["max_bet"])
MIN_EV          = float(_cfg["min_ev"])
MAX_PRICE       = float(_cfg["max_price"])
MIN_VOLUME      = int(_cfg["min_volume"])
MIN_HOURS       = float(_cfg["min_hours"])
MAX_HOURS       = float(_cfg["max_hours"])
KELLY_FRACTION  = float(_cfg["kelly_fraction"])
MAX_SLIPPAGE    = float(_cfg["max_slippage"])
SCAN_INTERVAL   = int(_cfg["scan_interval"])
CALIBRATION_MIN = int(_cfg["calibration_min"])
VC_KEY          = str(_cfg.get("vc_key", ""))

# Forecast uncertainty defaults
SIGMA_F = 2.0   # Fahrenheit
SIGMA_C = 1.2   # Celsius

DATA_DIR.mkdir(exist_ok=True)
STATE_FILE       = DATA_DIR / "state.json"
MARKETS_DIR      = DATA_DIR / "markets"
MARKETS_DIR.mkdir(exist_ok=True)
CALIBRATION_FILE = DATA_DIR / "calibration.json"

MONITOR_INTERVAL = 600  # 10 min between position checks
STATUS_INTERVAL  = 900  # 15 min periodic terminal status update

# =============================================================================
# CLOB / RELAYER CONFIG — from .env
# =============================================================================

CLOB_HOST     = os.getenv("POLYMARKET_HOST", "https://clob.polymarket.com")
CHAIN_ID      = int(os.getenv("CHAIN_ID", "137"))
SIGNATURE_TYPE = int(os.getenv("SIGNATURE_TYPE", "1"))

# Accept any of several common env var names for the private key
PRIVATE_KEY = (
    os.getenv("PRIVATE_KEY")
    or os.getenv("CLOB_PRIVATE_KEY")
    or os.getenv("FUNDING_PRIVATE_KEY")
    or os.getenv("POLYGON_PRIVATE_KEY")
    or os.getenv("POLY_PRIVATE_KEY")
)

FUNDER_ADDRESS = os.getenv("POLYMARKET_FUNDER_ADDRESS") or os.getenv("WALLET_ADDRESS") or ""

# Relayer (gasless) — optional
RELAYER_URL              = os.getenv("RELAYER_URL", "https://relayer.polymarket.com")
RELAYER_API_KEY          = os.getenv("RELAYER_API_KEY", "")
RELAYER_API_KEY_ADDRESS  = (
    os.getenv("RELAYER_API_KEY_ADDRESS")
    or os.getenv("RELAYER_ADDRESS")
    or os.getenv("RELAYER_KEY_ADDRESS")
    or ""
)
RELAYER_ENABLED = bool(RELAYER_API_KEY and RELAYER_API_KEY_ADDRESS)

# Derived API creds from .env (API_KEY / API_SECRET / API_PASSPHRASE)
CLOB_API_KEY        = os.getenv("API_KEY") or os.getenv("POLY_API_KEY") or ""
CLOB_API_SECRET     = os.getenv("API_SECRET") or os.getenv("POLY_API_SECRET") or ""
CLOB_API_PASSPHRASE = os.getenv("API_PASSPHRASE") or os.getenv("POLY_PASSPHRASE") or ""

# =============================================================================
# LOGGING
# =============================================================================

logging.basicConfig(
    filename="trades_log.txt",
    level=logging.INFO,
    format="%(asctime)s — %(message)s",
)

# =============================================================================
# TERMINAL COLOURS
# =============================================================================

class C:
    GREEN  = "\033[92m"
    YELLOW = "\033[93m"
    RED    = "\033[91m"
    CYAN   = "\033[96m"
    GRAY   = "\033[90m"
    BOLD   = "\033[1m"
    RESET  = "\033[0m"

def ok(msg):   print(f"{C.GREEN}  ✅ {msg}{C.RESET}")
def warn(msg): print(f"{C.YELLOW}  ⚠  {msg}{C.RESET}")
def info(msg): print(f"{C.CYAN}  {msg}{C.RESET}")
def skip(msg): print(f"{C.GRAY}  [SKIP] {msg}{C.RESET}")
def err(msg):  print(f"{C.RED}  ✗  {msg}{C.RESET}")

# =============================================================================
# CLOB CLIENT — singleton, lazy-initialised
# =============================================================================

_CLOB_CLIENT = None

def get_clob_client():
    """
    Returns an initialised ClobClient or None.
    Uses cached singleton after first successful init.
    """
    global _CLOB_CLIENT
    if _CLOB_CLIENT is not None:
        return _CLOB_CLIENT

    if not CLOB_AVAILABLE:
        return None
    if not PRIVATE_KEY:
        warn("CLOB: PRIVATE_KEY missing in .env — live trading disabled.")
        return None

    try:
        client = ClobClient(
            host=CLOB_HOST,
            key=PRIVATE_KEY,
            chain_id=CHAIN_ID,
            signature_type=SIGNATURE_TYPE,
            funder=FUNDER_ADDRESS or None,
        )

        # Use derived API creds from .env if available (avoids on-chain derivation every run)
        if CLOB_API_KEY and CLOB_API_SECRET and CLOB_API_PASSPHRASE:
            try:
                from py_clob_client.clob_types import ApiCreds
                client.set_api_creds(ApiCreds(
                    api_key=CLOB_API_KEY,
                    api_secret=CLOB_API_SECRET,
                    api_passphrase=CLOB_API_PASSPHRASE,
                ))
                ok("CLOB: API creds loaded from .env")
            except Exception as e:
                warn(f"CLOB: Could not load API creds from .env: {e}")
                creds = client.create_or_derive_api_creds()
                client.set_api_creds(creds)
        else:
            creds = client.create_or_derive_api_creds()
            client.set_api_creds(creds)

        _CLOB_CLIENT = client
        ok(f"CLOB: client ready | host={CLOB_HOST} | chain={CHAIN_ID} | relayer={'yes' if RELAYER_ENABLED else 'no (post_order fallback)'}")
        return client

    except Exception as e:
        err(f"CLOB: client init failed: {e}")
        logging.exception("CLOB client init failed: %s", e)
        return None


def _serialize_order(signed_order) -> dict:
    """Safely convert a signed order object to a plain dict."""
    if signed_order is None:
        return {}
    if isinstance(signed_order, dict):
        return signed_order
    if hasattr(signed_order, "dict"):
        return signed_order.dict()
    try:
        return json.loads(json.dumps(signed_order, default=str))
    except Exception:
        return {}


def place_order(token_id: str, side: str, usdc_amount: float, limit_price: float,
                dry_run: bool = False) -> dict:
    """
    Place a BUY or SELL order on Polymarket CLOB.

    Args:
        token_id:    Outcome token ID (clobTokenIds[0] for YES).
                     This is a long hex string — NOT the numeric Gamma market ID.
        side:        BUY or SELL constant from py_clob_client
        usdc_amount: Dollar amount to spend (BUY) or proceeds to receive (SELL)
        limit_price: Price per share (0.0–1.0). Used as the limit for GTC orders.
        dry_run:     If True, just log and return simulated success.

    Returns:
        dict with 'orderID' on success, 'error' key on failure.
    """
    if dry_run:
        logging.info("SIM %s token=%s amount=$%.2f price=%.3f", side, token_id[:12], usdc_amount, limit_price)
        return {"orderID": "SIM", "simulated": True}

    client = get_clob_client()
    if client is None:
        return {"error": "CLOB client unavailable"}

    # Validate token_id — must be a hex string, not a short numeric ID
    if not token_id or len(str(token_id)) < 20:
        return {"error": f"Invalid token_id '{token_id}' — must be clobTokenIds hex, not Gamma numeric ID"}

    # ── Strategy: try GTC limit order first (fills immediately if price is right),
    #    fall back to FOK market order if GTC times out or fails.
    resp = None

    # 1. Try GTC limit order (preferred — better fill rate on low-volume markets)
    try:
        order_args = OrderArgs(
            token_id=str(token_id),
            price=round(limit_price, 4),
            size=round(usdc_amount / limit_price, 2),  # shares = USDC / price
            side=side,
        )
        signed = client.create_order(order_args)

        if RELAYER_ENABLED:
            try:
                payload  = _serialize_order(signed)
                response = requests.post(
                    f"{RELAYER_URL}/order",
                    headers={
                        "Content-Type": "application/json",
                        "RELAYER_API_KEY": RELAYER_API_KEY,
                        "RELAYER_API_KEY_ADDRESS": RELAYER_API_KEY_ADDRESS,
                    },
                    json=payload,
                    timeout=(5, 15),
                )
                response.raise_for_status()
                resp = response.json()
                if resp and resp.get("orderID"):
                    logging.info("GTC via relayer: orderID=%s side=%s token=%s price=%.3f amount=%.2f",
                                 resp["orderID"], side, token_id[:16], limit_price, usdc_amount)
                    return resp
            except Exception as relayer_err:
                warn(f"CLOB: relayer failed ({relayer_err}), trying post_order...")

        # Fallback: post_order directly
        if resp is None:
            resp = client.post_order(signed, OrderType.GTC)
            if resp and resp.get("orderID"):
                logging.info("GTC via post_order: orderID=%s side=%s token=%s price=%.3f amount=%.2f",
                             resp["orderID"], side, token_id[:16], limit_price, usdc_amount)
                return resp

    except Exception as gtc_err:
        warn(f"CLOB: GTC order failed ({gtc_err}), trying FOK fallback...")

    # 2. FOK fallback — market order, fill-or-kill
    try:
        mkt_args = MarketOrderArgs(
            token_id=str(token_id),
            amount=round(usdc_amount, 2),
            side=side,
        )
        signed_fok = client.create_market_order(mkt_args)

        if RELAYER_ENABLED:
            try:
                payload  = _serialize_order(signed_fok)
                response = requests.post(
                    f"{RELAYER_URL}/order",
                    headers={
                        "Content-Type": "application/json",
                        "RELAYER_API_KEY": RELAYER_API_KEY,
                        "RELAYER_API_KEY_ADDRESS": RELAYER_API_KEY_ADDRESS,
                    },
                    json=payload,
                    timeout=(5, 15),
                )
                response.raise_for_status()
                resp = response.json()
                if resp and resp.get("orderID"):
                    logging.info("FOK via relayer: orderID=%s side=%s token=%s price=%.3f amount=%.2f",
                                 resp["orderID"], side, token_id[:16], limit_price, usdc_amount)
                    return resp
            except Exception as fok_relayer_err:
                warn(f"CLOB: FOK relayer failed ({fok_relayer_err}), trying post_order...")

        if resp is None:
            resp = client.post_order(signed_fok, OrderType.FOK)
            if resp and resp.get("orderID"):
                logging.info("FOK via post_order: orderID=%s side=%s token=%s price=%.3f amount=%.2f",
                             resp["orderID"], side, token_id[:16], limit_price, usdc_amount)
                return resp

    except Exception as fok_err:
        err(f"CLOB: FOK also failed: {fok_err}")
        logging.exception("FOK order error: %s", fok_err)
        return {"error": str(fok_err)}

    # If we got a response but no orderID
    if resp:
        logging.warning("Order no orderID: %s", resp)
        return resp
    return {"error": "Order submission failed — no response"}


def order_ok(resp: dict) -> bool:
    """True if an order response indicates success."""
    return bool(resp and resp.get("orderID") and "error" not in resp)


# =============================================================================
# CITIES — airport-exact coordinates
# =============================================================================

LOCATIONS = {
    # USA
    "nyc":          {"lat":  40.7772,  "lon":  -73.8726, "name": "New York City",  "station": "KLGA",  "unit": "F", "region": "us"},
    "chicago":      {"lat":  41.9742,  "lon":  -87.9073, "name": "Chicago",        "station": "KORD",  "unit": "F", "region": "us"},
    "miami":        {"lat":  25.7959,  "lon":  -80.2870, "name": "Miami",          "station": "KMIA",  "unit": "F", "region": "us"},
    "dallas":       {"lat":  32.8471,  "lon":  -96.8518, "name": "Dallas",         "station": "KDAL",  "unit": "F", "region": "us"},
    "seattle":      {"lat":  47.4502,  "lon": -122.3088, "name": "Seattle",        "station": "KSEA",  "unit": "F", "region": "us"},
    "los-angeles":  {"lat":  33.9416,  "lon": -118.4085, "name": "Los Angeles",    "station": "KLAX",  "unit": "F", "region": "us"},
    "atlanta":      {"lat":  33.6407,  "lon":  -84.4277, "name": "Atlanta",        "station": "KATL",  "unit": "F", "region": "us"},
    # Europe
    "london":       {"lat":  51.5048,  "lon":    0.0495, "name": "London",         "station": "EGLC",  "unit": "C", "region": "eu"},
    "paris":        {"lat":  48.9962,  "lon":    2.5979, "name": "Paris",          "station": "LFPG",  "unit": "C", "region": "eu"},
    "munich":       {"lat":  48.3537,  "lon":   11.7750, "name": "Munich",         "station": "EDDM",  "unit": "C", "region": "eu"},
    "ankara":       {"lat":  40.1281,  "lon":   32.9951, "name": "Ankara",         "station": "LTAC",  "unit": "C", "region": "eu"},
    # Asia
    "seoul":        {"lat":  37.4691,  "lon":  126.4505, "name": "Seoul",          "station": "RKSI",  "unit": "C", "region": "asia"},
    "tokyo":        {"lat":  35.7647,  "lon":  140.3864, "name": "Tokyo",          "station": "RJTT",  "unit": "C", "region": "asia"},
    "shanghai":     {"lat":  31.1443,  "lon":  121.8083, "name": "Shanghai",       "station": "ZSPD",  "unit": "C", "region": "asia"},
    "singapore":    {"lat":   1.3502,  "lon":  103.9940, "name": "Singapore",      "station": "WSSS",  "unit": "C", "region": "asia"},
    "lucknow":      {"lat":  26.7606,  "lon":   80.8893, "name": "Lucknow",        "station": "VILK",  "unit": "C", "region": "asia"},
    "tel-aviv":     {"lat":  32.0114,  "lon":   34.8867, "name": "Tel Aviv",       "station": "LLBG",  "unit": "C", "region": "asia"},
    # Canada / South America / Oceania
    "toronto":      {"lat":  43.6772,  "lon":  -79.6306, "name": "Toronto",        "station": "CYYZ",  "unit": "C", "region": "ca"},
    "sao-paulo":    {"lat": -23.4356,  "lon":  -46.4731, "name": "Sao Paulo",      "station": "SBGR",  "unit": "C", "region": "sa"},
    "buenos-aires": {"lat": -34.8222,  "lon":  -58.5358, "name": "Buenos Aires",   "station": "SAEZ",  "unit": "C", "region": "sa"},
    "wellington":   {"lat": -41.3272,  "lon":  174.8052, "name": "Wellington",     "station": "NZWN",  "unit": "C", "region": "oc"},
}

TIMEZONES = {
    "nyc":          "America/New_York",
    "chicago":      "America/Chicago",
    "miami":        "America/New_York",
    "dallas":       "America/Chicago",
    "seattle":      "America/Los_Angeles",
    "los-angeles":  "America/Los_Angeles",
    "atlanta":      "America/New_York",
    "london":       "Europe/London",
    "paris":        "Europe/Paris",
    "munich":       "Europe/Berlin",
    "ankara":       "Europe/Istanbul",
    "seoul":        "Asia/Seoul",
    "tokyo":        "Asia/Tokyo",
    "shanghai":     "Asia/Shanghai",
    "singapore":    "Asia/Singapore",
    "lucknow":      "Asia/Kolkata",
    "tel-aviv":     "Asia/Jerusalem",
    "toronto":      "America/Toronto",
    "sao-paulo":    "America/Sao_Paulo",
    "buenos-aires": "America/Argentina/Buenos_Aires",
    "wellington":   "Pacific/Auckland",
}

MONTHS = [
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december"
]

# =============================================================================
# MATH
# =============================================================================

def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

def bucket_prob(forecast, t_low, t_high, sigma=None) -> float:
    s = sigma or SIGMA_F
    if t_low == -999:
        return norm_cdf((t_high - float(forecast)) / s)
    if t_high == 999:
        return 1.0 - norm_cdf((t_low - float(forecast)) / s)
    return 1.0 if in_bucket(forecast, t_low, t_high) else 0.0

def calc_ev(p: float, price: float) -> float:
    if price <= 0 or price >= 1:
        return 0.0
    return round(p * (1.0 / price - 1.0) - (1.0 - p), 4)

def calc_kelly(p: float, price: float) -> float:
    if price <= 0 or price >= 1:
        return 0.0
    b = 1.0 / price - 1.0
    f = (p * b - (1.0 - p)) / b
    return round(min(max(0.0, f) * KELLY_FRACTION, 1.0), 4)

def bet_size(kelly: float, balance: float) -> float:
    return round(min(kelly * balance, MAX_BET), 2)

# =============================================================================
# CALIBRATION
# =============================================================================

_cal: dict = {}

def load_cal() -> dict:
    if CALIBRATION_FILE.exists():
        try:
            return json.loads(CALIBRATION_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
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
            group  = [m for m in resolved if m["city"] == city]
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
            old = cal.get(key, {}).get("sigma", get_sigma(city, source))
            new = round(mae, 3)
            cal[key] = {"sigma": new, "n": len(errors),
                        "updated_at": datetime.now(timezone.utc).isoformat()}
            if abs(new - old) > 0.05:
                updated.append(f"{LOCATIONS[city]['name']} {source}: {old:.2f}→{new:.2f}")
    CALIBRATION_FILE.write_text(json.dumps(cal, indent=2), encoding="utf-8")
    if updated:
        print(f"  [CAL] Updated: {', '.join(updated)}")
    return cal

# =============================================================================
# FORECAST SOURCES
# =============================================================================

def get_ecmwf(city_slug: str, dates: list) -> dict:
    loc  = LOCATIONS[city_slug]
    unit = loc["unit"]
    url  = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={loc['lat']}&longitude={loc['lon']}"
        f"&daily=temperature_2m_max&temperature_unit={'fahrenheit' if unit=='F' else 'celsius'}"
        f"&forecast_days=7&timezone={TIMEZONES.get(city_slug, 'UTC')}"
        f"&models=ecmwf_ifs025&bias_correction=true"
    )
    for attempt in range(3):
        try:
            data = requests.get(url, timeout=(5, 10)).json()
            if "error" not in data:
                result = {}
                for date, temp in zip(data["daily"]["time"], data["daily"]["temperature_2m_max"]):
                    if date in dates and temp is not None:
                        result[date] = round(temp, 1) if unit == "C" else round(temp)
                return result
        except Exception as e:
            if attempt == 2:
                warn(f"ECMWF {city_slug}: {e}")
            else:
                time.sleep(3)
    return {}

def get_hrrr(city_slug: str, dates: list) -> dict:
    if LOCATIONS[city_slug]["region"] != "us":
        return {}
    loc = LOCATIONS[city_slug]
    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={loc['lat']}&longitude={loc['lon']}"
        f"&daily=temperature_2m_max&temperature_unit=fahrenheit"
        f"&forecast_days=3&timezone={TIMEZONES.get(city_slug, 'UTC')}"
        f"&models=gfs_seamless"
    )
    for attempt in range(3):
        try:
            data = requests.get(url, timeout=(5, 10)).json()
            if "error" not in data:
                result = {}
                for date, temp in zip(data["daily"]["time"], data["daily"]["temperature_2m_max"]):
                    if date in dates and temp is not None:
                        result[date] = round(temp)
                return result
        except Exception as e:
            if attempt == 2:
                warn(f"HRRR {city_slug}: {e}")
            else:
                time.sleep(3)
    return {}

def get_metar(city_slug: str):
    loc = LOCATIONS[city_slug]
    try:
        data = requests.get(
            f"https://aviationweather.gov/api/data/metar?ids={loc['station']}&format=json",
            timeout=(5, 8)
        ).json()
        if data and isinstance(data, list):
            temp_c = data[0].get("temp")
            if temp_c is not None:
                return round(float(temp_c) * 9/5 + 32) if loc["unit"] == "F" else round(float(temp_c), 1)
    except Exception as e:
        warn(f"METAR {city_slug}: {e}")
    return None

def get_actual_temp(city_slug: str, date_str: str):
    if not VC_KEY:
        return None
    loc     = LOCATIONS[city_slug]
    vc_unit = "us" if loc["unit"] == "F" else "metric"
    url = (
        f"https://weather.visualcrossing.com/VisualCrossingWebServices/rest/services/timeline"
        f"/{loc['station']}/{date_str}/{date_str}"
        f"?unitGroup={vc_unit}&key={VC_KEY}&include=days&elements=tempmax"
    )
    try:
        data = requests.get(url, timeout=(5, 8)).json()
        days = data.get("days", [])
        if days and days[0].get("tempmax") is not None:
            return round(float(days[0]["tempmax"]), 1)
    except Exception as e:
        warn(f"VisualCrossing {city_slug} {date_str}: {e}")
    return None

def take_forecast_snapshot(city_slug: str, dates: list) -> dict:
    now_str   = datetime.now(timezone.utc).isoformat()
    ecmwf     = get_ecmwf(city_slug, dates)
    hrrr      = get_hrrr(city_slug, dates)
    today     = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    d2_cutoff = (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%d")
    loc       = LOCATIONS[city_slug]
    snapshots = {}
    for date in dates:
        snap = {
            "ts":    now_str,
            "ecmwf": ecmwf.get(date),
            "hrrr":  hrrr.get(date) if date <= d2_cutoff else None,
            "metar": get_metar(city_slug) if date == today else None,
        }
        if loc["region"] == "us" and snap["hrrr"] is not None:
            snap["best"], snap["best_source"] = snap["hrrr"], "hrrr"
        elif snap["ecmwf"] is not None:
            snap["best"], snap["best_source"] = snap["ecmwf"], "ecmwf"
        else:
            snap["best"], snap["best_source"] = None, None
        snapshots[date] = snap
    return snapshots

# =============================================================================
# POLYMARKET GAMMA API  — market discovery + price fetching
# =============================================================================

def get_polymarket_event(city_slug: str, month: str, day: int, year: int):
    slug = f"highest-temperature-in-{city_slug}-on-{month}-{day}-{year}"
    try:
        r    = requests.get(f"https://gamma-api.polymarket.com/events?slug={slug}", timeout=(5, 8))
        data = r.json()
        if data and isinstance(data, list) and data:
            return data[0]
    except Exception as e:
        warn(f"Polymarket event {city_slug}: {e}")
    return None

def get_market_detail(market_id: str) -> dict:
    """
    Fetch full market detail from Gamma API.
    Returns dict with bestAsk, bestBid, outcomePrices, clobTokenIds, closed, etc.
    """
    try:
        r = requests.get(f"https://gamma-api.polymarket.com/markets/{market_id}", timeout=(3, 6))
        return r.json()
    except Exception:
        return {}

def extract_yes_token_id(market_detail: dict) -> str:
    """
    Extract the YES outcome token_id (clobTokenIds[0]) from a Gamma market detail.

    This is the CLOB token_id needed for placing orders.
    It is a long hex string, completely different from the numeric Gamma market ID.
    """
    clob_ids = market_detail.get("clobTokenIds")
    if clob_ids:
        # clobTokenIds can be a JSON-encoded string or a list
        if isinstance(clob_ids, str):
            try:
                clob_ids = json.loads(clob_ids)
            except Exception:
                pass
        if isinstance(clob_ids, list) and clob_ids:
            return str(clob_ids[0])
    return ""

def check_market_resolved(market_detail: dict):
    """
    Returns True (YES won), False (NO won), or None (still open / indeterminate).
    Accepts already-fetched market detail dict.
    """
    try:
        if not market_detail.get("closed", False):
            return None
        prices    = json.loads(market_detail.get("outcomePrices", "[0.5,0.5]"))
        yes_price = float(prices[0])
        if yes_price >= 0.95:
            return True
        if yes_price <= 0.05:
            return False
    except Exception as e:
        warn(f"Resolve check: {e}")
    return None

# =============================================================================
# PARSING
# =============================================================================

def parse_temp_range(question: str):
    if not question:
        return None
    num = r"(-?\d+(?:\.\d+)?)"
    if re.search(r"or below", question, re.IGNORECASE):
        m = re.search(num + r"[°]?[FC] or below", question, re.IGNORECASE)
        if m: return (-999.0, float(m.group(1)))
    if re.search(r"or higher", question, re.IGNORECASE):
        m = re.search(num + r"[°]?[FC] or higher", question, re.IGNORECASE)
        if m: return (float(m.group(1)), 999.0)
    m = re.search(r"between " + num + r"-" + num + r"[°]?[FC]", question, re.IGNORECASE)
    if m: return (float(m.group(1)), float(m.group(2)))
    m = re.search(r"be " + num + r"[°]?[FC] on", question, re.IGNORECASE)
    if m:
        v = float(m.group(1))
        return (v, v)
    return None

def in_bucket(forecast, t_low, t_high) -> bool:
    if t_low == t_high:
        return round(float(forecast)) == round(t_low)
    return t_low <= float(forecast) <= t_high

def hours_to_resolution(end_date_str: str) -> float:
    try:
        end   = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
        delta = (end - datetime.now(timezone.utc)).total_seconds() / 3600
        return max(0.0, delta)
    except Exception:
        return 999.0

# =============================================================================
# MARKET STORAGE
# =============================================================================

def market_path(city_slug: str, date_str: str) -> Path:
    return MARKETS_DIR / f"{city_slug}_{date_str}.json"

def load_market(city_slug: str, date_str: str):
    p = market_path(city_slug, date_str)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None
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

# =============================================================================
# STATE
# =============================================================================

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
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

def reset_state():
    if STATE_FILE.exists():
        STATE_FILE.unlink()
    ok(f"State reset — balance back to ${BALANCE:.2f}")

# =============================================================================
# CORE SCAN LOOP
# =============================================================================

def scan_and_update(dry_run: bool = False):
    """
    One full scan cycle across all 20 cities × 4 horizons.
    Returns (new_positions, closed, resolved).
    """
    global _cal
    now      = datetime.now(timezone.utc)
    state    = load_state()
    balance  = state["balance"]
    new_pos  = 0
    closed   = 0
    resolved = 0

    for city_slug, loc in LOCATIONS.items():
        unit     = loc["unit"]
        unit_sym = "F" if unit == "F" else "C"
        print(f"  -> {loc['name']}...", end=" ", flush=True)

        try:
            dates     = [(now + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(4)]
            snapshots = take_forecast_snapshot(city_slug, dates)
            time.sleep(0.3)
        except Exception as e:
            print(f"skipped ({e})")
            continue

        # Forecast summary line
        parts_by_date = []
        for i, date in enumerate(dates):
            snap  = snapshots.get(date, {})
            parts = [f"{s.upper()} {snap[s]}{unit_sym}"
                     for s in ("ecmwf", "hrrr", "metar") if snap.get(s) is not None]
            if parts:
                parts_by_date.append(f"D+{i}: {', '.join(parts)}")
        print(f"({' | '.join(parts_by_date) or 'no forecast'})")

        for i, date in enumerate(dates):
            dt    = datetime.strptime(date, "%Y-%m-%d")
            event = get_polymarket_event(city_slug, MONTHS[dt.month - 1], dt.day, dt.year)
            if not event:
                continue
            time.sleep(0.4)

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

            # Build outcomes — fetch clobTokenIds for each market bucket
            outcomes = []
            for market in event.get("markets", []):
                question = market.get("question", "")
                gamma_id = str(market.get("id", ""))
                volume   = float(market.get("volume", 0))
                rng      = parse_temp_range(question)
                if not rng:
                    continue
                try:
                    prices = json.loads(market.get("outcomePrices", "[0.5,0.5]"))
                    bid    = float(prices[0])
                    ask    = float(prices[1]) if len(prices) > 1 else bid
                except Exception:
                    continue

                # ── KEY FIX: extract clobTokenIds (YES token) ──────────────
                # clobTokenIds may already be in the event's market object
                clob_token_id = ""
                raw_clob = market.get("clobTokenIds")
                if raw_clob:
                    if isinstance(raw_clob, str):
                        try:
                            raw_clob = json.loads(raw_clob)
                        except Exception:
                            pass
                    if isinstance(raw_clob, list) and raw_clob:
                        clob_token_id = str(raw_clob[0])

                # If not in event data, fetch from market detail endpoint
                if not clob_token_id and gamma_id:
                    try:
                        mdetail       = get_market_detail(gamma_id)
                        clob_token_id = extract_yes_token_id(mdetail)
                        time.sleep(0.1)
                    except Exception:
                        pass

                # Skip if we still don't have a valid token_id
                if not clob_token_id or len(str(clob_token_id)) < 20:
                    continue

                outcomes.append({
                    "question":      question,
                    "gamma_id":      gamma_id,           # Gamma numeric ID (for detail fetch)
                    "token_id":      clob_token_id,      # CLOB token ID (for orders) ← THE FIX
                    "range":         rng,
                    "bid":           round(bid, 4),
                    "ask":           round(ask, 4),
                    "price":         round(bid, 4),
                    "spread":        round(ask - bid, 4),
                    "volume":        round(volume, 0),
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
            top = max(outcomes, key=lambda x: x["price"]) if outcomes else None
            mkt["market_snapshots"].append({
                "ts":         snap.get("ts"),
                "top_bucket": f"{top['range'][0]}-{top['range'][1]}{unit_sym}" if top else None,
                "top_price":  top["price"] if top else None,
            })

            forecast_temp = snap.get("best")
            best_source   = snap.get("best_source")

            # ── Stop-loss / trailing stop ────────────────────────────────
            if mkt.get("position") and mkt["position"].get("status") == "open":
                pos = mkt["position"]
                token_id = pos.get("token_id")
                if not token_id:
                    # Try to get token_id from gamma_id if missing
                    try:
                        mdetail = get_market_detail(pos.get("gamma_id"))
                        token_id = extract_yes_token_id(mdetail)
                        if token_id:
                            pos["token_id"] = token_id  # Update the position
                    except Exception:
                        pass

                if not token_id:
                    warn(f"Skip stop-loss for {loc['name']} {date} — no token_id available")
                    save_market(mkt)
                    continue

                current_price = next(
                    (o.get("bid", o["price"]) for o in outcomes if o["token_id"] == token_id),
                    None
                )
                # Fallback match by gamma_id for backwards-compat
                if current_price is None:
                    current_price = next(
                        (o.get("bid", o["price"]) for o in outcomes if o["gamma_id"] == pos.get("gamma_id")),
                        None
                    )
                if current_price is not None:
                    entry = pos["entry_price"]
                    stop  = pos.get("stop_price", round(entry * 0.80, 4))

                    if current_price >= entry * 1.20 and stop < entry:
                        pos["stop_price"]         = entry
                        pos["trailing_activated"] = True
                        info(f"TRAILING {loc['name']} {date} — stop → ${entry:.3f}")

                    if current_price <= stop:
                        pnl    = round((current_price - entry) * pos["shares"], 2)
                        reason = "STOP" if current_price < entry else "TRAILING BE"
                        resp   = place_order(
                            token_id, SELL,
                            pos["cost"], current_price,
                            dry_run=dry_run
                        )
                        if order_ok(resp):
                            if not dry_run:
                                balance += pos["cost"] + pnl
                            _close_position(pos, snap.get("ts"), reason, current_price, pnl)
                            closed += 1
                            sim = " [SIM]" if resp.get("simulated") else ""
                            col = C.GREEN if pnl >= 0 else C.RED
                            print(f"  {col}[{reason}]{C.RESET} {loc['name']} {date} | "
                                  f"${entry:.3f}→${current_price:.3f} | PnL: {'+' if pnl>=0 else ''}{pnl:.2f}{sim}")
                            logging.info("SELL %s %s reason=%s entry=%.3f exit=%.3f pnl=%.2f orderID=%s",
                                         loc["name"], date, reason, entry, current_price, pnl, resp.get("orderID"))
                        else:
                            warn(f"SELL failed {loc['name']} {date}: {resp.get('error', resp)}")

            # ── Forecast-change exit ─────────────────────────────────────
            if mkt.get("position") and forecast_temp is not None and mkt["position"].get("status") == "open":
                pos = mkt["position"]
                token_id = pos.get("token_id")
                if not token_id:
                    # Try to get token_id from gamma_id if missing
                    try:
                        mdetail = get_market_detail(pos.get("gamma_id"))
                        token_id = extract_yes_token_id(mdetail)
                        if token_id:
                            pos["token_id"] = token_id  # Update the position
                    except Exception:
                        pass

                if not token_id:
                    warn(f"Skip forecast exit for {loc['name']} {date} — no token_id available")
                    save_market(mkt)
                    continue

                bl, bh = pos["bucket_low"], pos["bucket_high"]
                buf    = 2.0 if unit == "F" else 1.0
                mid_b  = (bl + bh) / 2 if bl != -999 and bh != 999 else forecast_temp
                if not in_bucket(forecast_temp, bl, bh) and abs(forecast_temp - mid_b) > (abs(mid_b - bl) + buf):
                    current_price = next(
                        (o["price"] for o in outcomes if o["token_id"] == token_id),
                        None
                    )
                    if current_price is not None:
                        pnl  = round((current_price - pos["entry_price"]) * pos["shares"], 2)
                        resp = place_order(
                            token_id, SELL,
                            pos["cost"], current_price,
                            dry_run=dry_run
                        )
                        if order_ok(resp):
                            if not dry_run:
                                balance += pos["cost"] + pnl
                            _close_position(pos, snap.get("ts"), "forecast_changed", current_price, pnl)
                            closed += 1
                            sim = " [SIM]" if resp.get("simulated") else ""
                            col = C.GREEN if pnl >= 0 else C.RED
                            print(f"  {col}[CLOSE]{C.RESET} {loc['name']} {date} — forecast shifted | "
                                  f"PnL: {'+' if pnl>=0 else ''}{pnl:.2f}{sim}")
                            logging.info("SELL %s %s reason=forecast_changed pnl=%.2f orderID=%s",
                                         loc["name"], date, pnl, resp.get("orderID"))
                        else:
                            warn(f"SELL failed {loc['name']} {date}: {resp.get('error', resp)}")

            # ── Open new position ────────────────────────────────────────
            if not mkt.get("position") and forecast_temp is not None and hours >= MIN_HOURS:
                matched = next((o for o in outcomes if in_bucket(forecast_temp, *o["range"])), None)
                if matched:
                    t_low, t_high = matched["range"]
                    volume = matched["volume"]
                    ask    = matched.get("ask", matched["price"])
                    bid    = matched.get("bid", matched["price"])
                    spread = matched.get("spread", 0)
                    sigma  = get_sigma(city_slug, best_source or "ecmwf")

                    if volume >= MIN_VOLUME and ask < MAX_PRICE and spread <= MAX_SLIPPAGE:
                        p  = bucket_prob(forecast_temp, t_low, t_high, sigma)
                        ev = calc_ev(p, ask)
                        if ev >= MIN_EV:
                            kelly = calc_kelly(p, ask)
                            size  = bet_size(kelly, balance)
                            if size >= 0.50:
                                # Real-time price confirm from Gamma API
                                token_id = matched["token_id"]
                                try:
                                    mdetail  = get_market_detail(matched["gamma_id"])
                                    real_ask = float(mdetail.get("bestAsk", ask))
                                    real_bid = float(mdetail.get("bestBid", bid))
                                    real_spr = round(real_ask - real_bid, 4)
                                    # Refresh token_id in case it was missing earlier
                                    fresh_token = extract_yes_token_id(mdetail)
                                    if fresh_token:
                                        token_id = fresh_token
                                    if real_spr > MAX_SLIPPAGE or real_ask >= MAX_PRICE:
                                        skip(f"{loc['name']} {date} — real spread ${real_spr:.3f}, skip")
                                        save_market(mkt)
                                        continue
                                    ask    = real_ask
                                    bid    = real_bid
                                    spread = real_spr
                                    ev     = calc_ev(p, ask)
                                except Exception as e:
                                    warn(f"Real ask fetch {matched['gamma_id']}: {e}")

                                if not token_id:
                                    warn(f"{loc['name']} {date} — no clobTokenId found, skip")
                                    save_market(mkt)
                                    continue

                                bucket_label = f"{t_low}-{t_high}{unit_sym}"
                                shares       = round(size / ask, 2)

                                resp = place_order(token_id, BUY, size, ask, dry_run=dry_run)

                                if order_ok(resp) or (dry_run and resp.get("simulated")):
                                    if not dry_run:
                                        balance -= size
                                    mkt["position"] = {
                                        "gamma_id":           matched["gamma_id"],
                                        "token_id":           token_id,       # ← CLOB token
                                        "question":           matched["question"],
                                        "bucket_low":         t_low,
                                        "bucket_high":        t_high,
                                        "entry_price":        ask,
                                        "bid_at_entry":       bid,
                                        "spread":             spread,
                                        "shares":             shares,
                                        "cost":               size,
                                        "p":                  round(p, 4),
                                        "ev":                 round(ev, 4),
                                        "kelly":              round(kelly, 4),
                                        "forecast_temp":      forecast_temp,
                                        "forecast_src":       best_source,
                                        "sigma":              sigma,
                                        "opened_at":          snap.get("ts"),
                                        "stop_price":         round(ask * 0.80, 4),
                                        "trailing_activated": False,
                                        "status":             "open",
                                        "pnl":                None,
                                        "exit_price":         None,
                                        "close_reason":       None,
                                        "closed_at":          None,
                                        "order_id":           resp.get("orderID"),
                                    }
                                    state["total_trades"] += 1
                                    new_pos               += 1
                                    sim = " [SIM]" if resp.get("simulated") else ""
                                    ok(f"BUY  {loc['name']} {horizon} {date} | {bucket_label} | "
                                       f"${ask:.3f} | EV {ev:+.2f} | ${size:.2f} "
                                       f"[{(best_source or 'ecmwf').upper()}]{sim}")
                                    if not dry_run:
                                        logging.info("BUY %s %s bucket=%s ask=%.3f ev=%.2f size=%.2f "
                                                     "token=%s orderID=%s",
                                                     loc["name"], date, bucket_label, ask, ev, size,
                                                     token_id[:16], resp.get("orderID"))
                                else:
                                    warn(f"BUY failed {loc['name']} {date}: {resp.get('error', resp)}")
                    else:
                        if volume < MIN_VOLUME:
                            skip(f"{loc['name']} {date} — vol {volume:.0f} < {MIN_VOLUME}")
                        elif ask >= MAX_PRICE:
                            skip(f"{loc['name']} {date} — ask ${ask:.3f} >= ${MAX_PRICE:.2f}")
                        elif spread > MAX_SLIPPAGE:
                            skip(f"{loc['name']} {date} — spread ${spread:.3f} > ${MAX_SLIPPAGE:.2f}")

            if hours < 0.5 and mkt["status"] == "open":
                mkt["status"] = "closed"

            save_market(mkt)
            time.sleep(0.1)

    # ── Auto-resolution ───────────────────────────────────────────────────────
    for mkt in load_all_markets():
        if mkt["status"] == "resolved":
            continue
        pos = mkt.get("position")
        if not pos or pos.get("status") != "open":
            continue
        gamma_id = pos.get("gamma_id") or pos.get("market_id")
        if not gamma_id:
            continue

        mdetail = get_market_detail(gamma_id)
        won     = check_market_resolved(mdetail)
        if won is None:
            continue

        price  = pos["entry_price"]
        size   = pos["cost"]
        shares = pos["shares"]
        pnl    = round(-size, 2) if won else round(shares * (1 - price), 2)

        if not dry_run:
            balance += size + pnl
            if won:
                state["losses"] += 1
            else:
                state["wins"]   += 1

        pos.update({
            "exit_price":   0.0 if won else 1.0,
            "pnl":          pnl,
            "close_reason": "resolved",
            "closed_at":    now.isoformat(),
            "status":       "closed",
        })
        mkt.update({
            "pnl":              pnl,
            "status":           "resolved",
            "resolved_outcome": "loss" if won else "win",
        })
        result  = "WIN" if won else "LOSS"
        pnl_str = f"{'+'if pnl>=0 else ''}{pnl:.2f}"
        col = C.GREEN if won else C.RED
        print(f"  {col}[{result}]{C.RESET} {mkt['city_name']} {mkt['date']} | PnL: {pnl_str}")
        resolved += 1
        save_market(mkt)
        time.sleep(0.3)

    if not dry_run:
        state["balance"]      = round(balance, 2)
        state["peak_balance"] = max(state.get("peak_balance", balance), balance)
        save_state(state)

    all_mkts = load_all_markets()
    if len([m for m in all_mkts if m["status"] == "resolved"]) >= CALIBRATION_MIN:
        global _cal
        _cal = run_calibration(all_mkts)

    return new_pos, closed, resolved


def _close_position(pos: dict, ts, reason: str, exit_price: float, pnl: float):
    """Mutates position dict in place to mark it closed."""
    pos.update({
        "closed_at":    ts or datetime.now(timezone.utc).isoformat(),
        "close_reason": reason,
        "exit_price":   exit_price,
        "pnl":          pnl,
        "status":       "closed",
    })

# =============================================================================
# POSITION MONITOR — every 10 min between full scans
# =============================================================================

def monitor_positions(dry_run: bool = False) -> int:
    markets  = load_all_markets()
    open_pos = [m for m in markets if m.get("position") and m["position"].get("status") == "open"]
    if not open_pos:
        return 0

    state   = load_state()
    balance = state["balance"]
    closed  = 0

    for mkt in open_pos:
        pos      = mkt["position"]
        gamma_id = pos.get("gamma_id") or pos.get("market_id", "")
        token_id = pos.get("token_id", "")

        # Fetch real bestBid from Gamma API
        current_price = None
        try:
            mdetail = get_market_detail(gamma_id)

            # Refresh token_id if missing (handles legacy positions stored without it)
            if not token_id:
                token_id = extract_yes_token_id(mdetail)
                if token_id:
                    pos["token_id"] = token_id
                    save_market(mkt)

            best_bid = mdetail.get("bestBid")
            if best_bid is not None:
                current_price = float(best_bid)
            time.sleep(0.25)
        except Exception:
            pass

        if current_price is None:
            for o in mkt.get("all_outcomes", []):
                if o.get("token_id") == token_id or o.get("gamma_id") == gamma_id:
                    current_price = o.get("bid", o["price"])
                    break

        if current_price is None:
            continue

        entry      = pos["entry_price"]
        stop       = pos.get("stop_price", round(entry * 0.80, 4))
        city_name  = LOCATIONS.get(mkt["city"], {}).get("name", mkt["city"])
        hours_left = hours_to_resolution(mkt.get("event_end_date", ""))

        # Dynamic take-profit based on time left
        if hours_left < 24:
            take_profit = None        # hold to resolution
        elif hours_left < 48:
            take_profit = 0.85
        else:
            take_profit = 0.75

        # Trailing: move stop to breakeven at +20%
        if current_price >= entry * 1.20 and stop < entry:
            pos["stop_price"]         = entry
            pos["trailing_activated"] = True
            info(f"TRAILING {city_name} {mkt['date']} — stop → ${entry:.3f}")
            save_market(mkt)

        take_triggered = take_profit is not None and current_price >= take_profit
        stop_triggered = current_price <= stop

        if take_triggered or stop_triggered:
            pnl    = round((current_price - entry) * pos["shares"], 2)
            reason = "TAKE" if take_triggered else ("STOP" if current_price < entry else "TRAILING BE")
            resp   = place_order(token_id, SELL, pos["cost"], current_price, dry_run=dry_run)
            if order_ok(resp):
                if not dry_run:
                    balance += pos["cost"] + pnl
                _close_position(pos, datetime.now(timezone.utc).isoformat(), reason, current_price, pnl)
                closed += 1
                sim = " [SIM]" if resp.get("simulated") else ""
                col = C.GREEN if pnl >= 0 else C.RED
                print(f"  {col}[{reason}]{C.RESET} {city_name} {mkt['date']} | "
                      f"${entry:.3f}→${current_price:.3f} | {hours_left:.0f}h left | "
                      f"PnL: {'+'if pnl>=0 else ''}{pnl:.2f}{sim}")
                logging.info("SELL %s %s reason=%s entry=%.3f exit=%.3f pnl=%.2f orderID=%s",
                             city_name, mkt["date"], reason, entry, current_price, pnl, resp.get("orderID"))
                save_market(mkt)
            else:
                warn(f"SELL failed {city_name} {mkt['date']}: {resp.get('error', resp)}")

    if closed and not dry_run:
        state["balance"] = round(balance, 2)
        save_state(state)

    return closed

# =============================================================================
# STATUS & REPORT
# =============================================================================

def print_status():
    state    = load_state()
    markets  = load_all_markets()
    open_pos = [m for m in markets if m.get("position") and m["position"].get("status") == "open"]
    closed_pos = [m for m in markets if m.get("position") and m["position"].get("status") == "closed"]
    resolved = [m for m in markets if m["status"] == "resolved" and m.get("pnl") is not None]

    bal         = state["balance"]
    start       = state["starting_balance"]
    ret_pct     = (bal - start) / start * 100
    wins        = state["wins"]
    losses      = state["losses"]
    total_trades = state.get("total_trades", wins + losses)
    resolved_count = len(resolved)
    closed_count   = len([m for m in closed_pos if m.get("status") != "resolved"])

    print(f"\n{'='*58}")
    print(f"  {C.BOLD}WEATHERBET — STATUS{C.RESET}")
    print(f"{'='*58}")
    ret_col = C.GREEN if ret_pct >= 0 else C.RED
    print(f"  Balance:  ${bal:,.2f}  (start ${start:,.2f}, "
          f"{ret_col}{'+'if ret_pct>=0 else ''}{ret_pct:.1f}%{C.RESET})")
    if total_trades:
        win_rate = f"{wins/total_trades:.0%}" if total_trades else "0%"
        print(f"  Trades:   {total_trades} | W: {wins} | L: {losses} | WR: {win_rate}")
    else:
        print("  Trades:   none yet")
    print(f"  Open:     {len(open_pos)}")
    print(f"  Closed:   {closed_count}")
    print(f"  Resolved: {resolved_count}")

    if open_pos:
        print(f"\n  Open positions:")
        total_unreal = 0.0
        for m in open_pos:
            pos      = m["position"]
            unit_sym = "F" if m["unit"] == "F" else "C"
            label    = f"{pos['bucket_low']}-{pos['bucket_high']}{unit_sym}"
            # Current price from cached outcomes
            cp = pos["entry_price"]
            for o in m.get("all_outcomes", []):
                if o.get("token_id") == pos.get("token_id") or o.get("gamma_id") == pos.get("gamma_id"):
                    cp = o["price"]
                    break
            unreal = round((cp - pos["entry_price"]) * pos["shares"], 2)
            total_unreal += unreal
            col = C.GREEN if unreal >= 0 else C.RED
            src = pos.get("forecast_src", "?").upper()
            print(f"    {m['city_name']:<16} {m['date']} | {label:<14} | "
                  f"${pos['entry_price']:.3f}→${cp:.3f} | "
                  f"PnL: {col}{'+'if unreal>=0 else ''}{unreal:.2f}{C.RESET} | {src}")
        col = C.GREEN if total_unreal >= 0 else C.RED
        print(f"\n  Unrealized PnL: {col}{'+'if total_unreal>=0 else ''}{total_unreal:.2f}{C.RESET}")

    if closed_pos:
        print(f"\n  Recent closed trades:")
        recent_closed = sorted(closed_pos, key=lambda x: x["position"].get("closed_at", ""), reverse=True)[:6]
        for m in recent_closed:
            pos      = m["position"]
            unit_sym = "F" if m["unit"] == "F" else "C"
            label    = f"{pos.get('bucket_low','?')}-{pos.get('bucket_high','?')}{unit_sym}"
            pnl      = pos.get("pnl")
            close_r  = pos.get("close_reason", "closed")
            entry    = pos.get("entry_price", 0.0)
            exit_p   = pos.get("exit_price", 0.0)
            status   = m.get("status", "")
            cond     = "resolved" if status == "resolved" else close_r.upper()
            col = C.GREEN if pnl is not None and pnl >= 0 else C.RED
            pnl_str = f"{'+' if pnl is not None and pnl >= 0 else ''}{pnl:.2f}" if pnl is not None else "0.00"
            print(f"    {m['city_name']:<16} {m['date']} | {label:<14} | "
                  f"${entry:.3f}→${exit_p:.3f} | {cond:<9} | "
                  f"PnL: {col}{pnl_str}{C.RESET}")

    print(f"{'='*58}\n")


def print_terminal_summary():
    state    = load_state()
    markets  = load_all_markets()
    open_pos = [m for m in markets if m.get("position") and m["position"].get("status") == "open"]
    closed_pos = [m for m in markets if m.get("position") and m["position"].get("status") == "closed"]
    resolved = [m for m in markets if m["status"] == "resolved" and m.get("pnl") is not None]

    bal         = state["balance"]
    start       = state["starting_balance"]
    wins        = state.get("wins", 0)
    losses      = state.get("losses", 0)
    total_trades = state.get("total_trades", wins + losses)
    win_rate    = f"{wins/total_trades:.0%}" if total_trades else "0%"

    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] STATUS UPDATE")
    print(f"  Balance: ${bal:,.2f}  (start ${start:,.2f})")
    print(f"  Trades: {total_trades} | W: {wins} | L: {losses} | WR: {win_rate}")
    print(f"  Open positions: {len(open_pos)}")
    print(f"  Closed positions: {len(closed_pos)}")
    print(f"  Resolved markets: {len(resolved)}")

    if open_pos:
        print(f"  Open trades:")
        for m in open_pos[:5]:
            pos = m["position"]
            unit_sym = "F" if m["unit"] == "F" else "C"
            label = f"{pos.get('bucket_low','?')}-{pos.get('bucket_high','?')}{unit_sym}"
            entry = pos.get("entry_price", 0.0)
            current = entry
            for o in m.get("all_outcomes", []):
                if o.get("token_id") == pos.get("token_id") or o.get("gamma_id") == pos.get("gamma_id"):
                    current = o.get("price", entry)
                    break
            pnl = round((current - entry) * pos.get("shares", 0), 2)
            print(f"    {m['city_name']:<14} {m['date']} | {label:<10} | "
                  f"${entry:.3f}→${current:.3f} | PnL: {'+' if pnl>=0 else ''}{pnl:.2f}")
        if len(open_pos) > 5:
            print(f"    ...and {len(open_pos)-5} more open positions")

    if closed_pos:
        print(f"  Recent closed trades:")
        recent_closed = sorted(closed_pos, key=lambda x: x["position"].get("closed_at", ""), reverse=True)[:5]
        for m in recent_closed:
            pos = m["position"]
            unit_sym = "F" if m["unit"] == "F" else "C"
            label = f"{pos.get('bucket_low','?')}-{pos.get('bucket_high','?')}{unit_sym}"
            entry = pos.get("entry_price", 0.0)
            exit_p = pos.get("exit_price", 0.0)
            pnl = pos.get("pnl", 0.0)
            reason = pos.get("close_reason", "closed").upper()
            print(f"    {m['city_name']:<14} {m['date']} | {label:<10} | "
                  f"${entry:.3f}→${exit_p:.3f} | {reason:<8} | "
                  f"PnL: {'+' if pnl>=0 else ''}{pnl:.2f}")

    print(f"{'='*58}\n")


def print_report():
    markets  = load_all_markets()
    resolved = [m for m in markets if m["status"] == "resolved" and m.get("pnl") is not None]

    print(f"\n{'='*58}")
    print(f"  {C.BOLD}WEATHERBET — FULL REPORT{C.RESET}")
    print(f"{'='*58}")

    if not resolved:
        print("  No resolved markets yet.")
        return

    total_pnl = sum(m["pnl"] for m in resolved)
    wins      = [m for m in resolved if m["resolved_outcome"] == "win"]
    losses    = [m for m in resolved if m["resolved_outcome"] == "loss"]

    print(f"\n  Total resolved: {len(resolved)}")
    print(f"  Wins:     {len(wins)} | Losses: {len(losses)} | WR: {len(wins)/len(resolved):.0%}")
    col = C.GREEN if total_pnl >= 0 else C.RED
    print(f"  Total PnL: {col}{'+'if total_pnl>=0 else ''}{total_pnl:.2f}{C.RESET}")

    print(f"\n  By city:")
    for city in sorted(set(m["city"] for m in resolved)):
        group = [m for m in resolved if m["city"] == city]
        w     = sum(1 for m in group if m["resolved_outcome"] == "win")
        pnl   = sum(m["pnl"] for m in group)
        print(f"    {LOCATIONS[city]['name']:<16}  {w}/{len(group)} ({w/len(group):.0%})"
              f"  PnL: {'+'if pnl>=0 else ''}{pnl:.2f}")

    print(f"\n  Market details:")
    for m in sorted(resolved, key=lambda x: x["date"]):
        pos      = m.get("position") or {}
        unit_sym = "F" if m["unit"] == "F" else "C"
        snaps    = m.get("forecast_snapshots", [])
        first_fc = snaps[0].get("best") if snaps else None
        last_fc  = snaps[-1].get("best") if snaps else None
        label    = f"{pos.get('bucket_low')}-{pos.get('bucket_high')}{unit_sym}" if pos else "no pos"
        result   = m["resolved_outcome"].upper()
        pnl_str  = f"{'+'if m['pnl']>=0 else ''}{m['pnl']:.2f}" if m["pnl"] is not None else "-"
        fc_str   = f"{first_fc}→{last_fc}{unit_sym}" if first_fc else "no fc"
        actual   = f"actual {m['actual_temp']}{unit_sym}" if m.get("actual_temp") else ""
        col = C.GREEN if result == "WIN" else C.RED
        print(f"    {m['city_name']:<16} {m['date']} | {label:<14} | "
              f"fc {fc_str} | {actual} | {col}{result}{C.RESET} {pnl_str}")
    print(f"{'='*58}\n")

# =============================================================================
# MAIN LOOP
# =============================================================================

def run_loop(dry_run: bool = False):
    global _cal
    _cal = load_cal()

    print(f"\n{'='*58}")
    print(f"  {C.BOLD}WEATHERBET — {'DRY RUN' if dry_run else 'LIVE'}{C.RESET}")
    print(f"{'='*58}")
    print(f"  Cities:   {len(LOCATIONS)}")
    print(f"  Balance:  ${BALANCE:,.2f} | Max bet: ${MAX_BET}")
    print(f"  Min EV:   {MIN_EV:+.2f} | Max price: ${MAX_PRICE} | Max slippage: ${MAX_SLIPPAGE}")
    print(f"  Scan:     {SCAN_INTERVAL//60} min | Monitor: {MONITOR_INTERVAL//60} min")
    print(f"  Data:     {DATA_DIR.resolve()}")
    if dry_run:
        print(f"  {C.YELLOW}DRY RUN — no real orders will be placed{C.RESET}")
    else:
        ok_str = C.GREEN + "OK" + C.RESET
        mis    = C.RED + "MISSING" + C.RESET
        print(f"  PRIVATE_KEY:         {'OK' if PRIVATE_KEY else mis}")
        print(f"  CLOB host:           {CLOB_HOST}")
        print(f"  RELAYER_API_KEY:     {'OK (gasless)' if RELAYER_API_KEY else 'not set (post_order fallback)'}")
        print(f"  API creds in .env:   {'yes' if CLOB_API_KEY else 'no (will derive)'}")
        if not PRIVATE_KEY:
            warn("PRIVATE_KEY missing — live orders will fail. Use --dry-run or set PRIVATE_KEY in .env")
    print(f"  Ctrl+C to stop\n")

    # Eagerly init CLOB client so startup errors are visible immediately
    if not dry_run:
        get_clob_client()

    last_full_scan = 0
    last_status   = time.time()

    while True:
        now_ts  = time.time()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if now_ts - last_status >= STATUS_INTERVAL:
            print_terminal_summary()
            last_status = now_ts

        if now_ts - last_full_scan >= SCAN_INTERVAL:
            print(f"\n[{now_str}] full scan...")
            try:
                new_pos, closed_n, resolved_n = scan_and_update(dry_run=dry_run)
                state = load_state()
                print(f"  balance: ${state['balance']:,.2f} | "
                      f"new: {new_pos} | closed: {closed_n} | resolved: {resolved_n}")
                last_full_scan = time.time()
            except KeyboardInterrupt:
                raise
            except requests.exceptions.ConnectionError:
                warn("Connection lost — retrying in 60 s")
                time.sleep(60)
                continue
            except Exception as e:
                err(f"Scan error: {e}")
                logging.exception("Scan error: %s", e)
                time.sleep(60)
                continue
        else:
            print(f"[{now_str}] monitoring positions...")
            try:
                stopped = monitor_positions(dry_run=dry_run)
                if stopped:
                    state = load_state()
                    print(f"  balance: ${state['balance']:,.2f}")
            except Exception as e:
                warn(f"Monitor error: {e}")

        try:
            time.sleep(MONITOR_INTERVAL)
        except KeyboardInterrupt:
            print(f"\n  Stopping — saving state...")
            save_state(load_state())
            print(f"  Done. Bye!")
            break

# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="WeatherBet — Polymarket weather trading bot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Commands:
  (default)   run bot — full scans every hour, monitor every 10 min
  status      show balance + open positions
  report      full breakdown of all resolved markets

Flags:
  --dry-run   print signals/stops without placing real orders
  --reset     wipe state.json (market data kept)
        """,
    )
    parser.add_argument("cmd", nargs="?", default="run",
                        choices=["run", "status", "report"])
    parser.add_argument("--dry-run", action="store_true",
                        help="Paper mode — no real orders")
    parser.add_argument("--reset", action="store_true",
                        help="Reset state.json")
    args = parser.parse_args()

    if args.reset:
        reset_state()
        return

    if args.cmd == "status":
        globals()["_cal"] = load_cal()
        print_status()
    elif args.cmd == "report":
        globals()["_cal"] = load_cal()
        print_report()
    else:
        run_loop(dry_run=args.dry_run)


if __name__ == "__main__":
    main()