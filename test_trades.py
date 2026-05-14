"""
Quick test harness — injects fake trades into swissbot without any network calls.
Run:  python test_trades.py
"""

import asyncio
import os
from unittest.mock import patch, MagicMock

# Set dry run before importing swissbot
os.environ["DRY_RUN"] = "true"

from swissbot import main, BotState, SwisstonyTrade, TradeSide

async def inject_mock_trades():
    """Start bot and inject trades into its queue."""

    print("\n" + "="*70)
    print("  SWISSBOT COLUMN OUTPUT TEST  —  Injecting mock trades")
    print("="*70 + "\n")

    # Patch network-dependent parts
    with patch('swissbot._clob') as mock_clob, \
         patch('swissbot.poll_data_api') as mock_poller, \
         patch('swissbot.ws_monitor') as mock_ws, \
         patch('swissbot.aiohttp.ClientSession') as mock_session:

        # Build a minimal mock state
        state = BotState()
        state.my_balance = 1000.0

        # Mock balance fetch
        mock_clob.get_balance_allowance.return_value = {"balance": "1000"}

        # Fake trade queue reference (will be replaced by main)
        from swissbot import trade_queue as real_queue
        real_queue._queue.clear()

        async def delayed_inject():
            """Inject a few fake trades after startup."""
            await asyncio.sleep(2)

            trades = [
                SwisstonyTrade(
                    tx_hash="0xabc123",
                    condition_id="0xcond001",
                    token_id="0xtok001",
                    side=TradeSide.BUY,
                    usd_size=100.0,
                    price=0.55,
                    timestamp=1234567890,
                    outcome="Yes",
                    market_slug="Bitcoin Price > $80k",
                ),
                SwisstonyTrade(
                    tx_hash="0xdef456",
                    condition_id="0xcond001",
                    token_id="0xtok001",
                    side=TradeSide.SELL,
                    usd_size=200.0,
                    price=0.62,
                    timestamp=1234567900,
                    outcome="Yes",
                    market_slug="Bitcoin Price > $80k",
                ),
            ]

            for trade in trades:
                print(f"[TEST] Injecting {trade.side.value} ${trade.usd_size} @ {trade.price}")
                await real_queue.put(trade)
                await asyncio.sleep(3)

            # Wait so output is visible
            await asyncio.sleep(2)

            # Shutdown signal
            raise KeyboardInterrupt()

        # Patch functions, then run main with our injector
        asyncio.create_task(delayed_inject())

        try:
            # Reduce timeouts so we don't wait forever
            import swissbot
            original_timeout = swissbot.POLL_INTERVAL_SEC
            swissbot.POLL_INTERVAL_SEC = 9999  # effectively disable poller
            await main()
        except KeyboardInterrupt:
            print("\n[TEST] Test complete.")

if __name__ == "__main__":
    asyncio.run(inject_mock_trades())
