"""Backtest the HALF-TIME draw inside the deployed draw gate.

Rows: experiments/draw_dataset.jsonl (strictly pre-match features) joined to
the corpus half-time scores (dataset.jsonl h1) on (ts, home, away).
Gate: the deployed book_draw.in_pocket 'both' pocket with the 2.0 SoT
evenness floor. Everything is walked in kickoff order; the model on top is
scored ONLY on its own held-out slice (newest 20% of pocket rows).
"""
import json, pickle, datetime as dt, collections, sys
import numpy as np
ROOT = '/Users/apple/Downloads/draw/experiments/'
B = pickle.load(open(ROOT + 'draw_model.pkl', 'rb'))
P, MODEL, FEATS, P_CUT = B['pocket'], B['model'], B['feats'], B['p_cut']

def in_pocket(f):
    if not (f['xg'] < P['xg_max'] and f['cd'] >= P['cd_min'] and f['mismatch'] <= P['mm_max']):
        return False
    if f.get('sxg') is None or f.get('smis') is None:
        return False
    if f['sxg'] > P['sxg_max'] or f['smis'] > min(P['smis_max'], 2.0):
        return False
    if f['blank'] < P.get('blank_min', 0):
        return False
    if abs(f['h_blank'] - f['a_blank']) > P.get('bgap_max', 99):
        return False
    return True

ht = {}; alt = collections.defaultdict(set)
for l in open(ROOT + 'dataset.jsonl'):
    r = json.loads(l)
    if r.get('h1') is None or r.get('ft') is None: continue
    if 'h' in r:
        ht[(r['ts'], r['h'], r['a'])] = tuple(r['h1'])
    else:   # newer crawl rows carry no names: join on kickoff+league+final score, unique only
        alt[(r['ts'], r['lg'], r['ft'][0], r['ft'][1])].add(tuple(r['h1']))
rows = [json.loads(l) for l in open(ROOT + 'draw_dataset.jsonl')]
for r in rows:
    k = (r['ts'], r['home'], r['away'])
    if k not in ht:
        s = alt.get((r['ts'], r['lg'], r['ft_h'], r['ft_a']))
        if s and len(s) == 1: ht[k] = next(iter(s))
rows.sort(key=lambda r: r['ts'])
print(f"draw_dataset rows {len(rows)}   with HT score {sum((r['ts'],r['home'],r['away']) in ht for r in rows)}")

pocket = [r for r in rows if in_pocket(r)]
gate = [r for r in pocket if (r['ts'], r['home'], r['away']) in ht]
for r in gate:
    h1 = ht[(r['ts'], r['home'], r['away'])]
    r['htd'] = int(h1[0] == h1[1]); r['ht_goals'] = h1[0] + h1[1]
    r['d'] = dt.datetime.utcfromtimestamp(r['ts'])
allht = [r for r in rows if (r['ts'], r['home'], r['away']) in ht]
base = np.mean([ht[(r['ts'], r['home'], r['away'])][0] == ht[(r['ts'], r['home'], r['away'])][1] for r in allht])
print(f"pocket rows {len(pocket)}   pocket rows with HT score {len(gate)}")
print(f"\nALL matches HT draw {base:.1%}   GATE HT draw {np.mean([r['htd'] for r in gate]):.1%} (fair {1/np.mean([r['htd'] for r in gate]):.2f})"
      f"   GATE FT draw {np.mean([r['draw'] for r in gate]):.1%}")
print(f"HT draw AND FT draw {np.mean([r['htd'] and r['draw'] for r in gate]):.1%}   HT draw but FT not {np.mean([r['htd'] and not r['draw'] for r in gate]):.1%}"
      f"   FT draw without HT draw {np.mean([r['draw'] and not r['htd'] for r in gate]):.1%}")

def block(title, groups):
    print(f"\n{title}")
    print(f"{'':28}{'n':>6}{'HT draw':>9}{'fair':>6}{'FT draw':>9}")
    for k, g in groups:
        if not g: continue
        p = np.mean([r['htd'] for r in g]); q = np.mean([r['draw'] for r in g])
        print(f"{str(k):28}{len(g):>6}{p:>9.1%}{1/p if p else 0:>6.2f}{q:>9.1%}")

# by period, walked forward
per = collections.OrderedDict()
for r in gate:
    key = f"{r['d'].year}-H{1 if r['d'].month<=6 else 2}" if r['d'].year < 2026 else f"{r['d'].year}-{r['d'].month:02d}"
    per.setdefault(key, []).append(r)
block("BY PERIOD (gate, kickoff order)", per.items())

