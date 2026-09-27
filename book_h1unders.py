#!/usr/bin/env python3
"""FIRST-HALF UNDERS, scanned straight off the board.

  python3 book_h1unders.py [--line 1.5|2.5] [--until HH] [--days N] [--dry]
                           [--min-rate 0.90] [--min-model 0.70]

26 Sep. This does not go through the evaluator: it reads SportyBet's 1st-half
total market (id 68) for every fixture in the window and ranks on the two sides'
own first-half records - the home side's home first halves and the away side's
away first halves.

Measured on the 26 Sep board, 50 legs booked as NRBQCG at Under 2.5:

    tally said 100%   13 of 14 won
    tally said 90-99% 10 of 11 won
    overall           30 of 32  = 94%

The Under 1.5 scan the same day went 13 of 20 (65%) and the TALLY's ordering was
inverted - the legs it rated 90%+ went 1 of 2 while the legs it rated under 80%
went 4 of 4. One goal decides a first half at 1.5, so counting past first halves
that stayed under it measures the league's base rate rather than this fixture.

So 1.5 is ranked on EXPECTED GOALS instead of on the count. The two sides' own
venue first halves give an expected first-half total, and a Poisson on that total
gives the chance of 0 or 1 goal. A fixture qualifies when the model says it and
the model is not simply repeating the price. The count stays on the leg as
evidence; it no longer decides.
"""
import sys, re, math, datetime as dt, json
import os as _o; sys.path.insert(0, _o.path.dirname(_o.path.abspath(__file__)))
import acca as A
import book_v3 as B
import dynamic_v4 as D
import fetcher_v2 as F2

LINE = 2.5
MIN_RATE = 0.90          # the count bar, used at 2.5 where the count orders correctly
MIN_MODEL = 0.70         # the expected-goals bar, used at 1.5
MAX_PRICE_15 = 1.40      # 27 Sep: at a 1.5 line the BOOK orders the games and our count does not.
                         # 26 Sep board, 30 settled legs: priced 1.25 or shorter 7 of 8 (88%),
                         # 1.25-1.43 10 of 13 (77%), 1.43 or longer 4 of 9 (44%). Seven of the
                         # nine losers were priced 1.31+, and Annan v Hibernian B went on at 1.89
                         # - the book calling it a coin flip - because the count read 5/7+5/5.
                         # Nothing above this price goes on the Under 1.5 slip.
MIN_GAMES = 5            # ... over at least this many games each


def poisson_under(lmbda, cap):
    """Chance of at most `cap` goals in the half, from the expected total."""
    p, term = 0.0, math.exp(-lmbda)
    for k in range(cap + 1):
        p += term
        term *= lmbda / (k + 1)
    return p


def expected_half(hp, ap):
    """Expected first-half goals: what the home side's home halves produce and
    concede, averaged with what the away side's away halves produce and concede."""
    hf = sum(f for f, _ in hp) / len(hp); ha = sum(a for _, a in hp) / len(hp)
    af = sum(f for f, _ in ap) / len(ap); aa = sum(a for _, a in ap) / len(ap)
    return (hf + aa) / 2 + (af + ha) / 2


SEASON_GAP_DAYS = 35     # 27 Sep: a side whose last game at this venue is older than this is
                         # coming off a summer break - new squad, old window. Kawkab v Hassania
                         # (Botola Pro opener, 87 days) and Union Touarga v FUS Rabat (67/86)
                         # were the only such legs over 26-27 Sep; both lost at Under 1.5.
                         # Same rule the points sports have had since 20 Sep.


def venue_gap(fid, kickoff):
    """Days since the home side's last HOME game and the away side's last AWAY
    game before this kick-off, from the head-to-head feed. The larger of the two."""
    try:
        raw = F2.fetch(f"df_hh_1_{fid}")
    except Exception:
        return None
    gaps = []
    for tab in raw.split('~KA÷')[1:]:
        name = tab.split('¬')[0]
        if not (name.endswith(' - Home') or name.endswith(' - Away')):
            continue
        for blk in tab.split('~KB÷')[1:2]:
            ts = [int(d['KC']) for d in (dict(re.findall(r'([A-Z]{2,3})÷([^¬]*)', r))
                                          for r in re.split(r'~(?=KC÷)', blk))
                  if 'KC' in d and int(d['KC']) < kickoff - 3600]
            if ts:
                gaps.append((kickoff - max(ts)) / 86400)
    return max(gaps) if len(gaps) == 2 else None


def under_market(ev, line=None):
    line = LINE if line is None else line
    for m in (ev.get('markets') or []):
        if str(m.get('id')) != '68' or f"total={line}" not in (m.get('specifier') or ''):
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


