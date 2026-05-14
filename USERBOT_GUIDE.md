# USERBOT.PY - USER TRACKING GUIDE

## Kaunse User Ko Track Kiya Ja Raha Hai?

**Answer:** Bot yeh wallet address track karta hai:
```
0x82ff01408b945af138d3c4619dcf876387d52b09
```

Ye address file ke top par, CONFIG SECTION mein likha hota hai:
```python
POLYMARKET_USER_WALLET = "0x82ff01408b945af138d3c4619dcf876387d52b09"  # Jo track hoga
```

## Kaise Pata Chalega?

### 1. **Console Output Mein**
जब bot start karo, ye message dikhega:
```
POLYMARKET INTELLIGENCE REPORT FOR USER: 0x82ff01408b945af138d3c4619dcf876387d52b09
Timestamp: 2026-04-21 13:10:29
```

### 2. **intelligence_log.txt File Mein**
Sab log file mein save hota hai. Isme bhi wallet address likha hota hai.

### 3. **Command Line Output **
Har 30 seconds mein ye sections print honge:
- SECTION 1: OTHER BOT INTELLIGENCE
- SECTION 2: POLYMARKET ORDER BOOK INTELLIGENCE  
- SECTION 3: POLYMARKET USER - RUNNING POSITIONS (for ye WALLET)
- SECTION 4: POLYMARKET USER - PREVIOUS TRADES (for ye WALLET)

---

## Alag User Ko Track Karna Hai?

1. **userbot.py file kholo**
2. **Line 20 par yeh dhoondo:**
   ```python
   POLYMARKET_USER_WALLET = "0x82ff01408b945af138d3c4619dcf876387d52b09"
   ```
3. **Apna wallet address likho:**
   ```python
   POLYMARKET_USER_WALLET = "0xAAPKA_NAYA_WALLET_ADDRESS"
   ```
4. **File save karo aur bot start karo**

---

## Kya Data Track Ho Raha Hai?

### SECTION 1 - OTHER BOT
- Kisi or bot ke trades
- Win rate, profit/loss, strategy

### SECTION 2 - ORDER BOOK  
- Bid-ask prices
- Market liquidity
- Token pricing

### SECTION 3 - RUNNING POSITIONS
- **WALLET ke current OPEN trades**
- Kitni size
- Unrealized P&L
- Kaun se markets mein paise hain

### SECTION 4 - PREVIOUS TRADES
- **WALLET ke CLOSED trades**
- Total realized P&L
- Win rate
- Trading strategy (Scalping/Position Trading etc)

---

## Log File Location

```
d:\btcupdownclaudebot\intelligence_log.txt
```

Yeh file continuously update hota hai aur sab data save karta hai.

---

## Agar Error Aye?

Ya tum directly run karo:
```
python userbot.py
```

Ya terminal mein:
```
cd d:\btcupdownclaudebot
python userbot.py
```

---

**NOTE:** Har 30 seconds mein naya analysis run hota hai aur intelligence_log.txt mein add hota hai.
