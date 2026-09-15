#!/usr/bin/env python3
"""Draw search, round two. Rules judged on return at the closing price (and at
the best price) with the sample split into two halves of seasons so a rule
that only works in one half is a coincidence."""
import json, collections, statistics as S, csv, re
import numpy as np
rows = [json.loads(l) for l in open('experiments/odds_history.jsonl')]
rows.sort(key=lambda r: r['ts'])
def pick(r, *ks):
    for k in ks:
        if r.get(k): return r[k]
def imp_draw(r):
    od, oh, oa = pick(r,'pscd','psd','avgd','b365d'), pick(r,'psch','psh','avgh','b365h'), pick(r,'psca','psa','avga','b365a')
    if not (od and oh and oa): return None, None
    s = 1/oh + 1/od + 1/oa
    return (1/od)/s, od
def half(r): return 'A 15/16-20/21' if int(r['season']) <= 2021 else 'B 21/22-25/26'
C = collections.defaultdict(lambda: collections.Counter())
def acc(name, r, d, od, mx=None):
    for h in (half(r), 'all'):
        c = C[(name, h)]; c['n'] += 1; c['draw'] += d; c['ret'] += od if d else 0; c['retmax'] += (mx or od) if d else 0
        c['imp'] += r['_imp']

# per-team prior-season draw rate and season position
prev_season_draw = {}
season_games = collections.defaultdict(list)
for r in rows: season_games[(r['lg'], r['season'])].append(r)
team_season_draw = collections.defaultdict(lambda: [0, 0])
for r in rows:
    d = int(r['hg'] == r['ag'])
    for t in (r['home'], r['away']):
        team_season_draw[(r['lg'], r['season'], t)][0] += d; team_season_draw[(r['lg'], r['season'], t)][1] += 1
def prev_rate(lg, season, team):
    ps = str(int(season) - 101).zfill(4)
    d, n = team_season_draw.get((lg, ps, team), (0, 0))
    return d / n if n >= 20 else None
# rounds played this season per team (to find the final rounds)
played = collections.Counter()
last_ts = {}
for r in rows:
    d = int(r['hg'] == r['ag']); imp, od = imp_draw(r)
    if imp is None: continue
    r['_imp'] = imp
    mx = r.get('maxd') or od
    key = (r['lg'], r['season'])
    n_home = played[(key, r['home'])]; n_away = played[(key, r['away'])]
    total_rounds = len({g['date'] for g in season_games[key]})
    played[(key, r['home'])] += 1; played[(key, r['away'])] += 1
    band = 0.32 <= imp < 0.36
    if band: acc('band 32-36%', r, d, od, mx)
    # A. soft book above sharp book
    if r.get('b365d') and r.get('pscd'):
        if r['b365d'] >= r['pscd'] * 1.04: acc('B365 draw >= Pinnacle closing x1.04, bet at B365', r, d, r['b365d'])
        if r['b365d'] >= r['pscd'] * 1.04 and band: acc('  ... and 32-36% band', r, d, r['b365d'])
    # B. under-2.5 favoured + draw band
    if r.get('b365<2.5') is None:
        pass
    # C. Asian handicap level line
    # D. last rounds of the season
    rounds_left_h = None
    # E. both teams drew 30%+ last season
    ph, pa = prev_rate(r['lg'], r['season'], r['home']), prev_rate(r['lg'], r['season'], r['away'])
    if ph is not None and pa is not None:
        if ph >= 0.30 and pa >= 0.30: acc('both teams drew 30%+ of last season', r, d, od, mx)
        if ph >= 0.30 and pa >= 0.30 and band: acc('  ... and 32-36% band', r, d, od, mx)
        if ph <= 0.20 and pa <= 0.20: acc('both teams drew <=20% last season', r, d, od, mx)
    # F. season timing: first 5 games of a team's season / last 5
    if n_home <= 4 and n_away <= 4: acc('first 5 games of the season', r, d, od, mx)
    # G. league x band
    if band and r['lg'] in ('I2', 'SC1', 'D1', 'N1', 'D2'): acc('band 32-36% in I2/SC1/D1/N1/D2', r, d, od, mx)
    if band and r['lg'] in ('P1', 'SC3', 'SC2', 'E2', 'B1'): acc('band 32-36% in P1/SC3/SC2/E2/B1', r, d, od, mx)
    # H. HALF-TIME states (for the live product): FT draw given HT 0-0 / 1-1 by pre-match band
    if r.get('hth') is not None:
        ht = (r['hth'], r['hta'])
        if ht == (0, 0): acc(f"HT 0-0, pre-match draw {'32%+' if imp >= 0.32 else '27-32%' if imp >= 0.27 else '<27%'}", r, d, od)
        if ht == (1, 1): acc(f"HT 1-1, pre-match draw {'32%+' if imp >= 0.32 else '27-32%' if imp >= 0.27 else '<27%'}", r, d, od)
        if ht[0] == ht[1] and ht[0] >= 2: acc('HT 2-2 or more', r, d, od)
    # I. month
    mo = int(r['date'][5:7])
    if mo in (12, 1): acc('December-January', r, d, od, mx)
    if mo in (4, 5): acc('April-May', r, d, od, mx)

names = []
for (name, h) in C:
    if name not in names: names.append(name)
print(f"{'rule':52}{'half':14}{'n':>7}{'drew':>7}{'market':>8}{'ret@close':>11}{'ret@max':>9}")
for name in names:
    for h in ('A 15/16-20/21', 'B 21/22-25/26', 'all'):
        c = C.get((name, h))
        if not c or c['n'] < 150: continue
        print(f"{name:52}{h:14}{c['n']:7}{c['draw']/c['n']:7.1%}{c['imp']/c['n']:8.1%}{c['ret']/c['n']-1:+11.1%}{c['retmax']/c['n']-1:+9.1%}")
