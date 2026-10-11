#!/usr/bin/env python3
"""STRONG GROUPS - only the legs from the groups that won above their price on both halves of the data.

  python3 book_strong.py [--until HH] [--targets 300,2000,...] [--dry]

11 Oct, user: "So all this losses what is the fix" / "Apply". Settled legs 1-11 Oct
(1,548 unique, every product), won against the book's no-margin price:
    draw after 5 minutes                     129 of 134  96%  (priced 82%)
    England league games, other markets       87 of 99   88%  (priced 77%)  94% / 85% on 1-6 / 7-11 Oct
    Scotland games, other markets             29 of 33   88%  (priced 79%)  89% / 88%
    0-0 at 10 minutes                         72 of 85   85%  (priced 75%)  7-11 Oct only
    the whole board                         1250 of 1548 81%  (priced 77%)
No market family lost more than its price; what separates is the game and the market.
A code of only these groups, one leg per game, would have gone 42/42 on 9 Oct (96x),
14/14 on 6 Oct, 23/24 on 3 Oct and 84/94 on 10 Oct (4 of the 10 losses were 5-minute
draws the 10 Oct minute fix now stops).

Each game takes one leg, the most likely of: its draw after 5 minutes and its 0-0 at
10 minutes (any league, only where the match record passes the minute check in
findings.py - book_onemarket.legs_for), and, for England and Scotland games, the book's
most likely option at 1.15+ (book_allgames.candidates, the Every game pick). All the
legs book in time order in codes of up to 50; each --targets multiplier books the set
reaching it with the highest chance of every leg landing (book_allgames.to_target), and
past 50 legs the 50 longest prices book as one more code - the biggest the board allows.
Uruguay and Russia never reach here (sportybet.SKIP_COUNTRIES).
"""
import sys, math, datetime as dt
import os as _o; sys.path.insert(0, _o.path.dirname(_o.path.abspath(__file__)))
import acca as A
import book_v3 as B
import book_allgames as G
import book_onemarket as OM
import findings as FD

MAX_LEGS = 50
COUNTRIES = ('England', 'Scotland')
FLOOR = 1.15
LABEL = 'strong groups: 5-min draw, 0-0 at 10 min, England and Scotland games'
RATE = {'d5': 129 / 134, 'g10': 72 / 85, 'country': 116 / 132}     # won 1-11 Oct (see the docstring)


def group(l):
    return 'd5' if '5 Minutes' in l['sel'] else 'g10' if '1 to 10' in l['sel'] else 'country'


def country(ev):
    return ((ev.get('sport') or {}).get('category') or {}).get('name') or ''


def picks(start, cut):
    """One leg per game, the most likely of its strong-group options."""
    inwin = lambda e: (start < dt.datetime.fromtimestamp(int(e.get('estimateStartTime', 0)) / 1000, tz=A.WAT) <= cut
                       and 'SRL' not in (e.get('homeTeamName') or ''))
    minute = OM.board(['900069', '900313'], start, cut)
    rich = [e for e in B.fetch_events_rich() if inwin(e) and country(e) in COUNTRIES]
    if FD.any_active():                          # minute markets are checked every day from 10 Oct
        FD.prepare(minute['900069'] + minute['900313'] + rich)
    by = {}
    for key, mid in (('d5', '900069'), ('g10', '900313')):
        for l in OM.legs_for(key, minute[mid]):
            l['why'] = f"{OM.MARKETS[key]['name']} - 96% of 134 settled" if key == 'd5' else f"{OM.MARKETS[key]['name']} - 85% of 85 settled"
            by.setdefault(l['ids']['eventId'], []).append(l)
    for ev in rich:
        c = [x for x in G.candidates(ev) if x[1] >= FLOOR]
        c = [x + (FD.ok(ev, x[5]['marketId'], x[3], x[4])[1],) for x in c if FD.ok(ev, x[5]['marketId'], x[3], x[4])[0]]
        if not c:
            continue
        chance, price, mname, spec, desc, ids, rec = max(c, key=lambda x: (round(x[0], 3), x[1]))
        by.setdefault(ev['eventId'], []).append(dict(
            ts=int(ev['estimateStartTime']) / 1000, match=f"{ev['homeTeamName']} v {ev['awayTeamName']}",
            sel=f"{mname} / {desc}", odds=price, chance=chance, ids=ids, rec=rec,
            why=f"{country(ev)} game, the book's most likely option at {FLOOR}+ - 88% of 132 settled"))
    return sorted((max(v, key=lambda l: (round(l['chance'], 3), l['odds'])) for v in by.values()), key=lambda l: l['ts'])


