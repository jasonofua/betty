#!/usr/bin/env python3
"""Draw search, round three. Structural and situational angles, judged by
return at the closing price on both halves of 2015-26."""
import json, collections, math
import numpy as np
rows = [json.loads(l) for l in open('experiments/odds_history.jsonl')]
rows.sort(key=lambda r: r['ts'])
def pick(r, *ks):
    for k in ks:
        if r.get(k): return r[k]
C = collections.defaultdict(lambda: collections.Counter())
def acc(name, r, d, od, imp):
    for h in ('A' if int(r['season']) <= 2021 else 'B', 'all'):
        c = C[(name, h)]; c['n'] += 1; c['d'] += d; c['ret'] += od if d else 0; c['imp'] += imp

# --- Poisson / Dixon-Coles draw probability from the market's supremacy and total
def pois(k, l): return math.exp(-l) * l ** k / math.factorial(k)
def dc_draw(lh, la, rho=-0.10):
    p = sum(pois(k, lh) * pois(k, la) for k in range(0, 9))
    # Dixon-Coles low-score correction on 0-0 and 1-1
    p += pois(0, lh) * pois(0, la) * (-lh * la * rho) + pois(1, lh) * pois(1, la) * (-rho)
    return p
def total_from_ou(o_over, o_under):
    """expected total goals from an Over/Under 2.5 pair (Poisson inversion)."""
    p_over = (1 / o_over) / (1 / o_over + 1 / o_under)
    lo, hi = 0.5, 6.0
    for _ in range(40):
        mid = (lo + hi) / 2
        pu = sum(pois(k, mid) for k in range(0, 3))
        if 1 - pu > p_over: hi = mid
        else: lo = mid
    return (lo + hi) / 2
def split(total, imp_h, imp_a):
    """home/away lambdas: total split by the market's 1X2 supremacy (grid search)."""
    best = None
    for f in np.linspace(0.2, 0.8, 25):
        lh, la = total * f, total * (1 - f)
        ph = sum(pois(i, lh) * pois(j, la) for i in range(9) for j in range(9) if i > j)
        pa = sum(pois(i, lh) * pois(j, la) for i in range(9) for j in range(9) if i < j)
        err = (ph - imp_h) ** 2 + (pa - imp_a) ** 2
        if best is None or err < best[0]: best = (err, lh, la)
    return best[1], best[2]

# --- standings for the dead-rubber test
table = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0]))   # (lg, season) -> team -> [pts, played]
ref_hist = collections.defaultdict(list)
for r in rows:
    od, oh, oa = pick(r, 'pscd', 'psd', 'avgd', 'b365d'), pick(r, 'psch', 'psh', 'avgh', 'b365h'), pick(r, 'psca', 'psa', 'avga', 'b365a')
    d = int(r['hg'] == r['ag'])
    key = (r['lg'], r['season']); tb = table[key]
    if od and oh and oa:
        s = 1 / oh + 1 / od + 1 / oa; imp_d, imp_h, imp_a = (1 / od) / s, (1 / oh) / s, (1 / oa) / s
        # 1. structural mispricing (needs the O/U 2.5 price - main leagues)
        if r.get('b365>2.5') or r.get('avg>2.5'):
            pass
        ou_o, ou_u = r.get('avg>2.5') or r.get('b365>2.5'), r.get('avg<2.5') or r.get('b365<2.5')
        if ou_o and ou_u:
            tot = total_from_ou(ou_o, ou_u); lh, la = split(tot, imp_h, imp_a)
            pm = dc_draw(lh, la)
            gap = pm - imp_d
            band = 'model >= market + 3pts' if gap >= 0.03 else 'model >= market + 1pt' if gap >= 0.01 else 'model < market'
            acc(f"DC structural: {band}", r, d, od, imp_d)
            if gap >= 0.03 and 0.30 <= imp_d < 0.36: acc('DC structural +3pts, band 30-36', r, d, od, imp_d)
        # 2. bookmaker spread on the draw
        if r.get('maxd') and r.get('avgd'):
            sp = r['maxd'] / r['avgd']
            acc(f"draw spread max/avg {'>= 1.08' if sp >= 1.08 else '1.04-1.08' if sp >= 1.04 else '< 1.04'} (at max)", r, d, r['maxd'], imp_d)
        # 3. favourite side
        acc('away side favourite' if imp_a > imp_h else 'home side favourite', r, d, od, imp_d)
        if abs(imp_h - imp_a) < 0.05: acc('near-even match (|home-away| < 5pts)', r, d, od, imp_d)
        # 4. dead rubber: last 8 rounds, both mid-table
        n_teams = len(tb) or 20
        if tb and tb[r['home']][1] >= 26 and tb[r['away']][1] >= 26:
            ranks = sorted(tb, key=lambda t: -tb[t][0])
            rh, ra = ranks.index(r['home']) + 1, ranks.index(r['away']) + 1
            mid = lambda rk: 6 <= rk <= n_teams - 5
            if mid(rh) and mid(ra): acc('late season, both mid-table', r, d, od, imp_d)
            if rh <= 3 or ra <= 3: acc('late season, a title/promotion side', r, d, od, imp_d)
            if rh >= n_teams - 2 and ra >= n_teams - 2: acc('late season, both in the drop zone', r, d, od, imp_d)
        # 5. referee (E0 files carry it)
        ref = r.get('referee')
    # update standings
    pts_h = 3 if r['hg'] > r['ag'] else 1 if d else 0; pts_a = 3 if r['ag'] > r['hg'] else 1 if d else 0
    tb[r['home']][0] += pts_h; tb[r['home']][1] += 1; tb[r['away']][0] += pts_a; tb[r['away']][1] += 1

names = []
for (name, h) in C:
    if name not in names: names.append(name)
print(f"{'rule':44}{'half':5}{'n':>7}{'drew':>7}{'market':>8}{'return':>9}")
for name in names:
    for h in ('A', 'B', 'all'):
        c = C.get((name, h))
        if not c or c['n'] < 150: continue
        print(f"{name:44}{h:5}{c['n']:7}{c['d']/c['n']:7.1%}{c['imp']/c['n']:8.1%}{c['ret']/c['n']-1:+9.1%}")
