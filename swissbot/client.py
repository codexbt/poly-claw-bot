# ============================================================
#  Polymarket Client - SwissTony Bot
#  Wrapper around py-clob-client for API interactions
# ============================================================

import asyncio
import hashlib
import hmac
import time
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Tuple
from urllib.parse import urljoin

import httpx
from eth_account import Account
from eth_account.signers.local import LocalAccount
from web3 import Web3

from .config import get_config, BotConfig
from .models import (
    Market, Outcome, Order, OrderSide, OrderType, OrderStatus,
    MarketStatus, Position, Trade, BotState
)


class PolymarketClient:
    """
    Polymarket CLOB API Client
    Handles authentication, order creation, cancels, and market data
    """
    
    def __init__(self, config: Optional[BotConfig] = None):
        self.config = config or get_config()
        self._client: Optional[httpx.AsyncClient] = None
        self._account: Optional[LocalAccount] = None
        self._wallet_address: str = ""
        self._headers: Dict[str, str] = {}
        
        # Rate limiting
        self._rate_limit_delay = 0.1  # seconds between requests
        self._last_request_time = 0.0
        
        # Cache
        self._markets_cache: Dict[str, Market] = {}
        self._cache_ttl = 5  # seconds
        
    async def __aenter__(self):
        await self.connect()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.disconnect()
    
    @property
    def is_connected(self) -> bool:
        return self._client is not None and self._client.is_closed == False
    
    async def connect(self):
        """Initialize connection and authenticate"""
        if self._client is not None:
            return
            
        # Create HTTP client
        self._client = httpx.AsyncClient(
            base_url=self.config.polymarket.host,
            timeout=30.0,
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=100)
        )
        
        # Initialize wallet
        self._account = Account.from_key(self.config.wallet.private_key)
        self._wallet_address = self._account.address
        
        # Build auth headers
        self._headers = await self._build_headers()
        
        # Verify connection
        await self.get_balance()
        
    async def disconnect(self):
        """Close connection"""
        if self._client:
            await self._client.aclose()
            self._client = None
    
    async def _build_headers(self) -> Dict[str, str]:
        """Build authentication headers for API requests"""
        timestamp = str(int(time.time()))
        message = timestamp + self.config.polymarket.api_key
        
        # Create signature using API secret
        signature = hmac.new(
            self.config.polymarket.api_secret.encode(),
            message.encode(),
            hashlib.sha256
        ).hexdigest()
        
        return {
            "POLY-API-KEY": self.config.polymarket.api_key,
            "POLY-API-TIMESTAMP": timestamp,
            "POLY-API-SIGNATURE": signature,
            "POLY-API-PASSPHRASE": self.config.polymarket.api_passphrase,
            "Content-Type": "application/json"
        }
    
    async def _request(
        self, 
        method: str, 
        endpoint: str, 
        **kwargs
    ) -> Dict[str, Any]:
        """Make authenticated API request"""
        # Rate limiting
        now = time.time()
        elapsed = now - self._last_request_time
        if elapsed < self._rate_limit_delay:
            await asyncio.sleep(self._rate_limit_delay - elapsed)
        
        self._last_request_time = time.time()
        
        url = urljoin(self.config.polymarket.host, endpoint)
        
        try:
            response = await self._client.request(
                method=method,
                url=url,
                headers=self._headers,
                **kwargs
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            error_msg = f"API Error {e.response.status_code}: {e.response.text}"
            raise Exception(error_msg) from e
        except Exception as e:
            raise Exception(f"Request failed: {str(e)}") from e
    
    # ==================== Market Data ====================
    
    async def get_markets(
        self, 
        limit: int = 100,
        active: bool = True,
        category: Optional[str] = None
    ) -> List[Market]:
        """Fetch active markets from API"""
        params = {
            "limit": limit,
            "active": active
        }
        if category:
            params["category"] = category
            
        data = await self._request("GET", "/markets", params=params)
        
        markets = []
        for m in data.get("data", []):
            try:
                market = self._parse_market(m)
                markets.append(market)
            except Exception as e:
                print(f"Failed to parse market: {e}")
                continue
                
        return markets
    
    async def get_market(self, market_id: str) -> Optional[Market]:
        """Fetch specific market by ID"""
        # Check cache
        if market_id in self._markets_cache:
            cached = self._markets_cache[market_id]
            cache_time = getattr(cached, '_cache_time', 0)
            if time.time() - cache_time < self._cache_ttl:
                return cached
        
        data = await self._request("GET", f"/markets/{market_id}")
        market = self._parse_market(data.get("data", {}))
        
        # Update cache
        market._cache_time = time.time()
        self._markets_cache[market_id] = market
        
        return market
    
    async def get_market_order_book(self, market_id: str) -> Dict[str, Any]:
        """Get order book for a market"""
        data = await self._request("GET", f"/markets/{market_id}/orderbook")
        return data.get("data", {})
    
    async def get_market_prices(self, market_id: str) -> Tuple[float, float]:
        """Get current yes/no prices"""
        orderbook = await self.get_market_order_book(market_id)
        
        yes_bids = orderbook.get("yes", {}).get("bids", [])
        yes_asks = orderbook.get("yes", {}).get("asks", [])
        
        no_bids = orderbook.get("no", {}).get("bids", [])
        no_asks = orderbook.get("no", {}).get("asks", [])
        
        # Get best prices
        yes_price = 0.5
        no_price = 0.5
        
        if yes_bids:
            yes_price = float(yes_bids[0].get("price", 0.5))
        if no_bids:
            no_price = float(no_bids[0].get("price", 0.5))
            
        return yes_price, no_price
    
    async def search_markets(
        self, 
        query: str, 
        limit: int = 20
    ) -> List[Market]:
        """Search markets by query"""
        params = {"q": query, "limit": limit}
        data = await self._request("GET", "/markets/search", params=params)
        
        markets = []
        for m in data.get("data", []):
            try:
                market = self._parse_market(m)
                markets.append(market)
            except Exception:
                continue
                
        return markets
    
    async def get_sports_markets(self) -> List[Market]:
        """Get all sports-related markets"""
        categories = ["sports", "tennis", "basketball", "football", "soccer", "baseball"]
        all_markets = []
        
        for cat in categories:
            try:
                markets = await self.get_markets(category=cat, active=True)
                all_markets.extend(markets)
            except Exception as e:
                print(f"Failed to fetch {cat} markets: {e}")
                
        return all_markets
    
    def _parse_market(self, data: Dict[str, Any]) -> Market:
        """Parse market data from API"""
        outcomes = []
        for token in data.get("tokens", []):
            outcome = Outcome(
                id=token.get("token_id", ""),
                token=token.get("address", ""),
                price=float(token.get("price", 0.5)),
                volume=float(token.get("volume", 0)),
                liquidity=float(token.get("liquidity", 0)),
                side=token.get("outcome", "unknown")
            )
            outcomes.append(outcome)
        
        status = MarketStatus.ACTIVE
        if data.get("closed"):
            status = MarketStatus.CLOSED
        elif data.get("resolved"):
            status = MarketStatus.RESOLVED
            
        ends_at = None
        if data.get("endDate"):
            try:
                ends_at = datetime.fromisoformat(data["endDate"].replace("Z", "+00:00"))
            except Exception:
                pass
        
        return Market(
            id=data.get("id", ""),
            question=data.get("question", ""),
            description=data.get("description"),
            outcomes=outcomes,
            status=status,
            volume=float(data.get("volume", 0)),
            liquidity=float(data.get("liquidity", 0)),
            ends_at=ends_at
        )
    
    # ==================== Order Management ====================
    
    async def create_order(
        self,
        market_id: str,
        outcome_id: str,
        side: OrderSide,
        size: float,
        price: float,
        order_type: OrderType = OrderType.LIMIT
    ) -> Order:
        """Create a new order"""
        order_data = {
            "market": market_id,
            "outcome": outcome_id,
            "side": side.value.lower(),
            "size": str(size),
            "price": str(price),
            "type": order_type.value.lower()
        }
        
        if self.config.trading.dry_run:
            # Simulate order creation
            order = Order(
                market_id=market_id,
                outcome_id=outcome_id,
                side=side,
                order_type=order_type,
                size=size,
                price=price,
                status=OrderStatus.FILLED,
                filled_size=size,
                average_fill_price=price
            )
            return order
        
        data = await self._request("POST", "/orders", json=order_data)
        
        order = Order(
            id=data.get("order", {}).get("id"),
            market_id=market_id,
            outcome_id=outcome_id,
            side=side,
            order_type=order_type,
            size=size,
            price=price,
            status=OrderStatus.OPEN
        )
        
        return order
    
    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an order"""
        if self.config.trading.dry_run:
            return True
            
        await self._request("DELETE", f"/orders/{order_id}")
        return True
    
    async def cancel_all_orders(self, market_id: Optional[str] = None) -> int:
        """Cancel all open orders"""
        if self.config.trading.dry_run:
            return 0
            
        if market_id:
            await self._request("DELETE", f"/orders?market={market_id}")
        else:
            await self._request("DELETE", "/orders")
            
        return 0
    
    async def get_orders(
        self, 
        market_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100
    ) -> List[Order]:
        """Get orders"""
        params = {"limit": limit}
        if market_id:
            params["market"] = market_id
        if status:
            params["status"] = status
            
        data = await self._request("GET", "/orders", params=params)
        
        orders = []
        for o in data.get("data", []):
            order = Order(
                id=o.get("id"),
                market_id=o.get("market", ""),
                outcome_id=o.get("outcome", ""),
                side=OrderSide.BUY if o.get("side") == "buy" else OrderSide.SELL,
                size=float(o.get("size", 0)),
                price=float(o.get("price", 0)),
                filled_size=float(o.get("filled_size", 0)),
                average_fill_price=float(o.get("avg_fill_price", 0)),
                status=OrderStatus(o.get("status", "open"))
            )
            orders.append(order)
            
        return orders
    
    async def get_open_orders(self, market_id: Optional[str] = None) -> List[Order]:
        """Get open orders"""
        return await self.get_orders(market_id=market_id, status="open")
    
    # ==================== Positions & Balance ====================
    
    async def get_balance(self) -> float:
        """Get current USDC balance"""
        try:
            data = await self._request("GET", f"/accounts/{self._wallet_address}/balance")
            return float(data.get("data", {}).get("balance", 0))
        except Exception:
            return self.config.trading.starting_balance
    
    async def get_positions(self, market_id: Optional[str] = None) -> List[Position]:
        """Get current positions"""
        params = {}
        if market_id:
            params["market"] = market_id
            
        data = await self._request("GET", f"/positions/{self._wallet_address}", params=params)
        
        positions = []
        for p in data.get("data", []):
            position = Position(
                market_id=p.get("market", ""),
                outcome_id=p.get("outcome", ""),
                side=OrderSide.BUY if p.get("side") == "buy" else OrderSide.SELL,
                size=float(p.get("size", 0)),
                entry_price=float(p.get("avg_price", 0)),
                current_price=float(p.get("current_price", 0)),
                realized_pnl=float(p.get("realized_pnl", 0)),
                unrealized_pnl=float(p.get("unrealized_pnl", 0))
            )
            positions.append(position)
            
        return positions
    
    async def get_trades(
        self, 
        market_id: Optional[str] = None,
        limit: int = 100
    ) -> List[Trade]:
        """Get trade history"""
        params = {"limit": limit}
        if market_id:
            params["market"] = market_id
            
        data = await self._request("GET", f"/trades/{self._wallet_address}", params=params)
        
        trades = []
        for t in data.get("data", []):
            trade = Trade(
                market_id=t.get("market", ""),
                market_question=t.get("question", ""),
                outcome=t.get("outcome", ""),
                side=OrderSide.BUY if t.get("side") == "buy" else OrderSide.SELL,
                size=float(t.get("size", 0)),
                price=float(t.get("price", 0)),
                value=float(t.get("value", 0)),
                fees=float(t.get("fees", 0)),
                order_id=t.get("order_id"),
                tx_hash=t.get("tx_hash")
            )
            trades.append(trade)
            
        return trades
    
    # ==================== Market Making ====================
    
    async def get_best_bid_ask(self, market_id: str) -> Tuple[float, float, float, float]:
        """Get best bid and ask prices"""
        orderbook = await self.get_market_order_book(market_id)
        
        yes_bids = orderbook.get("yes", {}).get("bids", [])
        yes_asks = orderbook.get("yes", {}).get("asks", [])
        
        best_bid = float(yes_bids[0].get("price", 0)) if yes_bids else 0
        best_ask = float(yes_asks[0].get("price", 1)) if yes_asks else 1
        
        return best_bid, best_ask, 1 - best_ask, 1 - best_bid
    
    async def place_market_order(
        self,
        market_id: str,
        outcome_id: str,
        side: OrderSide,
        size: float
    ) -> Order:
        """Place market order (instant execution)"""
        return await self.create_order(
            market_id=market_id,
            outcome_id=outcome_id,
            side=side,
            size=size,
            price=1.0 if side == OrderSide.BUY else 0.0,
            order_type=OrderType.MARKET
        )
    
    async def place_limit_order(
        self,
        market_id: str,
        outcome_id: str,
        side: OrderSide,
        size: float,
        price: float
    ) -> Order:
        """Place limit order"""
        return await self.create_order(
            market_id=market_id,
            outcome_id=outcome_id,
            side=side,
            size=size,
            price=price,
            order_type=OrderType.LIMIT
        )
    
    # ==================== Utility ====================
    
    async def health_check(self) -> bool:
        """Check API health"""
        try:
            await self._request("GET", "/health")
            return True
        except Exception:
            return False
    
    def get_wallet_address(self) -> str:
        """Get wallet address"""
        return self._wallet_address
    
    async def get_funder_address(self) -> str:
        """Get funder address for deposits"""
        data = await self._request("GET", "/accounts/funder")
        return data.get("data", {}).get("address", "")
    
    def clear_cache(self):
        """Clear market cache"""
        self._markets_cache.clear()


# Singleton instance
_client: Optional[PolymarketClient] = None


async def get_client() -> PolymarketClient:
    """Get or create client instance"""
    global _client
    if _client is None:
        _client = PolymarketClient()
        await _client.connect()
    return _client


async def close_client():
    """Close client connection"""
    global _client
    if _client:
        await _client.disconnect()
        _client = None