def main():
    arg = lambda k, d: sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d
    dry = '--dry' in sys.argv
    until = int(arg('--until', 23))
    targets = [float(t) for t in arg('--targets', '').split(',') if t.strip()]
    now = dt.datetime.now(A.WAT); start = now + dt.timedelta(hours=1)   # standing rule: an hour out
    cut = now.replace(hour=until, minute=59, second=59, microsecond=0)
    if cut <= now:
        cut += dt.timedelta(days=1)
    legs = picks(start, cut)
    n5 = sum('5 Minutes' in l['sel'] for l in legs); n10 = sum('1 to 10' in l['sel'] for l in legs)
    print(f"window {start:%a %H:%M} -> {cut:%a %H:%M}  |  {len(legs)} games: {n5} draw after 5 min, {n10} 0-0 at 10 min, "
          f"{len(legs) - n5 - n10} England/Scotland picks  |  all together {math.prod(l['odds'] for l in legs):,.2f}x")
    parts = [legs[i:i + MAX_LEGS] for i in range(0, len(legs), MAX_LEGS)]
    if len(parts) > 1:                                  # balance the parts instead of 50 + a stub
        size = -(-len(legs) // len(parts))
        parts = [legs[i:i + size] for i in range(0, len(legs), size)]
    slips = [(f"part {n}" if len(parts) > 1 else 'every strong game', part, 'one of the strong-group games') for n, part in enumerate(parts, 1)]
    if len(legs) > MAX_LEGS:
        top = sorted(sorted(legs, key=lambda l: -l['odds'])[:MAX_LEGS], key=lambda l: l['ts'])
        slips.append((f"the {MAX_LEGS} longest prices", top, f"among the {MAX_LEGS} longest prices of the strong-group games"))
    for t in targets:
        pick = G.to_target(legs, t, MAX_LEGS)
        if not pick:
            print(f"\n=== {t:,.0f}x target: the {len(legs)} strong-group games cannot reach it within {MAX_LEGS} - not booked")
            continue
        slips.append((f"{t:,.0f}x target", pick, f"one of the {len(pick)} games reaching {t:,.0f}x with the highest chance of all landing"))
    for name, part, why in slips:
        combo = math.prod(l['odds'] for l in part)
        print(f"\n=== {name}: {len(part)} games, {combo:,.2f}x, book chance of every leg landing {math.prod(l['chance'] for l in part):.2%}, "
              f"on the groups' measured rates {math.prod(RATE[group(l)] for l in part):.1%}")
        for l in part:
            print(f"   {dt.datetime.fromtimestamp(l['ts'], tz=A.WAT):%a %H:%M}  {l['match'][:42]:42} {l['sel'][-34:]:34} @{l['odds']:<5} book {l['chance']:.0%}")
        if dry or len(part) < 2:
            continue
        bk = A.book([l['ids'] for l in part])
        if bk and bk.get('code'):
            print(f"\n   >> code {bk['code']}   {bk['url']}")          # lower case: the server's job log reads 'code XXXXXX'
            A.log_booking(bk['code'], bk['url'], f"{LABEL} - {name} - {combo:,.2f}x ({len(part)} games)",
                          [(l['ts'], l['match'], l['sel'], l['odds'], [f"book's no-margin chance {l['chance']:.0%} - {l['why']} - {why}"]
                            + ([f"match record backs it: {l['rec']}"] if l.get('rec') and l['rec'] != 'finding off' else [])) for l in part])
        else:
            print(f"   >> booking failed: {bk.get('msg') if bk else 'no selections'}")


if __name__ == '__main__':
    main()
