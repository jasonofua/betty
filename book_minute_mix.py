#!/usr/bin/env python3
"""MINUTE MIX - one leg per game from the whole Minutes tab, picked by the match's own record.

  python3 book_minute_mix.py [--until HH | HH:MM] [--days N] [--first] [--cap 1.08] [--dry]

11 Oct, user: "all this options i want you to be able to mix all of them in a slip so if
the stats show 5 min draw or 10 mins draw wont work you go for another option that wold
work according to stats that way we have more games".

Every game in the window that carries the Minutes tab gets ONE leg, the first of:
  1. Match Result after 5 Minutes, Draw          when the record is 90%+ level at 5'
  2. 10 Minutes - 1X2, Draw (market 105)         when the record is 80%+ level at 10'
  3. among the other Minutes-tab options priced 1.03 to --cap (1.08) whose record is 90%+
     (Total Goals from 1 to X min Under/Over, Match Result after X Minutes), the one with the
     best value: how often options like it won when the record was 90%+ (minute_calib.json,
     10+ games) times its price. Until 12 Oct it took the longest price, which pushed it to
     the longest window: Rosario 1-75 Under 3.5 and St. Louis 1-50 Under 3.5 lost on 11 Oct
Each rung needs 3+ answered record games for EACH side, and a record game with an untimed
goal that could change the answer counts against the option.

The record is the home side's last 7 home games and the away side's last 7 away games
(findings._venue), with every goal minute from Flashscore, read in today's roles.

Backtest, 168 finished minute-tab games from our codes 1-11 Oct (prices = the 11 Oct
board's typical price for each option, records cut at kickoff):
    rung 1, 5-min draw              46/47 on Flashscore minutes, 47/47 as SportyBet settled
    rung 2, 10-min draw (80%+)      15/17
    rung 3, other options <= 1.08   73/77
    every leg                       134/141  95.0%, average price 1.099; 26 games got no leg
The 10-minute draw by its record level, all games: 95%+ 6/7, 90-95% 9/10, 85-90% 13/14,
80-85% 17/18, 70-80% 33/45, under 70% 39/47 - so its floor sits at 80%. It never cleared
90% where the 5-min draw had failed. Raising the cap buys price with misses at about the
book's rate (cap 1.10 133/142, 1.12 130/142, 1.20 124/142 with a 90% 10-min floor).

12 Oct, rung 3 by value instead of longest price, tested where the calibration did not see
the games (each half of 1-11 Oct calibrated on the other half; the 11 Oct night board with
its real prices calibrated on 1-11 Oct):
    longest price (old)     rung 3 87/93  avg price 1.072   11 Oct night 26/28, slip lost
    best value, 1.03+       rung 3 85/88  avg price 1.056   11 Oct night 25/26
Requiring the calibrated rate to beat the price cut a third of the games for no gain.
"""
import sys, re, math, datetime as dt
from concurrent.futures import ThreadPoolExecutor
import os as _o; sys.path.insert(0, _o.path.dirname(_o.path.abspath(__file__)))
import acca as A
import book_v3 as B
import findings as FD
import fetcher_v2 as F2
import fetcher_v3 as F3

MAX_LEGS = 50
FLOOR = 0.90
FLOOR10 = 0.80               # the 10-minute draw (see the docstring)
MIN_SIDE = 3
MIDS = ('105', '900069', '900313')
MIN3 = 1.03                  # rung 3 price floor: below it a leg adds nothing to the slip
CAL_N = 10                   # games an option needs in minute_calib.json before rung 3 uses it
import json as _json
CAL = {(r[0], r[1], r[2]): (r[3], r[4]) for r in _json.load(open(_o.path.join(_o.path.dirname(_o.path.abspath(__file__)), 'minute_calib.json')))['rows']}
D5 = ('900069', 'minute=5', 'Draw')
D10 = ('105', 'from=1|to=10', 'Draw')
LABEL = 'minute mix: 5-min draw, 10-min draw or the Minutes-tab option the record backs'


def sel_name(mid, spec, desc):
    if mid == '105':
        return f"10 Minutes - 1X2 / {desc}"
    x = re.search(r'minute=(\d+)', spec).group(1)
    if mid == '900069':
        return f"Match Result after {x} Minutes / {desc}"
    return f"Total Goals from 1 to {x} min / {desc}"


