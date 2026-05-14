#!/usr/bin/env python3
"""
Temp script: buy $1 on a running market window and immediately sell the filled shares.

Usage:
  1) Put your `PRIVATE_KEY` in `.env` or export it.
  2) Set `TEMP_TOKEN_ID` to the live token_id for the running window.
  3) Run: python temp_buy_sell.py

Notes:
  - BUY orders send USD amount.
  - SELL orders send share quantity.
  - This script is only for learning/testing, not production.
"""

import os
import time
import json
import re
import logging
import requests
from dotenv import load_dotenv
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import (
    MarketOrderArgs,
    OrderType,
    BalanceAllowanceParams,
    AssetType,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

load_dotenv()

PRIVATE_KEY = os.getenv("PRIVATE_KEY") or os.getenv("FUNDING_PRIVATE_KEY")
CHAIN_ID = int(os.getenv("CHAIN_ID", "137"))
SIGNATURE_TYPE = int(os.getenv("SIGNATURE_TYPE", "0"))
CLOB_HOST = os.getenv("CLOB_HOST", "https://clob.polymarket.com")
TOKEN_ID = os.getenv("TEMP_TOKEN_ID", "")
BUY_USD = float(os.getenv("TEMP_BUY_USD", "1.0"))
BUY_SIDE = os.getenv("TEMP_BUY_SIDE", "BUY")
SELL_SIDE = os.getenv("TEMP_SELL_SIDE", "SELL")
RELAYER_URL = os.getenv("RELAYER_URL", "https://relayer.polymarket.com")
RELAYER_API_KEY = os.getenv("RELAYER_API_KEY", "")
RELAYER_API_KEY_ADDRESS = os.getenv("RELAYER_API_KEY_ADDRESS", "")

if not PRIVATE_KEY:
    raise SystemExit("Missing PRIVATE_KEY in environment")


def build_client():
    client = ClobClient(
        host=CLOB_HOST,
        key=PRIVATE_KEY,
        chain_id=CHAIN_ID,
        signature_type=SIGNATURE_TYPE,
    )
    try:
        creds = client.create_or_derive_api_creds()
        client.set_api_creds(creds)
    except Exception as exc:
        log.warning("Could not derive API creds: %s", exc)
    return client


def discover_current_btc_5m_token_id() -> str:
    market_ts = int(time.time()) - (int(time.time()) % 300)
    url = f"https://polymarket.com/event/btc-updown-5m-{market_ts}"
    log.info("Discovering current BTC 5m market token from %s", url)
    try:
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        resp.raise_for_status()
        token_match = re.search(r'"clobTokenIds":\s*\[([^\]]+)\]', resp.text)
        if not token_match:
            raise ValueError("clobTokenIds not found in market page")
        token_ids = json.loads("[" + token_match.group(1) + "]")
        if not token_ids:
            raise ValueError("No token ids discovered")
        return str(token_ids[0])
    except Exception as exc:
        raise RuntimeError(f"Could not discover current BTC 5m token id: {exc}")


def get_usdc_balance(client) -> float:
    try:
        bal = client.get_balance_allowance(
            BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        )
        return int(bal.get("balance", 0)) / 1e6
    except Exception as exc:
        log.warning("Could not fetch USDC balance: %s", exc)
        return 0.0


def _normalize_signed_payload(signed_payload: object) -> dict:
    if isinstance(signed_payload, dict):
        return signed_payload
    if hasattr(signed_payload, "dict"):
        return signed_payload.dict()
    if hasattr(signed_payload, "to_dict"):
        return signed_payload.to_dict()
    if hasattr(signed_payload, "__dict__"):
        return dict(signed_payload.__dict__)
    return signed_payload


def _submit_relayer(signed_payload: object) -> dict:
    payload = _normalize_signed_payload(signed_payload)
    r = requests.post(
        f"{RELAYER_URL}/order",
        headers={
            "Content-Type": "application/json",
            "RELAYER_API_KEY": RELAYER_API_KEY,
            "RELAYER_API_KEY_ADDRESS": RELAYER_API_KEY_ADDRESS,
        },
        json=payload,
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def place_market_order(client, token_id: str, amount: float, side: str):
    order = MarketOrderArgs(
        token_id=token_id,
        amount=amount,
        side=side,
        order_type=OrderType.FOK,
    )
    signed = client.create_market_order(order)

    if RELAYER_API_KEY and RELAYER_API_KEY_ADDRESS:
        try:
            log.info("  Attempting relayer submit")
            return _submit_relayer(signed)
        except Exception as e:
            log.warning("  Relayer submit failed: %s", e)
            log.info("  Falling back to direct CLOB post")

    return client.post_order(signed, OrderType.FOK)


def extract_fill_price(resp: dict) -> float:
    for key in ("avgFillPrice", "avg_fill_price", "avg_price", "price", "filled_price"):
        if resp.get(key) is not None:
            try:
                return float(resp[key])
            except (TypeError, ValueError):
                continue
    if isinstance(resp.get("order"), dict):
        return extract_fill_price(resp["order"])
    fills = resp.get("fills") or resp.get("fill") or resp.get("executions")
    if isinstance(fills, list) and fills:
        first = fills[0]
        if isinstance(first, dict):
            for key in ("price", "fill_price", "avg_price", "avgFillPrice"):
                if first.get(key) is not None:
                    try:
                        return float(first[key])
                    except (TypeError, ValueError):
                        continue
    raise ValueError("Could not extract fill price from response")


def main():
    log.info("Building CLOB client")
    client = build_client()

    global TOKEN_ID
    if not TOKEN_ID:
        TOKEN_ID = discover_current_btc_5m_token_id()
        log.info("Discovered current BTC 5m token id: %s", TOKEN_ID)

    balance = get_usdc_balance(client)
    log.info("Current USDC collateral balance: $%.2f", balance)
    if balance < BUY_USD:
        raise SystemExit(
            f"Insufficient USDC balance for ${BUY_USD:.2f} trade. Fund wallet and retry."
        )

    log.info("Placing BUY market order: $%.2f on token %s", BUY_USD, TOKEN_ID)
    buy_resp = place_market_order(client, TOKEN_ID, BUY_USD, BUY_SIDE)
    log.info("BUY response: %s", buy_resp)

    buy_price = extract_fill_price(buy_resp)
    shares = round(BUY_USD / buy_price, 6)
    log.info("Filled BUY at %.6f, computed shares=%.6f", buy_price, shares)

    if shares <= 0:
        raise SystemExit("No shares to sell after buy")

    time.sleep(1)
    log.info("Placing SELL market order: %.6f shares on token %s", shares, TOKEN_ID)
    sell_resp = place_market_order(client, TOKEN_ID, shares, SELL_SIDE)
    log.info("SELL response: %s", sell_resp)

    try:
        sell_price = extract_fill_price(sell_resp)
        log.info("SELL fill price: %.6f", sell_price)
    except Exception as exc:
        log.warning("Could not parse sell fill price: %s", exc)


if __name__ == "__main__":
    main()
