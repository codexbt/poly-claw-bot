# ============================================================
#  Data Models - SwissTony Bot
#  Market, Order, and Event data structures
# ============================================================

from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
import json


class OrderSide(str, Enum):
    """Order side"""
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    """Order type"""
    LIMIT = "LIMIT"
    MARKET = "MARKET"
    GTC = "GTC"  # Good Till Cancel
    FOK = "FOK"  # Fill Or Kill
    IOC = "IOC"  # Immediate Or Cancel


class OrderStatus(str, Enum):
    """Order status"""
    PENDING = "PENDING"
    OPEN = "OPEN"
    FILLED = "FILLED"
    PARTIAL = "PARTIAL"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"


class MarketStatus(str, Enum):
    """Market status"""
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"
    RESOLVED = "RESOLVED"
    PENDING = "PENDING"


class Outcome(BaseModel):
    """Single outcome in a market"""
    id: str = Field(description="Token ID")
    token: str = Field(description="Token address")
    price: float = Field(description="Current price (0-1)")
    volume: float = Field(description="Trading volume")
    liquidity: float = Field(description="Liquidity")
    side: str = Field(description="Outcome name (Yes/No/Over/Under)")


class Market(BaseModel):
    """Prediction market"""
    id: str = Field(description="Market ID")
    question: str = Field(description="Market question")
    description: Optional[str] = Field(default=None, description="Market description")
    outcomes: List[Outcome] = Field(default_factory=list, description="Available outcomes")
    status: MarketStatus = Field(default=MarketStatus.ACTIVE)
    volume: float = Field(default=0.0, description="Total volume")
    liquidity: float = Field(default=0.0, description="Total liquidity")
    ends_at: Optional[datetime] = Field(default=None, description="Market end time")
    created_at: Optional[datetime] = Field(default=None)
    updated_at: Optional[datetime] = Field(default=None)
    
    # Computed properties
    @property
    def yes_price(self) -> float:
        """Get Yes outcome price"""
        for o in self.outcomes:
            if o.side.lower() in ["yes", "true", "over"]:
                return o.price
        return 0.5
    
    @property
    def no_price(self) -> float:
        """Get No outcome price"""
        for o in self.outcomes:
            if o.side.lower() in ["no", "false", "under"]:
                return o.price
        return 0.5
    
    @property
    def mid_price(self) -> float:
        """Get mid price"""
        return (self.yes_price + self.no_price) / 2
    
    @property
    def spread(self) -> float:
        """Get bid-ask spread"""
        return abs(self.yes_price - self.no_price)
    
    @property
    def is_liquid(self) -> bool:
        """Check if market is liquid enough"""
        return self.liquidity >= 1000.0
    
    @property
    def is_active(self) -> bool:
        """Check if market is active"""
        return self.status == MarketStatus.ACTIVE
    
    def to_dict(self) -> dict:
        """Convert to dictionary"""
        return {
            "id": self.id,
            "question": self.question,
            "yes_price": self.yes_price,
            "no_price": self.no_price,
            "mid_price": self.mid_price,
            "spread": self.spread,
            "volume": self.volume,
            "liquidity": self.liquidity,
            "status": self.status.value,
            "ends_at": self.ends_at.isoformat() if self.ends_at else None
        }


class Order(BaseModel):
    """Trading order"""
    id: Optional[str] = Field(default=None, description="Order ID")
    market_id: str = Field(description="Market ID")
    outcome_id: str = Field(description="Outcome/Token ID")
    side: OrderSide = Field(description="Buy or Sell")
    order_type: OrderType = Field(default=OrderType.LIMIT)
    size: float = Field(description="Order size in USD")
    price: float = Field(description="Order price")
    filled_size: float = Field(default=0.0, description="Filled amount")
    average_fill_price: float = Field(default=0.0, description="Average fill price")
    status: OrderStatus = Field(default=OrderStatus.PENDING)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: Optional[datetime] = Field(default=None)
    error_message: Optional[str] = Field(default=None)
    
    @property
    def remaining_size(self) -> float:
        """Get remaining size to fill"""
        return self.size - self.filled_size
    
    @property
    def fill_pct(self) -> float:
        """Get fill percentage"""
        if self.size == 0:
            return 0.0
        return self.filled_size / self.size
    
    @property
    def is_complete(self) -> bool:
        """Check if order is complete"""
        return self.status in [OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.EXPIRED]
    
    def to_dict(self) -> dict:
        """Convert to dictionary"""
        return {
            "id": self.id,
            "market_id": self.market_id,
            "outcome_id": self.outcome_id,
            "side": self.side.value,
            "type": self.order_type.value,
            "size": self.size,
            "price": self.price,
            "filled_size": self.filled_size,
            "avg_fill_price": self.average_fill_price,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "fill_pct": self.fill_pct
        }


class Position(BaseModel):
    """User position in a market"""
    market_id: str
    outcome_id: str
    side: OrderSide
    size: float = Field(description="Position size in USD")
    entry_price: float = Field(description="Average entry price")
    current_price: float = Field(description="Current market price")
    realized_pnl: float = Field(default=0.0, description="Realized P&L")
    unrealized_pnl: float = Field(default=0.0, description="Unrealized P&L")
    
    @property
    def value(self) -> float:
        """Current position value"""
        return self.size * self.current_price
    
    @property
    def cost(self) -> float:
        """Total cost"""
        return self.size * self.entry_price
    
    @property
    def pnl_pct(self) -> float:
        """P&L percentage"""
        if self.cost == 0:
            return 0.0
        return (self.unrealized_pnl + self.realized_pnl) / self.cost * 100
    
    def to_dict(self) -> dict:
        return {
            "market_id": self.market_id,
            "outcome_id": self.outcome_id,
            "side": self.side.value,
            "size": self.size,
            "entry_price": self.entry_price,
            "current_price": self.current_price,
            "realized_pnl": self.realized_pnl,
            "unrealized_pnl": self.unrealized_pnl,
            "value": self.value,
            "pnl_pct": self.pnl_pct
        }


