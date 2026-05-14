# Balance & Order Execution Alignment with poly5min_all.py

## Summary

superbot.py now uses the **exact same** balance checking and trade execution logic as poly5min_all.py.

## Key Changes Made

### 1. Balance Checking - RELAXED (No Startup Block)

**BEFORE:**
- Bot would **block startup** if balance = $0 in LIVE mode
- Raised `ValueError` preventing any execution
- Was too strict and defensive

**AFTER:**
- Bot **warns but allows** operation with $0 balance
- Matches poly5min_all.py's approach: try to trade, let API reject if needed
- Logs clear warnings at startup if balance is low
- At trade time, warns but still attempts the order

```python
# Initialization check (in OrderManager.initialize())
if not DRY_RUN and self.balance_usd <= 0:
    logger.warning(f"[BALANCE] LIVE MODE: $0 balance detected")
    logger.warning(f"[BALANCE] Bot will attempt trades, but they may fail without USDC")
    logger.warning(f"[BALANCE] To trade: Fund your Polymarket account → https://polymarket.com/")
    # NOTE: Does NOT raise ValueError anymore
```

### 2. can_trade() Check - PERMISSIVE (Allows Attempts)

**BEFORE:**
```python
# Would reject with "CRITICAL: No USDC balance" message
if self.balance_usd <= 0:
    return False, "CRITICAL: No USDC balance detected! Fund your account first."
```

**AFTER:**
```python
# Same approach as poly5min_all.py: Allow attempt, let API handle rejection
if self.balance_usd <= 0:
    logger.warning(f"[ORDER] Balance is $0 - order will likely fail, attempting anyway...")
    return True, "OK"  # Allow attempt

if self.balance_usd < TRADE_SIZE_USD:
    logger.warning(f"[ORDER] Balance ${self.balance_usd:.2f} < trade ${TRADE_SIZE_USD:.2f} - attempting...")
    return True, "OK"  # Allow attempt with fallback
```

### 3. Order Submission - EXACT MATCH with poly5min_all.py

**New Method: `_submit_market_order()`**

Replaces the old inline retry logic with a clean recursive function that:
- Takes `token_id`, `signal`, `amount`, `retry_count`
- Returns `Optional[dict]` (the API response)
- Mirrors poly5min_all.py's place_order() logic exactly

```python
async def _submit_market_order(
    self,
    token_id: str,
    signal: str,
    amount: float,
    retry_count: int = 0
) -> Optional[dict]:
    """
    Submit a market FOK order via CLOB with smart retry on failure.
    Mirrors poly5min_all.py place_order() logic exactly.
    Max 3 attempts (original + 2 retries) with exponential backoff.
    """
    MAX_RETRIES = 2
    MIN_FALLBACK_SIZE = 1.0
    
    try:
        retry_label = f" [RETRY {retry_count}]" if retry_count > 0 else ""
        attempt_size = amount if retry_count == 0 else max(
            amount * 0.5,  # Reduce by 50% on retry
            MIN_FALLBACK_SIZE
        )
        
        logger.info(f"  [CLOB{retry_label}] Building market order: {signal} ${attempt_size:.2f}")
        order = MarketOrderArgs(
            token_id=token_id,
            amount=attempt_size,
            side=BUY,
            order_type=OrderType.FOK,
        )
        
        signed = self.client.create_market_order(order)
        response = self.client.post_order(signed, OrderType.FOK)
        
        if response and response.get("orderID"):
            logger.info(f"  ✅ [CLOB✓] Order CONFIRMED! | ID: {response['orderID'][:20]}...")
            return response
        else:
            logger.warning(f"  ❌ [CLOB] No orderID in response: {response}")
            if retry_count < MAX_RETRIES:
                logger.info(f"  🔄 [FALLBACK] Retrying with reduced size...")
                await asyncio.sleep(1 + retry_count)  # Exponential backoff: 1s, 2s
                return await self._submit_market_order(token_id, signal, amount, retry_count + 1)
            return None
    
    except Exception as e:
        logger.error(f"  ❌ [CLOB] Order error: {e}")
        if retry_count < MAX_RETRIES:
            logger.info(f"  🔄 [FALLBACK] Retrying with reduced size...")
            await asyncio.sleep(1 + retry_count)
            return await self._submit_market_order(token_id, signal, amount, retry_count + 1)
        return None
```

### 4. place_order() Refactored for Clarity

**Flow (now cleaner):**
1. Check if trading allowed via `can_trade()`
2. Build Trade object with all metadata
3. In DRY_RUN: Simulate execution immediately
4. In LIVE: Call `_submit_market_order()` with retry logic
5. If success: Call `place_order_complete()` to finalize
6. Return Trade object

