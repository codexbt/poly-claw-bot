import csv

fn='trades_log.csv'
rows=[]
with open(fn, newline='', encoding='utf-8') as f:
    r=csv.reader(f)
    for row in r:
        rows.append(row)

starting_balance=100.0
wins=[]
losses=[]
for row in rows:
    if len(row)<12:
        continue
    outcome=row[10].strip()
    try:
        pnl=float(row[11])
    except:
        pnl=0.0
    entry_ts=row[0]
    market=row[1]
    side=row[2]
    exit_ts=row[13] if len(row)>13 else ''
    rec={'entry_ts':entry_ts,'market':market,'side':side,'outcome':outcome,'pnl':pnl,'fee':row[12] if len(row)>12 else '', 'exit_ts': exit_ts}
    if outcome.upper()=='WIN':
        wins.append(rec)
    elif outcome.upper()=='LOSS':
        losses.append(rec)

sum_win=sum(r['pnl'] for r in wins)
sum_loss=sum(r['pnl'] for r in losses)
net = sum_win - sum_loss
updated = starting_balance + net
print(f"wins={len(wins)}, losses={len(losses)}")
print(f"sum_win={sum_win:.4f}, sum_loss={sum_loss:.4f}, net_pnl={net:.4f}")
print(f"starting_balance={starting_balance:.2f}, updated_balance={updated:.4f}\n")
print('DETAILS:')
for r in wins+losses:
    print(f"{r['entry_ts']} | {r['market']} | {r['side']} | {r['outcome']} | pnl={r['pnl']:.4f} | fee={r['fee']} | exit={r['exit_ts']}")
