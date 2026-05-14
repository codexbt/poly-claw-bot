# MODE 2 THRESHOLD - Decision Logic & Prompt

## What is MODE 2?

**Mode 2 is the fallback when Mode 1 fails.** It's for when:
- Price hits 86¢ (threshold confirmed conviction)
- BUT momentum is weak OR misaligned with the signal

Instead of betting $3 fixed like Mode 1, Mode 2 **sizes the bet dynamically** based on how strong the overall signal is.

---

## Mode 2 Activation Condition

```
IF (momentum_signal != signal) OR (mom_score < 0.3) THEN:
    IF yes_price >= 86¢ OR no_price >= 86¢ THEN:
        ✅ ACTIVATE MODE 2 THRESHOLD
        sized by total_score
    ELSE:
        ❌ SKIP (neither token at 86¢)
```

**How it compares to Mode 1:**
- Mode 1: "Strong technical edge, momentum confirms" → $3 fixed
- Mode 2: "Market at threshold but momentum weak" → $1-3 dynamic

---

## Score Calculation (The Heart of Mode 2)

Mode 2 evaluates the signal using **3 weighted factors**:

### Factor 1: Momentum Score (Weight: 40%)
```
m_score = momentum_score * 0.40
```

**What it is:**
- Even though momentum doesn't match the signal in Mode 2, we still use momentum as a factor
- Example: Price moved +0.008% UP (weak), but YES token is at 0.92 (UP signal)
- We take the momentum strength (mom_score = 0.40) and give it 40% weight in the total

**Real numbers:**
```
If mom_score = 0.40 (40% confidence)
Then m_score = 0.40 * 0.40 = 0.16
```

### Factor 2: Candle Pattern Analysis (Weight: 35%)
```
c_score = candle_score * 0.35
```

**Patterns detected:**
| Pattern | Score | Meaning |
|---------|-------|---------|
| BULLISH_ENGULF | +0.25 | Strong buy signal (bullish candle consumed bearish) |
| BEARISH_ENGULF | +0.25 | Strong sell signal (bearish consumed bullish) |
| HAMMER | +0.20 | Bottom reversal pattern (long lower wick) |
| SHOOTING_STAR | +0.20 | Top reversal pattern (long upper wick) |
| THREE_BULL | +0.15 | 3 consecutive bullish candles |
| THREE_BEAR | +0.15 | 3 consecutive bearish candles |
| DOJI | -0.15 | Indecision marker (reduces score) |
| PLAIN | 0.0 | No pattern, baseline score |

**Also calculated:**
- **Body ratio (30%)**: How thick is the candle body vs total range?
  - Thick body = strong conviction
  - Thin body = weak/indecisive
  
- **Consecutive run (25%)**: How many candles in a row are same direction?
  - 4 bullish in a row = run_score of 1.0
  - Mix of bullish/bearish = lower score

**Example:**
```
Last candle: Bullish engulfing pattern + strong body ratio
candle_score = 0.50
c_score = 0.50 * 0.35 = 0.175
```

### Factor 3: Market Confirmation (Weight: 25%)
```
market_score = 0.5 + (excess / 0.14) clamped to [0.0, 1.0]
p_score = market_score * 0.25
```

**Logic:**
- **IF signal matches price (YES≥86¢ for UP, NO≥86¢ for DOWN):**
  - Base market_score = 0.5 (market somewhat agrees)
  - Bonus for being far above 86¢: `(price - 0.86) / 0.14`
    - At 86¢: bonus = 0, market_score = 0.5
    - At 92¢: bonus = 0.43, market_score = 0.93
    - At 100¢: bonus = 1.0 (clamped), market_score = 1.0
  
- **IF signal doesn't match (UP but NO≥86¢, or DOWN but YES≥86¢):**
  - market_score = 0.0 (HARD GATE - trade rejected)

**Example:**
```
YES token at $0.92, signal is UP
excess = 0.92 - 0.86 = 0.06
market_score = 0.5 + (0.06 / 0.14) = 0.5 + 0.43 = 0.93
p_score = 0.93 * 0.25 = 0.2325
```

---

## Total Score Calculation

```
total_score = m_score + c_score + p_score
            = (mom*0.40) + (candle*0.35) + (market*0.25)
```

**Range:** 0.0 – 1.0

**Example (putting it all together):**
```
Scenario: YES@0.92, momentum_score=0.4, candle_score=0.5, signal=UP

m_score = 0.40 * 0.40 = 0.160
c_score = 0.50 * 0.35 = 0.175
market_score = 0.93
p_score = 0.93 * 0.25 = 0.2325

total_score = 0.160 + 0.175 + 0.2325 = 0.5675  (57%)
```

---

## Trade Sizing Based on Total Score

### Hard Gating Rule
```
IF market_score == 0.0 (price doesn't confirm):
    ❌ REJECT TRADE (return $0)
    Reason: BLOCKED(price_check, pattern)
```

**Why?** The 86¢ threshold is non-negotiable. If the market price isn't there, skip it.

### Dynamic Sizing Table

| Score Range | Trade Size | Signal Strength |
|-------------|-----------|-----------------|
| < 0.40 | $0.00 | Too weak, skip |
| 0.40 – 0.60 | $1.00 – $1.35 | Weak signal |
| 0.60 – 0.75 | $1.35 – $2.00 | Medium signal |
| 0.75 – 1.00 | $2.00 – $3.00 | Strong signal |

### Fine-Grained Interpolation

**For 0.40 – 0.75 range:**
```
size = 1.0 + ((total - 0.40) / 0.35) * 1.0
```
- At 0.40: size = $1.00
- At 0.57: size = $1.49
- At 0.75: size = $2.00

