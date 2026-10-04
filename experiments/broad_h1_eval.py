"""Evaluate first-half rules on experiments/broad_h1_rows.jsonl (see broad_h1_test.py)."""
import json, sys, statistics
sys.path.insert(0, '/Users/apple/Downloads/draw')
import book_h1unders as H
rows = [json.loads(l) for l in open('/Users/apple/Downloads/draw/experiments/broad_h1_rows.jsonl')]
rows = [r for r in rows if len(r['hp']) >= 5 and len(r['ap']) >= 5]
mid = statistics.median(r['ko'] for r in rows)
for r in rows: r['early'] = r['ko'] < mid
def f(s):
    w = sum(s); return f"{w}/{len(s)}={w / max(1, len(s)):.0%}"
def rep(name, legs):
    """legs: [(won, early)]"""
    print(f"  {name:58} {f([w for w, _ in legs]):13} early {f([w for w, e in legs if e]):12} late {f([w for w, e in legs if not e])}")
print(f"{len(rows)} fixtures with 5+ venue games a side; median kickoff splits early/late\n")
# ---- match first-half Under 2.5 / 1.5
def feats(r, cap):
    hp, ap = r['hp'], r['ap']; tot = [x + y for x, y in hp + ap]
    d = dict(rate=sum(v <= cap for v in tot) / len(tot),
             hb=sum(x + y > cap for x, y in hp), ab=sum(x + y > cap for x, y in ap),
             minb=min(sum(x + y == 0 for x, y in hp), sum(x + y == 0 for x, y in ap)),
             attack=sum(x for x, _ in hp) / len(hp) + sum(x for x, _ in ap) / len(ap),
             minside=min(sum(x + y <= cap for x, y in hp) / len(hp), sum(x + y <= cap for x, y in ap) / len(ap)),
             two=sum(v >= 2 for v in tot) / len(tot), maxhalf=max(tot))
    lam = H.expected_half(hp, ap); d['model'] = H.poisson_under(lam, cap); d['lam'] = lam
    d['won'] = sum(r['ht']) <= cap
    return d
print('== 1st Half Under 2.5')
L = [(r, feats(r, 2)) for r in rows]
base = [(r, d) for r, d in L if d['rate'] >= H.MIN_RATE and not (d['hb'] and d['ab'])]
rep('qualifies before the goalless gate', [(d['won'], r['early']) for r, d in base])
cur = [(r, d) for r, d in base if d['minb'] >= H.MIN_BLANK_HALVES]
rep('CURRENT (with goalless gate)', [(d['won'], r['early']) for r, d in cur])
rep('  dropped by goalless gate', [(d['won'], r['early']) for r, d in base if d['minb'] < H.MIN_BLANK_HALVES])
for k in (3,):
    rep(f'  goalless gate at {k}', [(d['won'], r['early']) for r, d in base if d['minb'] >= k])
rep('  current + clean only', [(d['won'], r['early']) for r, d in cur if not d['hb'] and not d['ab']])
rep('  current + one-breach only', [(d['won'], r['early']) for r, d in cur if bool(d['hb']) != bool(d['ab'])])
for t in (0.25, 0.35):
    rep(f'  current, 2+ goal halves under {int(t*100)}%', [(d['won'], r['early']) for r, d in cur if d['two'] < t])
    rep(f'  current, 2+ goal halves {int(t*100)}%+', [(d['won'], r['early']) for r, d in cur if d['two'] >= t])
for t in (0.8, 1.1):
    rep(f'  current, attack under {t}', [(d['won'], r['early']) for r, d in cur if d['attack'] < t])
    rep(f'  current, attack {t}+', [(d['won'], r['early']) for r, d in cur if d['attack'] >= t])
