# Buy & Sell (Entry & Exit) Workflow

## How Polymarket Trading Works

Unlike stock trading, Polymarket is **prediction markets**:
- You don't buy stock → sell stock
- Instead: You **predict YES or NO** → market resolves → tokens settle

---

## Complete Trade Lifecycle

### Phase 1: BUY (Entry) ✅
**When**: Signal fires with >40% confidence in sniper zone (T-10s to T-5s)
**What**: Bot buys YES or NO tokens based on signal direction
**How**: 
```python
order = MarketOrderArgs(
    token_id=token_id,
    amount=TRADE_SIZE_USD,    # $1-$3 from .env
    side=BUY,                  # Always BUY (entry)
    order_type=OrderType.FOK,  # Fill or Kill
)
signed = self.client.create_market_order(order)
resp = self.client.post_order(signed, OrderType.FOK)
orderID = resp.get("orderID")
```

**Log Output:**
```
[INFO] [SNIPER] [EVAL] T-8s -- evaluating signal...
[INFO] [SIGNAL] [UP] Δ=+0.0234% | Conf=78.5% | Dir=YES
[WARNING] [SNIPER] [FIRE] YES order @ 0.5245 | Conf=78.5% | Size=$3.00
[INFO] [ORDER] Submitting market order: YES $3.00
[WARNING] [ORDER] [FIRE] Order CONFIRMED! | ID: 0x1234567890ab...
[WARNING] [TRADE] [OK] Position opened | ID: trade_abc123 | YES @ 0.5245 | $3.00
```

---

### Phase 2: HOLD (Wait for Resolution) ⏳
**When**: Order confirmed → market closes (5 minutes later)
**What**: Bot monitors the market for resolution
**How**:
```python
# Every 10-30 seconds, check if market is resolved
status = await self._check_market_status(market_id)
if status == "resolved":
    outcome = await self._get_resolution(market_id)
    # outcome = "YES" or "NO"
```

**Log Output:**
```
[DEBUG] [MARKET] Monitoring market 0x1234567890ab for resolution...
[DEBUG] [MARKET] Market still active, checking again in 10s...
2026-04-08 03:55:00 [INFO] [WINDOW] Window closed, market resolving...
```

---

### Phase 3: REDEEM (Exit / Auto-Settle) ✅
**When**: Market resolves (5-30 seconds after window close)
**What**: Bot automatically redeems winning/losing tokens
**How**:

#### If **WON** (your prediction was correct):
```python
if outcome == trade.direction:  # e.g., predicted YES, result was YES
    # Tokens automatically worth $1 each
    profit = tokens_bought - initial_spend
    # Example: You paid $3.00, got 5.73 YES tokens @ 0.524
    #          Now each YES token = $1.00
    #          Profit = 5.73 - 3.00 = +$2.73
    
    await self._redeem_position(trade)  # Claim your $5.73
```

**Log Output:**
```
[INFO] [RESULT] [WIN] Trade trade_abc123 | PnL: +$2.73 | Dir: YES
[WARNING] [TRADE] [OK] Redeemed gain $5.73 | Net profit: +$2.73
```

#### If **LOST** (your prediction was wrong):
```python
else:  # e.g., predicted YES, result was NO
    # Your tokens become worthless
    loss = -initial_spend
    # You paid $3.00, got nothing
    # Loss = -$3.00
```

**Log Output:**
```
[INFO] [RESULT] [LOSS] Trade trade_xyz789 | PnL: -$3.00 | Dir: YES
[TELEGRAM] LOST | -$3.00 | Direction: YES
```

---

## Complete Flow Example

```
22:15:00 UTC - Window starts
└─ BTC price: $70,250.00

22:15:00 - 22:19:55
└─ Bot collects 1-min candles
└─ Calculates trend, momentum, RSI, volume

22:19:50 (T-10s) - Sniper Zone Enters
│
├─ Signal fires: YES with 78.5% confidence
│  (Price up +0.0234%, strong momentum)
│
├─ ORDER SUBMITTED (BUY)
│  Amount: $3.00
│  Token: YES @ 0.5245
│  Tokens got: 5.73 YES tokens
│  Order ID: 0x1234567890ab...
│
└─ [OK] POSITION OPEN ✅
   Trade ID: trade_abc123

22:20:00 UTC - Window closes
└─ Market resolves in ~10-30 seconds

22:20:15 UTC - Resolution comes
└─ Outcome: YES (BTC did go up!)
│
├─ [WIN] Your prediction was CORRECT! ✅
│  You had 5.73 YES tokens
│  Each now worth $1.00
│  Your tokens = $5.73
│
├─ REDEEM (Auto-execute)
│  Tokens: 5.73 YES → $5.73 USDC
│  Original spend: $3.00
│  NET PROFIT: +$2.73 🎯
│
└─ [OK] POSITION CLOSED ✅
   Status: WON
   PnL: +$2.73
   Balance: $38.73 (was $35.00)

[TELEGRAM NOTIFICATION]
WON | +$2.73
Direction: YES | Entry: 0.525
```