def scan(until_h=23, days=0, min_rate=MIN_RATE, verbose=True, line=None, min_model=MIN_MODEL):
    line = LINE if line is None else line
    cap = int(line)          # Under 1.5 allows 1 goal, Under 2.5 allows 2
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
        got = under_market(ev, line)
        if not got:
            continue
        gap = venue_gap(f['id'], int(f['ts']))
        if gap is None or gap > SEASON_GAP_DAYS:
            continue                                 # off-season window, see SEASON_GAP_DAYS
        h, a = D.records_for(f['id'])
        if not h or not a:
            continue
        hp, ap = h.pairs('h1'), a.pairs('h1')
        if len(hp) < MIN_GAMES or len(ap) < MIN_GAMES:
            continue
        tot = [x + y for x, y in hp] + [x + y for x, y in ap]
        hits = sum(1 for v in tot if v <= cap)
        rate = hits / len(tot)
        odds, ids = got
        lam = expected_half(hp, ap)
        model = poisson_under(lam, cap)
        hb = sum(1 for x, y in hp if x + y > cap)      # home halves that broke the line
        ab = sum(1 for x, y in ap if x + y > cap)      # away halves that broke the line
        window = 'clean' if not hb and not ab else 'one-breach' if bool(hb) != bool(ab) else 'both-breach'
        if cap >= 2:
            # 27 Sep, user's call: clean windows first, then one-breach legs to fill.
            # Settled 26-27 Sep: neither side with a 3-goal half 35/36 = 97%, one
            # side with one 29/33 = 88%. Both sides breaching is never taken.
            if window == 'both-breach' or rate < min_rate:
                continue
        else:
            # 1.5: the price cap and the expected-goals bar. A "model must beat the
            # price by two points" test was added on 27 Sep and removed the same
            # day - it cut 23 of the 55 games inside the cap and nothing measured
            # supports it. The measured fact is the price band itself: on the 26
            # Sep board, legs at 1.25 or shorter went 7 of 8, 1.25-1.43 went 10 of
            # 13, 1.43+ went 4 of 9 - and that holds whether or not our number
            # happens to sit above the book's.
            if odds > MAX_PRICE_15 or model < min_model:
                continue
        ts = dt.datetime.fromtimestamp(int(ev['estimateStartTime']) / 1000, tz=A.WAT)
        out.append(dict(ts=ts.timestamp(), when=ts.strftime('%a %H:%M'),
                        match=f"{f['home']} v {f['away']}", lg=f.get('league', ''),
                        odds=odds, rate=rate, lam=lam, model=model, ids=ids, window=window,
                        series=f"{sum(1 for v in [x + y for x, y in hp] if v <= cap)}/{len(hp)}+"
                               f"{sum(1 for v in [x + y for x, y in ap] if v <= cap)}/{len(ap)}"))
    if cap >= 2:
        out.sort(key=lambda r: (r['window'] != 'clean', -r['rate'], r['odds']))
    else:
        # 27 Sep: at 1.5 neither our count nor our model has ordered the results
        # over two days; the book's price has. Inside the 1.40 cap, 26-27 Sep:
        # 1.25 or shorter 10 of 12 (83%), 1.25-1.40 20 of 26 (77%). Shortest first.
        for r in out:
            r['window'] = 'short' if r['odds'] <= SHORT_15 else 'fill'
        out.sort(key=lambda r: (r['odds'], -r['model']))
    return out


SHORT_15 = 1.25          # Under 1.5: priced this or shorter goes on first (see scan)
MIN_LEGS_15 = 30
MAX_LEGS_15 = 50


def pick_15(legs):
    short = [l for l in legs if l['window'] == 'short']
    fill = [l for l in legs if l['window'] == 'fill']
    out = short[:MAX_LEGS_15]
    if len(out) < MIN_LEGS_15:
        out += fill[:MIN_LEGS_15 - len(out)]
    return out


MIN_LEGS_25 = 30         # user's call 27 Sep: 30-50 legs; clean windows first,
MAX_LEGS_25 = 50         # one-breach legs only to bring the slip up to 30


def pick_25(legs):
    clean = [l for l in legs if l['window'] == 'clean']
    fill = [l for l in legs if l['window'] == 'one-breach']
    out = clean[:MAX_LEGS_25]
    if len(out) < MIN_LEGS_25:
        out += fill[:MIN_LEGS_25 - len(out)]
    return out


HIGH_PCT = 0.85          # combined high-% slip: chosen line's model probability at least this
HIGH_MAX = 30
BIG_MIN, BIG_MAX = 40, 50


def combined(until_h=23, days=0):
    """27 Sep, user's call: one slip mixing both lines, where each game gets the
    line that suits it. Both scans run with their own gates (and the season gate);
    a game eligible on both lines takes the one with the better expected value -
    the model's chance of that line landing times its price. A very quiet game
    (expected first-half goals ~0.4) goes Under 1.5 for the longer price; a busier
    one goes Under 2.5 for the cushion.

    Returns (high, big): the high-% slip (chosen line at 85%+ on the model, up to
    30 legs) and the 40-50 leg slip, both ranked by that probability."""
    l15 = {l['ids']['eventId']: l for l in scan(until_h, days, line=1.5, verbose=True)}
    l25 = {l['ids']['eventId']: l for l in scan(until_h, days, line=2.5, verbose=False)}
    chosen = []
    for eid in set(l15) | set(l25):
        a, b = l15.get(eid), l25.get(eid)
        if a and b:
            pick = a if a['model'] * a['odds'] >= b['model'] * b['odds'] else b
        else:
            pick = a or b
        pick = dict(pick, line=1.5 if pick is a else 2.5)
        chosen.append(pick)
    chosen.sort(key=lambda l: (-l['model'], l['odds']))
    high = [l for l in chosen if l['model'] >= HIGH_PCT][:HIGH_MAX]
    big = chosen[:BIG_MAX]
    return high, big