# the held-out slice of the deployed model (newest 20% of pocket rows) - model on top
n = len(pocket); te = pocket[int(n*0.8):]
te = [r for r in te if 'htd' in r]
X = np.array([[np.nan if r.get(k) is None else r[k] for k in FEATS] for r in te], dtype=float)
p = MODEL.predict_proba(X)[:, 1]
above = [r for r, pp in zip(te, p) if pp >= P_CUT]; below = [r for r, pp in zip(te, p) if pp < P_CUT]
block("MODEL ON TOP - held-out 20% only (model trained on FT draws)",
      [("held-out gate rows", te), (f"model p >= {P_CUT:.3f} (deployed)", above), ("model below cut", below)])

def bucket(name, key, edges):
    groups = []
    for lo, hi in zip(edges, edges[1:]):
        groups.append((f"{name} {lo}..{hi}", [r for r in gate if r.get(key) is not None and lo <= r[key] < hi]))
    groups.append((f"{name} missing", [r for r in gate if r.get(key) is None]))
    return groups
block("LEAGUE DRAW RATE (pre-match)", bucket('lg_draw', 'lg_draw', [0, 0.26, 0.30, 0.34, 1.0]))
block("TRAILING 1H GOALS, both sides summed", bucket('sum_htgoals', 'sum_htgoals', [0, 1.5, 2.0, 2.5, 9]))
block("HT-DRAW HABIT, both sides summed", bucket('sum_htdraw', 'sum_htdraw', [0, 0.6, 0.8, 1.0, 1.2, 3]))
block("EXPECTED GOALS (xg)", bucket('xg', 'xg', [0, 1.8, 2.0, 2.2, 2.4]))
block("EXPECTED SoT (sxg)", bucket('sxg', 'sxg', [0, 5, 6, 7, 8.01]))

# combined 1H filters inside the gate
def sub(title, fn):
    g = [r for r in gate if fn(r)]
    p = np.mean([r['htd'] for r in g]) if g else 0
    print(f"{title:60}{len(g):>6}{p:>9.1%}{(1/p if p else 0):>6.2f}")
print(f"\n{'COMBINED CUTS INSIDE THE GATE':60}{'n':>6}{'HT draw':>9}{'fair':>6}")
sub("gate", lambda r: True)
sub("league >= 34%", lambda r: (r.get('lg_draw') or 0) >= 0.34)
sub("sum_htgoals < 2.0", lambda r: r.get('sum_htgoals') is not None and r['sum_htgoals'] < 2.0)
sub("sum_htdraw >= 1.0", lambda r: r.get('sum_htdraw') is not None and r['sum_htdraw'] >= 1.0)
sub("league >= 34% AND sum_htgoals < 2.0", lambda r: (r.get('lg_draw') or 0) >= 0.34 and r.get('sum_htgoals') is not None and r['sum_htgoals'] < 2.0)
sub("sum_htgoals < 2.0 AND sum_htdraw >= 1.0", lambda r: r.get('sum_htgoals') is not None and r['sum_htgoals'] < 2.0 and (r.get('sum_htdraw') or 0) >= 1.0)
sub("xg < 2.0", lambda r: r['xg'] < 2.0)

# money: flat 1 unit on every gate HT draw at an assumed price, kickoff order
print("\nFLAT-STAKE P&L over the gate in kickoff order (assumed 1st-half draw price)")
print(f"{'price':>6}{'bets':>6}{'units':>8}{'ROI':>8}{'max DD':>8}{'worst run':>10}{'worst 50':>9}{'best 50':>9}")
y = np.array([r['htd'] for r in gate])
for price in (2.00, 2.05, 2.10, 2.20):
    pnl = np.where(y == 1, price - 1, -1.0); eq = np.cumsum(pnl)
    dd = np.max(np.maximum.accumulate(eq) - eq)
    run = 0; worst = 0
    for v in y:
        run = run + 1 if v == 0 else 0; worst = max(worst, run)
    roll = [y[i:i+50].mean() for i in range(0, len(y) - 49)]
    print(f"{price:>6.2f}{len(y):>6}{eq[-1]:>8.1f}{eq[-1]/len(y):>8.1%}{dd:>8.1f}{worst:>10}{min(roll):>9.1%}{max(roll):>9.1%}")

# slip-level: all gate candidates of a day in ONE slip
days = collections.defaultdict(list)
for r in gate:
    days[r['d'].date()].append(r['htd'])
bylen = collections.defaultdict(list)
for d, v in days.items():
    bylen[min(len(v), 5)].append(all(v))
print("\nONE SLIP PER DAY with every gate candidate as a 1H draw leg")
print(f"{'legs':>6}{'days':>6}{'all won':>9}{'indep. expectation':>20}")
p1 = y.mean()
for k in sorted(bylen):
    print(f"{('5+' if k==5 else k):>6}{len(bylen[k]):>6}{np.mean(bylen[k]):>9.1%}{p1**k:>20.1%}")
