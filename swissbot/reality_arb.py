# ============================================================
#  Reality Arbitrage Module - SwissTony Bot
#  Alpha layer: captures value from real-time sports data
# ============================================================

import asyncio
import random
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set, Tuple
from dataclasses import dataclass
from enum import Enum

from .config import get_config, BotConfig
from .client import PolymarketClient
from .models import (
    Market, Event, OrderSide, Trade
)
from .risk import RiskManager


class FeedStatus(str, Enum):
    """Sports feed status"""
    CONNECTED = "CONNECTED"
    DISCONNECTED = "DISCONNECTED"
    ERROR = "ERROR"


@dataclass
class ArbSignal:
    """Arbitrage signal"""
    event: Event
    market: Market
    outcome: str  # "Yes" or "No"
    entry_price: float
    expected_exit: float
    size: float
    confidence: float
    timestamp: float


class SportsDataFeed:
    """
    Simulated sports data feed
    In production, replace with real WebSocket feed
    """
    
    def __init__(self):
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._subscribers: List[asyncio.Queue] = []
        
        # Simulated events
        self._active_events: Dict[str, Event] = {}
        
    async def connect(self):
        """Connect to feed"""
        self._running = True
        self._task = asyncio.create_task(self._generate_events())
        print("📡 Sports feed connected")
    
    async def disconnect(self):
        """Disconnect from feed"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        print("📡 Sports feed disconnected")
    
    async def subscribe(self) -> asyncio.Queue:
        """Subscribe to event stream"""
        queue = asyncio.Queue()
        self._subscribers.append(queue)
        return queue
    
    async def unsubscribe(self, queue: asyncio.Queue):
        """Unsubscribe from event stream"""
        if queue in self._subscribers:
            self._subscribers.remove(queue)
    
    async def _generate_events(self):
        """Generate simulated sports events"""
        # Sample events for simulation
        sample_events = [
            Event(
                id="event_1",
                sport="tennis",
                match_id="match_001",
                home_team="Jannik Sinner",
                away_team="Arthur Fils",
                home_score=2,
                away_score=1,
                status="live"
            ),
            Event(
                id="event_2",
                sport="basketball",
                match_id="match_002",
                home_team="Lakers",
                away_team="Celtics",
                home_score=98,
                away_score=102,
                status="live"
            ),
            Event(
                id="event_3",
                sport="soccer",
                match_id="match_003",
                home_team="Real Madrid",
                away_team="Barcelona",
                home_score=2,
                away_score=2,
                status="live"
            ),
        ]
        
        while self._running:
            # Randomly update events
            for event in sample_events:
                # Simulate score changes
                if random.random() < 0.1:  # 10% chance of score change
                    if random.random() < 0.5:
                        event.home_score += 1
                    else:
                        event.away_score += 1
                    
                    # Check for final
                    if event.home_score >= 4 or event.away_score >= 4:
                        event.status = "final"
                        event.end_time = datetime.utcnow()
                    
                    # Broadcast to subscribers
                    await self._broadcast(event)
            
            await asyncio.sleep(5)  # Update every 5 seconds
    
    async def _broadcast(self, event: Event):
        """Broadcast event to subscribers"""
        for queue in self._subscribers:
            try:
                await queue.put(event)
            except Exception:
                pass
    
    async def get_active_events(self) -> List[Event]:
        """Get all active events"""
        return list(self._active_events.values())
    
    async def find_event_for_market(
        self,
        market: Market
    ) -> Optional[Event]:
        """Find matching sports event for market"""
        # Simple matching based on team names
        question = market.question.lower()
        
        for event in self._active_events.values():
            home = event.home_team.lower()
            away = event.away_team.lower()
            
            if home in question or away in question:
                return event
        
        return None


class RealityArb:
    """
    Reality Arbitrage Module
    
    Strategy:
    1. Connect to live sports data feed
    2. On game-ending event, check Polymarket for binary market
    3. If outcome confirmed, buy at price < 90 cents
    4. Wait 20-40 seconds for TV-delayed bettors
    5. Sell for profit near $1.0
    6. Fail-safe: liquidate at market if no movement in 2 minutes
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
        
        # Feed
        self._feed = SportsDataFeed()
        self._feed_queue: Optional[asyncio.Queue] = None
        
        # Tracking
        self._pending_arbs: Dict[str, ArbSignal] = {}  # market_id -> signal
        self._active_positions: Dict[str, Tuple[Trade, float]] = {}  # market_id -> (trade, entry_time)
        
        # Config
        self._max_entry_price = self.config.reality_arb.max_entry_price
        self._wait_time_min = self.config.reality_arb.wait_time_min
        self._wait_time_max = self.config.reality_arb.wait_time_max
        self._exit_timeout = self.config.reality_arb.exit_timeout
        
    @property
    def is_running(self) -> bool:
        return self._running
    
    async def start(self):
        """Start reality arbitrage"""
        if self._running:
            return
        
        if not self.config.reality_arb.enabled:
            print("⚠️ Reality Arb disabled in config")
            return
        
        self._running = True
        
        # Connect to feed
        await self._feed.connect()
        self._feed_queue = await self._feed.subscribe()
        
        # Start main loop
        self._task = asyncio.create_task(self._run_loop())
        print("🎯 RealityArb started")
    
    async def stop(self):
        """Stop reality arbitrage"""
        self._running = False
        
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        
        # Disconnect feed
        await self._feed.disconnect()
        
        # Close all positions
        await self._close_all_positions()
        
        print("🎯 RealityArb stopped")
    
    async def _run_loop(self):
        """Main arbitrage loop"""
        while self._running:
            try:
                # Process feed events
                await self._process_feed_events()
                
                # Check pending arbs
                await self._check_pending_arbs()
                
                # Wait
                await asyncio.sleep(1)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"RealityArb error: {e}")
                await asyncio.sleep(5)
    
    async def _process_feed_events(self):
        """Process incoming sports events"""
        if not self._feed_queue:
            return
        
        # Check for new events (non-blocking)
        while not self._feed_queue.empty():
            try:
                event = self._feed_queue.get_nowait()
                await self._handle_event(event)
            except asyncio.QueueEmpty:
                break
            except Exception as e:
                print(f"Failed to process event: {e}")
    
    async def _handle_event(self, event: Event):
        """Handle sports event"""
        # Check if event is final
        if not event.is_final:
            return
        
        print(f"🏆 Event final: {event.home_team} vs {event.away_team}")
        print(f"   Score: {event.home_score} - {event.away_score}")
        print(f"   Winner: {event.winner}")
        
        # Find matching Polymarket
        markets = await self._find_matching_markets(event)
        
        for market in markets:
            await self._execute_arb(event, market)
    
    async def _find_matching_markets(
        self,
        event: Event
    ) -> List[Market]:
        """Find Polymarket markets for event"""
        markets = []
        
        # Search by team names
        try:
            results = await self.client.search_markets(
                f"{event.home_team} {event.away_team}",
                limit=5
            )
            
            for m in results:
                if m.status.value == "ACTIVE" and m.liquidity > 1000:
                    markets.append(m)
        except Exception as e:
            print(f"Failed to find markets: {e}")
        
        return markets
    
    async def _execute_arb(
        self,
        event: Event,
        market: Market
    ):
        """Execute arbitrage"""
        # Determine outcome
        winner = event.winner
        if not winner:
            return
        
        # Map winner to outcome
        outcome = None
        for o in market.outcomes:
            if winner.lower() in o.side.lower():
                outcome = o
                break
        
        if not outcome:
            return
        
        # Check entry price
        if outcome.price >= self._max_entry_price:
            print(f"   Price too high: ${outcome.price:.2f}")
            return
        
        # Calculate size
        size = self.risk.calculate_position_size(market, confidence=0.95)
        
        # Check risk
        allowed, reason = self.risk.can_open_position(market, size)
        if not allowed:
            print(f"   Risk check failed: {reason}")
            return
        
        # Execute entry
        print(f"   🎯 Entering arb: {outcome.side} @ ${outcome.price:.2f}")
        
        try:
            order = await self.client.place_market_order(
                market_id=market.id,
                outcome_id=outcome.id,
                side=OrderSide.BUY,
                size=size
            )
            
            # Record trade
            trade = Trade(
                market_id=market.id,
                market_question=market.question,
                outcome=outcome.side,
                side=OrderSide.BUY,
                size=size,
                price=outcome.price,
                value=size * outcome.price,
                notes=f"RealityArb entry: {event.id}"
            )
            
            self.risk.record_trade(trade)
            
            # Track position
            self._active_positions[market.id] = (
                trade,
                time.time()
            )
            
            print(f"   ✅ Entered: {size} shares @ ${outcome.price:.2f}")
            
            # Schedule exit
            asyncio.create_task(self._schedule_exit(market.id, trade))
            
        except Exception as e:
            print(f"   ❌ Entry failed: {e}")
    
    async def _schedule_exit(self, market_id: str, trade: Trade):
        """Schedule exit after wait time"""
        # Random wait time
        wait_time = random.randint(
            self._wait_time_min,
            self._wait_time_max
        )
        
        print(f"   ⏳ Waiting {wait_time}s for exit...")
        
        await asyncio.sleep(wait_time)
        
        # Execute exit
        await self._execute_exit(market_id, trade)
    
    async def _execute_exit(
        self,
        market_id: str,
        trade: Trade
    ):
        """Execute exit"""
        try:
            # Get current price
            market = await self.client.get_market(market_id)
            if not market:
                return
            
            # Find the outcome we hold
            outcome_id = None
            for o in market.outcomes:
                if o.side.lower() == trade.outcome.lower():
                    outcome_id = o.id
                    break
            
            if not outcome_id:
                return
            
            # Sell at market
            print(f"   📤 Exiting: {trade.size} shares")
            
            order = await self.client.place_market_order(
                market_id=market_id,
                outcome_id=outcome_id,
                side=OrderSide.SELL,
                size=trade.size
            )
            
            # Calculate P&L
            exit_price = order.average_fill_price or market.yes_price
            pnl = trade.size * (exit_price - trade.price)
            
            print(f"   ✅ Exited @ ${exit_price:.2f}")
            print(f"   💰 P&L: ${pnl:.2f}")
            
            # Remove from tracking
            if market_id in self._active_positions:
                del self._active_positions[market_id]
            
        except Exception as e:
            print(f"   ❌ Exit failed: {e}")
    
    async def _check_pending_arbs(self):
        """Check pending arbitrages for timeout"""
        now = time.time()
        
        for market_id, (trade, entry_time) in list(self._active_positions.items()):
            # Check timeout
            if now - entry_time > self._exit_timeout:
                print(f"   ⚠️ Timeout: liquidating {market_id}")
                await self._execute_exit(market_id, trade)
    
    async def _close_all_positions(self):
        """Close all active positions"""
        for market_id, (trade, _) in list(self._active_positions.items()):
            try:
                await self._execute_exit(market_id, trade)
            except Exception as e:
                print(f"Failed to close position {market_id}: {e}")
    
    # ==================== Manual Trigger ====================
    
    async def trigger_arb(
        self,
        market_id: str,
        outcome: str,
        size: float
    ) -> Trade:
        """Manually trigger arbitrage"""
        # Get market
        market = await self.client.get_market(market_id)
        if not market:
            raise Exception(f"Market not found: {market_id}")
        
        # Find outcome
        outcome_obj = None
        for o in market.outcomes:
            if o.side.lower() == outcome.lower():
                outcome_obj = o
                break
        
        if not outcome_obj:
            raise Exception(f"Outcome not found: {outcome}")
        
        # Check price
        if outcome_obj.price >= self._max_entry_price:
            raise Exception(f"Price too high: {outcome_obj.price}")
        
        # Check risk
        allowed, reason = self.risk.can_open_position(market, size)
        if not allowed:
            raise Exception(f"Risk check failed: {reason}")
        
        # Execute
        order = await self.client.place_market_order(
            market_id=market_id,
            outcome_id=outcome_obj.id,
            side=OrderSide.BUY,
            size=size
        )
        
        trade = Trade(
            market_id=market_id,
            market_question=market.question,
            outcome=outcome,
            side=OrderSide.BUY,
            size=size,
            price=outcome_obj.price,
            value=size * outcome_obj.price,
            notes="Manual arb trigger"
        )
        
        self.risk.record_trade(trade)
        
        # Track
        self._active_positions[market_id] = (trade, time.time())
        
        # Schedule exit
        asyncio.create_task(self._schedule_exit(market_id, trade))
        
        return trade
    
    # ==================== Status ====================
    
    def get_status(self) -> Dict:
        """Get reality arb status"""
        return {
            "running": self._running,
            "feed_connected": self._feed._running,
            "pending_arbs": len(self._pending_arbs),
            "active_positions": len(self._active_positions),
            "positions": [
                {
                    "market_id": mid,
                    "size": t.size,
                    "entry_price": t.price,
                    "entry_time": et
                }
                for mid, (t, et) in self._active_positions.items()
            ]
        }