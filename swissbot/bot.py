# ============================================================
#  SwissTony Bot - Main Orchestration
#  Combines MarketMaker and RealityArb modules
# ============================================================

import asyncio
import signal
import sys
import time
from datetime import datetime
from typing import Optional, Dict, List
import json

from .config import get_config, BotConfig
from .client import PolymarketClient, get_client, close_client
from .models import Market, Trade, BotState
from .risk import RiskManager
from .market_maker import MarketMaker
from .reality_arb import RealityArb


class SwissTonyBot:
    """
    Main SwissTony Bot
    Orchestrates MarketMaker and RealityArb modules
    """
    
    def __init__(self, config: Optional[BotConfig] = None):
        self.config = config or get_config()
        
        # Components
        self._client: Optional[PolymarketClient] = None
        self._risk: Optional[RiskManager] = None
        self._market_maker: Optional[MarketMaker] = None
        self._reality_arb: Optional[RealityArb] = None
        
        # State
        self._running = False
        self._start_time: Optional[datetime] = None
        self._main_task: Optional[asyncio.Task] = None
        
        # Logging
        self._log_file = "swissbot_trades.json"
        self._trades: List[Trade] = []
        
    async def __aenter__(self):
        await self.start()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.stop()
    
    @property
    def is_running(self) -> bool:
        return self._running
    
    @property
    def status(self) -> Dict:
        """Get bot status"""
        return {
            "running": self._running,
            "uptime": (datetime.utcnow() - self._start_time).total_seconds() if self._start_time else 0,
            "mode": "LIVE" if self.config.is_live else "DRY_RUN",
            "balance": self._risk.state.current_balance if self._risk else 0,
            "daily_pnl": self._risk.state.daily_pnl if self._risk else 0,
            "total_pnl": self._risk.state.total_pnl if self._risk else 0,
            "trades_today": self._risk.state.trades_today if self._risk else 0,
            "market_maker": self._market_maker.get_status() if self._market_maker else {},
            "reality_arb": self._reality_arb.get_status() if self._reality_arb else {}
        }
    
    async def start(self):
        """Start the bot"""
        if self._running:
            print("Bot already running")
            return
        
        print("=" * 60)
        print("🏆 SwissTony Bot v1.0")
        print("=" * 60)
        print(f"Mode: {'LIVE' if self.config.is_live else 'DRY_RUN'}")
        print(f"Starting Balance: ${self.config.trading.starting_balance:.2f}")
        print(f"Wallet: {self.config.wallet.wallet_address[:10]}...")
        print("=" * 60)
        
        # Initialize client
        print("\n📡 Connecting to Polymarket...")
        self._client = await get_client()
        print("✅ Connected")
        
        # Initialize risk manager
        print("\n🛡️ Initializing Risk Manager...")
        self._risk = RiskManager(self.config)
        print("✅ Risk Manager ready")
        
        # Initialize MarketMaker
        print("\n📈 Initializing Market Maker...")
        self._market_maker = MarketMaker(self._client, self._risk, self.config)
        print("✅ Market Maker ready")
        
        # Initialize RealityArb
        print("\n🎯 Initializing Reality Arbitrage...")
        self._reality_arb = RealityArb(self._client, self._risk, self.config)
        print("✅ Reality Arb ready")
        
        # Start components
        await self._market_maker.start()
        
        if self.config.reality_arb.enabled:
            await self._reality_arb.start()
        
        # Start main loop
        self._running = True
        self._start_time = datetime.utcnow()
        self._main_task = asyncio.create_task(self._main_loop())
        
        print("\n" + "=" * 60)
        print("🚀 Bot started successfully!")
        print("=" * 60)
    
    async def stop(self):
        """Stop the bot"""
        if not self._running:
            return
        
        print("\n🛑 Stopping bot...")
        self._running = False
        
        # Stop components
        if self._market_maker:
            await self._market_maker.stop()
        
        if self._reality_arb:
            await self._reality_arb.stop()
        
        # Cancel main task
        if self._main_task:
            self._main_task.cancel()
            try:
                await self._main_task
            except asyncio.CancelledError:
                pass
        
        # Close client
        await close_client()
        
        # Save trades
        self._save_trades()
        
        # Print summary
        self._print_summary()
        
        print("\n✅ Bot stopped")
    
    async def _main_loop(self):
        """Main bot loop"""
        while self._running:
            try:
                # Print status every 30 seconds
                self._print_status()
                
                # Wait
                await asyncio.sleep(30)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"Main loop error: {e}")
                await asyncio.sleep(5)
    
    def _print_status(self):
        """Print periodic status"""
        if not self._risk:
            return
        
        state = self._risk.get_status()
        
        print(f"\n[{datetime.utcnow().strftime('%H:%M:%S')}] Status:")
        print(f"   Balance: ${state['balance']:.2f}")
        print(f"   Daily P&L: ${state['daily_pnl']:.2f}")
        print(f"   Trades: {state['trades_today']}")
        print(f"   Wins: {state['wins_today']} | Losses: {state['losses_today']}")
        print(f"   Positions: {state['open_positions']}")
        print(f"   Paused: {state['paused']}")
        if state['pause_reason']:
            print(f"   Reason: {state['pause_reason']}")
    
    def _print_summary(self):
        """Print trading summary"""
        if not self._risk:
            return
        
        state = self._risk.get_state()
        
        print("\n" + "=" * 60)
        print("📊 Trading Summary")
        print("=" * 60)
        print(f"Start Time: {self._start_time}")
        print(f"End Time: {datetime.utcnow()}")
        print(f"Duration: {datetime.utcnow() - self._start_time}")
        print(f"\nBalance:")
        print(f"   Starting: ${self.config.trading.starting_balance:.2f}")
        print(f"   Current: ${state.current_balance:.2f}")
        print(f"\nP&L:")
        print(f"   Daily: ${state.daily_pnl:.2f}")
        print(f"   Total: ${state.total_pnl:.2f}")
        print(f"\nTrades:")
        print(f"   Today: {state.trades_today}")
        print(f"   Wins: {state.wins_today}")
        print(f"   Losses: {state.losses_today}")
        if state.trades_today > 0:
            win_rate = state.wins_today / state.trades_today * 100
            print(f"   Win Rate: {win_rate:.1f}%")
        print("=" * 60)
    
    def _save_trades(self):
        """Save trades to file"""
        try:
            # Load existing trades
            try:
                with open(self._log_file, 'r') as f:
                    all_trades = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                all_trades = []
            
            # Add new trades
            new_trades = [t.to_dict() for t in self._trades]
            all_trades.extend(new_trades)
            
            # Save
            with open(self._log_file, 'w') as f:
                json.dump(all_trades, f, indent=2)
                
        except Exception as e:
            print(f"Failed to save trades: {e}")
    
    # ==================== Manual Trading ====================
    
    async def buy(
        self,
        market_id: str,
        side: str = "BUY",
        size: Optional[float] = None
    ) -> Trade:
        """Manual buy order"""
        if not self._market_maker:
            raise Exception("Bot not started")
        
        # Use default size if not specified
        if size is None:
            size = self.config.trading.trade_size
        
        from .models import OrderSide
        side_enum = OrderSide.BUY if side.upper() == "BUY" else OrderSide.SELL
        
        return await self._market_maker.place_market_order(
            market_id=market_id,
            side=side_enum,
            size=size
        )
    
    async def sell(
        self,
        market_id: str,
        size: Optional[float] = None
    ) -> Trade:
        """Manual sell order"""
        return await self.buy(market_id, "SELL", size)
    
    async def get_balance(self) -> float:
        """Get current balance"""
        if self._client:
            return await self._client.get_balance()
        return self.config.trading.starting_balance
    
    async def get_markets(self, category: Optional[str] = None) -> List[Market]:
        """Get available markets"""
        if self._client:
            if category:
                return await self._client.get_markets(category=category)
            return await self._client.get_sports_markets()
        return []
    
    async def search_markets(self, query: str) -> List[Market]:
        """Search markets"""
        if self._client:
            return await self._client.search_markets(query)
        return []
    
    def get_risk_status(self) -> Dict:
        """Get risk status"""
        if self._risk:
            return self._risk.get_status()
        return {}


# ==================== CLI Interface ====================

async def run_bot():
    """Run the bot"""
    bot = SwissTonyBot()
    
    # Handle signals
    loop = asyncio.get_event_loop()
    
    def signal_handler():
        print("\n🛑 Received shutdown signal")
        asyncio.create_task(bot.stop())
    
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, signal_handler)
        except NotImplementedError:
            pass
    
    # Start bot
    try:
        await bot.start()
        
        # Keep running
        while bot.is_running:
            await asyncio.sleep(1)
            
    except KeyboardInterrupt:
        pass
    finally:
        await bot.stop()


def main():
    """Main entry point"""
    print("🏆 SwissTony Bot - Polymarket Trading Bot")
    print("=" * 60)
    
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        print("\n👋 Goodbye!")
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()