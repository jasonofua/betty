#!/usr/bin/env python3
"""EVERY GAME, ONE PICK EACH - the option the book itself makes most likely.

  python3 book_allgames.py [--until HH] [--days N] [--floor 1.15] [--targets 2000,...] [--dry]

6 Oct, measured. The hand-read slips on 5 Oct picked each game's option by its
venue record (the highest rate of ~14 options on 5-7 games a side): those legs won
15 of 25, while the legs picked off the price alone won 31 of 36. Tested leak-free on
38,716 priced matches 2021-26 (experiments/selection_rule_test.py), one option per
game from 1X / X2 / 12 / Over-Under 2.5, venue windows cut at kickoff:
    highest venue rate            71.3% won (book 71.1%)
    book's most likely option     75.0% won (book 74.4%)    <- this script
    book's pick, venue veto       72.4%
    book's pick where the record is 10+ points BELOW it   75.6%
Every rule wins what the book says to within a point, on both halves of the data:
the venue record adds nothing to the price in either direction on these markets,
and ranking by it chooses lower-chance legs. So each game takes the option with the
highest no-margin chance at a price of at least --floor (default 1.15, so a leg is
never a 1.01), and the slip is split into codes of at most 50 legs.

9 Oct, user's call ("I need 2k odds upwards"): --targets books one more code per
multiplier from the same picks - the set of games that reaches the target with the
highest chance of every leg landing on the book's own numbers (exact, see to_target).
It takes the longer prices among the picks: a leg's margin is paid per leg, so
fewer legs at longer prices reach the same multiplier at a better chance (WCH45X).
"""
import sys, math, datetime as dt
import os as _o; sys.path.insert(0, _o.path.dirname(_o.path.abspath(__file__)))
import acca as A
import book_v3 as B
import findings as FD

MAX_LEGS = 50


def to_target(legs, target, cap=MAX_LEGS):
    """The legs whose prices multiply to at least `target` with the highest product of
    book chances: a knapsack on log-odds. Prices sit on a grid, so the bar is raised a
    step at a time until the chosen legs clear the target at their real prices. None
    when every leg together falls short."""
    if math.prod(l['odds'] for l in legs) < target:
        return None
    T0 = round(math.log(target) * 1000)
    for extra in range(0, 400):
        pick = _knapsack(legs, T0 + extra)
        if pick is None:
            return None
        if math.prod(l['odds'] for l in pick) >= target:
            return sorted(pick, key=lambda l: l['ts']) if len(pick) <= cap else None
    return None


def _knapsack(legs, T, R=1000):
    NEG = -1e18
    if sum(round(math.log(l['odds']) * R) for l in legs) < T:
        return None
    dp = [0.0] + [NEG] * T                       # dp[c]: best sum of log-chances at log-odds bucket c (capped at T)
    took = []
    for l in legs:
        a, b = round(math.log(l['odds']) * R), math.log(l['chance'])
        nxt, mark = dp[:], [False] * (T + 1)
        for c in range(T + 1):
            if dp[c] > NEG:
                nc = min(T, c + a)
                if dp[c] + b > nxt[nc]:
                    nxt[nc] = dp[c] + b; mark[nc] = True
        took.append((mark, dp)); dp = nxt
    c, out = T, []
    for i in range(len(legs) - 1, -1, -1):
        mark, prev = took[i]
        if not mark[c]:
            continue
        l = legs[i]; a, b = round(math.log(l['odds']) * R), math.log(l['chance'])
        c = next(pc for pc in ([c - a] if c < T else range(max(0, T - a), T + 1)) if pc >= 0 and prev[pc] > NEG
                 and abs(prev[pc] + b - (took[i + 1][1][c] if i + 1 < len(legs) else dp[c])) < 1e-9)
        out.append(l)
    return out
TWO_WAY = (('Over/Under', 'Over', 'Under'), ('1st Half - Over/Under', 'Over', 'Under'),
           ('2nd Half - Total', 'Over', 'Under'))


def candidates(ev):
    """[(chance, price, market, spec, outcome, ids)] for every option with a fair chance."""
    mk = {}
    for m in ev.get('markets') or []:
        if str(m.get('status', '0')) != '0':
            continue
        key = ((m.get('name') or m.get('desc') or ''), m.get('specifier') or '')
        mk[key] = (m, {o.get('desc'): o for o in m.get('outcomes') or [] if o.get('isActive', 1)})
    out = []

    def add(chance, mkey, desc):
        m, outs = mk[mkey]
        o = outs.get(desc)
        if not o:
            return
        out.append((chance, float(o['odds']), mkey[0], mkey[1], desc,
                    dict(eventId=ev['eventId'], productId=3, marketId=str(m['id']),
                         specifier=mkey[1] or None, outcomeId=str(o['id']))))
    x = mk.get(('1X2', ''))
    if x and all(k in x[1] for k in ('Home', 'Draw', 'Away')):
        h, d, a = (1 / float(x[1][k]['odds']) for k in ('Home', 'Draw', 'Away'))
        s = h + d + a
        ph, pd, pa = h / s, d / s, a / s
        if ('Double Chance', '') in mk:
            add(ph + pd, ('Double Chance', ''), 'Home or Draw')
            add(pd + pa, ('Double Chance', ''), 'Draw or Away')
            add(ph + pa, ('Double Chance', ''), 'Home or Away')
        add(ph, ('1X2', ''), 'Home'); add(pa, ('1X2', ''), 'Away')
    for name, up, down in TWO_WAY:
        for (n, spec), (m, outs) in mk.items():
            if n != name:
                continue
            ln = spec.replace('total=', '')
            if not ln.endswith('.5'):
                continue                    # whole and quarter lines can push - their chance is not one number
            o, u = outs.get(f'{up} {ln}'), outs.get(f'{down} {ln}')
            if not o or not u:
                continue
            po, pu = 1 / float(o['odds']), 1 / float(u['odds'])
            add(po / (po + pu), (n, spec), f'{up} {ln}')
            add(pu / (po + pu), (n, spec), f'{down} {ln}')
    g = mk.get(('GG/NG', ''))
    if g and 'Yes' in g[1] and 'No' in g[1]:
        y, no = 1 / float(g[1]['Yes']['odds']), 1 / float(g[1]['No']['odds'])
        add(y / (y + no), ('GG/NG', ''), 'Yes'); add(no / (y + no), ('GG/NG', ''), 'No')
    return out


