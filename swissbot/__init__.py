# ============================================================
#  SwissTony Bot - Polymarket Automated Trading
#  Replicates swisstony's reality arbitrage strategy
# ============================================================

from .config import BotConfig, get_config, reload_config
from .models import (
    Market, Order, OrderSide, OrderType, OrderStatus,
    Position, Trade, Event, BotState
)
from .client import PolymarketClient, get_client, close_client
from .risk import RiskManager
from .market_maker import MarketMaker
from .reality_arb import RealityArb
from .bot import SwissTonyBot, run_bot

__version__ = "1.0.0"
__author__ = "SwissTony Bot"

__all__ = [
    "BotConfig",
    "get_config",
    "reload_config",
    "Market",
    "Order",
    "OrderSide",
    "OrderType",
    "OrderStatus",
    "Position",
    "Trade",
    "Event",
    "BotState",
    "PolymarketClient",
    "get_client",
    "close_client",
    "RiskManager",
    "MarketMaker",
    "RealityArb",
    "SwissTonyBot",
    "run_bot",
]