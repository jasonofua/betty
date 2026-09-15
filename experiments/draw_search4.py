#!/usr/bin/env python3
"""Draw search, round four: fixture history, league regime, in-season draw
streaks, promoted sides, kick-off slots, the sharp-vs-soft flip, bounce-back
after a heavy defeat, games after a league break. Return at closing, both halves."""
import json, collections, datetime as dt
rows = [json.loads(l) for l in open('experiments/odds_history.jsonl')]
rows.sort(key=lambda r: r['ts'])
def pick(r, *ks):
    for k in ks:
        if r.get(k): return r[k]
C = collections.defaultdict(lambda: collections.Counter())
def acc(name, r, d, od, imp):
    for h in ('A' if int(r['season']) <= 2021 else 'B', 'all'):
        c = C[(name, h)]; c['n'] += 1; c['d'] += d; c['ret'] += od if d else 0; c['imp'] += imp
h2h = collections.defaultdict(list)                       # frozenset(lg, a, b) -> [d]
team_games = collections.defaultdict(list)                # (lg, team) -> [(ts, d, gf, ga, season)]
lg_games = collections.defaultdict(list)                  # lg -> [(ts, d)]
lg_last_day = {}
seasons_in_lg = collections.defaultdict(set)              # (lg, team) -> {season}
for r in rows:
    od, oh, oa = pick(r, 'pscd', 'psd', 'avgd', 'b365d'), pick(r, 'psch', 'psh', 'avgh', 'b365h'), pick(r, 'psca', 'psa', 'avga', 'b365a')
    d = int(r['hg'] == r['ag']); lg = r['lg']
    if od and oh and oa:
        s = 1/oh + 1/od + 1/oa; imp = (1/od)/s
        band = 0.27 <= imp < 0.36
        # 1. fixture history: last 5 meetings
        key = (lg, frozenset((r['home'], r['away'])))
        hh = h2h[key][-5:]
        if len(hh) >= 3:
            rate = sum(hh) / len(hh)
            acc('h2h: 3+ of last 5 meetings drawn' if rate >= 0.6 else 'h2h: no draw in last 3-5 meetings' if rate == 0 else 'h2h: some draws', r, d, od, imp)
        # 2. league regime: last 120 games vs the league's long run (prior 600)
        lgl = lg_games[lg]
        if len(lgl) >= 720:
            recent = sum(x[1] for x in lgl[-120:]) / 120; longrun = sum(x[1] for x in lgl[-720:-120]) / 600
            if recent >= longrun + 0.05: acc('league drawing 5pts+ above its long run', r, d, od, imp)
            if recent <= longrun - 0.05: acc('league drawing 5pts+ below its long run', r, d, od, imp)
        # 3. in-season streak: 4+ draws in the last 8 for both / either
        th = team_games[(lg, r['home'])][-8:]; ta = team_games[(lg, r['away'])][-8:]
        if len(th) == 8 and len(ta) == 8:
            dh, da = sum(x[1] for x in th), sum(x[1] for x in ta)
            if dh >= 4 and da >= 4: acc('both sides 4+ draws in last 8', r, d, od, imp)
            if dh >= 4 or da >= 4: acc('either side 4+ draws in last 8', r, d, od, imp)
            if dh == 0 and da == 0: acc('neither side drew in last 8', r, d, od, imp)
            # 7. bounce-back after a heavy defeat
            lh, la = th[-1], ta[-1]
            if lh[3] - lh[2] >= 3: acc('home side lost last game by 3+', r, d, od, imp)
            if la[3] - la[2] >= 3: acc('away side lost last game by 3+', r, d, od, imp)
            # 7b. both won last game
            if lh[2] > lh[3] and la[2] > la[3]: acc('both sides won their last game', r, d, od, imp)
        # 4. promoted / new to the division
        ps = str(int(r['season']) - 101).zfill(4)
        newh = ps not in seasons_in_lg[(lg, r['home'])] and len(seasons_in_lg[(lg, r['home'])]) > 0
        newa = ps not in seasons_in_lg[(lg, r['away'])] and len(seasons_in_lg[(lg, r['away'])]) > 0
        if int(r['season']) > 1516:
            if newh and newa: acc('both sides new to the division this season', r, d, od, imp)
            elif newh or newa: acc('one side new to the division', r, d, od, imp)
        # 5. kick-off slot
        t = dt.datetime.fromtimestamp(r['ts'])
        if t.hour or t.minute:
            slot = 'weekend early (before 15:00)' if t.weekday() >= 5 and t.hour < 15 else 'weekend 15:00-17:59' if t.weekday() >= 5 and t.hour < 18 else 'weekend evening' if t.weekday() >= 5 else 'midweek'
            acc(f'kick-off: {slot}', r, d, od, imp)
        # 6. the flip: Pinnacle above Bet365 by 5%+ (soft book shading the draw)
        if r.get('b365d') and r.get('pscd') and r['pscd'] >= r['b365d'] * 1.05: acc('Pinnacle draw >= Bet365 x1.05 (bet at Pinnacle)', r, d, r['pscd'], imp)
        # 8. after a league break of 12+ days
        if lg in lg_last_day and (r['ts'] - lg_last_day[lg]) >= 12 * 86400: acc('first game after a 12-day+ league break', r, d, od, imp)
    h2h[(lg, frozenset((r['home'], r['away'])))].append(d)
    team_games[(lg, r['home'])].append((r['ts'], d, r['hg'], r['ag'], r['season'])); team_games[(lg, r['away'])].append((r['ts'], d, r['ag'], r['hg'], r['season']))
    lg_games[lg].append((r['ts'], d)); seasons_in_lg[(lg, r['home'])].add(r['season']); seasons_in_lg[(lg, r['away'])].add(r['season'])
    day = r['ts'] // 86400 * 86400
    if lg in lg_last_day and day - lg_last_day[lg] > 0 or lg not in lg_last_day:
        pass
    lg_last_day[lg] = max(lg_last_day.get(lg, 0), day) if lg in lg_last_day else day
names = []
for (name, h) in C:
    if name not in names: names.append(name)
print(f"{'rule':46}{'half':5}{'n':>7}{'drew':>7}{'market':>8}{'return':>9}")
for name in names:
    for h in ('A', 'B', 'all'):
        c = C.get((name, h))
        if not c or c['n'] < 200: continue
        print(f"{name:46}{h:5}{c['n']:7}{c['d']/c['n']:7.1%}{c['imp']/c['n']:8.1%}{c['ret']/c['n']-1:+9.1%}")
