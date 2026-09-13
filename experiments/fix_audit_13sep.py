#!/usr/bin/env python3
"""13 Sep loss audit - measure the candidate fixes on the corpus before wiring any.
A. winners: favourite (venue-GD proxy) whose prior SoT-for is below the opponent's
B. live Under 1.5: pressing at HT 1-0 (shots share), and the 12/13/14-of-14 floor
C. live Draw: gate-free level-at-HT games by 1H SoT difference
"""
import json, collections, statistics as S
rows = [json.loads(l) for l in open('experiments/dataset.jsonl')]
rows = [r for r in rows if r.get('ft') and len(r['ft']) == 2]
rows.sort(key=lambda r: r['ts'])
print(f"{len(rows)} matches")

def gd(gf, ga): return (sum(gf) - sum(ga)) / len(gf) if gf else None

# ---- per-team prior SoT-for / against (any venue), and prior 2H totals
hist = collections.defaultdict(list)          # team -> [(ts, sot_for, sot_against, h2_total)]
def prior(team, ts, n=7):
    h = [x for x in hist[team] if x[0] < ts]
    return h[-n:]
A = collections.Counter(); B = collections.Counter(); D = collections.Counter(); C = collections.Counter()
for r in rows:
    h, a = r['h'], r['a']; fh, fa = r['ft']
    # ---------- A: winners with SoT check
    if len(r['hgf']) >= 4 and len(r['agf']) >= 4:
        hg, ag = gd(r['hgf'], r['hga']), gd(r['agf'], r['aga'])
        side = None
        if hg - ag >= 1.0: side = 'H'
        elif ag - hg >= 1.5: side = 'A'
        if side:
            ph, pa = prior(h, r['ts']), prior(a, r['ts'])
            sh = [x[1] for x in ph if x[1] is not None]; sa = [x[1] for x in pa if x[1] is not None]
            if len(sh) >= 3 and len(sa) >= 3:
                fav_sot = S.mean(sh) if side == 'H' else S.mean(sa)
                opp_sot = S.mean(sa) if side == 'H' else S.mean(sh)
                won = fh > fa if side == 'H' else fa > fh
                drew = fh == fa
                key = (side, 'fav lower SoT' if fav_sot < opp_sot else 'fav higher/equal SoT')
                A[key + ('n',)] += 1; A[key + ('won',)] += won; A[key + ('wd',)] += (won or drew)
    # ---------- B/D: live Under from trailing 2H totals + HT state
    h1 = r.get('h1'); h2 = r.get('h2')
    if h1 and h2 and len(h1) == 2 and len(h2) == 2:
        ph, pa = prior(h, r['ts']), prior(a, r['ts'])
        t2 = [x[3] for x in ph if x[3] is not None] + [x[3] for x in pa if x[3] is not None]
        if len([x for x in ph if x[3] is not None]) >= 7 and len([x for x in pa if x[3] is not None]) >= 7:
            u = sum(x <= 1 for x in t2[:7]) + sum(x <= 1 for x in [x[3] for x in pa if x[3] is not None][-7:])
            u = sum(x <= 1 for x in ([x[3] for x in ph if x[3] is not None][-7:] + [x[3] for x in pa if x[3] is not None][-7:]))
            banked = h1[0] + h1[1]
            h2tot = h2[0] + h2[1]
            if u >= 12 and banked <= 1:
                D[(u, banked, 'n')] += 1; D[(u, banked, 'u15')] += (h2tot <= 1)
                st = r.get('st') or {}
                sh1 = st.get('shots_h1')
                if sh1 and banked == 1:
                    tot = sh1[0] + sh1[1]
                    trailing = 0 if h1[0] < h1[1] else 1          # index of the side behind
                    share = sh1[trailing] / tot if tot else 0
                    press = tot >= 10 and share >= 0.6
                    B[('1-0', 'pressing' if press else 'not', 'n')] += 1; B[('1-0', 'pressing' if press else 'not', 'u15')] += (h2tot <= 1)
    # ---------- C: level at HT -> FT draw by 1H SoT gap
    st = r.get('st') or {}
    if h1 and len(h1) == 2 and h1[0] == h1[1] and h1[0] <= 1 and st.get('sot_h1'):
        gap = abs(st['sot_h1'][0] - st['sot_h1'][1])
        band = 'gap 0-1' if gap <= 1 else 'gap 2' if gap == 2 else 'gap 3+'
        C[(band, 'n')] += 1; C[(band, 'draw')] += (fh == fa)
    # ---------- update history (after use: no leak)
    sot = st.get('sot')
    hist[h].append((r['ts'], sot[0] if sot else None, sot[1] if sot else None, (h2[0] + h2[1]) if h2 and len(h2) == 2 else None))
    hist[a].append((r['ts'], sot[1] if sot else None, sot[0] if sot else None, (h2[0] + h2[1]) if h2 and len(h2) == 2 else None))

print("\nA. winners rule (venue-GD favourite) split by prior SoT-for, fav vs opp")
for side in ('H', 'A'):
    for k in ('fav lower SoT', 'fav higher/equal SoT'):
        n = A[(side, k, 'n')]
        if n: print(f"  {side} {k:22} n {n:5}  won {A[(side,k,'won')]/n:.1%}  win-or-draw {A[(side,k,'wd')]/n:.1%}")
print("\nD. 2H Under 1.5 by trailing-halves count (<=1) and goals banked at HT")
for u in (12, 13, 14):
    for b in (0, 1):
        n = D[(u, b, 'n')]
        if n: print(f"  {u}/14  HT total {b}  n {n:5}  2H <=1 goal {D[(u,b,'u15')]/n:.1%}")
print("\nB. same rule at HT 1-0: trailing side pressing (>=10 1H shots, >=60% of them) vs not")
for p in ('pressing', 'not'):
    n = B[('1-0', p, 'n')]
    if n: print(f"  {p:9} n {n:4}  2H <=1 goal {B[('1-0',p,'u15')]/n:.1%}")
print("\nC. level at HT (0-0/1-1) -> FT draw by 1H shots-on-target gap")
for band in ('gap 0-1', 'gap 2', 'gap 3+'):
    n = C[(band, 'n')]
    if n: print(f"  {band:8} n {n:5}  FT draw {C[(band,'draw')]/n:.1%}")
