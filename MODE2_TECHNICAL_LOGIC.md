# MODE 2 TECHNICAL - Revised Logic & Implementation

## What is MODE 2?

**Mode 2 is the SOPHISTICATED technical analysis trade.** It activates when:
- Price hits 86¢ (YES or NO token)
- BUT momentum is weak OR misaligned with signal direction
- Technical analysis (candles + patterns) confirms the move
- Bet is sized dynamically based on confidence score

This is your **high-accuracy, momentum + pattern-driven trading mode**.

---

## Mode 2 Activation Gate

```
IF price token >= $0.86 THEN:
    IF momentum_score < 0.30 OR momentum_direction != signal THEN:
        ✅ PROCEED TO MODE 2 EVALUATION
    ELSE:
        ❌ DEFER TO MODE 1 (momentum is strong & aligned)
ELSE:
    ❌ SKIP (no market conviction at 86¢ threshold)
```

**Key Point:** Mode 2 takes over when Mode 1 logic doesn't apply.

---

## Score Calculation (The Heart of Mode 2)

Mode 2 evaluates the signal using **3 weighted factors**:

### Factor 1: Momentum Score (Weight: 40%)
```
m_score = momentum_score * 0.40
```

**How it's calculated:**
- **Lookback period:** Last 3-4 candles
- **Metric:** Rate of change (ROC) = (latest_close - oldest_close) / oldest_close
- **Range:** 0.0 – 1.0
  - 1% move = score 1.0 (normalized)
  - 0.5% move = score 0.5
  - 0% move = score 0.0

**Example:**
```
Candles: CLOSE[2.05, 2.06, 2.07, 2.10]
ROC = (2.10 - 2.05) / 2.05 = 2.44%
momentum_score = min(0.0244 / 0.01, 1.0) = 1.0 (capped)
m_score = 1.0 * 0.40 = 0.40
```

### Factor 2: Candle Pattern Analysis (Weight: 35%)
```
c_score = candle_score * 0.35
```

**Pattern Detection (Scored):**

| Pattern | Base Score | Type | Meaning |
|---------|-----------|------|---------|
| BULLISH_ENGULF | +0.25 | Strong | Bullish candle consumed bearish |
| BEARISH_ENGULF | +0.25 | Strong | Bearish consumed bullish |
| HAMMER | +0.20 | Reversal | Long lower wick = bottom bounce |
| SHOOTING_STAR | +0.20 | Reversal | Long upper wick = top rejection |
| THREE_BULL | +0.15 | Trend | 3+ consecutive bullish candles |
| THREE_BEAR | +0.15 | Trend | 3+ consecutive bearish candles |
| DOJI | -0.15 | Weakness | Indecision, reduces confidence |
| PLAIN | +0.00 | Neutral | No pattern detected |

**Additional Components:**

```
Body Ratio (30% of candle_score):
  = (close - open) / (high - low)
  = How "thick" the candle is
  - Thick body (0.7+) = strong conviction ✅
  - Thin body (0.1-0.3) = weak, indecisive ❌

Consecutive Run (25% of candle_score):
  = Count of consecutive candles in same direction
  - 4 out of 4 same direction = 1.0 score
  - 2 out of 4 = 0.5 score
  - 1 out of 4 = 0.25 score
```

**Calculation:**
```
base_score = PATTERN_SCORES[pattern]
body_effect = body_ratio * 0.30
run_effect = run_score * 0.25
candle_score = base_score + body_effect + run_effect
candle_score = clamp(0.0, 1.0)

Then: c_score = candle_score * 0.35
```

**Example:**
```
Pattern: BULLISH_ENGULF (+0.25)
Body ratio: 0.8 → body_effect = 0.8 * 0.30 = 0.24
Run: 3 out of 4 → run_effect = 0.75 * 0.25 = 0.1875

candle_score = 0.25 + 0.24 + 0.19 = 0.68
c_score = 0.68 * 0.35 = 0.238
```

