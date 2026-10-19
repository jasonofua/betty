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
one code at most.
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

D5_EST, D10_EST = 48 / 49, 35 / 39          # the two draw rungs' own records (47 of 47, 34 of 37), smoothed


def _ko(e):
    return dt.datetime.fromtimestamp(int(e['estimateStartTime']) / 1000, tz=A.WAT)


def _skip(e):
    cat = ((e.get('sport') or {}).get('category') or {}).get('name') or ''
    return 'SRL' in (e.get('homeTeamName') or '') or cat in A.S.SKIP_COUNTRIES


def picks(start, cut):
    """One leg per game from the five options, sorted by kickoff."""
    rich = [e for e in B.fetch_events_rich() if start < _ko(e) <= cut and not _skip(e)]
    mins = [e for e in B._fetch_events_for(','.join(M.MIDS)) if start < _ko(e) <= cut and not _skip(e) and M.options(e)]
    print(f"window {start:%a %H:%M} -> {cut:%a %H:%M}: {len(rich)} games, {len(mins)} with Minutes-tab markets", flush=True)
    cand = collections.defaultdict(list)
    name = lambda e: f"{e['homeTeamName']} v {e['awayTeamName']}"
    for ev in rich:
        c = [x for x in AG.candidates(ev) if x[1] >= 1.15]
        if not c:
            continue
        chance, price, mname, spec, desc, ids = max(c, key=lambda x: (round(x[0], 3), x[1]))
        if (mname, desc) in (('Double Chance', 'Home or Draw'), ('Double Chance', 'Draw or Away')) and price <= 2.0:
            cand[ev['eventId']].append(dict(ts=_ko(ev).timestamp(), match=name(ev), sel=f"{mname} / {desc}", odds=price, chance=chance,
                                            why=f"the book's most likely option on this game, chance {chance:.2f}"))
            cand[ev['eventId']][-1]['ids'] = ids
    pairs = D.join(mins, FD._fixtures(), _ko, lambda f: dt.datetime.fromtimestamp(f['ts'], tz=A.WAT))

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
            elif k == M.D10 and r >= M.FLOOR10:
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
    if not codes:
        print(f"no code: the {len(legs)} games together cannot reach {lo:,.0f}x within 50 legs - nothing booked")
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
            A.log_booking(bk['code'], bk['url'], f"five options {lo:,.0f}-{hi:,.0f}x - code {i} of {len(codes)} - {combo:,.2f}x ({len(part)} games)",
                          [(l['ts'], l['match'], l['sel'], l['odds'], [l['why']]) for l in part])
        else:
            print(f"   >> booking failed: {bk.get('msg') if bk else 'no selections'}")


if __name__ == '__main__':
    main()