class Event(BaseModel):
    """Sports event for reality arbitrage"""
    id: str = Field(description="Event ID")
    sport: str = Field(description="Sport type (tennis, basketball, etc.)")
    match_id: str = Field(description="Match ID")
    home_team: str = Field(description="Home team")
    away_team: str = Field(description="Away team")
    home_score: int = Field(default=0)
    away_score: int = Field(default=0)
    status: str = Field(default="live", description="Event status")
    start_time: Optional[datetime] = Field(default=None)
    end_time: Optional[datetime] = Field(default=None)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    
    @property
    def is_final(self) -> bool:
        """Check if event is final"""
        return self.status in ["final", "completed", "ended"]
    
    @property
    def winner(self) -> Optional[str]:
        """Get winner if final"""
        if not self.is_final:
            return None
        if self.home_score > self.away_score:
            return self.home_team
        elif self.away_score > self.home_score:
            return self.away_team
        return None
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "sport": self.sport,
            "match_id": self.match_id,
            "home_team": self.home_team,
            "away_team": self.away_team,
            "home_score": self.home_score,
            "away_score": self.away_score,
            "status": self.status,
            "winner": self.winner,
            "is_final": self.is_final
        }


class Trade(BaseModel):
    """Executed trade record"""
    id: str = Field(default_factory=lambda: datetime.utcnow().strftime("%Y%m%d%H%M%S%f"))
    market_id: str
    market_question: str
    outcome: str
    side: OrderSide
    size: float
    price: float
    value: float
    fees: float = Field(default=0.0)
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    order_id: Optional[str] = None
    tx_hash: Optional[str] = None
    notes: Optional[str] = None
    
    @property
    def pnl(self) -> float:
        """Calculate P&L"""
        return self.value * self.price - self.size
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "market_id": self.market_id,
            "market_question": self.market_question,
            "outcome": self.outcome,
            "side": self.side.value,
            "size": self.size,
            "price": self.price,
            "value": self.value,
            "fees": self.fees,
            "timestamp": self.timestamp.isoformat(),
            "pnl": self.pnl
        }


class BotState(BaseModel):
    """Bot runtime state"""
    # Balance
    starting_balance: float = 0.0
    current_balance: float = 0.0
    
    # P&L
    daily_pnl: float = 0.0
    total_pnl: float = 0.0
    
    # Counts
    trades_today: int = 0
    wins_today: int = 0
    losses_today: int = 0
    loss_streak: int = 0
    
    # Status
    is_paused: bool = False
    pause_reason: Optional[str] = None
    pause_until: Optional[datetime] = None
    
    # Risk
    daily_loss_pct: float = 0.0
    volatility_triggered: bool = False
    
    # Positions
    positions: Dict[str, Position] = Field(default_factory=dict)
    open_orders: Dict[str, Order] = Field(default_factory=dict)
    
    def to_dict(self) -> dict:
        return {
            "balance": self.current_balance,
            "daily_pnl": self.daily_pnl,
            "total_pnl": self.total_pnl,
            "trades_today": self.trades_today,
            "wins_today": self.wins_today,
            "losses_today": self.losses_today,
            "loss_streak": self.loss_streak,
            "is_paused": self.is_paused,
            "pause_reason": self.pause_reason,
            "daily_loss_pct": self.daily_loss_pct,
            "positions_count": len(self.positions),
            "open_orders_count": len(self.open_orders)
        }


class MarketSnapshot(BaseModel):
    """Snapshot of market data for analysis"""
    market_id: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    yes_price: float
    no_price: float
    volume: float
    liquidity: float
    yes_change_1m: float = Field(default=0.0, description="Price change 1min")
    yes_change_5m: float = Field(default=0.0, description="Price change 5min")
    yes_change_15m: float = Field(default=0.0, description="Price change 15min")
    
    @property
    def momentum(self) -> float:
        """Price momentum"""
        return self.yes_change_1m
    
    @property
    def volatility(self) -> float:
        """Price volatility"""
        return abs(self.yes_change_5m - self.yes_change_1m)


# Utility functions
def load_markets_from_json(filepath: str) -> List[Market]:
    """Load markets from JSON file"""
    with open(filepath, 'r') as f:
        data = json.load(f)
    return [Market(**m) for m in data]


def save_markets_to_json(markets: List[Market], filepath: str):
    """Save markets to JSON file"""
    with open(filepath, 'w') as f:
        json.dump([m.to_dict() for m in markets], f, indent=2)


def load_trades_from_json(filepath: str) -> List[Trade]:
    """Load trades from JSON file"""
    try:
        with open(filepath, 'r') as f:
            data = json.load(f)
        return [Trade(**t) for t in data]
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_trades_to_json(trades: List[Trade], filepath: str):
    """Save trades to JSON file"""
    with open(filepath, 'w') as f:
        json.dump([t.to_dict() for t in trades], f, indent=2)