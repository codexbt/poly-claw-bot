# MODE 1 THRESHOLD - Decision Logic (CORRECTED)

## What is MODE 1?

**Mode 1 is the SIMPLE threshold-based trade.** It's the basic entry:
- Price hits 86¢ (YES or NO token)
- Direction is determined by which token is ≥ 86¢
- Place $3.00 fixed bet
- No complex analysis needed

This is your **high-volume, straightforward trading mode**.

---

## Mode 1 Activation (Simple & Direct)

```
IF yes_price >= $0.86 THEN:
    signal = "UP"
    ✅ TRADE $3.00 UP
    
ELSE IF no_price >= $0.86 THEN:
    signal = "DOWN"
    ✅ TRADE $3.00 DOWN
    
ELSE:
    ❌ SKIP (neither token at 86¢)
```

That's it. No momentum check, no candle analysis. Just **price at threshold = trade**.

---

## Why 86¢ Threshold?

```
Market Price Zones:

50¢ zone  → Market uncertainty, no conviction (avoid)
70¢ zone  → Some conviction forming
86¢ zone  → STRONG CONVICTION ← Your trigger point
95¢ zone  → Very high conviction, position near resolution
```

**At 86¢, the market is saying:**
- "I'm 86% confident in this outcome"
- "There's real money on this side"
- "The bet size makes sense"

---

## Mode 1 Decision Tree (Visual)

```
START: Check next 5-min candle
  │
  ├─→ Time Window Check (80-120s left)?
  │   ├─ NO  → Skip this symbol
  │   └─ YES → Continue
  │
  ├─→ Check Price:
  │   ├─ YES ≥ 86¢?
  │   │   └─ YES → signal = UP, size = $3.00
  │   │
  │   └─ NO ≥ 86¢?
  │       └─ YES → signal = DOWN, size = $3.00
  │
  └─→ Execute:
      ✅ Place $3.00 order
      Save market ID (prevent duplicates in next 5 min)
```

---

## Mode 1 Parameters (Fixed & Simple)

| Parameter | Value | Meaning |
|-----------|-------|---------|
| **Price Threshold** | $0.86 | Activation point for trade |
| **Golden Window** | 80-120s | When to trade before candle closes |
| **Trade Size** | $3.00 | Fixed bet amount |
| **Direction** | Based on which token ≥ 86¢ | UP = YES, DOWN = NO |
| **Cooldown** | 300s per symbol | No duplicate trades within 5 min |

---

## Real-World Examples

### Example 1: Simple UP Trade
```
BTC 5-min Candle:
  - Time left: 95 seconds ✅ (in golden window)
  - YES token: $0.88
  - NO token: $0.12
  
Evaluation:
  - YES ≥ 86¢? YES → signal = UP
  - Size? $3.00 fixed
  
Action:
  ✅ MODE 1 ACTIVATED
  Trade: BTC UP $3.00
  Type: Simple threshold entry
```

### Example 2: DOWN Trade with Higher Conviction
```
ETH 5-min Candle:
  - Time left: 110 seconds ✅ (in golden window)
  - YES token: $0.15
  - NO token: $0.92
  
Evaluation:
  - NO ≥ 86¢? YES → signal = DOWN
  - Size? $3.00 fixed
  
Action:
  ✅ MODE 1 ACTIVATED
  Trade: ETH DOWN $3.00
  Type: Simple threshold entry (market strongly believes in DOWN)
```

### Example 3: Skip (Too Early)
```
SOL 5-min Candle:
  - Time left: 140 seconds ❌ (too early, >120s)
  - YES token: $0.88
  - NO token: $0.12
  
Evaluation:
  - Time window? NO (too early)
  
Action:
  ⏳ SKIP SOL
  Reason: "Too early (140s left, need 80-120s)"
  Retry in next cycle when time window is valid
```

### Example 4: Skip (No Conviction)
```
XRP 5-min Candle:
  - Time left: 100 seconds ✅ (in golden window)
  - YES token: $0.78
  - NO token: $0.22
  
Evaluation:
  - YES ≥ 86¢? NO
  - NO ≥ 86¢? NO
  - Neither token at 86¢
  
Action:
  ⏳ SKIP XRP
  Reason: "No conviction (YES=$0.78, NO=$0.22)"
  Wait for market to develop conviction
```

---

## Advantages of Mode 1

✅ **Fast decision** - Just check price, no complex calculations  
✅ **High volume** - Can trigger multiple times per minute  
✅ **Market-validated** - 86¢ means real money is there  
✅ **Predictable** - Always $3.00 bet  
✅ **Scalable** - Can add more symbols easily  

---

## Risk Profile

| Aspect | Risk |
|--------|------|
| **Win Rate** | Higher (market consensus at 86¢) |
| **Per-Trade Loss** | $3.00 max |
| **False Positives** | Lower (price validates entry) |
| **Time Sensitivity** | Medium (window-dependent) |

---

## Key Difference: Mode 1 vs Mode 2

| Aspect | Mode 1 (Threshold) | Mode 2 (Technical) |
|--------|---|---|
| **Entry Signal** | Price ≥ 86¢ | Momentum + Candles + Market score |
| **Analysis** | None | Full scoring (40/35/25) |
| **Bet Size** | $3.00 fixed | $1.00 – $3.00 dynamic |
| **Speed** | Instant | Requires calculation |
| **Frequency** | Higher (more triggers) | Lower (stricter criteria) |
| **Best For** | High volume trading | High accuracy trading |

---

## Implementation Notes

Mode 1 is your **bread and butter** entry. Keep it simple:

1. Check time window (80-120s)
2. Check if YES ≥ 86¢ or NO ≥ 86¢
3. Trade $3.00 (UP if YES, DOWN if NO)
4. Move to next symbol
5. Repeat every 0.5 seconds

**No overthinking. No analysis. Price confirms the bet.**

Ready to code this!
