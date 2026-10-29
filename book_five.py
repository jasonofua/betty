#!/usr/bin/env python3
"""FIVE OPTIONS - codes of 100x to 200x built only from the options that paid.

  python3 book_five.py [--until HH] [--days N] [--lo 100] [--hi 200] [--dry]

17 Oct, user: "I am getting tired of all this losses" / "Ok do that" / "Redeploy, but
I need at least 100-200 odds from this 5". On the site's own settled legs 3-17 Oct
(3,827 legs, each counted once, staked as singles at the booked price) only five
options returned more than was put in:
    5-minute draw           163 of 171   106 back per 100
    10-minute draw           57 of 60    106
    minute Unders           126 of 152   104   (goals from 1 to X minutes, Under)
    Draw or Away             79 of 92    104
    Home win                 55 of 71    about 108 (one 24.0 Viking v Bayern win left out)
Everything else - match and half goal lines, Home or Draw, Home or Away - won often
and paid back 88 to 99. So this script books nothing else.

19 Oct, user: "i cant keep lossing like this why did you pick home and not home or draw ?" -> "2"
(Home or Draw instead of Home) -> "update the website and deploy it". Home or Draw REPLACES the
home win: on the site's legs 3-19 Oct 14 of Home's 23 losses were draws (76 of 99), and on
19 Oct Varberg 0-0, DVTK 2-2 and Diosgyori 2-2 lost as Home. Home or Draw went 224 of 278 on
the same legs. The codes reach 100x on fewer days (lower prices); no other number changed.

22 Oct, user: "Look at the stats and the result, what pointed to that result" -> "Whats the fix"
-> "Implement them". Tested on every site leg 15-21 Oct, history cut an hour before kickoff:
  Home or Draw is skipped when the home side lost 5+ of its last 10, or when the away side's
  goal difference a game in its away window is as good as the home side's at home (HOD_*).
  Those went 34 of 47 (back 87 per 100); the rest 56 of 60 (back 112), 40 of 43 on 15-17 Oct
  and 16 of 17 on 18-21 Oct. It caught Ferro v Colon Res, Juarez v Atlante U21 and Belgrano v
  Estudiantes Res; the away side's own form did not separate (36 of 44 when it won 4+ of 10).
  A Home or Draw game with no Flashscore record is skipped, since the check cannot be made.
  The 10-minute draw is booked only under D10_MAX (1.19): 26 of 29 and 39 of 46 below it
  (back 104, 96), 14 of 19 and 11 of 15 at 1.19 or longer (back 89, 90).

One leg per game, the option with the higher chance:
  minute legs  - only where the two sides' own past games back it, with the minute-mix
                 floors (book_minute_mix): 5-minute draw 90%+ of the record, 10-minute
                 draw 80%+, a minute Under 90%+ with 10+ calibrated games, 3+ answered
                 games a side. Chance = what that rung has won before.
  Home or Draw / Draw or Away - only where it IS the book's most likely option at 1.15+, priced
                 2.00 or less, no record filter (the filter is how Lamelas Home @28.0
                 reached four codes on 17 Oct). Chance = the book's no-margin chance.
Codes: the safest set of at most 50 games reaching --lo (ranged), trimmed under --hi,
then again from the games left, until the rest cannot reach --lo. Every game is on
one code at most. 23 Oct, user: "Even if it isn't up to 100x it should still book" - a
day that cannot reach --lo books one short code with every game picked (Thu 22 Oct had
28 picks and no code).

29 Oct, user: "whats the fix" -> "Implement them". NE2DRW lost on Wacker Innsbruck (2. Liga) v WSG Tirol
(Bundesliga), 10-minute draw, Tirol scoring at 5' - the "12 of 14" record was built in two divisions.
A cup or continental game between sides from different leagues now gets no leg (predict_stats.different_leagues,
the stats picks' 14 Oct rule). Domestic league games are not checked. Not tested on these options.
"""
import sys, math, datetime as dt, collections
from concurrent.futures import ThreadPoolExecutor
import os as _o; sys.path.insert(0, _o.path.dirname(_o.path.abspath(__file__)))
import acca as A
import book_v3 as B
import findings as FD
import dynamic_v4 as D
import book_minute_mix as M
import book_allgames as AG
import predict_stats as PS

