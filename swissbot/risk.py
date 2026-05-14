# ============================================================
#  Risk Manager - SwissTony Bot
#  3-layer risk system for protection
# ============================================================

import asyncio
import time
from datetime import datetime, timedelta
from typing import Optional, Dict, List
from collections import deque
from dataclasses import dataclass, field

from .config import get_config, BotConfig
from .models import BotState, Order, Trade, Market


@dataclass
class RiskMetrics:
    """Risk metrics snapshot"""
    timestamp: float = field(default_factory=time.time)
    daily_pnl: float = 0.0
    daily_pnl_pct: float = 0.0
    hourly_trades: int = 0
    loss_streak: int = 0
    max_drawdown: float = 0.0
    win_rate: float = 0.0
    avg_trade_pnl: float = 0.0
    volatility: float = 0.0


class RiskManager:
    """
    Central risk management system
    3-layer protection:
    1. Global stop-loss (pause if daily loss > 5%)
    2. Volatility freeze (suspend if price moves > 3% in 1 min)
    3. Sleep mode (no orders for 5 min after 3 loss streak)
    """
    
    def __init__(self, config: Optional[BotConfig] = None):
        self.config = config or get_config()
        self.state = BotState()
        
        # Initialize state
        self.state.starting_balance = self.config.trading.starting_balance
        self.state.current_balance = self.config.trading.starting_balance
        
        # Risk tracking
        self._trade_history: deque = deque(maxlen=1000)
        self._price_history: Dict[str, deque] = {}
        self._hourly_trades: deque = deque(maxlen=60)  # Last 60 minutes
        self._last_check = time.time()
        
        # Pause tracking
        self._pause_until: Optional[datetime] = None
        self._pause_reason: str = ""
        
        # Risk counters
        self._consecutive_losses = 0
        self._total_wins = 0
        self._total_losses = 0
        
    @property
    def is_paused(self) -> bool:
        """Check if bot is paused"""
        if self._pause_until and datetime.utcnow() < self._pause_until:
            return True
        return self.state.is_paused
    
    @property
    def pause_reason(self) -> str:
        """Get pause reason"""
        if self._pause_until and datetime.utcnow() < self._pause_until:
            return f"Sleep mode until {self._pause_until.isoformat()}"
        return self._pause_reason
    
    # ==================== Risk Checks ====================
    
    async def check_risk(self, market: Optional[Market] = None) -> bool:
        """
        Run all risk checks
        Returns True if trading is allowed, False if paused
        """
        # Check pause status
        if self.is_paused:
            return False
        
        # Layer 1: Global stop-loss
        if not self._check_daily_loss():
            return False
        
        # Layer 2: Volatility freeze
        if market and not self._check_volatility(market):
            return False
        
        # Layer 3: Loss streak sleep
        if not self._check_loss_streak():
            return False
        
        # Rate limit check
        if not self._check_rate_limit():
            return False
        
        return True
    
    def _check_daily_loss(self) -> bool:
        """Layer 1: Check daily loss limit"""
        daily_loss_pct = self.config.risk.daily_loss_limit_pct
        
        if self.state.daily_pnl < 0:
            loss_pct = abs(self.state.daily_pnl) / self.state.starting_balance
            
            if loss_pct >= daily_loss_pct:
                self._pause_bot(
                    f"Daily loss limit reached: {loss_pct:.2%}",
                    duration_minutes=60
                )
                return False
                
        return True
    
    def _check_volatility(self, market: Market) -> bool:
        """Layer 2: Check price volatility"""
        if market.id not in self._price_history:
            self._price_history[market.id] = deque(maxlen=60)
        
        history = self._price_history[market.id]
        current_price = market.yes_price
        
        # Add current price
        history.append({
            "timestamp": time.time(),
            "price": current_price
        })
        
        # Check 1-minute change
        if len(history) >= 2:
            oldest = list(history)[0]
            newest = list(history)[-1]
            
            time_diff = newest["timestamp"] - oldest["timestamp"]
            if time_diff >= 60:  # At least 1 minute
                price_change = abs(newest["price"] - oldest["price"]) / oldest["price"]
                
                if price_change > self.config.risk.volatility_freeze_pct:
                    self.state.volatility_triggered = True
                    self._pause_bot(
                        f"Volatility freeze: {price_change:.2%} move in 1 min",
                        duration_minutes=10
                    )
                    return False
                    
        return True
    
    def _check_loss_streak(self) -> bool:
        """Layer 3: Check loss streak"""
        max_streak = self.config.risk.loss_streak_sleep
        sleep_minutes = self.config.risk.sleep_duration_minutes
        
        if self._consecutive_losses >= max_streak:
            self._pause_bot(
                f"Loss streak: {_consecutive_losses} consecutive losses",
                duration_minutes=sleep_minutes
            )
            return False
            
        return True
    
    def _check_rate_limit(self) -> bool:
        """Check hourly trade rate"""
        now = time.time()
        
        # Clean old trades from hourly tracking
        while self._hourly_trades and now - self._hourly_trades[0] > 3600:
            self._hourly_trades.popleft()
        
        hourly_limit = self.config.risk.max_trades_per_hour
        if len(self._hourly_trades) >= hourly_limit:
            return False
            
        return True
    
    # ==================== Position Sizing ====================
    
    def calculate_position_size(
        self,
        market: Market,
        confidence: float = 0.5
    ) -> float:
        """
        Calculate position size based on Kelly Criterion
        """
        # Get config
        kelly = self.config.trading.kelly_fraction
        max_size = self.config.risk.max_trade_size
        min_size = self.config.risk.min_trade_size
        default_size = self.config.risk.default_trade_size
        
        # Get current balance
        balance = self.state.current_balance
        
        # Calculate Kelly size
        # Kelly % = (bp - q) / b where b = odds - 1, p = win probability, q = loss probability
        # Simplified: use confidence as probability
        win_prob = confidence
        loss_prob = 1 - confidence
        
        # Expected value
        avg_win = default_size * 0.2  # Assume 20% avg win
        avg_loss = default_size * 0.1  # Assume 10% avg loss
        
        if avg_loss > 0:
            kelly_pct = (avg_win / avg_loss * win_prob - loss_prob) * kelly
            kelly_pct = max(0, min(kelly_pct, 0.25))  # Cap at 25%
        else:
            kelly_pct = 0
        
        # Calculate size
        size = balance * kelly_pct
        
        # Apply limits
        size = max(min_size, min(size, max_size))
        
        # Check max position % per market
        max_position_pct = self.config.market_maker.max_position_pct
        max_position_value = balance * max_position_pct
        size = min(size, max_position_value)
        
        return round(size, 2)
    
    def can_open_position(
        self,
        market: Market,
        proposed_size: float
    ) -> tuple[bool, str]:
        """
        Check if can open new position
        Returns (allowed, reason)
        """
        # Check if paused
        if self.is_paused:
            return False, f"Bot paused: {self.pause_reason}"
        
        # Check balance
        if proposed_size > self.state.current_balance:
            return False, "Insufficient balance"
        
        # Check max position per market
        max_pct = self.config.market_maker.max_position_pct
        max_value = self.state.starting_balance * max_pct
        
        if market.id in self.state.positions:
            existing = self.state.positions[market.id]
            if existing.size + proposed_size > max_value:
                return False, f"Max position size for {market.id} reached"
        
        # Check total exposure
        total_exposure = sum(
            p.size for p in self.state.positions.values()
        )
        max_total = self.state.starting_balance * self.config.market_maker.max_total_exposure_pct
        
        if total_exposure + proposed_size > max_total:
            return False, "Max total exposure reached"
        
        # Check price range
        if market.yes_price < self.config.trading.price_min:
            return False, f"Price too low: {market.yes_price:.2f}"
        
        if market.yes_price > self.config.trading.price_max:
            return False, f"Price too high: {market.yes_price:.2f}"
        
        return True, "OK"
    
    # ==================== Trade Recording ====================
    
    def record_trade(self, trade: Trade):
        """Record executed trade for risk tracking"""
        self._trade_history.append(trade)
        self._hourly_trades.append(time.time())
        
        # Update P&L
        self.state.current_balance += trade.value * trade.price - trade.size
        self.state.daily_pnl += trade.value * trade.price - trade.size
        self.state.total_pnl += trade.value * trade.price - trade.size
        self.state.trades_today += 1
        
        # Update win/loss
        pnl = trade.value * trade.price - trade.size
        if pnl > 0:
            self.state.wins_today += 1
            self._total_wins += 1
            self._consecutive_losses = 0
        else:
            self.state.losses_today += 1
            self._total_losses += 1
            self._consecutive_losses += 1
        
        # Check for daily reset
        self._check_daily_reset()
    
    def _check_daily_reset(self):
        """Reset daily counters if new day"""
        # Simple reset based on time - in production use proper date check
        now = datetime.utcnow()
        if now.hour == 0 and now.minute == 0:
            self.state.daily_pnl = 0
            self.state.trades_today = 0
            self.state.wins_today = 0
            self.state.losses_today = 0
    
    # ==================== Pause Management ====================
    
    def _pause_bot(self, reason: str, duration_minutes: int = 5):
        """Pause the bot"""
        self.state.is_paused = True
        self.state.pause_reason = reason
        self._pause_reason = reason
        self._pause_until = datetime.utcnow() + timedelta(minutes=duration_minutes)
        self.state.pause_until = self._pause_until
        
        print(f"⚠️ RISK PAUSE: {reason}")
        print(f"   Paused for {duration_minutes} minutes")
    
    def resume_bot(self):
        """Resume the bot"""
        self.state.is_paused = False
        self.state.pause_reason = None
        self._pause_until = None
        self.state.pause_until = None
        self.state.volatility_triggered = False
        
        print("✅ Bot resumed")
    
    # ==================== Metrics ====================
    
    def get_metrics(self) -> RiskMetrics:
        """Get current risk metrics"""
        metrics = RiskMetrics()
        
        metrics.daily_pnl = self.state.daily_pnl
        metrics.daily_pnl_pct = self.state.daily_pnl / self.state.starting_balance if self.state.starting_balance > 0 else 0
        metrics.hourly_trades = len(self._hourly_trades)
        metrics.loss_streak = self._consecutive_losses
        
        # Win rate
        total = self._total_wins + self._total_losses
        metrics.win_rate = self._total_wins / total if total > 0 else 0
        
        # Avg trade P&L
        if self._trade_history:
            pnls = [t.value * t.price - t.size for t in self._trade_history]
            metrics.avg_trade_pnl = sum(pnls) / len(pnls) if pnls else 0
        
        return metrics
    
    def get_state(self) -> BotState:
        """Get current bot state"""
        return self.state
    
    def get_status(self) -> Dict:
        """Get status summary"""
        return {
            "paused": self.is_paused,
            "pause_reason": self.pause_reason,
            "balance": self.state.current_balance,
            "daily_pnl": self.state.daily_pnl,
            "total_pnl": self.state.total_pnl,
            "trades_today": self.state.trades_today,
            "wins_today": self.state.wins_today,
            "losses_today": self.state.losses_today,
            "loss_streak": self._consecutive_losses,
            "open_positions": len(self.state.positions),
            "open_orders": len(self.state.open_orders)
        }
    
    # ==================== Position Management ====================
    
    def update_position(
        self,
        market_id: str,
        outcome_id: str,
        size: float,
        price: float,
        side: str
    ):
        """Update position tracking"""
        from .models import Position, OrderSide
        
        if market_id in self.state.positions:
            pos = self.state.positions[market_id]
            # Update existing
            new_size = pos.size + size
            new_avg = (pos.size * pos.entry_price + size * price) / new_size if new_size > 0 else 0
            pos.size = new_size
            pos.entry_price = new_avg
            pos.current_price = price
        else:
            # Create new
            self.state.positions[market_id] = Position(
                market_id=market_id,
                outcome_id=outcome_id,
                side=OrderSide.BUY if side == "BUY" else OrderSide.SELL,
                size=size,
                entry_price=price,
                current_price=price
            )
    
    def remove_position(self, market_id: str):
        """Remove position"""
        if market_id in self.state.positions:
            del self.state.positions[market_id]
    
    def update_order(self, order: Order):
        """Track open order"""
        if order.id:
            self.state.open_orders[order.id] = order
    
    def remove_order(self, order_id: str):
        """Remove order from tracking"""
        if order_id in self.state.open_orders:
            del self.state.open_orders[order_id]
    
    # ==================== Reset ====================
    
    def reset_daily(self):
        """Reset daily counters"""
        self.state.daily_pnl = 0
        self.state.trades_today = 0
        self.state.wins_today = 0
        self.state.losses_today = 0
        self._consecutive_losses = 0
        
    def reset_all(self):
        """Reset all state"""
        self.state = BotState()
        self.state.starting_balance = self.config.trading.starting_balance
        self.state.current_balance = self.config.trading.starting_balance
        self._trade_history.clear()
        self._hourly_trades.clear()
        self._price_history.clear()
        self._consecutive_losses = 0
        self._total_wins = 0
        self._total_losses = 0
        self.resume_bot()