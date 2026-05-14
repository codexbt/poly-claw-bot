from tennis_edge_bot import CLOBClient, MarketScanner, TennisEdgeBot, CHAMP_PRICE_MIN, CHAMP_PRICE_MAX, INITIAL_ENTRY

clob = CLOBClient()
scanner = MarketScanner(clob)
bot = TennisEdgeBot()

print('Scanning markets (up to 5 pages) for tennis champions...')
markets = scanner.scan(max_pages=5)
print('Total tennis markets discovered:', len(markets))

executed = False
for m in markets:
    try:
        print('Checking:', m.player_a, 'vs', m.player_b, '| yes_price=', m.yes_price, 'vol=', m.volume)
        # check champion price range
        if CHAMP_PRICE_MIN <= m.yes_price <= CHAMP_PRICE_MAX:
            print('Market matches champion price range; attempting dry-run BUY')
            decision = {
                'action': 'BUY_CHAMPION',
                'player_name': m.player_a,
                'size_dollars': float(INITIAL_ENTRY),
                'confidence': 90,
            }
            ok = bot.execute(decision, m)
            if ok:
                print('Executed dry-run BUY for', m.player_a)
                executed = True
                break
        else:
            # also consider opposite side if yes_price is for underdog; swap logic
            alt_yes = m.no_price
            if CHAMP_PRICE_MIN <= alt_yes <= CHAMP_PRICE_MAX:
                # create decision to buy champion but player is player_b
                print('Champion appears to be NO-side; attempting dry-run BUY on underdog (player_b)')
                decision = {
                    'action': 'BUY_CHAMPION',
                    'player_name': m.player_b,
                    'size_dollars': float(INITIAL_ENTRY),
                    'confidence': 90,
                }
                ok = bot.execute(decision, m)
                if ok:
                    print('Executed dry-run BUY for', m.player_b)
                    executed = True
                    break
    except Exception as e:
        print('Error processing market:', e)

if not executed:
    print('No champion-priced markets found in this scan.')
else:
    print('Trade executed. Check ten n isedge_state.json and match_logs for details.')