### Factor 3: Market Confirmation (Weight: 25%)
```
p_score = market_score * 0.25
```

**Logic:**
1. **Check if price confirms signal:**
   - Signal UP → YES token must be ≥ 86¢ ✅
   - Signal DOWN → NO token must be ≥ 86¢ ✅
   - Otherwise → GATE CLOSES, no trade ❌

2. **Score based on how far above threshold:**
   ```
   excess = token_price - 0.86
   market_score = 0.5 + (excess / 0.14)
   market_score = clamp(0.0, 1.0)
   
   At 86¢: market_score = 0.5
   At 92¢: market_score = 0.93
   At 100¢: market_score = 1.0 (capped)
   ```

**Why this formula?**
- **Base 0.5:** Market has some conviction at 86¢
- **0.14 denominator:** Maximum reasonable spread (14¢ above threshold)
- **Bonus per point:** Each additional ¢ increases score by ~7%

**Example:**
```
Signal: DOWN, NO@0.94
excess = 0.94 - 0.86 = 0.08
market_score = 0.5 + (0.08 / 0.14) = 0.5 + 0.571 = 1.071 → clamped to 1.0
p_score = 1.0 * 0.25 = 0.25
```

---

## Total Score Calculation

```
total_score = m_score + c_score + p_score
            = (momentum*0.40) + (candle*0.35) + (market*0.25)
```

**Range:** 0.0 – 1.0 (0% to 100% confidence)

**Example (Complete Calculation):**
```
Scenario: BTC momentum=+2.5%, pattern=three_bull, NO@0.94 (DOWN signal)

Step 1: Momentum
  ROC = +2.5% → momentum_score = min(2.5 / 1.0, 1.0) = 1.0
  m_score = 1.0 * 0.40 = 0.40

Step 2: Candles
  Pattern: THREE_BULL (+0.15)
  Body ratio: 0.75 → 0.75 * 0.30 = 0.225
  Run: 4 out of 4 → 1.0 * 0.25 = 0.25
  candle_score = 0.15 + 0.225 + 0.25 = 0.625
  c_score = 0.625 * 0.35 = 0.219

Step 3: Market
  NO@0.94, signal DOWN ✅
  excess = 0.08
  market_score = 0.5 + (0.08/0.14) = 1.0 (clamped)
  p_score = 1.0 * 0.25 = 0.25

TOTAL SCORE: 0.40 + 0.219 + 0.25 = 0.869 (87%)
```

---

## Gating & Trade Sizing

### Hard Gate: Price Confirmation (Non-Negotiable)
```
IF (signal UP and YES < 0.86) OR (signal DOWN and NO < 0.86):
    ❌ REJECT TRADE
    message = "BLOCKED: price doesn't confirm signal"
```

### Soft Gate: Minimum Total Score
```
IF total_score < 0.40:
    ❌ REJECT TRADE
    message = "below minimum confidence threshold"
```

### Dynamic Sizing (Interpolated)

**Score Range 0.40 – 0.75:**
```
size = 1.0 + ((total_score - 0.40) / 0.35) * 1.0

Examples:
  score=0.40 → size = $1.00 (minimum)
  score=0.575 → size = $1.49
  score=0.75 → size = $2.00
```

**Score Range 0.75 – 1.00:**
```
size = 2.0 + ((total_score - 0.75) / 0.25) * 1.0

Examples:
  score=0.75 → size = $2.00
  score=0.875 → size = $2.50
  score=1.0 → size = $3.00 (maximum)
```

**Summary Table:**
| Score | Size | Confidence | Action |
|-------|------|-----------|--------|
| < 0.40 | $0.00 | Too weak | Skip entirely |
| 0.40-0.60 | $1.00-$1.35 | Weak | Small bet |
| 0.60-0.75 | $1.35-$2.00 | Medium | Medium bet |
| 0.75-1.00 | $2.00-$3.00 | Strong | Big bet |

---

## Key Decision Tree

