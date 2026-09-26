#!/usr/bin/env python3
"""FIRST-HALF UNDER 2.5, scanned straight off the board.

  python3 book_h1unders.py [--until HH] [--days N] [--min-rate 0.90] [--dry]

26 Sep. This does not go through the evaluator: it reads SportyBet's 1st-half
total market (id 68) for every fixture in the window and ranks on the two sides'
own first-half records - the home side's home first halves and the away side's
away first halves.

Measured on the 26 Sep board, 50 legs booked as NRBQCG:

    tally said 100%   13 of 14 won
    tally said 90-99% 10 of 11 won
    overall           30 of 32  = 94%

The Under 1.5 version of the same scan went 13 of 20 (65%) and its ordering was
INVERTED - the legs it rated 90%+ went 1 of 2 while the legs it rated under 80%
went 4 of 4. One goal decides a first half at 1.5, so a count of past first
halves that stayed under it is measuring the base rate, not the fixture. Only
2.5 is booked here; 1.5 needs a real expected-goals read before it comes back.
"""
import sys, datetime as dt, json
import os as _o; sys.path.insert(0, _o.path.dirname(_o.path.abspath(__file__)))
import acca as A
import book_v3 as B
import dynamic_v4 as D
import fetcher_v2 as F2

LINE = 2.5
CAP = 2                  # goals allowed in the half
MIN_RATE = 0.90          # both sides' first halves must clear this
MIN_GAMES = 5            # ... over at least this many games each


def under_market(ev):
    for m in (ev.get('markets') or []):
        if str(m.get('id')) != '68' or f"total={LINE}" not in (m.get('specifier') or ''):
            continue
        if str(m.get('status', '0')) != '0':
            continue
        for o in (m.get('outcomes') or []):
            if (o.get('desc') or '').lower().startswith('under') and o.get('isActive', 1):
                try:
                    return float(o['odds']), dict(eventId=ev['eventId'], productId=3,
                                                  marketId=str(m['id']), specifier=m.get('specifier', ''),
                                                  outcomeId=str(o['id']))
                except (KeyError, ValueError):
                    return None
    return None


def scan(until_h=23, days=0, min_rate=MIN_RATE, verbose=True):
    now = dt.datetime.now(A.WAT)
    start = now + dt.timedelta(hours=1)
    cut = now.replace(hour=until_h, minute=0, second=0, microsecond=0)
    if cut <= now:
        cut += dt.timedelta(days=1)
    cut += dt.timedelta(days=days)
    evs = [e for e in B.fetch_events_rich()
           if start < dt.datetime.fromtimestamp(int(e.get('estimateStartTime', 0)) / 1000, tz=A.WAT) <= cut]
    seen, fx = set(), []
    for off in range(max(2, (cut.date() - now.date()).days) + 1):
        for f in F2.get_fixtures(off):
            if f['id'] not in seen:
                seen.add(f['id']); fx.append(f)
    pairs = D.join(evs, fx,
                   lambda e: dt.datetime.fromtimestamp(int(e['estimateStartTime']) / 1000, tz=A.WAT),
                   lambda f: dt.datetime.fromtimestamp(f['ts'], tz=A.WAT))
    if verbose:
        print(f"window {start:%a %H:%M} -> {cut:%a %H:%M}  |  sportybet {len(evs)}  joined {len(pairs)}", flush=True)
    out = []
    for ev, f, _s in pairs:
        got = under_market(ev)
        if not got:
            continue
        h, a = D.records_for(f['id'])
        if not h or not a:
            continue
        hp, ap = h.pairs('h1'), a.pairs('h1')
        if len(hp) < MIN_GAMES or len(ap) < MIN_GAMES:
            continue
        tot = [x + y for x, y in hp] + [x + y for x, y in ap]
        hits = sum(1 for v in tot if v <= CAP)
        rate = hits / len(tot)
        if rate < min_rate:
            continue
        odds, ids = got
        ts = dt.datetime.fromtimestamp(int(ev['estimateStartTime']) / 1000, tz=A.WAT)
        out.append(dict(ts=ts.timestamp(), when=ts.strftime('%a %H:%M'),
                        match=f"{f['home']} v {f['away']}", lg=f.get('league', ''),
                        odds=odds, rate=rate, ids=ids,
                        series=f"{sum(1 for v in [x + y for x, y in hp] if v <= CAP)}/{len(hp)}+"
                               f"{sum(1 for v in [x + y for x, y in ap] if v <= CAP)}/{len(ap)}"))
    out.sort(key=lambda r: (-r['rate'], r['odds']))
    return out


def main():
    dry = '--dry' in sys.argv
    until = int(sys.argv[sys.argv.index('--until') + 1]) if '--until' in sys.argv else 23
    days = int(sys.argv[sys.argv.index('--days') + 1]) if '--days' in sys.argv else 0
    mr = float(sys.argv[sys.argv.index('--min-rate') + 1]) if '--min-rate' in sys.argv else MIN_RATE
    legs = scan(until, days, mr)
    if len(legs) < 2:
        print(f">> only {len(legs)} games clear {mr:.0%} - nothing booked")
        return
    combo = 1.0
    for l in legs:
        combo *= l['odds']
    print(f"\n=== 1st Half Under {LINE}  -  {len(legs)} games, {combo:,.2f}x\n")
    for l in legs:
        print(f"   {l['when']}  {l['match'][:38]:38} {l['series']:9} {l['rate']*100:3.0f}%  @{l['odds']:<5} {l['lg'][:24]}")
    if dry:
        return
    bk = A.book([l['ids'] for l in legs])
    if bk and bk.get('code'):
        print(f"\n   >> CODE {bk['code']}   {bk['url']}")
        A.log_booking(bk['code'], bk['url'],
                      f"one market: 1st Half - Over/Under / Under {LINE} - {combo:,.2f}x ({len(legs)} games)",
                      [(l['ts'], l['match'], f"1st Half - Over/Under / Under {LINE}", l['odds'],
                        [f"first halves under {LINE}: {l['series']} = {l['rate']*100:.0f}%"]) for l in legs])
    else:
        print(f"   >> booking failed: {bk.get('msg') if bk else 'no selections'}")


if __name__ == '__main__':
    main()
