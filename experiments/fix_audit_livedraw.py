#!/usr/bin/env python3
"""Live Draw at half-time: 1 won, 4 lost since 12 Sep. Corpus: quiet-gate games
(approximated from the venue series) level at the break, FT draw rate split by
the trailing second halves (how many of the 14 scored) and by 1H shot dominance."""
import json, collections, statistics as S
rows = [json.loads(l) for l in open('experiments/dataset.jsonl')]
rows = [r for r in rows if r.get('ft') and len(r['ft']) == 2 and r.get('h1') and len(r['h1']) == 2 and r.get('h2') and len(r['h2']) == 2]
rows.sort(key=lambda r: r['ts'])
hist = collections.defaultdict(list)   # team -> [(ts, 2h total)]
C = collections.Counter(); Dm = collections.Counter(); base = collections.Counter()
for r in rows:
    h, a = r['h'], r['a']
    ok = len(r['hgf']) >= 5 and len(r['agf']) >= 5
    if ok:
        hg = [x + y for x, y in zip(r['hgf'], r['hga'])]; ag = [x + y for x, y in zip(r['agf'], r['aga'])]
        xg = (S.mean(hg) + S.mean(ag)) / 2
        cd = sum(x == y for x, y in zip(r['hgf'], r['hga'])) + sum(x == y for x, y in zip(r['agf'], r['aga']))
        mm = abs((sum(r['hgf']) - sum(r['hga'])) / len(r['hgf']) - (sum(r['agf']) - sum(r['aga'])) / len(r['agf']))
        quiet = xg < 2.4 and cd >= 3 and mm <= 1.0
        level = r['h1'][0] == r['h1'][1] and r['h1'][0] <= 1
        if quiet and level:
            draw = r['ft'][0] == r['ft'][1]
            base['n'] += 1; base['draw'] += draw
            ph = [x[1] for x in hist[h] if x[0] < r['ts']][-7:]; pa = [x[1] for x in hist[a] if x[0] < r['ts']][-7:]
            if len(ph) == 7 and len(pa) == 7:
                scored = sum(x >= 1 for x in ph + pa)
                band = '14/14 scored' if scored == 14 else '12-13/14' if scored >= 12 else '<=11/14'
                C[(band, 'n')] += 1; C[(band, 'draw')] += draw
            st = r.get('st') or {}
            sh = st.get('shots_h1')
            if sh and sh[0] + sh[1] >= 4:
                dom = max(sh) / (sh[0] + sh[1])
                b2 = 'one side 75%+ of 1H shots' if dom >= 0.75 else 'shots shared'
                Dm[(b2, 'n')] += 1; Dm[(b2, 'draw')] += draw
    hist[h].append((r['ts'], r['h2'][0] + r['h2'][1])); hist[a].append((r['ts'], r['h2'][0] + r['h2'][1]))
print(f"quiet-gate games level at HT (0-0/1-1): n {base['n']}  FT draw {base['draw']/base['n']:.1%}")
print("\nby trailing second halves that scored (both sides, last 7 each)")
for k in ('<=11/14', '12-13/14', '14/14 scored'):
    n = C[(k, 'n')]
    if n: print(f"   {k:14} n {n:5}  FT draw {C[(k,'draw')]/n:.1%}")
print("\nby first-half shot dominance (rows with 1H shots)")
for k in ('shots shared', 'one side 75%+ of 1H shots'):
    n = Dm[(k, 'n')]
    if n: print(f"   {k:28} n {n:5}  FT draw {Dm[(k,'draw')]/n:.1%}")
