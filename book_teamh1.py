#!/usr/bin/env python3
"""TEAM FIRST-HALF UNDERS - one team's goals before half time.

  python3 book_teamh1.py [--until HH] [--days N] [--dry]

3 Oct, user's call. SportyBet prices '1st Half - Home O/U' (market 69) and
'1st Half - Away O/U' (market 70) on the event page, not on the board feed.

For the HOME team's Under the two columns are the home side's first halves at home
(goals it SCORED) and the away side's first halves away (goals it CONCEDED); for the
AWAY team's Under, the away side's scoring away and the home side's conceding at
home. A leg needs both columns at 6 of 7 or better. Each game contributes its one
strongest team-Under, and two slips are booked: Under 0.5 (the team does not score
in the first half) and Under 1.5. Untested on past results when first booked -
these slips are the first measured run.
"""
import sys, datetime as dt
import os as _o; sys.path.insert(0, _o.path.dirname(_o.path.abspath(__file__)))
import acca as A
import book_v3 as B
import dynamic_v4 as D
import fetcher_v2 as F2
import book_amfoot as P
import book_h1unders as H

COL_MIN = 6 / 7


def team_markets(eid):
    try:
        d = P.get(P.BASE + f"event?eventId={eid}&productId=3")
    except Exception:
        return {}
    out = {}
    for m in (d.get('data') or {}).get('markets') or []:
        mid = str(m.get('id'))
        if mid not in ('69', '70') or str(m.get('status', '0')) != '0':
            continue
        spec = m.get('specifier') or ''
        try:
            line = float(spec.split('=')[-1])
        except ValueError:
            continue
        for o in m.get('outcomes') or []:
            if (o.get('desc') or '').lower().startswith('under') and o.get('isActive', 1):
                try:
                    out[('home' if mid == '69' else 'away', line)] = (float(o['odds']), dict(
                        eventId=eid, productId=3, marketId=mid, specifier=spec, outcomeId=str(o['id'])))
                except (KeyError, ValueError):
                    pass
    return out


def scan(until_h=23, days=0):
    now = dt.datetime.now(A.WAT); start = now + dt.timedelta(hours=1)
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
    pairs = D.join(evs, fx, lambda e: dt.datetime.fromtimestamp(int(e['estimateStartTime']) / 1000, tz=A.WAT),
                   lambda f: dt.datetime.fromtimestamp(f['ts'], tz=A.WAT))
    print(f"window {start:%a %H:%M} -> {cut:%a %H:%M}  |  sportybet {len(evs)}  joined {len(pairs)}", flush=True)
    best = {0.5: [], 1.5: []}
    for ev, f, _s in pairs:
        gap = H.venue_gap(f['id'], int(f['ts']))
        if gap is None or gap > H.SEASON_GAP_DAYS:
            continue
        h, a = D.records_for(f['id'])
        if not h or not a:
            continue
        hp, ap = h.pairs('h1'), a.pairs('h1')
        if len(hp) < 5 or len(ap) < 5:
            continue
        # 4 Oct: team Under 1.5 also needs both sides to have 2+ goalless first halves
        # at their venue. On 315 fresh fixtures (30 Sep - 4 Oct, leak-free) the current
        # rule went 265/293 = 90% - under the 1.07-1.12 price; with the gate 131/139 =
        # 94% (96% / 94% by half), without it 134/154 = 87%. Danubio (Cerro Largo's six
        # away first halves all had a goal) and Montevideo Wanderers (Cerro's home first
        # halves goalless once in six) both lost at Under 1.5 on 4 Oct. Team Under 0.5
        # had 13 legs in the same set - too few to set anything.
        quiet = min(sum(1 for x, y in hp if x + y == 0), sum(1 for x, y in ap if x + y == 0)) >= 2
        mk = team_markets(ev['eventId'])
        if not mk:
            continue
        ts = dt.datetime.fromtimestamp(int(ev['estimateStartTime']) / 1000, tz=A.WAT)
        for line in (0.5, 1.5):
            if line == 1.5 and not quiet:
                continue
            cands = []
            for side in ('home', 'away'):
                if (side, line) not in mk:
                    continue
                if side == 'home':
                    own = [x for x, _ in hp]; opp = [y for _, y in ap]     # home scores at home, away concedes away
                else:
                    own = [x for x, _ in ap]; opp = [y for _, y in hp]     # away scores away, home concedes at home
                oc = sum(1 for v in own if v < line) / len(own)
                pc = sum(1 for v in opp if v < line) / len(opp)
                if min(oc, pc) < COL_MIN - 1e-9:
                    continue
                odds, ids = mk[(side, line)]
                team = f['home'] if side == 'home' else f['away']
                cands.append(dict(score=(oc + pc), odds=odds, ids=ids, side=side, team=team, line=line,
                                  ts=ts.timestamp(), when=ts.strftime('%a %H:%M'), match=f"{f['home']} v {f['away']}",
                                  lg=f.get('league', ''), series=f"{sum(1 for v in own if v < line)}/{len(own)}+"
                                  f"{sum(1 for v in opp if v < line)}/{len(opp)}"))
            if cands:
                best[line].append(max(cands, key=lambda c: (c['score'], c['odds'])))
    for line in best:
        best[line].sort(key=lambda c: (-c['score'], c['odds']))
    return best


def main():
    dry = '--dry' in sys.argv
    until = int(sys.argv[sys.argv.index('--until') + 1]) if '--until' in sys.argv else 23
    days = int(sys.argv[sys.argv.index('--days') + 1]) if '--days' in sys.argv else 0
    best = scan(until, days)
    for line, legs in best.items():
        legs = legs[:50]
        if len(legs) < 2:
            print(f"\n   (team 1H Under {line}: only {len(legs)} games qualify - nothing booked)")
            continue
        combo = 1.0
        for l in legs:
            combo *= l['odds']
        print(f"\n=== Team 1st Half Under {line}  -  {len(legs)} games, {combo:,.2f}x\n")
        for l in legs:
            print(f"   {l['when']}  {l['match'][:34]:34} {l['team'][:18]:18} U{line}  {l['series']:9} @{l['odds']:<5} {l['lg'][:20]}")
        if dry:
            continue
        bk = A.book([l['ids'] for l in legs])
        if bk and bk.get('code'):
            print(f"\n   >> CODE {bk['code']}   {bk['url']}")
            A.log_booking(bk['code'], bk['url'], f"one market: Team 1st Half Under {line} - {combo:,.2f}x ({len(legs)} games)",
                          [(l['ts'], l['match'], f"1st Half - {l['side'].title()} O/U / Under {line} ({l['team']})", l['odds'],
                            [f"{l['team']} first halves under {line} and opponent's: {l['series']}"]) for l in legs])
        else:
            print(f"   >> booking failed: {bk.get('msg') if bk else 'no selections'}")


if __name__ == '__main__':
    main()