def settle(goals, ft, ht, mid, spec, desc):
    """True/False for one option on one game's goals [(minute|None, 'h'|'a')], None when an
    untimed goal could change the answer. The half-time score places untimed goals for
    goal lines that run past the break."""
    known = [(m, t) for m, t in goals if m is not None]
    n_all = ft[0] + ft[1]
    if mid in ('105', '900069'):
        if len(known) < n_all:
            return None
        x = int(re.search(r'(?:to|minute)=(\d+)', spec).group(1))
        h = sum(1 for m, t in known if m <= x and t == 'h'); a = sum(1 for m, t in known if m <= x and t == 'a')
        return {'Draw': h == a, 'Home': h > a, 'Away': a > h}[desc]
    x = int(re.search(r'minute=(\d+)', spec).group(1)); line = float(re.search(r'total=([\d.]+)', spec).group(1))
    k = sum(1 for m, t in known if m <= x); unk = n_all - len(known)
    if unk and ht and x >= 45:
        k = max(k, ht[0] + ht[1] + sum(1 for m, t in known if 46 <= m <= x))
        unk = max(n_all - (ht[0] + ht[1]) - sum(1 for m, t in known if m >= 46), 0)
    if desc.startswith('Under'):
        return False if k > line else (True if k + unk < line else None)
    return True if k > line else (False if k + unk < line else None)


def record(fid, ko):
    """The venue windows with every goal minute: [dict(side, ft, ht, goals)] (goals None = no summary)."""
    raw = F2.fetch(f"df_hh_1_{fid}")
    if not raw:
        return None
    hr, ar, _ = F3.parse_history(raw)
    out = []
    for rows, side in ((hr, 'home'), (ar, 'away')):
        for r in FD._venue(rows, side, ko):
            g = dict(side=side, ft=(r['hg'], r['ag']), ht=None, goals=[])
            if r['hg'] + r['ag'] > 0:
                s = F3.parse_match_summary(r['match_id'], r['hg'], r['ag'])
                if s:
                    g['ht'] = (s['ht_home'], s['ht_away']) if s.get('halves_ok') else None
                    g['goals'] = [(x['minute'], x['team'][0]) for x in s['goals']]
                else:
                    g['goals'] = None
            out.append(g)
    return out


def rate(rec, k):
    """(hit rate over every record game, fewest answered games on one side)."""
    res = [(g['side'], None if g['goals'] is None else settle(g['goals'], g['ft'], g['ht'], *k)) for g in rec]
    per = {s: sum(1 for x, v in res if x == s and v is not None) for s in ('home', 'away')}
    return sum(1 for _, v in res if v) / len(res), min(per.values())


def options(ev):
    """Every Minutes-tab outcome on the event: {(mid, spec, desc): (odds, no-margin chance, outcomeId)}."""
    out = {}
    for m in ev.get('markets') or []:
        if str(m.get('id')) not in MIDS or str(m.get('status', '0')) != '0':
            continue
        outs = [o for o in m.get('outcomes') or [] if o.get('isActive', 1) and o.get('odds') and float(o['odds']) > 1]
        if len(outs) < 2:
            continue
        inv = sum(1 / float(o['odds']) for o in outs)
        for o in outs:
            out[(str(m['id']), m.get('specifier') or '', o['desc'])] = (float(o['odds']), (1 / float(o['odds'])) / inv, str(o['id']))
    return out


def cal(k):
    """How often options like k won when the record was 90%+, smoothed: (w+1)/(n+2); None under CAL_N games."""
    w, n = CAL.get(k, (0, 0))
    return (w + 1) / (n + 2) if n >= CAL_N else None


def pick(ev, rec, cap):
    """(key, rung) for the game, or None when no Minutes-tab option clears the floor."""
    opts = options(ev)
    rr = {k: rate(rec, k) for k in opts}
    ok = lambda k, f=FLOOR: rr[k][1] >= MIN_SIDE and rr[k][0] >= f
    if D5 in opts and ok(D5):
        return D5, '5-min draw'
    if D10 in opts and ok(D10, FLOOR10):
        return D10, '10-min draw'
    cand = [k for k in opts if k not in (D5, D10) and MIN3 <= opts[k][0] <= cap and ok(k) and cal(k)]
    if not cand:
        return None
    return max(cand, key=lambda k: (cal(k) * opts[k][0], rr[k][0])), 'other option'


