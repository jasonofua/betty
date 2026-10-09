#!/usr/bin/env python3
"""ONE MARKET TO A TARGET - one option on every game that offers it, on the book's own numbers.

  python3 book_onemarket.py --markets d5,g10,h1o05,h1u25 [--targets 300] [--max d5] [--until HH] [--dry]

9 Oct, user's call for Saturday: "the 5 mins draw up to 300x and above and 10 mins
draw too, and other options that you see are playing well". Settled legs since
25 Sep (1,059 unique, every product), won against what the price said:
    Match Result after 5 Minutes, Draw     34 of 34   (price 90%)
    1st Half Over 0.5                      17 of 20   (price 80%)
    1st Half Under 2.5                    238 of 264  (price 92%)
    1st Half Under 1.5                    128 of 186  (price 75%)  - not booked here
SportyBet's 'Match Result after X Minutes' (900069) jumps from 5 to 15 - there is
no 10-minute line on any game - so the 10-minute draw is 'Total Goals from 1 to
10 min, Under 0.5' (900313): 0-0 at ten minutes. It only leaves out 1-1 by then.

Each market takes every game in the window that offers it, priced by the book's
no-margin chance (the option's share of its own market). For each --targets
multiplier the code is the set of games reaching it with the highest chance of
every leg landing (book_allgames.to_target, exact, at most 50). --max also books
the 50 longest prices of a market - the most the board allows on one code.
"""
import sys, math, datetime as dt
import os as _o; sys.path.insert(0, _o.path.dirname(_o.path.abspath(__file__)))
import acca as A
import book_v3 as B
import book_allgames as G

MAX_LEGS = 50
MARKETS = {
    'd5': dict(mid='900069', spec='minute=5', want='Draw', other=('Home', 'Away'),
               sel='Match Result after 5 Minutes / Draw', name='draw after 5 minutes'),
    'g10': dict(mid='900313', spec='minute=10|total=0.5', want='Under 0.5', other=('Over 0.5',),
                sel='Total Goals from 1 to 10 min / Under 0.5', name='0-0 at 10 minutes (no 10-minute result market)'),
    'h1o05': dict(mid='68', spec='total=0.5', want='Over 0.5', other=('Under 0.5',),
                  sel='1st Half - Over/Under / Over 0.5', name='first half Over 0.5'),
    'h1u25': dict(mid='68', spec='total=2.5', want='Under 2.5', other=('Over 2.5',),
                  sel='1st Half - Over/Under / Under 2.5', name='first half Under 2.5'),
}


def legs_for(key, evs):
    """Every game offering the market, with its price and the book's no-margin chance."""
    mk = MARKETS[key]; out = []
    for ev in evs:
        for m in ev.get('markets') or []:
            if str(m.get('id')) != mk['mid'] or (m.get('specifier') or '') != mk['spec'] or str(m.get('status', '0')) != '0':
                continue
            o = {x.get('desc'): x for x in m.get('outcomes') or [] if x.get('isActive', 1)}
            if not all(k in o for k in (mk['want'],) + mk['other']):
                continue
            p = {k: 1 / float(o[k]['odds']) for k in (mk['want'],) + mk['other']}
            out.append(dict(ts=int(ev['estimateStartTime']) / 1000, match=f"{ev['homeTeamName']} v {ev['awayTeamName']}",
                            sel=mk['sel'], odds=float(o[mk['want']]['odds']), chance=p[mk['want']] / sum(p.values()),
                            ids=dict(eventId=ev['eventId'], productId=3, marketId=str(m['id']),
                                     specifier=m.get('specifier'), outcomeId=str(o[mk['want']]['id']))))
            break
    return sorted(out, key=lambda l: l['ts'])


def board(mids, start, cut):
    """The window's football events for each market id (one board pull per id)."""
    out = {}
    for mid in mids:
        out[mid] = [e for e in B._fetch_events_for(mid)
                    if start < dt.datetime.fromtimestamp(int(e.get('estimateStartTime', 0)) / 1000, tz=A.WAT) <= cut
                    and 'SRL' not in (e.get('homeTeamName') or '')]
    return out


def book(key, name, part, why, dry):
    combo = math.prod(l['odds'] for l in part)
    print(f"\n=== {MARKETS[key]['name']} - {name}: {len(part)} games, {combo:,.2f}x, "
          f"book chance of every leg landing {math.prod(l['chance'] for l in part):.3%}")
    for l in part:
        print(f"   {dt.datetime.fromtimestamp(l['ts'], tz=A.WAT):%a %H:%M}  {l['match'][:44]:44} @{l['odds']:<5} book {l['chance']:.0%}")
    if dry or len(part) < 2:
        return
    bk = A.book([l['ids'] for l in part])
    if bk and bk.get('code'):
        print(f"\n   >> code {bk['code']}   {bk['url']}")          # lower case: the server's job log reads 'code XXXXXX'
        A.log_booking(bk['code'], bk['url'], f"one market: {MARKETS[key]['sel']} - {name} - {combo:,.2f}x ({len(part)} games)",
                      [(l['ts'], l['match'], l['sel'], l['odds'], [f"book's no-margin chance {l['chance']:.0%} - {why}"]) for l in part])
    else:
        print(f"   >> booking failed: {bk.get('msg') if bk else 'no selections'}")


def run(keys, targets, maxkeys, start, cut, dry):
    evs = board(sorted({MARKETS[k]['mid'] for k in keys}), start, cut)
    print(f"window {start:%a %H:%M} -> {cut:%a %H:%M}")
    for key in keys:
        legs = legs_for(key, evs[MARKETS[key]['mid']])
        print(f"\n{MARKETS[key]['name']}: offered on {len(legs)} games")
        for t in targets:
            pick = G.to_target(legs, t, MAX_LEGS)
            if not pick:
                print(f"=== {t:,.0f}x: the {len(legs)} games cannot reach it within {MAX_LEGS} - not booked")
                continue
            book(key, f"{t:,.0f}x target", pick, f"one of the {len(pick)} games reaching {t:,.0f}x with the highest chance of all landing", dry)
        if key in maxkeys and len(legs) >= 2:
            top = sorted(sorted(legs, key=lambda l: -l['odds'])[:MAX_LEGS], key=lambda l: l['ts'])
            book(key, f"the {len(top)} longest prices", top, f"among the {len(top)} longest prices on the board", dry)


def main():
    arg = lambda k, d: sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d
    keys = [k for k in arg('--markets', 'd5').split(',') if k in MARKETS]
    targets = [float(t) for t in arg('--targets', '300').split(',') if t.strip()]
    maxkeys = [k for k in arg('--max', '').split(',') if k]
    until = int(arg('--until', 23))
    now = dt.datetime.now(A.WAT); start = now + dt.timedelta(hours=1)   # standing rule: an hour out
    cut = now.replace(hour=until, minute=59, second=59, microsecond=0)
    if cut <= now:
        cut += dt.timedelta(days=1)
    run(keys, targets, maxkeys, start, cut, '--dry' in sys.argv)


if __name__ == '__main__':
    main()