D5_EST, D10_EST = 48 / 49, 35 / 39          # the two draw rungs' own records (47 of 47, 34 of 37), smoothed
D10_MAX = 1.19                               # 22 Oct: 10-minute draw only below this price
HOD_HOME_LOSSES = 5                          # 22 Oct: no Home or Draw when the home side lost this many of its last 10


def _ko(e):
    return dt.datetime.fromtimestamp(int(e['estimateStartTime']) / 1000, tz=A.WAT)


def _skip(e):
    cat = ((e.get('sport') or {}).get('category') or {}).get('name') or ''
    return 'SRL' in (e.get('homeTeamName') or '') or cat in A.S.SKIP_COUNTRIES


def hod_check(fid, ko):
    """None when Home or Draw may go on, else the reason it may not (22 Oct rule).
    Home side's last 10 and both venue windows (last 7 home / last 7 away), friendlies
    out, everything an hour before kickoff or later dropped."""
    import fetcher_v2 as F2, fetcher_v3 as F3
    raw = F2.fetch(f"df_hh_1_{fid}")
    if not raw:
        return 'no Flashscore record'
    hr, ar, _ = F3.parse_history(raw)
    hr = [r for r in hr if not r['friendly'] and r['kc'] < ko - 3600]
    ar = [r for r in ar if not r['friendly'] and r['kc'] < ko - 3600]
    hh = [r for r in hr if r['venue'] == 'home'][:7]
    aa = [r for r in ar if r['venue'] == 'away'][:7]
    if not hr or not ar or not hh or not aa:
        return 'no Flashscore record'
    lost = sum(r['res'] == 'L' for r in hr[:10])
    if lost >= HOD_HOME_LOSSES:
        return f"home side lost {lost} of its last {len(hr[:10])}"
    gd = lambda rows: sum(r['gf'] - r['ga'] for r in rows) / len(rows)
    if gd(aa) >= gd(hh):
        return f"away side's away goal difference {gd(aa):+.1f} a game, home side's at home {gd(hh):+.1f}"
    return None


