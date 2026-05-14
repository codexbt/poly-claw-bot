# ============================================================
#  Market Maker Module - SwissTony Bot
#  Places limit buy/sell orders around mid-price
# ============================================================

import asyncio
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

from .config import get_config, BotConfig
from .client import PolymarketClient
from .models import (
    Market, Order, OrderSide, OrderType, OrderStatus, Trade
)
from .risk import RiskManager


@dataclass
class Quote:
    """Market making quote"""
    market_id: str
    outcome_id: str
    side: OrderSide
    size: float
    price: float
    timestamp: float = 0


class MarketMaker:
    """
    Market Making Module
    - Fetches active sports markets
    - Places limit orders around mid-price
    - Manages inventory risk
    - Auto-hedges by skewing quotes
    """
    
    def __init__(
        self,
        client: PolymarketClient,
        risk_manager: RiskManager,
        config: Optional[BotConfig] = None
    ):
        self.client = client
        self.risk = risk_manager
        self.config = config or get_config()
        
        # State
        self._running = False
        self._task: Optional[asyncio.Task] = None
        
        # Order tracking
        self._active_quotes: Dict[str, List[Quote]] = {}  # market_id -> quotes
        self._order_map: Dict[str, str] = {}  # quote_id -> order_id
        
        # Market tracking
        self._tracked_markets: Dict[str, Market] = {}
        self._last_refresh: Dict[str, float] = {}
        
        # Refresh interval
        self._refresh_interval = self.config.market_maker.refresh_interval
        self._market_refresh_interval = 60  # seconds
        
    @property
    def is_running(self) -> bool:
        return self._running
    
    async def start(self):
        """Start market making"""
        if self._running:
            return
            
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        print("📈 MarketMaker started")
    
    async def stop(self):
        """Stop market making"""
        self._running = False
        
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
                
        # Cancel all quotes
        await self._cancel_all_quotes()
        
        print("📉 MarketMaker stopped")
    
    async def _run_loop(self):
        """Main market making loop"""
        while self._running:
            try:
                # Refresh markets
                await self._refresh_markets()
                
                # Update quotes
                await self._update_quotes()
                
                # Reconcile orders
                await self._reconcile_orders()
                
                # Wait for next cycle
                await asyncio.sleep(self._refresh_interval)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"MarketMaker error: {e}")
                await asyncio.sleep(5)
    
    async def _refresh_markets(self):
        """Fetch and update market data"""
        now = time.time()
        
        # Check if refresh needed
        for market_id in list(self._tracked_markets.keys()):
            last = self._last_refresh.get(market_id, 0)
            if now - last < self._market_refresh_interval:
                continue
                
            # Refresh market
            try:
                market = await self.client.get_market(market_id)
                if market:
                    self._tracked_markets[market_id] = market
                    self._last_refresh[market_id] = now
            except Exception as e:
                print(f"Failed to refresh market {market_id}: {e}")
        
        # Add new sports markets
        try:
            sports_markets = await self.client.get_sports_markets()
            
            for market in sports_markets:
                if market.id not in self._tracked_markets:
                    # Check liquidity
                    if market.liquidity >= self.config.market_maker.min_liquidity:
                        self._tracked_markets[market.id] = market
                        self._last_refresh[market.id] = now
                        print(f"Added market: {market.question[:50]}...")
        except Exception as e:
            print(f"Failed to fetch sports markets: {e}")
    
    async def _update_quotes(self):
        """Update quotes for all tracked markets"""
        for market_id, market in self._tracked_markets.items():
            try:
                # Check risk
                if not await self.risk.check_risk(market):
                    continue
                
                # Get order book
                yes_price, no_price = await self.client.get_market_prices(market_id)
                
                # Calculate quotes
                quotes = self._generate_quotes(market, yes_price, no_price)
                
                # Place quotes
                await self._place_quotes(market_id, quotes)
                
            except Exception as e:
                print(f"Failed to update quotes for {market_id}: {e}")
    
    def _generate_quotes(
        self,
        market: Market,
        yes_price: float,
        no_price: float
    ) -> List[Quote]:
        """Generate quotes around mid-price"""
        quotes = []
        
        spread = self.config.market_maker.spread_pct
        mid = (yes_price + no_price) / 2
        
        # Get outcome IDs
        yes_outcome = None
        no_outcome = None
        
        for o in market.outcomes:
            if o.side.lower() in ["yes", "true"]:
                yes_outcome = o
            elif o.side.lower() in ["no", "false"]:
                no_outcome = o
        
        if not yes_outcome or not no_outcome:
            return quotes
        
        # Calculate position size
        position_size = self.risk.calculate_position_size(market, yes_price)
        
        if position_size < self.config.risk.min_trade_size:
            return quotes
        
        # Check if can open position
        allowed, reason = self.risk.can_open_position(market, position_size)
        if not allowed:
            return quotes
        
        # Generate bid (buy Yes)
        bid_price = max(
            self.config.market_maker.min_price,
            mid - spread / 2
        )
        
        # Generate ask (sell Yes)
        ask_price = min(
            self.config.market_maker.max_price,
            mid + spread / 2
        )
        
        # Add quotes
        quotes.append(Quote(
            market_id=market.id,
            outcome_id=yes_outcome.id,
            side=OrderSide.BUY,
            size=position_size,
            price=bid_price
        ))
        
        quotes.append(Quote(
            market_id=market.id,
            outcome_id=yes_outcome.id,
            side=OrderSide.SELL,
            size=position_size,
            price=ask_price
        ))
        
        return quotes
    
    async def _place_quotes(self, market_id: str, quotes: List[Quote]):
        """Place quotes on market"""
        # Get existing quotes for market
        existing = self._active_quotes.get(market_id, [])
        
        # Cancel old quotes
        for quote in existing:
            if quote.timestamp < time.time() - self._refresh_interval * 2:
                try:
                    if quote.timestamp > 0:
                        order_id = self._order_map.get(f"{market_id}_{quote.side.value}")
                        if order_id:
                            await self.client.cancel_order(order_id)
                except Exception as e:
                    print(f"Failed to cancel quote: {e}")
        
        # Place new quotes
        self._active_quotes[market_id] = []
        
        for quote in quotes:
            try:
                # Check risk again
                if not await self.risk.check_risk():
                    break
                
                # Place order
                order = await self.client.place_limit_order(
                    market_id=quote.market_id,
                    outcome_id=quote.outcome_id,
                    side=quote.side,
                    size=quote.size,
                    price=quote.price
                )
                
                # Track
                quote.timestamp = time.time()
                self._active_quotes[market_id].append(quote)
                
                if order.id:
                    self._order_map[f"{market_id}_{quote.side.value}"] = order.id
                    
                # Update risk
                self.risk.update_order(order)
                
                print(f"📝 Placed {quote.side.value} quote: {quote.size} @ ${quote.price:.2f}")
                
            except Exception as e:
                print(f"Failed to place quote: {e}")
    
    async def _reconcile_orders(self):
        """Reconcile open orders"""
        try:
            # Get open orders
            open_orders = await self.client.get_open_orders()
            
            # Cancel stale orders
            for order in open_orders:
                age = (datetime.utcnow() - order.created_at).total_seconds()
                
                if age > 300:  # 5 minutes
                    await self.client.cancel_order(order.id)
                    self.risk.remove_order(order.id)
                    print(f"❌ Cancelled stale order: {order.id}")
                    
        except Exception as e:
            print(f"Failed to reconcile orders: {e}")
    
    async def _cancel_all_quotes(self):
        """Cancel all active quotes"""
        for market_id, quotes in self._active_quotes.items():
            for quote in quotes:
                try:
                    order_id = self._order_map.get(f"{market_id}_{quote.side.value}")
                    if order_id:
                        await self.client.cancel_order(order_id)
                except Exception as e:
                    print(f"Failed to cancel quote: {e}")
        
        self._active_quotes.clear()
        self._order_map.clear()
    
    # ==================== Manual Trading ====================
    
    async def place_market_order(
        self,
        market_id: str,
        side: OrderSide,
        size: float,
        outcome_id: Optional[str] = None
    ) -> Trade:
        """Place a market order"""
        # Get market
        market = self._tracked_markets.get(market_id)
        if not market:
            market = await self.client.get_market(market_id)
        
        # Get outcome
        if not outcome_id:
            for o in market.outcomes:
                if o.side.lower() == "yes":
                    outcome_id = o.id
                    break
        
        # Check risk
        allowed, reason = self.risk.can_open_position(market, size)
        if not allowed:
            raise Exception(f"Risk check failed: {reason}")
        
        # Place order
        order = await self.client.place_market_order(
            market_id=market_id,
            outcome_id=outcome_id,
            side=side,
            size=size
        )
        
        # Record trade
        trade = Trade(
            market_id=market_id,
            market_question=market.question,
            outcome=outcome_id or "",
            side=side,
            size=size,
            price=order.average_fill_price or market.yes_price,
            value=size * (order.average_fill_price or market.yes_price),
            order_id=order.id
        )
        
        self.risk.record_trade(trade)
        
        return trade
    
    # ==================== Status ====================
    
    def get_status(self) -> Dict:
        """Get market maker status"""
        return {
            "running": self._running,
            "tracked_markets": len(self._tracked_markets),
            "active_quotes": sum(len(q) for q in self._active_quotes.values()),
            "markets": [
                {
                    "id": m.id,
                    "question": m.question[:50],
                    "yes_price": m.yes_price,
                    "no_price": m.no_price,
                    "volume": m.volume
                }
                for m in list(self._tracked_markets.values())[:5]
            ]
        }
    
    def get_tracked_markets(self) -> List[Market]:
        """Get list of tracked markets"""
        return list(self._tracked_markets.values())