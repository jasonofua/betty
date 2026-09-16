#!/usr/bin/env python3
"""User's two hypotheses inside the 27%+ draw band: (1) the away side a little
stronger than the home side (home advantage levels it); (2) motivation - both
sides fine with a point (mid-table, safe, nothing to play for), any time of
season and in the run-in. football-data 2015-26, return at closing / best price."""
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
rounds = collections.defaultdict(set)
for r in rows:
    od, oh, oa = pick(r, 'pscd', 'psd', 'avgd', 'b365d'), pick(r, 'psch', 'psh', 'avgh', 'b365h'), pick(r, 'psca', 'psa', 'avga', 'b365a')
    d = int(r['hg'] == r['ag']); key = (r['lg'], r['season']); tb = table[key]
    if od and oh and oa:
        s = 1/oh + 1/od + 1/oa; imp, ih, ia = (1/od)/s, (1/oh)/s, (1/oa)/s; mx = r.get('maxd') or od
        if imp >= 0.27:
            # (1) away a little stronger - by the market
            edge = ia - ih
            band = 'home stronger by 10pts+' if edge <= -0.10 else 'home a little stronger' if edge < 0 else 'away a little stronger (0-10pts)' if edge < 0.10 else 'away stronger by 10pts+'
            acc(f"market: {band}", r, d, od, mx, imp)
            lh, la = last10[(r['lg'], r['home'])][-10:], last10[(r['lg'], r['away'])][-10:]
            if len(lh) == 10 and len(la) == 10:
                g = sum(la) / 10 - sum(lh) / 10
                band2 = 'home form better by 0.5+' if g <= -0.5 else 'home form a little better' if g < 0 else 'away form a little better (0-0.5)' if g < 0.5 else 'away form better by 0.5+'
                acc(f"form: {band2}", r, d, od, mx, imp)
                if 0 < g < 0.5 and 0 < edge < 0.10: acc('form AND market: away a little stronger', r, d, od, mx, imp)
            # (2) motivation from the standings
            if tb[r['home']][1] >= 8 and tb[r['away']][1] >= 8:
                n_teams = len(tb)
                ranks = sorted(tb, key=lambda t: -(tb[t][0] / max(1, tb[t][1])))
                rh, ra = ranks.index(r['home']) + 1, ranks.index(r['away']) + 1
                mid = lambda rk: 5 <= rk <= n_teams - 4
                played = tb[r['home']][1]; late = played >= 0.75 * 2 * (n_teams - 1)
                if mid(rh) and mid(ra): acc('both mid-table (any time)', r, d, od, mx, imp)
                if mid(rh) and mid(ra) and late: acc('both mid-table, last quarter of the season', r, d, od, mx, imp)
                if (rh <= 3 or ra <= 3): acc('a top-3 side involved', r, d, od, mx, imp)
                if (rh >= n_teams - 2 or ra >= n_teams - 2): acc('a bottom-3 side involved', r, d, od, mx, imp)
                if mid(rh) and mid(ra) and abs(rh - ra) <= 3: acc('both mid-table AND within 3 places', r, d, od, mx, imp)
    ph = 3 if r['hg'] > r['ag'] else 1 if d else 0; pa = 3 if r['ag'] > r['hg'] else 1 if d else 0
    tb[r['home']][0] += ph; tb[r['home']][1] += 1; tb[r['away']][0] += pa; tb[r['away']][1] += 1
    last10[(r['lg'], r['home'])].append(ph); last10[(r['lg'], r['away'])].append(pa)
names = []
for (name, h) in C:
    if name not in names: names.append(name)
print(f"{'rule (inside the 27%+ band)':46}{'half':5}{'n':>7}{'drew':>7}{'market':>8}{'ret@close':>11}{'ret@max':>9}")
for name in sorted(names):
    for h in ('A', 'B', 'all'):
        c = C.get((name, h))
        if not c or c['n'] < 200: continue
        print(f"{name:46}{h:5}{c['n']:7}{c['d']/c['n']:7.1%}{c['imp']/c['n']:8.1%}{c['ret']/c['n']-1:+11.1%}{c['retmax']/c['n']-1:+9.1%}")
