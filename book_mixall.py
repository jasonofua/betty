#!/usr/bin/env python3
"""ALL-OPTIONS MIXED SLIP - every first-half / early option, one per game.

  python3 book_mixall.py [--target 500] [--until HH] [--days N] [--singles] [--dry]

4 Oct, user's call. Options per game, each from its own scanner and gates:
  1st Half Under 2.5 / Under 1.5      (book_h1unders.scan)
  Team 1st Half Under 0.5 / Under 1.5 (book_teamh1.scan, markets 69/70)
  Match Result after 5 Minutes, Draw  (book_early.scan)
One option per game. The slip is built to the target the way the first-half mix is
(book_h1unders.to_target_both): each step takes the move with the most price gained
per landing chance lost - add a game on any of its options, or lift a game already
on to a longer-priced option of its own. Landing chances are the measured group
rates; the team-Under and early-draw rates come from their first graded slips only
(3 Oct: team U0.5 7/7, team U1.5 20/22, 5-minute draw 4/4) and are shrunk toward
the middle until more results come in.

--singles also books the team Under 0.5, team Under 1.5 and 5-minute draw slips
from the same scan.
"""
import sys, math, datetime as dt
import os as _o; sys.path.insert(0, _o.path.dirname(_o.path.abspath(__file__)))
import acca as A
import book_h1unders as H
import book_teamh1 as T
import book_early as E

# (wins + 1) / (games + 2) on the first graded slips, 3 Oct
RATE_TEAM = {0.5: (7 + 1) / (7 + 2), 1.5: (20 + 1) / (22 + 2)}
RATE_EARLY = (4 + 1) / (4 + 2)


def options(until, days):
    opts = {}

    def add(eid, o):
        opts.setdefault(eid, []).append(o)

    for line in (1.5, 2.5):
        for l in H.scan(until, days, line=line, verbose=line == 1.5):
            o = dict(l, line=line)
            add(l['ids']['eventId'], dict(o, rate=H.group_rate(o), sel=f"1st Half - Over/Under / Under {line}",
                                          tag=f"1H U{line}", why=[f"first halves under {line}: {l['series']} - {l['window']}"]))
    best = T.scan(until, days)
    for line, legs in best.items():
        for l in legs:
            add(l['ids']['eventId'], dict(l, rate=RATE_TEAM[line],
                                          sel=f"1st Half - {l['side'].title()} O/U / Under {line} ({l['team']})",
                                          tag=f"{l['team'][:12]} 1H U{line}",
                                          why=[f"{l['team']} first halves under {line} and opponent's: {l['series']}"]))
    early = E.scan(5, until, days)
    for l in early:
        add(l['ids']['eventId'], dict(l, rate=RATE_EARLY, sel="Match Result after 5 Minutes / Draw",
                                      tag="5 min draw", why=[f"level at 5 minutes in {l['n']} of both sides' venue games"]))
    return opts, best, early


def to_target(opts, target, cap=50):
    def gc(o):
        return math.log(o['odds']), -math.log(o['rate'])
    chosen, combo = {}, 1.0
    while combo < target:
        best = None
        for e, v in opts.items():
            cur = chosen.get(e)
            for o in v:
                if o['odds'] <= 1.0:
                    continue
                if cur is None:
                    if len(chosen) >= cap:
                        continue
                    g, c = gc(o)
                    r = g / c if c > 0 else 0
                elif o['odds'] > cur['odds']:
                    g1, c1 = gc(o); g0, c0 = gc(cur)
                    dg, dc = g1 - g0, c1 - c0
                    r = dg / dc if dc > 0 else (float('inf') if dg > 0 else 0)
                else:
                    continue
                if best is None or r > best[0]:
                    best = (r, e, o)
        if best is None:
            break
        _, e, o = best
        combo = (combo / chosen[e]['odds'] if e in chosen else combo) * o['odds']
        chosen[e] = o
    return sorted(chosen.values(), key=lambda l: l['ts']), combo


def book(legs, label, dry):
    combo = math.prod(l['odds'] for l in legs)
    print(f"\n=== {label}  -  {len(legs)} games, {combo:,.2f}x\n")
    for l in legs:
        print(f"   {l['when']}  {l['match'][:34]:34} {l['tag'][:24]:24} rate {l['rate']*100:3.0f}%  @{l['odds']:<5} {l['lg'][:20]}")
    if dry or len(legs) < 2:
        return
    bk = A.book([l['ids'] for l in legs])
    if bk and bk.get('code'):
        print(f"\n   >> CODE {bk['code']}   {bk['url']}")
        A.log_booking(bk['code'], bk['url'], f"{label} - {combo:,.2f}x ({len(legs)} games)",
                      [(l['ts'], l['match'], l['sel'], l['odds'], l['why']) for l in legs])
    else:
        print(f"   >> booking failed: {bk.get('msg') if bk else 'no selections'}")


def main():
    dry = '--dry' in sys.argv
    until = int(sys.argv[sys.argv.index('--until') + 1]) if '--until' in sys.argv else 23
    days = int(sys.argv[sys.argv.index('--days') + 1]) if '--days' in sys.argv else 0
    targets = ([float(t) for t in sys.argv[sys.argv.index('--target') + 1].split(',')]
               if '--target' in sys.argv else [500.0])
    opts, best, early = options(until, days)
    print(f"\n{len(opts)} games with at least one option", flush=True)
    if '--singles' in sys.argv:
        for line in (0.5, 1.5):
            legs = [dict(l, rate=RATE_TEAM[line], sel=f"1st Half - {l['side'].title()} O/U / Under {line} ({l['team']})",
                         tag=f"{l['team'][:12]} U{line}",
                         why=[f"{l['team']} first halves under {line} and opponent's: {l['series']}"]) for l in best[line][:50]]
            book(legs, f"one market: Team 1st Half Under {line}", dry)
        legs = [dict(l, rate=RATE_EARLY, sel="Match Result after 5 Minutes / Draw", tag="5 min draw",
                     why=[f"level at 5 minutes in {l['n']} of both sides' venue games"]) for l in early[:50]]
        book(legs, "one market: Match Result after 5 Minutes / Draw", dry)
    for t in targets:
        legs, combo = to_target(opts, t)
        if combo < t:
            print(f"\n   (target {t:g}x not reached - {len(legs)} legs give {combo:,.0f}x)")
        book(legs, f"all options mixed, one per game - {t:g}x target", dry)


if __name__ == '__main__':
    main()