**For 0.75 – 1.00 range:**
```
size = 2.0 + ((total - 0.75) / 0.25) * 1.0
```
- At 0.75: size = $2.00
- At 0.875: size = $2.50
- At 1.00: size = $3.00

---

## Complete MODE 2 Decision Tree

```
START: Mode 1 failed (momentum weak or misaligned)
  │
  ├─→ Check 86¢ Threshold?
  │   ├─ NO  → Skip symbol
  │   └─ YES → Proceed
  │
  ├─→ Calculate 3 Scores:
  │   ├─ Momentum (40%):     mom_score * 0.40
  │   ├─ Candles (35%):      candle_score * 0.35
  │   └─ Market (25%):       market_score * 0.25
  │
  ├─→ Sum Scores:
  │   └─ total_score = m_score + c_score + p_score
  │
  └─→ Gating & Sizing:
      ├─ IF market_score == 0.0 → BLOCKED (no trade)
      ├─ IF total < 0.40       → $0.00 (skip)
      ├─ IF 0.40 ≤ total < 0.60 → $1.0-1.35 (weak)
      ├─ IF 0.60 ≤ total < 0.75 → $1.35-2.0 (medium)
      └─ IF 0.75 ≤ total ≤ 1.0  → $2.0-3.0 (strong)
```

---

## Real-World Examples

### Example 1: Weak Momentum + Good Pattern
```
Condition:
  - BTC: momentum_score = 0.15 (weak momentum move)
  - Signal: UP (YES@0.89)
  - Candles: Three bullish candles + strong body ratio
  
Scores:
  m_score = 0.15 * 0.40 = 0.060
  candle_score = 0.55
  c_score = 0.55 * 0.35 = 0.1925
  market_score = 0.5 + (0.03/0.14) = 0.714
  p_score = 0.714 * 0.25 = 0.1785
  
  total = 0.060 + 0.1925 + 0.1785 = 0.431 (43%)
  
Trade:
  ✅ MODE 2 ACTIVATED
  Size = 1.0 + ((0.431-0.40)/0.35)*1.0 = $1.09
  Reason: "mom=0.15 | candle=0.55(THREE_BULL,plain) | mkt=0.71(YES@0.89) | TOTAL=0.43 | SIZE=$1.09"
```

### Example 2: No Pattern + Misaligned Momentum
```
Condition:
  - ETH: momentum_score = 0.25 DOWN
  - Signal: UP (YES@0.88, NO@0.12)
  - Candles: DOJI (indecision)
  
Scores:
  m_score = 0.25 * 0.40 = 0.10
  candle_score = -0.15 (DOJI penalty, clamped to 0.0)
  c_score = 0.0 * 0.35 = 0.0
  market_score = 0.5 + (0.02/0.14) = 0.643
  p_score = 0.643 * 0.25 = 0.161
  
  total = 0.10 + 0.0 + 0.161 = 0.261 (26%)
  
Trade:
  ❌ MODE 2 FAILED
  Size = $0.00
  Reason: below 0.40 threshold, too weak
```

### Example 3: Strong Pattern + Good Market Conviction
```
Condition:
  - SOL: momentum_score = 0.45 (moderate momentum)
  - Signal: DOWN (NO@0.95)
  - Candles: Bearish engulfing + shooting star
  
Scores:
  m_score = 0.45 * 0.40 = 0.180
  candle_score = 0.50 (engulf + pattern)
  c_score = 0.50 * 0.35 = 0.175
  market_score = 0.5 + (0.09/0.14) = 1.0 (clamped)
  p_score = 1.0 * 0.25 = 0.25
  
  total = 0.180 + 0.175 + 0.25 = 0.605 (60%)
  
Trade:
  ✅ MODE 2 ACTIVATED
  Size = 1.0 + ((0.605-0.40)/0.35)*1.0 = $1.59
  Reason: "mom=0.45 | candle=0.50(BEARISH_ENGULF) | mkt=1.0(NO@0.95) | TOTAL=0.60 | SIZE=$1.59"
```

---

## Key Differences: Mode 1 vs Mode 2

| Aspect | Mode 1 | Mode 2 |
|--------|--------|--------|
| **Activation** | Momentum confirms + ≥30% confidence | Momentum weak OR misaligned |
| **Size** | $3.00 fixed | $1.00 – $3.00 dynamic |
| **Weighting** | Binary (on/off) | 40% momentum + 35% candle + 25% market |
| **Entry** | High confidence technical edge | Conservative threshold trade |
| **Best Case** | Strong directional move confirmed | Market consensus at threshold |
| **Worst Case** | Wrong entry, lose $3 quickly | Weak signal, lose $1-2 slowly |

---

## Mode 2 Parameters

| Parameter | Value | Meaning |
|-----------|-------|---------|
| **Price Threshold** | $0.86 | Minimum price for activation |
| **Momentum Weight** | 40% | How much momentum factors in |
| **Candle Weight** | 35% | Pattern strength importance |
| **Market Weight** | 25% | Price proximity importance |
| **Total Gate** | 0.40 | Minimum total score to trade |
| **Min Size** | $1.00 | Weakest acceptable trade |
| **Max Size** | $3.00 | Strongest acceptable trade |

---

## Your Feedback Checklist

Review and provide feedback on:
1. **Weighting (40/35/25)** - Should momentum be less important in Mode 2 since it didn't match?
2. **Total threshold (0.40)** - Is this the right minimum score?
3. **Candle patterns** - Should any patterns have higher/lower weight?
4. **Market scoring formula** - Should the 0.86 threshold leverage more or less?
5. **Sizing granularity** - Are the $1-$3 brackets right, or should they be $0.50-$4?
6. **Hard gate** - Should we always reject if market score = 0, or give it a chance?

Ready for your revision!