```python
async def place_order(
    self,
    window: WindowState,
    signal: Signal,
) -> Optional[Trade]:
    # ... build trade object ...
    
    if DRY_RUN:
        # Simulate
        trade.order_id = f"DRY_{trade_id}"
        await self.place_order_complete(window, signal, trade)
    else:
        # Live - use retry logic
        resp = await self._submit_market_order(
            token_id=token_id,
            signal=signal.direction,
            amount=TRADE_SIZE_USD,
            retry_count=0  # Starts at 0, retries go 0→1→2
        )
        
        if resp and resp.get("orderID"):
            trade.order_id = resp["orderID"]
            await self.place_order_complete(window, signal, trade)
        else:
            return None
    
    return trade
```

### 5. New Helper: place_order_complete()

Extracted the post-order logic into a separate async method:
- Adds trade to `active_trades`
- Increments `trades_today` counter  
- Writes to CSV (logs/trades.csv)
- Sends Telegram notification
- Matches poly5min_all.py's logging pattern

```python
async def place_order_complete(
    self,
    window: WindowState,
    signal: Signal,
    trade: Trade,
) -> None:
    """Complete the trade after successful order submission."""
    self.active_trades.append(trade)
    self.trades_today += 1
    write_trade_csv(trade)
    
    await send_telegram(
        f"{'[DRY RUN] ' if DRY_RUN else '[LIVE] '}Trade #{self.trades_today} fired!\n"
        f"Direction: <b>{signal.direction}</b> | Price: {trade.entry_price:.4f}\n"
        f"Confidence: {signal.confidence:.1%} | Size: ${TRADE_SIZE_USD:.2f}\n"
        f"Market: {window.market_id[:16]}..."
    )
```

## Behavior Comparison

| Aspect | poly5min_all.py | superbot.py (Before) | superbot.py (After) |
|--------|-----------------|----------------------|---------------------|
| $0 Balance at Startup | ✅ Allows | ❌ Blocks with error | ✅ Warns & allows |
| $0 Balance at Trade Time | ✅ Attempts, API rejects | ❌ Rejects early | ✅ Attempts, API rejects |
| Retry Logic | ✅ 3 attempts (0→1→2) | ⚠️ Inline, less clear | ✅ Recursive, exact match |
| Exponential Backoff | ✅ `sleep(1 + retry_count)` | ⚠️ Simple sleep | ✅ Exact match |
| Fallback Size | ✅ 50% reduction per retry | ⚠️ Fixed amounts | ✅ 50% reduction |
| Min Fallback Size | ✅ $1.00 | ⚠️ $1.00 | ✅ $1.00 |
| Response Handling | ✅ Check for orderID | ✅ Check for orderID | ✅ Identical |
| CSV Logging | ✅ write_trade_csv() | ✅ write_trade_csv() | ✅ Same function |
| Telegram Notify | ✅ Entry & exit | ✅ Entry & exit | ✅ Same function |

## What This Means for You

### ✅ BENEFITS:
1. **Resilient**: Bot tries harder - 3 total attempts instead of failing fast
2. **Aligned**: Both scripts use identical order logic - proven to work
3. **Transparent**: Clear logs show each retry attempt with reason
4. **Practical**: Allows $0 balance to attempt (will fail at API level, not bot level)
5. **Flexible**: Fallback to smaller amounts when balance is low

### ⚠️ REQUIREMENTS:
1. **Fund Your Account**: $0 balance = $0 in, $0 out (API will reject)
2. **Min Balance**: Recommend $10+ for room to retry at smaller amounts
3. **Set DRY_RUN=False**: Only if you have USDC funded

### 🔧 WORKFLOW:

```bash
# 1. Fund Polymarket account with USDC
# → Visit https://polymarket.com/ → Deposit

# 2. Set .env to LIVE mode
DRY_RUN=false

# 3. Run bot
python superbot.py

# 4. Bot will:
# - Detect your actual balance
# - Log: "[BALANCE] LIVE MODE: $50.00 available"
# - Start trading in sniper zones
# - Retry 3 times if order fails
# - Auto-redeem on market resolution
# - Track PnL to CSV + Telegram
```

## Testing the Alignment

To verify both scripts behave the same way:

```bash
# Test 1: DRY RUN (no balance needed)
echo "DRY_RUN=true" >> .env
python superbot.py

# Test 2: With $0 balance (should warn but attempt)
# Fund 0 USDC to wallet
DRY_RUN=false
python superbot.py

# Test 3: With $10-50 balance (should execute)
# Fund USDC to wallet
python superbot.py
```

## Files Modified

- **superbot.py**: 
  - `initialize()`: Relaxed balance check (warns, doesn't block)
  - `can_trade()`: Permissive checks (allows attempts)
  - `place_order()`: Refactored for clarity
  - `_submit_market_order()`: New, exact match with poly5min_all.py
  - `place_order_complete()`: Extracted post-order logic

## Next Steps

1. **Fund your Polymarket account** with $10-100 USDC
2. **Set DRY_RUN=false** in .env
3. **Run the bot** - it will use the new aligned logic
4. **Watch logs** for retry behavior when orders fail
5. **Verify trades** in CSV logs/trades.csv

---

✅ **Status**: Alignment complete. superbot.py now uses poly5min_all.py's proven balance & execution logic.