def main():
    dry = '--dry' in sys.argv
    floor = float(sys.argv[sys.argv.index('--floor') + 1]) if '--floor' in sys.argv else 1.15
    until = int(sys.argv[sys.argv.index('--until') + 1]) if '--until' in sys.argv else 23
    days = int(sys.argv[sys.argv.index('--days') + 1]) if '--days' in sys.argv else 0
    targets = ([float(t) for t in sys.argv[sys.argv.index('--targets') + 1].split(',') if t.strip()]
               if '--targets' in sys.argv else [])
    now = dt.datetime.now(A.WAT); start = now + dt.timedelta(hours=1)   # standing rule: an hour out
    if '--from' in sys.argv:                    # --from HH: only games from that hour on (e.g. one kickoff block)
        start = max(start, now.replace(hour=int(sys.argv[sys.argv.index('--from') + 1]), minute=0, second=0, microsecond=0) - dt.timedelta(seconds=1))
    cut = now.replace(hour=until, minute=59, second=59, microsecond=0)
    if cut <= now:
        cut += dt.timedelta(days=1)
    cut += dt.timedelta(days=days)
    evs = [e for e in B.fetch_events_rich()
           if start < dt.datetime.fromtimestamp(int(e.get('estimateStartTime', 0)) / 1000, tz=A.WAT) <= cut
           and 'SRL' not in (e.get('homeTeamName') or '')]                 # simulated games have no real form
    legs, none = [], []
    if FD.active():
        FD.prepare(evs)
    for ev in evs:
        c = [x for x in candidates(ev) if x[1] >= floor]
        # 10 Oct, user: every game booked today follows the winners' finding - the
        # book's most likely option among those the match's own record backs.
        c = [x + (FD.ok(ev, x[5]['marketId'], x[3], x[4])[1],) for x in c if FD.ok(ev, x[5]['marketId'], x[3], x[4])[0]]
        if not c:
            none.append(f"{ev['homeTeamName']} v {ev['awayTeamName']}"); continue
        chance, price, mname, spec, desc, ids, rec = max(c, key=lambda x: (round(x[0], 3), x[1]))
        ts = int(ev['estimateStartTime']) / 1000
        legs.append(dict(ts=ts, match=f"{ev['homeTeamName']} v {ev['awayTeamName']}",
                         sel=f"{mname} / {desc}", odds=price, chance=chance, ids=ids, rec=rec))
    legs.sort(key=lambda l: l['ts'])
    print(f"window {start:%a %H:%M} -> {cut:%a %H:%M}  |  {len(evs)} games, {len(legs)} picked, {len(none)} with nothing at {floor}+")
    parts = [legs[i:i + MAX_LEGS] for i in range(0, len(legs), MAX_LEGS)]
    if len(parts) > 1:                                  # balance the parts instead of 50 + a stub
        k = len(parts); size = -(-len(legs) // k)
        parts = [legs[i:i + size] for i in range(0, len(legs), size)]
    slips = [(f"part {n}", part, f"the highest of this game's options at {floor}+") for n, part in enumerate(parts, 1)]
    for t in targets:
        pick = to_target(legs, t)
        if not pick:
            print(f"\n=== {t:,.0f}x target: the {len(legs)} picks cannot reach it within {MAX_LEGS} games - not booked")
            continue
        slips.append((f"{t:,.0f}x target", pick, f"one of the {len(pick)} picks reaching {t:,.0f}x with the highest chance of all landing"))
    for name, part, why in slips:
        combo = math.prod(l['odds'] for l in part)
        print(f"\n=== {name}: {len(part)} games, {combo:,.2f}x, book chance of every leg landing {math.prod(l['chance'] for l in part):.2%}")
        for l in part:
            print(f"   {dt.datetime.fromtimestamp(l['ts'], tz=A.WAT):%a %H:%M}  {l['match'][:42]:42} {l['sel'][-30:]:30} @{l['odds']:<5} book {l['chance']:.0%}")
        if dry or len(part) < 2:
            continue
        bk = A.book([l['ids'] for l in part])
        if bk and bk.get('code'):
            print(f"\n   >> code {bk['code']}   {bk['url']}")          # lower case: the server's job log reads 'code XXXXXX'
            A.log_booking(bk['code'], bk['url'], f"every game, the book's most likely option at {floor}+ - {name} - {combo:,.2f}x ({len(part)} games)",
                          [(l['ts'], l['match'], l['sel'], l['odds'], [f"book's no-margin chance {l['chance']:.0%} - {why}"]
                            + ([f"match record backs it: {l['rec']}"] if FD.active() else [])) for l in part])
        else:
            print(f"   >> booking failed: {bk.get('msg') if bk else 'no selections'}")


if __name__ == '__main__':
    main()
