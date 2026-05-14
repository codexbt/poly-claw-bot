# MODE 1 TECHNICAL EDGE - Decision Logic & Prompt

## Understanding Your Example
```
BTC price moved +0.015% up in last 45s → predicts UP → Threshold says YES@0.92 → TRADE $3 UP
```

### What is @0.92?
**@0.92 is NOT a hardcoded condition** — it's the **current market price** of the YES token at that moment. It's an example:
- In this example: YES token is trading at $0.92
- The 86¢ threshold check asks: "Is YES ≥ $0.86?" → YES ✅ (0.92 > 0.86)
- Since YES ≥ 86¢, the market is "Active" for trading

**Real prices change every tick** — @0.92 could be @0.88, @0.95, etc. The important part is that it's above 86¢.

---

## Complete MODE 1 Logic Flow

### Step 1: Time Window Validation (Non-Negotiable)
```
IF seconds_remaining NOT in (80-120 seconds) THEN skip symbol
ELSE proceed to Step 2
```
- **Why?** 80-120s = "golden window" before candle closes
- **Too early** (>120s): Momentum not formed yet, false signals
- **Too late** (<80s): Not enough time for trade execution + reversal
- **seconds_remaining** = Time until 5-min candle closes

### Step 2: Threshold Check (@86¢)
```
IF yes_token.price >= $0.86 THEN signal = "UP" (buy YES)
ELSE IF no_token.price >= $0.86 THEN signal = "DOWN" (buy NO)
ELSE skip symbol (neither side has liquidity/conviction)
```
- **Why 86¢?** Markets at 86¢+ show strong conviction one direction
- **Meaning:** If YES@0.92, someone believes 92% chance of UP outcome
- **Your control:** This determines UP vs DOWN direction

### Step 3: Momentum Calculation (Dynamic Threshold)
```
dynamic_threshold = 0.02% at 120s_left, transitions to 0.01% at 60s_left

pct, momentum_signal, mom_score = calc_momentum(
    candle_state, 
    threshold=dynamic_threshold
)

momentum_signal ∈ {UP, DOWN}      // Direction of price movement
mom_score ∈ [0.0, 1.0]            // Confidence 0-100%
```

**What is momentum_score?**
```python
# Simple version:
momentum_score = abs(price_change_pct) / dynamic_threshold
# If price changed 0.018% and threshold is 0.02%:
# mom_score = 0.018 / 0.02 = 0.90 (very confident)

# If price changed 0.002% and threshold is 0.02%:
# mom_score = 0.002 / 0.02 = 0.10 (barely above threshold)
```

### Step 4: MODE 1 Activation (Decision Gate)
```
IF (momentum_signal == signal) AND (mom_score >= 0.3) THEN:
    ✅ ACTIVATE MODE 1 TECHNICAL
    trade_size = $3.00 (FIXED)
    
ELSE:
    ❌ FALL BACK TO MODE 2
```

**English Translation:**
- Momentum direction must **MATCH** the threshold signal (both UP or both DOWN)
- Momentum confidence must be **AT LEAST 30%** (mom_score ≥ 0.3)
- If both conditions met → $3 fixed trade
- Otherwise → Try Mode 2

---

## Current Parameter Details

| Parameter | Value | Meaning |
|-----------|-------|---------|
| **Price Threshold** | $0.86 | Min price to consider "active market" |
| **Golden Window** | 80-120s | When to trade before candle closes |
| **Momentum Min** | 0.3 | Minimum confidence (30%) to trade |
| **MODE1 Size** | $3.00 | Fixed bet when technical edge detected |
| **Dynamic Threshold** | 0.02% → 0.01% | Sensitivity adjustment as time runs out |

---

## Mode 1 Decision Tree (Visual)

```
START: Evaluate Symbol
  │
  ├─→ Time Window Check (80-120s)?
  │   ├─ NO  → Skip this symbol
  │   └─ YES → Continue
  │
  ├─→ Threshold Check (YES or NO ≥ 86¢)?
  │   ├─ NO  → Skip (no conviction)
  │   └─ YES → Determine signal (UP/DOWN)
  │
  ├─→ Calc Momentum (dynamic threshold)
  │   └─→ Get: momentum_signal, mom_score
  │
  └─→ Decision Gate:
      ├─ momentum_signal == signal?  [AND]
      ├─ mom_score >= 0.3?
      │
      ├─ YES to both → ✅ MODE 1 TECHNICAL ($3 fixed)
      │                 Trade with confidence!
      │
      └─ NO (either fails) → ❌ Try MODE 2
                             Conservative $1-3 scoring
```

---

## Your Question: How to Make This Smarter?

### Current Weaknesses to Consider:
1. **Fixed mom_score threshold (0.3)** - Is 30% confidence enough? Should it increase as time runs out?
2. **Equal weighting of all 5-min candles** - Should recent candles count more than old ones?
3. **Dynamic threshold only changes by time** - Should it also consider volatility?
4. **No filtering on fake-out moves** - What if momentum reverses 10s into position?
5. **$3 is always $3** - Should size scale with momentum_score confidence?

### Potential Improvements:
```
Option A: Scale size by confidence
  IF mom_score >= 0.8 THEN $3.50
  IF mom_score >= 0.5 THEN $3.00
  IF mom_score >= 0.3 THEN $2.00

Option B: Increase confidence requirement near EOW
  IF seconds_left < 100 THEN mom_score_min = 0.4
  IF seconds_left < 90  THEN mom_score_min = 0.5
  
Option C: Add reversal protection
  IF price reverses >50% against signal in next 30s THEN reduce position size
  
Option D: Weight recent candles higher
  Current: avg of last 12 candles (all equal weight)
  Better: exponential weighting (recent = 2x, older = 0.5x)
```

---

## Action Items
Please review and provide:
1. **Clarification:** Is the @0.92 example now clear?
2. **Feedback:** Which parameters would you like to tweaks?
3. **Revisions:** Any logic changes you want to test?

Once you provide your revised logic, I'll update `calc_momentum()` and the decision gate accordingly.