def picks(start, cut):
    """One leg per game from the five options, sorted by kickoff."""
    rich = [e for e in B.fetch_events_rich() if start < _ko(e) <= cut and not _skip(e)]
    mins = [e for e in B._fetch_events_for(','.join(M.MIDS)) if start < _ko(e) <= cut and not _skip(e) and M.options(e)]
    print(f"window {start:%a %H:%M} -> {cut:%a %H:%M}: {len(rich)} games, {len(mins)} with Minutes-tab markets", flush=True)
    cand = collections.defaultdict(list)
    name = lambda e: f"{e['homeTeamName']} v {e['awayTeamName']}"
    fx = FD._fixtures()
    hod = []
    for ev in rich:
        c = [x for x in AG.candidates(ev) if x[1] >= 1.15]
        if not c:
            continue
        chance, price, mname, spec, desc, ids = max(c, key=lambda x: (round(x[0], 3), x[1]))
        if (mname, desc) in (('Double Chance', 'Home or Draw'), ('Double Chance', 'Draw or Away')) and price <= 2.0:
            leg = dict(ts=_ko(ev).timestamp(), match=name(ev), sel=f"{mname} / {desc}", odds=price, chance=chance,
                       why=f"the book's most likely option on this game, chance {chance:.2f}", ids=ids)
            if desc == 'Home or Draw':
                hod.append((ev, leg))
            else:
                cand[ev['eventId']].append(leg)
    fids = {id(e): f for e, f, _ in D.join([e for e, _ in hod], fx, _ko, lambda f: dt.datetime.fromtimestamp(f['ts'], tz=A.WAT))}

    def chk(p):
        e, leg = p
        f = fids.get(id(e))
        try:
            return e, leg, hod_check(f['id'], int(_ko(e).timestamp())) if f else 'no Flashscore record'
        except Exception as ex:
            return e, leg, f"record failed ({ex.__class__.__name__})"
    with ThreadPoolExecutor(10) as ex:
        checked = list(ex.map(chk, hod))
    for e, leg, why_not in checked:
        if why_not:
            print(f"   Home or Draw skipped: {leg['match'][:44]} @{leg['odds']} - {why_not}", flush=True)
        else:
            leg['why'] += "; home side's form and home record clear the 22 Oct check"
            cand[e['eventId']].append(leg)
    print(f"Home or Draw: {sum(1 for *_, w in checked if not w)} of {len(checked)} kept", flush=True)
    pairs = D.join(mins, fx, _ko, lambda f: dt.datetime.fromtimestamp(f['ts'], tz=A.WAT))

    def rec(p):
        e, f, _ = p
        try:
            return e, M.record(f['id'], int(f['ts']))
        except Exception:
            return e, None
    with ThreadPoolExecutor(10) as ex:
        recs = [x for x in ex.map(rec, pairs) if x[1]]
    for ev, r_ in recs:
        for k, (odds, _c, oid) in M.options(ev).items():
            if odds < M.MIN3:
                continue
            r, n = M.rate(r_, k)
            if n < M.MIN_SIDE:
                continue
            if k == M.D5 and r >= M.FLOOR:
                est, how = D5_EST, '5-minute draw'
            elif k == M.D10 and r >= M.FLOOR10 and odds < D10_MAX:
                est, how = D10_EST, '10-minute draw'
            elif k not in (M.D5, M.D10) and k[2].startswith('Under') and r >= M.FLOOR and M.cal(k):
                est, how = M.cal(k), f"minute Under (options like it won {M.CAL[k][0]} of {M.CAL[k][1]})"
            else:
                continue
            if not 0 < est < 1:
                continue
            cand[ev['eventId']].append(dict(ts=_ko(ev).timestamp(), match=name(ev), sel=M.sel_name(*k), odds=odds, chance=est,
                                            why=f"{how}: past games {round(r * len(r_))} of {len(r_)} ({n}+ a side)",
                                            ids=dict(eventId=ev['eventId'], productId=3, marketId=k[0], specifier=k[1] or None, outcomeId=oid)))
    # 29 Oct: no leg on a cup or continental game between sides from different leagues (NE2DRW lost Wacker Innsbruck,
    # 2. Liga, v WSG Tirol, Bundesliga, on the 10-minute draw at 5'). The same check the stats picks use since 14 Oct
    # (predict_stats.different_leagues); domestic league games are not checked. Untested on the five options.
    def cup_game(f):
        country, _, comp = (f.get('league') or '').partition(':')
        return country.strip().lower() in PS.CONTINENTAL or PS.is_cup(comp, country)
    evs = {e['eventId']: e for e in rich + mins}
    cups = [(e, f) for e, f, _ in D.join([evs[k] for k in cand if k in evs], fx, _ko, lambda f: dt.datetime.fromtimestamp(f['ts'], tz=A.WAT))
            if cup_game(f)]

    def diff(p):
        e, f = p
        try:
            return e, PS.different_leagues(f)
        except Exception:
            return e, None
    with ThreadPoolExecutor(10) as ex:
        for e, d in ex.map(diff, cups):
            if d:
                print(f"   different leagues, skipped: {name(e)[:44]} - {d[0]} / {d[1]}", flush=True)
                cand.pop(e['eventId'], None)
    return sorted((max(c, key=lambda o: (o['chance'], o['odds'])) for c in cand.values()), key=lambda l: l['ts'])


