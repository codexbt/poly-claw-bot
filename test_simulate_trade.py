from tennis_edge_bot import TennisEdgeBot, MarketInfo, PositionManager, DRY_RUN, MAX_BANKROLL, INITIAL_ENTRY

# Create bot instance
bot = TennisEdgeBot()

# Replace LLM decision with deterministic buy decision for champion
def fake_decision(ctx):
    market = ctx['market']
    return {
        'action': 'BUY_CHAMPION',
        'player_name': market['player_a'],
        'size_dollars': float(INITIAL_ENTRY),
        'confidence': 90,
        'match_phase': 'PRE_MATCH',
        'reason': 'Simulated test buy',
        'current_position': {
            'champion_shares': 0,
            'champion_avg_price': 0,
            'underdog_shares': 0,
            'underdog_avg_price': 0,
            'total_cost': 0
        }
    }

bot.llm.get_decision = fake_decision

# Create a fake market that looks like tennis
m = MarketInfo(
    condition_id='TEST-MKT-1',
    question='Tennis: Novak Djokovic vs. Carlos Alcaraz',
    yes_token_id='tok_yes_test',
    no_token_id='tok_no_test',
    yes_price=0.72,
    no_price=0.28,
    volume=5000.0,
    active=True,
    player_a='Novak Djokovic',
    player_b='Carlos Alcaraz'
)

# Patch CLOBClient price functions to return consistent prices for test tokens
bot.clob.best_ask_price = lambda token_id: 0.72 if token_id=='tok_yes_test' else 0.28
bot.clob.best_bid_price = lambda token_id: 0.71 if token_id=='tok_yes_test' else 0.27
bot.clob.get_balance = lambda: float(MAX_BANKROLL)

print('Starting simulated market process...')
bot.process_market(m)

# Show state
pm = bot.pm
print('\nPositions saved:')
for k,v in pm.positions.items():
    print(k, v.summary())

# Show per-match log file
import os
from tennis_edge_bot import MATCH_LOG_DIR, safe_filename
fname = os.path.join(MATCH_LOG_DIR, safe_filename(m.player_a)+'.txt')
print('\nMatch log file:', fname)
if os.path.exists(fname):
    print(open(fname,'r',encoding='utf-8').read())
else:
    print('No match log created')