```
START: Check 86¢ Threshold
  │
  ├─ Price YES/NO ≥ $0.86?
  │  └─ NO → SKIP SYMBOL
  │
  ├─ Signal = UP (YES≥86) or DOWN (NO≥86)
  │
  ├─ Get Momentum Score (last 3-4 candles)
  │
  ├─ Get Candle Score (pattern + body + run)
  │
  ├─ Get Market Score (price excess above 86¢)
  │
  ├─ Total Score = (mom*0.40) + (candle*0.35) + (market*0.25)
  │
  └─ Decision:
     ├─ total_score < 0.40 → $0.00 (skip)
     ├─ 0.40 ≤ total < 0.75 → size = interpolated $1-2
     ├─ 0.75 ≤ total ≤ 1.0  → size = interpolated $2-3
     └─ Place BUY order with (token_id, size_usd)
```

---

## Real-World Examples

### Example 1: Strong Everything
```
Asset: ETH
Current candles: ALL BULLISH (4 out of 4)
Pattern: THREE_BULL
Body ratio: 0.82 (thick)
Momentum: +1.8% (2 candles, strong move)
YES@0.93, NO@0.07 → signal = UP

Scores:
  momentum_score = min(1.8/1.0, 1.0) = 1.0
  m_score = 1.0 * 0.40 = 0.40
  
  pattern_base = 0.15 (THREE_BULL)
  body_effect = 0.82 * 0.30 = 0.246
  run_effect = 1.0 * 0.25 = 0.25
  candle_score = 0.15 + 0.246 + 0.25 = 0.646
  c_score = 0.646 * 0.35 = 0.226
  
  excess = 0.93 - 0.86 = 0.07
  market_score = 0.5 + (0.07/0.14) = 1.0 (clamped)
  p_score = 1.0 * 0.25 = 0.25
  
  TOTAL = 0.40 + 0.226 + 0.25 = 0.876 (88%)
  
Trade:
  ✅ MODE 2 ACTIVATED
  Size = 2.0 + ((0.876 - 0.75) / 0.25) * 1.0 = $2.50
  Bet = UP $2.50
  Confidence: "Strong momentum + bullish pattern + good market confirmation"
```

### Example 2: Weak Momentum + Good Pattern
```
Asset: BTC
Candles: Mixed (2 bull, 2 bear)
Pattern: HAMMER (local bottom bounce)
Body ratio: 0.45 (medium)
Momentum: +0.4% (very weak move)
YES@0.89, NO@0.11 → signal = UP

Scores:
  momentum_score = min(0.4/1.0, 1.0) = 0.40 (very weak)
  m_score = 0.40 * 0.40 = 0.16
  
  pattern_base = 0.20 (HAMMER)
  body_effect = 0.45 * 0.30 = 0.135
  run_effect = 0.5 * 0.25 = 0.125
  candle_score = 0.20 + 0.135 + 0.125 = 0.46
  c_score = 0.46 * 0.35 = 0.161
  
  excess = 0.89 - 0.86 = 0.03
  market_score = 0.5 + (0.03/0.14) = 0.714
  p_score = 0.714 * 0.25 = 0.179
  
  TOTAL = 0.16 + 0.161 + 0.179 = 0.50 (50%)
  
Trade:
  ✅ MODE 2 ACTIVATED
  Size = 1.0 + ((0.50 - 0.40) / 0.35) * 1.0 = $1.29
  Bet = UP $1.29
  Confidence: "Pattern reversal + weak momentum, conservative sizing"
```