def ranged(legs, lo, hi, cap=50):
    """The safest set of at most `cap` legs at lo..hi, or None. Each leg is scored
    ln(price) + mu * ln(chance); the largest mu whose best `cap` legs still reach lo
    is found by bisection (the exact knapsack in book_allgames.to_target piles up
    1.03 minute Unders past the 50-leg cap and returns nothing), then the weakest
    legs are dropped while the code stays at lo or above and until it is under hi."""
    v = lambda l, mu: math.log(l['odds']) + mu * math.log(l['chance'])

    def choose(mu):
        sel = [l for l in sorted(legs, key=lambda l: -v(l, mu))[:cap] if v(l, mu) > 0]
        return sel, math.prod(l['odds'] for l in sel)
    if choose(0.0)[1] < lo:
        return None
    a, b = 0.0, 500.0
    for _ in range(60):
        m = (a + b) / 2
        if choose(m)[1] >= lo:
            a = m
        else:
            b = m
    pick, prod = choose(a)
    while prod > hi:
        keep = [l for l in pick if prod / l['odds'] >= lo]
        if not keep:
            return None
        w = min(keep, key=lambda l: v(l, a))
        pick.remove(w); prod /= w['odds']
    return sorted(pick, key=lambda l: l['ts'])


def main():
    dry = '--dry' in sys.argv
    arg = lambda k, d: type(d)(sys.argv[sys.argv.index(k) + 1]) if k in sys.argv else d
    until, days, lo, hi = arg('--until', 23), arg('--days', 0), arg('--lo', 100.0), arg('--hi', 200.0)
    now = dt.datetime.now(A.WAT); start = now + dt.timedelta(hours=1)        # standing rule: an hour out
    cut = now.replace(hour=until, minute=59, second=59, microsecond=0)
    if cut <= now:
        cut += dt.timedelta(days=1)
    cut += dt.timedelta(days=days)
    legs = picks(start, cut)
    print(f"{len(legs)} games picked: " + ', '.join(f"{k} {v}" for k, v in collections.Counter(
        'Home or Draw' if 'Home or Draw' in l['sel'] else 'Draw or Away' if 'Draw or Away' in l['sel'] else
        '5-min draw' if 'after 5' in l['sel'] else '10-min draw' if '10 Minutes' in l['sel'] else 'minute Under' for l in legs).most_common()), flush=True)
    codes, left = [], list(legs)
    while True:
        p = ranged(left, lo, hi)
        if not p:
            break
        codes.append(p)
        ids = {id(l) for l in p}
        left = [l for l in left if id(l) not in ids]
    short = not codes and bool(legs)
    if short:
        # 23 Oct, user: "Even if it isn't up to 100x it should still book". The day cannot
        # reach --lo, so one code from every game picked (the 50 longest prices if more).
        codes = [sorted(sorted(legs, key=lambda l: -l['odds'])[:50], key=lambda l: l['ts'])]
        print(f"short: the {len(legs)} games together cannot reach {lo:,.0f}x - booking them as one "
              f"code of {math.prod(l['odds'] for l in codes[0]):,.2f}x anyway")
    if not codes:
        print("no code: no game picked - nothing booked")
        return
    A.FINDINGS_EXEMPT = True          # every leg already carries its own record check above
    for i, part in enumerate(codes, 1):
        combo = math.prod(l['odds'] for l in part)
        print(f"\n=== code {i} of {len(codes)}: {len(part)} games, {combo:,.2f}x, every leg landing about 1 in {1 / math.prod(l['chance'] for l in part):,.1f}")
        for l in part:
            print(f"   {dt.datetime.fromtimestamp(l['ts'], tz=A.WAT):%a %H:%M}  {l['match'][:40]:40} {l['sel'][-36:]:36} @{l['odds']:<5} {l['why']}")
        if dry:
            continue
        bk = A.book([l['ids'] for l in part])
        if bk and bk.get('code'):
            print(f"\n   >> code {bk['code']}   {bk['url']}   ({bk.get('booked')}/{bk.get('req')} on the code)")
            what = f"short of {lo:,.0f}x, every game picked" if short else f"{lo:,.0f}-{hi:,.0f}x - code {i} of {len(codes)}"
            A.log_booking(bk['code'], bk['url'], f"five options {what} - {combo:,.2f}x ({len(part)} games)",
                          [(l['ts'], l['match'], l['sel'], l['odds'], [l['why']]) for l in part])
        else:
            print(f"   >> booking failed: {bk.get('msg') if bk else 'no selections'}")


if __name__ == '__main__':
    main()
