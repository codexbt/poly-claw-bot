#!/usr/bin/env python3
# ============================================================
#  SwissTony Bot - CLI Entry Point
#  Run: python swissbot_main.py
# ============================================================

import asyncio
import sys
import argparse
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from swissbot import SwissTonyBot, get_config


async def interactive_mode(bot: SwissTonyBot):
    """Interactive CLI mode"""
    print("\n📟 Interactive Mode")
    print("Commands: status, markets, buy, sell, search, risk, quit")
    print()
    
    while bot.is_running:
        try:
            cmd = input("swissbot> ").strip().lower()
            
            if not cmd:
                continue
            
            parts = cmd.split()
            command = parts[0]
            
            if command in ["quit", "exit", "q"]:
                break
                
            elif command == "status":
                status = bot.status
                print(f"\n📊 Bot Status:")
                print(f"   Running: {status['running']}")
                print(f"   Mode: {status['mode']}")
                print(f"   Balance: ${status['balance']:.2f}")
                print(f"   Daily P&L: ${status['daily_pnl']:.2f}")
                print(f"   Total P&L: ${status['total_pnl']:.2f}")
                print(f"   Trades: {status['trades_today']}")
                
            elif command == "markets":
                markets = await bot.get_markets()
                print(f"\n📈 Available Markets ({len(markets)}):")
                for m in markets[:10]:
                    print(f"   {m.id[:20]}... | {m.yes_price:.2f} / {m.no_price:.2f} | ${m.volume:,.0f}")
                    
            elif command == "search" and len(parts) > 1:
                query = " ".join(parts[1:])
                markets = await bot.search_markets(query)
                print(f"\n🔍 Search Results for '{query}':")
                for m in markets[:5]:
                    print(f"   {m.id}")
                    print(f"   Q: {m.question[:60]}...")
                    print(f"   Price: {m.yes_price:.2f} / {m.no_price:.2f}")
                    print()
                    
            elif command == "buy" and len(parts) > 2:
                market_id = parts[1]
                size = float(parts[2])
                trade = await bot.buy(market_id, "BUY", size)
                print(f"\n✅ Buy order placed:")
                print(f"   Market: {trade.market_id}")
                print(f"   Size: {trade.size}")
                print(f"   Price: ${trade.price:.2f}")
                print(f"   Value: ${trade.value:.2f}")
                
            elif command == "sell" and len(parts) > 2:
                market_id = parts[1]
                size = float(parts[2])
                trade = await bot.sell(market_id, size)
                print(f"\n✅ Sell order placed:")
                print(f"   Market: {trade.market_id}")
                print(f"   Size: {trade.size}")
                print(f"   Price: ${trade.price:.2f}")
                print(f"   Value: ${trade.value:.2f}")
                
            elif command == "risk":
                status = bot.get_risk_status()
                print(f"\n🛡️ Risk Status:")
                print(f"   Paused: {status['paused']}")
                if status.get('pause_reason'):
                    print(f"   Reason: {status['pause_reason']}")
                print(f"   Open Positions: {status['open_positions']}")
                print(f"   Open Orders: {status['open_orders']}")
                
            else:
                print(f"Unknown command: {command}")
                print("Available: status, markets, buy, sell, search, risk, quit")
                
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"Error: {e}")
    
    print("\n👋 Exiting...")


async def one_shot_mode(bot: SwissTonyBot, args):
    """One-shot mode - do one action and exit"""
    
    if args.status:
        status = bot.status
        print(f"\n📊 Bot Status:")
        print(f"   Running: {status['running']}")
        print(f"   Mode: {status['mode']}")
        print(f"   Balance: ${status['balance']:.2f}")
        print(f"   Daily P&L: ${status['daily_pnl']:.2f}")
        print(f"   Total P&L: {status['total_pnl']:.2f}")
        print(f"   Trades: {status['trades_today']}")
        
    elif args.markets:
        markets = await bot.get_markets(args.markets)
        print(f"\n📈 Markets ({len(markets)}):")
        for m in markets[:20]:
            print(f"   {m.id}")
            print(f"   Q: {m.question[:70]}...")
            print(f"   Yes: {m.yes_price:.2f} | No: {m.no_price:.2f} | Vol: ${m.volume:,.0f}")
            print()
            
    elif args.search:
        markets = await bot.search_markets(args.search)
        print(f"\n🔍 Search: '{args.search}'")
        for m in markets[:10]:
            print(f"   {m.id}")
            print(f"   {m.question[:60]}")
            print(f"   {m.yes_price:.2f} / {m.no_price:.2f}")
            print()
            
    elif args.buy:
        parts = args.buy.split(":")
        if len(parts) != 2:
            print("Usage: --buy market_id:size")
            return
        market_id, size = parts[0], float(parts[1])
        trade = await bot.buy(market_id, "BUY", size)
        print(f"\n✅ Bought: {trade.size} @ ${trade.price:.2f} = ${trade.value:.2f}")
        
    elif args.sell:
        parts = args.sell.split(":")
        if len(parts) != 2:
            print("Usage: --sell market_id:size")
            return
        market_id, size = parts[0], float(parts[1])
        trade = await bot.sell(market_id, size)
        print(f"\n✅ Sold: {trade.size} @ ${trade.price:.2f} = ${trade.value:.2f}")
        
    elif args.risk:
        status = bot.get_risk_status()
        import json
        print(json.dumps(status, indent=2))


async def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="SwissTony Bot - Polymarket Trading"
    )
    
    # Mode options
    parser.add_argument("--interactive", "-i", action="store_true",
                        help="Interactive mode")
    parser.add_argument("--status", "-s", action="store_true",
                        help="Show bot status")
    parser.add_argument("--markets", "-m", nargs="?", const="sports",
                        help="List markets (optional: category)")
    parser.add_argument("--search", "-q", metavar="QUERY",
                        help="Search markets")
    parser.add_argument("--buy", metavar="MARKET:SIZE",
                        help="Buy (market_id:size)")
    parser.add_argument("--sell", metavar="MARKET:SIZE",
                        help="Sell (market_id:size)")
    parser.add_argument("--risk", "-r", action="store_true",
                        help="Show risk status")
    
    # Bot options
    parser.add_argument("--dry-run", action="store_true", default=None,
                        help="Force dry run mode")
    parser.add_argument("--balance", type=float,
                        help="Set starting balance")
    
    args = parser.parse_args()
    
    # Load config
    config = get_config()
    
    # Override config if specified
    if args.dry_run is not None:
        config.trading.dry_run = args.dry_run
    if args.balance:
        config.trading.starting_balance = args.balance
    
    print("🏆 SwissTony Bot v1.0")
    print("=" * 60)
    print(f"Mode: {'DRY_RUN' if config.trading.dry_run else 'LIVE'}")
    print(f"Balance: ${config.trading.starting_balance:.2f}")
    print("=" * 60)
    
    # Create and start bot
    bot = SwissTonyBot(config)
    
    try:
        await bot.start()
        
        # Run in appropriate mode
        if args.interactive:
            await interactive_mode(bot)
        elif any([args.status, args.markets, args.search, args.buy, args.sell, args.risk]):
            await one_shot_mode(bot, args)
        else:
            # Default: run indefinitely
            print("\n🚀 Bot running... Press Ctrl+C to stop")
            while bot.is_running:
                await asyncio.sleep(1)
                
    except KeyboardInterrupt:
        print("\n\n🛑 Interrupted")
    finally:
        await bot.stop()


if __name__ == "__main__":
    asyncio.run(main())