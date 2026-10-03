#!/usr/bin/env python3
"""EARLY DRAW - 'Match Result after X Minutes', Draw (default the 5th minute).

  python3 book_early.py [--minute 5] [--until HH] [--days N] [--dry]

3 Oct, user's call. For every fixture: the home side's last 7 HOME games and the
away side's last 7 AWAY games, each match's goals read with their minutes from
Flashscore's incident feed (fetcher_v3.parse_match_summary, which validates every
match against its known scoreline and drops stubs). A game qualifies when the score
was level at the minute mark in every one of those matches it could read (at least
5 per side). Untested on past results when first booked.
"""
import sys, re, datetime as dt
import os as _o; sys.path.insert(0, _o.path.dirname(_o.path.abspath(__file__)))
import acca as A
import book_v3 as B
import dynamic_v4 as D
import fetcher_v2 as F2
import fetcher_v3 as F3
import book_h1unders as H

MIN_READ = 5


def venue_matches(fid, kickoff, suffix):
    """[(match_id, home_goals, away_goals)] for the side's last 7 games at this venue."""
    try:
        raw = F2.fetch(f"df_hh_1_{fid}")
    except Exception:
        return []
    for tab in raw.split('~KA÷')[1:]:
        if not tab.split('¬')[0].endswith(suffix):
            continue
        for blk in tab.split('~KB÷')[1:2]:
            out = []
            for row in re.split(r'~(?=KC÷)', blk):
                d = dict(re.findall(r'([A-Z]{2,3})÷([^¬]*)', row))
                if 'KC' not in d or not d.get('KP') or d.get('KU') in (None, '') or int(d['KC']) >= kickoff - 3600:
                    continue
                out.append((d['KP'], int(d['KU']), int(d['KT'])))
            return out[:7]
    return []


def level_at(mid, hs, as_, minute):
    s = F3.parse_match_summary(mid, hs, as_)
    if not s or not s.get('goals_ok'):
        return None
    h = sum(1 for g in s['goals'] if g.get('minute') is not None and g['minute'] <= minute and g['team'] == 'home')
    a = sum(1 for g in s['goals'] if g.get('minute') is not None and g['minute'] <= minute and g['team'] == 'away')
    return h == a


def draw_market(ev, minute):
    for m in (ev.get('markets') or []):
        if (m.get('name') or m.get('desc') or '') != 'Match Result after X Minutes':
            continue
        if f"minute={minute}" != (m.get('specifier') or '') or str(m.get('status', '0')) != '0':
            continue
        for o in m.get('outcomes') or []:
            if o.get('desc') == 'Draw' and o.get('isActive', 1):
                return float(o['odds']), dict(eventId=ev['eventId'], productId=3, marketId=str(m['id']),
                                              specifier=m.get('specifier'), outcomeId=str(o['id']))
    return None


def main():
    dry = '--dry' in sys.argv
    minute = int(sys.argv[sys.argv.index('--minute') + 1]) if '--minute' in sys.argv else 5
    until = int(sys.argv[sys.argv.index('--until') + 1]) if '--until' in sys.argv else 23
    days = int(sys.argv[sys.argv.index('--days') + 1]) if '--days' in sys.argv else 0
    now = dt.datetime.now(A.WAT); start = now + dt.timedelta(hours=1)
    cut = now.replace(hour=until, minute=0, second=0, microsecond=0)
    if cut <= now:
        cut += dt.timedelta(days=1)
    cut += dt.timedelta(days=days)
    evs = [e for e in B.fetch_events_rich()
           if start < dt.datetime.fromtimestamp(int(e.get('estimateStartTime', 0)) / 1000, tz=A.WAT) <= cut]
    evs = [e for e in evs if draw_market(e, minute)]
    seen, fx = set(), []
    for off in range(max(2, (cut.date() - now.date()).days) + 1):
        for f in F2.get_fixtures(off):
            if f['id'] not in seen:
                seen.add(f['id']); fx.append(f)
    pairs = D.join(evs, fx, lambda e: dt.datetime.fromtimestamp(int(e['estimateStartTime']) / 1000, tz=A.WAT),
                   lambda f: dt.datetime.fromtimestamp(f['ts'], tz=A.WAT))
    print(f"window {start:%a %H:%M} -> {cut:%a %H:%M}  |  {minute}-minute draw offered on {len(evs)}, joined {len(pairs)}", flush=True)
    legs = []
    for i, (ev, f, _s) in enumerate(pairs):
        if i % 20 == 0:
            print(f"  {i}/{len(pairs)}  qualifying so far {len(legs)}", flush=True)
        gap = H.venue_gap(f['id'], int(f['ts']))
        if gap is None or gap > H.SEASON_GAP_DAYS:
            continue
        cols = []
        for suffix in (' - Home', ' - Away'):
            res = [level_at(mid, hs, as_, minute) for mid, hs, as_ in venue_matches(f['id'], int(f['ts']), suffix)]
            res = [r for r in res if r is not None]
            cols.append(res)
        if min(len(c) for c in cols) < MIN_READ or not all(all(c) for c in cols):
            continue
        odds, ids = draw_market(ev, minute)
        ts = dt.datetime.fromtimestamp(int(ev['estimateStartTime']) / 1000, tz=A.WAT)
        legs.append(dict(ts=ts.timestamp(), when=ts.strftime('%a %H:%M'), match=f"{f['home']} v {f['away']}",
                         lg=f.get('league', ''), odds=odds, ids=ids, n=f"{len(cols[0])}/{len(cols[0])}+{len(cols[1])}/{len(cols[1])}"))
    legs.sort(key=lambda l: l['ts'])
    legs = legs[:50]
    if len(legs) < 2:
        print(f">> only {len(legs)} games qualify - nothing booked"); return
    combo = 1.0
    for l in legs:
        combo *= l['odds']
    print(f"\n=== Draw after {minute} minutes  -  {len(legs)} games, {combo:,.2f}x\n")
    for l in legs:
        print(f"   {l['when']}  {l['match'][:40]:40} level at {minute}': {l['n']:9} @{l['odds']:<5} {l['lg'][:22]}")
    if dry:
        return
    bk = A.book([l['ids'] for l in legs])
    if bk and bk.get('code'):
        print(f"\n   >> CODE {bk['code']}   {bk['url']}")
        A.log_booking(bk['code'], bk['url'], f"one market: Match Result after {minute} Minutes / Draw - {combo:,.2f}x ({len(legs)} games)",
                      [(l['ts'], l['match'], f"Match Result after {minute} Minutes / Draw", l['odds'],
                        [f"level at {minute} minutes in {l['n']} of both sides' venue games"]) for l in legs])
    else:
        print(f"   >> booking failed: {bk.get('msg') if bk else 'no selections'}")


if __name__ == '__main__':
    main()
