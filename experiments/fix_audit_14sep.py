#!/usr/bin/env python3
"""14 Sep loss audit: the opponent's OVERALL form and its recent venue run,
measured on the corpus (venue-GD favourite proxy, prior results only)."""
import json, collections
rows = [json.loads(l) for l in open('experiments/dataset.jsonl')]
rows = [r for r in rows if r.get('ft') and len(r['ft']) == 2]
rows.sort(key=lambda r: r['ts'])
def gd(gf, ga): return (sum(gf) - sum(ga)) / len(gf) if gf else None
hist = collections.defaultdict(list)     # team -> [(ts, result W/D/L, venue 'H'/'A')]
A = collections.Counter(); Bc = collections.Counter(); Cc = collections.Counter()
for r in rows:
    h, a = r['h'], r['a']; fh, fa = r['ft']
    if len(r['hgf']) >= 4 and len(r['agf']) >= 4:
        hg, ag = gd(r['hgf'], r['hga']), gd(r['agf'], r['aga'])
        side = 'H' if hg - ag >= 1.0 else 'A' if ag - hg >= 1.5 else None
        if side:
            fav, opp = (h, a) if side == 'H' else (a, h)
            pf = [x for x in hist[fav] if x[0] < r['ts']][-10:]; po = [x for x in hist[opp] if x[0] < r['ts']][-10:]
            if len(pf) >= 8 and len(po) >= 8:
                fw = sum(x[1] == 'W' for x in pf); ow = sum(x[1] == 'W' for x in po)
                won = fh > fa if side == 'H' else fa > fh; drew = fh == fa
                band = 'opp wins 7+' if ow >= 7 else 'opp wins 5-6' if ow >= 5 else 'opp wins <5'
                A[(band, 'n')] += 1; A[(band, 'won')] += won; A[(band, 'wd')] += won or drew
                rel = 'opp wins >= fav wins' if ow >= fw else 'fav wins more'
                Bc[(rel, 'n')] += 1; Bc[(rel, 'won')] += won; Bc[(rel, 'wd')] += won or drew
                # opponent's last 4 at ITS venue (the venue it plays this match at)
                ov = 'A' if side == 'H' else 'H'
                last4 = [x for x in hist[opp] if x[0] < r['ts'] and x[2] == ov][-4:]
                if len(last4) == 4:
                    hot = 'opp won 3+ of last 4 at venue' if sum(x[1] == 'W' for x in last4) >= 3 else 'opp not hot'
                    Cc[(hot, 'n')] += 1; Cc[(hot, 'won')] += won; Cc[(hot, 'wd')] += won or drew
    res_h = 'W' if fh > fa else 'D' if fh == fa else 'L'
    res_a = 'W' if fa > fh else 'D' if fh == fa else 'L'
    hist[h].append((r['ts'], res_h, 'H')); hist[a].append((r['ts'], res_a, 'A'))
for name, C, keys in (('A. by opponent overall wins in its last 10', A, ('opp wins <5', 'opp wins 5-6', 'opp wins 7+')),
                      ('B. opponent vs favourite overall wins', Bc, ('fav wins more', 'opp wins >= fav wins')),
                      ('C. opponent hot at its venue (3+ of last 4)', Cc, ('opp not hot', 'opp won 3+ of last 4 at venue'))):
    print('\n' + name)
    for k in keys:
        n = C[(k, 'n')]
        if n: print(f"   {k:32} n {n:6}  won {C[(k,'won')]/n:.1%}  win-or-draw {C[(k,'wd')]/n:.1%}")