print('\n== 1st Half Under 1.5 (no prices here, so no price cap)')
L = [(r, feats(r, 1)) for r in rows]
base = [(r, d) for r, d in L if d['model'] >= H.MIN_MODEL]
rep('model >= 0.70 only', [(d['won'], r['early']) for r, d in base])
cur = [(r, d) for r, d in base if d['attack'] < H.BUSY_DROP_15 and d['minside'] >= H.MIN_SIDE_15 - 1e-9 and d['minb'] >= H.MIN_BLANK_HALVES]
rep('CURRENT (attack<1.1, side col 70%, goalless gate)', [(d['won'], r['early']) for r, d in cur])
rep('  without goalless gate', [(d['won'], r['early']) for r, d in base if d['attack'] < H.BUSY_DROP_15 and d['minside'] >= H.MIN_SIDE_15 - 1e-9])
for t in (0.8, 0.86):
    rep(f'  current + side col {int(t*100)}%+', [(d['won'], r['early']) for r, d in cur if d['minside'] >= t - 1e-9])
for t in (0.6, 0.8):
    rep(f'  current + attack under {t}', [(d['won'], r['early']) for r, d in cur if d['attack'] < t])
rep('  current + goalless gate 3', [(d['won'], r['early']) for r, d in cur if d['minb'] >= 3])
rep('  current + no 2-goal half at all', [(d['won'], r['early']) for r, d in cur if d['two'] == 0])
# ---- team first-half Unders
print('\n== Team 1st Half Under (each side that qualifies, current rule: own scoring col and opponent conceding col 6/7+)')
for line in (0.5, 1.5):
    legs = []
    for r in rows:
        hp, ap = r['hp'], r['ap']
        for side in ('home', 'away'):
            if side == 'home':
                own = [x for x, _ in hp]; opp = [y for _, y in ap]; got = r['ht'][0]
                oppown = [x for x, _ in ap]; mine_c = [y for _, y in hp]
            else:
                own = [x for x, _ in ap]; opp = [y for _, y in hp]; got = r['ht'][1]
                oppown = [x for x, _ in hp]; mine_c = [y for _, y in ap]
            oc = sum(v < line for v in own) / len(own); pc = sum(v < line for v in opp) / len(opp)
            legs.append(dict(oc=oc, pc=pc, won=got < line, early=r['early'],
                             minb=min(sum(x + y == 0 for x, y in hp), sum(x + y == 0 for x, y in ap)),
                             own_avg=sum(own) / len(own), opp_c_avg=sum(opp) / len(opp),
                             max_own=max(own), max_opp=max(opp), n=min(len(own), len(opp))))
    print(f' -- team Under {line}')
    cur = [l for l in legs if min(l['oc'], l['pc']) >= 6 / 7 - 1e-9]
    rep('CURRENT (both columns 6/7+)', [(l['won'], l['early']) for l in cur])
    rep('  both columns 100%', [(l['won'], l['early']) for l in cur if min(l['oc'], l['pc']) >= 1 - 1e-9])
    rep('  own 100%, opponent 6/7', [(l['won'], l['early']) for l in cur if l['oc'] >= 1 - 1e-9 and l['pc'] < 1 - 1e-9])
    rep('  own 6/7, opponent 100%', [(l['won'], l['early']) for l in cur if l['oc'] < 1 - 1e-9 and l['pc'] >= 1 - 1e-9])
    rep('  + match goalless gate (2+ each side)', [(l['won'], l['early']) for l in cur if l['minb'] >= 2])
    rep('  + match goalless gate fails', [(l['won'], l['early']) for l in cur if l['minb'] < 2])
    rep('  7 games each', [(l['won'], l['early']) for l in cur if l['n'] >= 7])
    rep('  5-6 games', [(l['won'], l['early']) for l in cur if l['n'] < 7])
    for t in ((0.15, 0.3) if line == 0.5 else (0.3, 0.5)):
        rep(f'  own first-half scoring avg under {t}', [(l['won'], l['early']) for l in cur if l['own_avg'] < t])
        rep(f'  own first-half scoring avg {t}+', [(l['won'], l['early']) for l in cur if l['own_avg'] >= t])
        rep(f'  opponent conceding avg under {t}', [(l['won'], l['early']) for l in cur if l['opp_c_avg'] < t])
        rep(f'  opponent conceding avg {t}+', [(l['won'], l['early']) for l in cur if l['opp_c_avg'] >= t])