---

## Side-by-Side: Win vs Loss

### SCENARIO A: Win (+$2.73)
```
Entry:     BUY $3.00 worth of YES @ 0.5245 = 5.73 tokens
Result:    Market resolves YES ✅
Exit:      Redeem 5.73 YES @ $1.00/token = $5.73
PnL:       $5.73 - $3.00 = +$2.73 
Balance:   $35.00 + $2.73 = $37.73
```

### SCENARIO B: Loss (-$3.00)
```
Entry:     BUY $3.00 worth of YES @ 0.5245 = 5.73 tokens
Result:    Market resolves NO ❌
Exit:      Tokens worth $0.00 (you were wrong)
PnL:       $0.00 - $3.00 = -$3.00
Balance:   $35.00 - $3.00 = $32.00
```

---

## Is It "Buy & Sell"?

**Technically**: Polymarket doesn't have traditional "sell"
- You don't **sell** your YES tokens to someone else
- Instead: Outcome tokens **auto-settle** to $1 (if correct) or $0 (if wrong)

**In Practice**: It works like buy/sell because:
```
BUY entry → HOLD → AUTO-REDEEM exit
  ↓          ↓        ↓
Entry     Position   Settlement
```

**Profit/Loss Formula:**
```
If you predicted CORRECT:
  Profit = (tokens_bought × $1) - initial_spend
  
If you predicted WRONG:
  Loss = (tokens_bought × $0) - initial_spend = -initial_spend
```

---

## What The Code Does

### BUY (Entry)
✅ **Implemented**
```python
async def place_order(self, window, signal):
    order = MarketOrderArgs(
        token_id=token_id,
        amount=TRADE_SIZE_USD,
        side=BUY,               # Always BUY (entry only)
        order_type=OrderType.FOK,
    )
    signed = self.client.create_market_order(order)
    resp = self.client.post_order(signed, OrderType.FOK)
    return trade  # Position opened
```

### REDEEM (Exit / Auto-Settle)
✅ **Implemented**
```python
async def check_and_redeem(self, session):
    for trade in self.active_trades:
        # 1. Check if market resolved
        status = await self._check_market_status(trade.market_id)
        
        if status == "resolved":
            # 2. Get outcome (YES or NO)
            outcome = await self._get_resolution(trade.market_id)
            
            # 3. If correct, redeem
            if outcome == trade.direction:
                pnl = trade.tokens_bought - trade.size_usd  # Profit
                await self._redeem_position(trade)  # Claim it
            else:
                pnl = -trade.size_usd  # Loss
            
            # 4. Log result & remove from active
            update_trade_csv(trade_id, status, pnl)
            self.active_trades.remove(trade)
```

---

## Run It & See Both Phases

### DRY RUN (Test without real money)
```bash
# .env: DRY_RUN=True
python superbot.py

# Expected output over 5 minutes:
# 22:19:50 [ORDER] [DRY RUN] Order simulated...      ← BUY simulated
# 22:20:15 [RESULT] [WIN] Trade ... | PnL: +$2.73   ← REDEEM simulated
# or
# 22:20:15 [RESULT] [LOSS] Trade ... | PnL: -$3.00  ← LOSS simulated
```

### LIVE (Real money)
```bash
# .env: DRY_RUN=False, PRIVATE_KEY=0x...
python superbot.py

# Expected output:
# 22:19:50 [ORDER] [FIRE] Order CONFIRMED! ID: 0x...  ← REAL BUY
# 22:20:15 [RESULT] [WIN/LOSS] ...                     ← REAL REDEEM
```

---

## Summary

| Phase | What | When | Code |
|-------|------|------|------|
| **ENTRY** | BUY YES/NO tokens | Signal fires (T-10s) | `create_market_order()` |
| **MONITOR** | Check market status | Every 10s after order | `_check_market_status()` |
| **EXIT** | Redeem tokens or lose | Market resolves (5m) | `_redeem_position()` |

### Answer: **YES ✅ Both buy AND sell work!**
- BUY: ✅ Implemented with FOK market orders
- SELL/EXIT: ✅ Automatic redemption when market resolves
- Profit tracking: ✅ Logs PnL to CSV and Telegram