### Example 3: Pattern Fails (DOJI)
```
Asset: SOL
Candles: Mix of directions
Pattern: DOJI (indecision)
Body ratio: 0.08 (very thin)
Momentum: +0.6%
YES@0.87, NO@0.13 → signal = UP

Scores:
  momentum_score = min(0.6/1.0, 1.0) = 0.60
  m_score = 0.60 * 0.40 = 0.24
  
  pattern_base = -0.15 (DOJI penalty!)
  body_effect = 0.08 * 0.30 = 0.024
  run_effect = 0.25 * 0.25 = 0.0625
  candle_score = max(-0.15 + 0.024 + 0.063, 0.0) = 0.0 (clamped)
  c_score = 0.0 * 0.35 = 0.0
  
  excess = 0.87 - 0.86 = 0.01
  market_score = 0.5 + (0.01/0.14) = 0.571
  p_score = 0.571 * 0.25 = 0.143
  
  TOTAL = 0.24 + 0.0 + 0.143 = 0.383 (38%)
  
Trade:
  ❌ MODE 2 FAILED
  Size = $0.00
  Reason: "below minimum threshold (38% < 40%)"
  Action: SKIP SOL, wait for clearer signal
```

---

## Parameters & Configurations

All parameters are configurable via `.env`:

| Parameter | Env Variable | Default | Meaning |
|-----------|---|---|---|
| Price Threshold | `PRICE_THRESHOLD` | 0.86 | Min token price to activate Mode 2 |
| Total Gate | `TOTAL_GATE` | 0.40 | Minimum total score to trade |
| Momentum Weight | `W_MOM` | 0.40 | Percentage weight (40%) |
| Candle Weight | `W_CANDLE` | 0.35 | Percentage weight (35%) |
| Market Weight | `W_MARKET` | 0.25 | Percentage weight (25%) |
| Min Bet | `MIN_SIZE` | 1.00 | Smallest trade size in USD |
| Max Bet | `MAX_SIZE` | 3.00 | Largest trade size in USD |
| Scan Interval | `SCAN_INTERVAL` | 300 | Seconds between cycles (5 min) |
| Max Open Trades | `MAX_OPEN_TRADES` | 3 | Concurrent position limit |
| Mom Score Cutoff | `MOM_SCORE_CUTOFF` | 0.30 | Threshold for Mode 2 activation |

---

## Mode 2 Strengths & Weaknesses

### ✅ Strengths
- **Pattern confirmation:** Uses actual candle analysis, not just price
- **Dynamic sizing:** Scales bet with confidence level
- **Gating:** Won't trade on weak signals
- **Momentum aware:** Detects genuine moves vs. noise
- **Market validated:** Requires 86¢ confirmation

### ❌ Weaknesses
- **Lag:** More calculations = slower decisions
- **Whipsaw risk:** Pattern can fail after entry
- **Less frequency:** Won't catch every 86¢ pop
- **Parameter sensitive:** Weights affect outcomes significantly

---

## Fine-Tuning Tips

**More conservative (fewer false positives):**
```
↑ TOTAL_GATE from 0.40 to 0.45
↓ W_CANDLE from 0.35 to 0.25
↓ MAX_SIZE from 3.00 to 2.00
```

**More aggressive (catch more trades):**
```
↓ TOTAL_GATE from 0.40 to 0.35
↑ W_CANDLE from 0.35 to 0.45
↑ MAX_SIZE from 3.00 to 4.00
```

**Momentum-focused:**
```
↑ W_MOM from 0.40 to 0.50
↓ W_CANDLE from 0.35 to 0.25
```

**Pattern-focused:**
```
↓ W_MOM from 0.40 to 0.25
↑ W_CANDLE from 0.35 to 0.50
```

---

## Implementation

File: [mode2_bot.py](mode2_bot.py)

Classes:
- **PriceFeed**: Fetches 5-min candles from Binance
- **PolymarketClient**: Handles Polymarket API (CLOB + auth)
- **CandleAnalyzer**: Detects patterns & momentum
- **Mode2Engine**: Calculates scores & sizes bets
- **Mode2Bot**: Main orchestration loop

Run:
```bash
python mode2_bot.py                  # Start bot (DRY_RUN=true by default)
python mode2_bot.py --list-markets   # Show available markets for market IDs
```

Config: Edit `.env` with your credentials and parameter tuning.