def _book(legs, title):
    combo = 1.0
    for l in legs:
        combo *= l['odds']
    n15 = sum(1 for l in legs if l['line'] == 1.5)
    print(f"\n=== {title}  -  {len(legs)} games, {combo:,.2f}x  ({n15} at Under 1.5, {len(legs) - n15} at Under 2.5)\n")
    for l in legs:
        print(f"   {l['when']}  {l['match'][:34]:34} U{l['line']}  {l['window']:10} exp {l['lam']:.2f}  "
              f"model {l['model']*100:3.0f}%  @{l['odds']:<5} {l['lg'][:20]}")
    if len(legs) < 2:
        print("   >> fewer than two games - nothing booked")
        return
    bk = A.book([l['ids'] for l in legs])
    if bk and bk.get('code'):
        print(f"\n   >> CODE {bk['code']}   {bk['url']}")
        A.log_booking(bk['code'], bk['url'],
                      f"one market: 1st Half Unders, best line per game - {title} - {combo:,.2f}x ({len(legs)} games)",
                      [(l['ts'], l['match'], f"1st Half - Over/Under / Under {l['line']}", l['odds'],
                        [f"first halves under {l['line']}: {l['series']} = {l['rate']*100:.0f}% - {l['window']}",
                         f"expected first-half goals {l['lam']:.2f}, model {l['model']*100:.0f}% v book {1/l['odds']*100:.0f}%"])
                       for l in legs])
    else:
        print(f"   >> booking failed: {bk.get('msg') if bk else 'no selections'}")


def main():
    dry = '--dry' in sys.argv
    until = int(sys.argv[sys.argv.index('--until') + 1]) if '--until' in sys.argv else 23
    days = int(sys.argv[sys.argv.index('--days') + 1]) if '--days' in sys.argv else 0
    mr = float(sys.argv[sys.argv.index('--min-rate') + 1]) if '--min-rate' in sys.argv else MIN_RATE
    mm = float(sys.argv[sys.argv.index('--min-model') + 1]) if '--min-model' in sys.argv else MIN_MODEL
    line = float(sys.argv[sys.argv.index('--line') + 1]) if '--line' in sys.argv else LINE
    if '--combined' in sys.argv:
        high, big = combined(until, days)
        if dry:
            for legs, t in ((high, 'high %'), (big, '40-50')):
                print(f"{t}: {len(legs)} legs")
            return
        _book(high, 'high %')
        if len(big) < BIG_MIN:
            print(f"\n   (only {len(big)} games qualify on either line - the big slip is short of {BIG_MIN})")
        _book(big, '40-50 legs')
        return
    legs = scan(until, days, mr, line=line, min_model=mm)
    legs = pick_25(legs) if line >= 2 else pick_15(legs)
    if len(legs) < 2:
        print(f">> only {len(legs)} games qualify at Under {line} - nothing booked")
        return
    combo = 1.0
    for l in legs:
        combo *= l['odds']
    nclean = sum(1 for l in legs if l.get('window') == 'clean')
    print(f"\n=== 1st Half Under {line}  -  {len(legs)} games, {combo:,.2f}x"
          + (f"  ({nclean} clean, {len(legs) - nclean} one-breach)" if line >= 2 else
             f"  ({sum(1 for l in legs if l.get('window') == 'short')} at 1.25 or shorter, "
             f"{sum(1 for l in legs if l.get('window') == 'fill')} at 1.25-1.40)") + "\n")
    for l in legs:
        print(f"   {l['when']}  {l['match'][:34]:34} {l['window']:10} {l['series']:9} count {l['rate']*100:3.0f}%  "
              f"exp {l['lam']:.2f} goals, model {l['model']*100:3.0f}%  @{l['odds']:<5} {l['lg'][:20]}")
    if dry:
        return
    bk = A.book([l['ids'] for l in legs])
    if bk and bk.get('code'):
        print(f"\n   >> CODE {bk['code']}   {bk['url']}")
        A.log_booking(bk['code'], bk['url'],
                      f"one market: 1st Half - Over/Under / Under {line} - {combo:,.2f}x ({len(legs)} games)",
                      [(l['ts'], l['match'], f"1st Half - Over/Under / Under {line}", l['odds'],
                        [f"first halves under {line}: {l['series']} = {l['rate']*100:.0f}% - {l['window']} window",
                         f"expected first-half goals {l['lam']:.2f}, model {l['model']*100:.0f}% v book {1/l['odds']*100:.0f}%"]) for l in legs])
    else:
        print(f"   >> booking failed: {bk.get('msg') if bk else 'no selections'}")


if __name__ == '__main__':
    main()