def main():
    arg = lambda k, d: sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d
    dry = '--dry' in sys.argv
    until = arg('--until', '23'); cap = float(arg('--cap', 1.08))
    hh, mm = (int(x) for x in until.split(':')) if ':' in until else (int(until), 59)   # --until 6:00 = kickoffs to 06:00
    now = dt.datetime.now(A.WAT); start = now + dt.timedelta(hours=1)       # standing rule: an hour out
    cut = now.replace(hour=hh, minute=mm, second=59 if mm == 59 else 0, microsecond=0)
    if cut <= now:
        cut += dt.timedelta(days=1)
    days = int(arg('--days', 0))                 # 13 Oct, user: "mix minutes till thursday" - --days 2 runs the cut 2 days on
    cut += dt.timedelta(days=days)
    evs = [e for e in B._fetch_events_for(','.join(MIDS))
           if start < dt.datetime.fromtimestamp(int(e.get('estimateStartTime', 0)) / 1000, tz=A.WAT) <= cut
           and 'SRL' not in (e.get('homeTeamName') or '') and options(e)]
    import dynamic_v4 as D
    fx = FD._fixtures() + [f for off in range(2, days + 2) for f in F2.get_fixtures(off)]
    pairs = D.join(evs, fx, lambda e: dt.datetime.fromtimestamp(int(e['estimateStartTime']) / 1000, tz=A.WAT),
                   lambda f: dt.datetime.fromtimestamp(f['ts'], tz=A.WAT))
    def one(p):
        e, f, _ = p
        try:
            return e, record(f['id'], int(f['ts']))
        except Exception:
            return e, None
    with ThreadPoolExecutor(10) as ex:
        recs = [x for x in ex.map(one, pairs) if x[1]]
    legs, skipped = [], []
    for ev, rec in recs:
        p = pick(ev, rec, cap)
        match = f"{ev['homeTeamName']} v {ev['awayTeamName']}"
        if not p:
            skipped.append(match); continue
        k, rung = p; odds, chance, oid = options(ev)[k]; r, n = rate(rec, k)
        legs.append(dict(ts=int(ev['estimateStartTime']) / 1000, match=match, sel=sel_name(*k), odds=odds, chance=chance, rung=rung,
                         rec=f"record {r:.0%} of {len(rec)} ({n}+ answered a side, floor {FLOOR10 if k == D10 else FLOOR:.0%})"
                             + (f"; options like it won {CAL[k][0]} of {CAL[k][1]} at a 90% record" if rung == 'other option' else ''),
                         ids=dict(eventId=ev['eventId'], productId=3, marketId=k[0], specifier=k[1], outcomeId=oid)))
    legs.sort(key=lambda l: l['ts'])
    print(f"window {start:%a %H:%M} -> {cut:%a %H:%M}  |  {len(evs)} games carry the Minutes tab, {len(recs)} with a record  |  "
          f"{len(legs)} legs: " + ', '.join(f"{sum(1 for l in legs if l['rung'] == r)} {r}" for r in ('5-min draw', '10-min draw', 'other option'))
          + f"  |  no option clears its floor: {len(skipped)}")
    parts = [legs[i:i + MAX_LEGS] for i in range(0, len(legs), MAX_LEGS)]
    if '--first' in sys.argv:                   # 13 Oct, user: "i need 50 codes in 1 slip" - the earliest MAX_LEGS legs, one code
        parts = parts[:1]
    elif len(parts) > 1:
        size = -(-len(legs) // len(parts))
        parts = [legs[i:i + size] for i in range(0, len(legs), size)]
    A.FINDINGS_EXEMPT = True                    # this script applies its own record check, per option
    for i, part in enumerate(parts, 1):
        name = f"part {i}" if len(parts) > 1 else (f'the first {MAX_LEGS} by kickoff' if '--first' in sys.argv else 'every minute game')
        combo = math.prod(l['odds'] for l in part)
        print(f"\n=== {name}: {len(part)} games, {combo:,.2f}x, book chance of every leg landing {math.prod(l['chance'] for l in part):.2%}")
        for l in part:
            print(f"   {dt.datetime.fromtimestamp(l['ts'], tz=A.WAT):%a %H:%M}  {l['match'][:40]:40} {l['sel'][-44:]:44} @{l['odds']:<5} {l['rec']}")
        if dry or len(part) < 2:
            continue
        bk = A.book([l['ids'] for l in part])
        if bk and bk.get('code'):
            print(f"\n   >> code {bk['code']}   {bk['url']}")
            A.log_booking(bk['code'], bk['url'], f"{LABEL} - {name} - {combo:,.2f}x ({len(part)} games)",
                          [(l['ts'], l['match'], l['sel'], l['odds'], [f"book's no-margin chance {l['chance']:.0%} - {l['rung']}",
                                                                       f"match record backs it: {l['rec']}"]) for l in part])
        else:
            print(f"   >> booking failed: {bk.get('msg') if bk else 'no selections'}")


if __name__ == '__main__':
    main()
