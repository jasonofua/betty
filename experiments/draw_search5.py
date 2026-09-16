#!/usr/bin/env python3
"""The user's hypothesis: within the market's 27%+ draw band, two sides close to
each other in the table both settle for a point. Tested two ways on football-data
2015-26: (a) the points gap in the live standings at match time (after 8+ rounds);
(b) the gap in points-per-game over each side's last 10 league games (what the
engine can read today from the form feed). Return at closing and at the best price."""
import json, collections
rows = [json.loads(l) for l in open('experiments/odds_history.jsonl')]
rows.sort(key=lambda r: r['ts'])
def pick(r, *ks):
    for k in ks:
        if r.get(k): return r[k]
C = collections.defaultdict(lambda: collections.Counter())
def acc(name, r, d, od, mx, imp):
    for h in ('A' if int(r['season']) <= 2021 else 'B', 'all'):
        c = C[(name, h)]; c['n'] += 1; c['d'] += d; c['ret'] += od if d else 0; c['retmax'] += mx if d else 0; c['imp'] += imp
table = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0]))
last10 = collections.defaultdict(list)
for r in rows:
    od, oh, oa = pick(r, 'pscd', 'psd', 'avgd', 'b365d'), pick(r, 'psch', 'psh', 'avgh', 'b365h'), pick(r, 'psca', 'psa', 'avga', 'b365a')
    d = int(r['hg'] == r['ag']); key = (r['lg'], r['season']); tb = table[key]
    if od and oh and oa:
        s = 1/oh + 1/od + 1/oa; imp = (1/od)/s; mx = r.get('maxd') or od
        if imp >= 0.27:
            acc('band 27%+ (all)', r, d, od, mx, imp)
            # (a) standings gap
            if tb[r['home']][1] >= 8 and tb[r['away']][1] >= 8:
                ppg_h = tb[r['home']][0] / tb[r['home']][1]; ppg_a = tb[r['away']][0] / tb[r['away']][1]
                ranks = sorted(tb, key=lambda t: -(tb[t][0] / max(1, tb[t][1])))
                gap = abs(ranks.index(r['home']) - ranks.index(r['away']))
                acc(f"band 27%+, table gap {'0-2 places' if gap <= 2 else '3-5 places' if gap <= 5 else '6+ places'}", r, d, od, mx, imp)
                acc(f"band 27%+, ppg gap {'< 0.2' if abs(ppg_h - ppg_a) < 0.2 else '0.2-0.5' if abs(ppg_h - ppg_a) < 0.5 else '0.5+'}", r, d, od, mx, imp)
            # (b) last-10 points-per-game gap (the form feed proxy)
            lh, la = last10[(r['lg'], r['home'])][-10:], last10[(r['lg'], r['away'])][-10:]
            if len(lh) == 10 and len(la) == 10:
                g = abs(sum(lh) / 10 - sum(la) / 10)
                acc(f"band 27%+, last-10 ppg gap {'< 0.2' if g < 0.2 else '0.2-0.5' if g < 0.5 else '0.5+'}", r, d, od, mx, imp)
    ph = 3 if r['hg'] > r['ag'] else 1 if d else 0; pa = 3 if r['ag'] > r['hg'] else 1 if d else 0
    tb[r['home']][0] += ph; tb[r['home']][1] += 1; tb[r['away']][0] += pa; tb[r['away']][1] += 1
    last10[(r['lg'], r['home'])].append(ph); last10[(r['lg'], r['away'])].append(pa)
names = []
for (name, h) in C:
    if name not in names: names.append(name)
print(f"{'rule':44}{'half':5}{'n':>7}{'drew':>7}{'market':>8}{'ret@close':>11}{'ret@max':>9}")
for name in sorted(names):
    for h in ('A', 'B', 'all'):
        c = C.get((name, h))
        if not c or c['n'] < 200: continue
        print(f"{name:44}{h:5}{c['n']:7}{c['d']/c['n']:7.1%}{c['imp']/c['n']:8.1%}{c['ret']/c['n']-1:+11.1%}{c['retmax']/c['n']-1:+9.1%}")
