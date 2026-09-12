#!/usr/bin/env python3
"""LIVE half-time watcher - every live game on SportyBet, not just the draw gate.

At the half-time whistle the live state is the score; the corpus says the
1H stats add nothing on top of it for 2H goals. What DOES carry information
is the two sides' trailing second halves. Measured on 26,447 corpus rows
(both teams with >= 5 prior 2H totals, cut in time order):

    2H Over 0.5   trailing 2H halves with a goal 14/14 -> 83.6% (base 78.6%)
                                              12-13/14 -> 80.3%
    2H Under 1.5  trailing 2H halves with <=1  12/14  -> 66.1% (base 54.3%)
                                               11/14  -> 64.7%
    Draw          draw-gate game LEVEL at HT         -> 49.3% (fair 2.03)

Rules booked here, one code per leg, pushed to Telegram as they land:
    DRAW      gate game, level at HT, live Draw >= 2.10
    2H O0.5   14/14 trailing 2H halves scored, live 2H Over 0.5 >= 1.20
              (also FT Over (HT total + 0.5) - same event, whichever is dearer)
    2H U1.5   >= 12/14 trailing 2H halves had <=1, live 2H Under 1.5 >= 1.55

Corners / bookings / shots are NOT offered in-play on the live feed (checked
12 Sep on J-League and NPL games; re-check on a top-league fixture). Every
half-time state is logged to experiments/live_ht_log.jsonl with the live
1H stats so a stats rule can be measured once the outcomes are in.

    python3 live_ht.py [--until 23] [--poll 45] [--dry]
"""
import sys, os, re, json, time, urllib.request, datetime as dt, collections
import acca as A
import fetcher_v3 as F3
import fetcher_v2 as F2
import dynamic_v4 as D
import book_draw as DRW
import live_draw as LD

POLL = 45
SEND = None
LOG = print
LOGFILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'experiments', 'live_ht_log.jsonl')
DRAW_MIN, O05_MIN, U15_MIN = 2.10, 1.20, 1.55


def live_markets(eid):
    return ((LD.get(LD.BASE + f"event?eventId={eid}&productId=1").get('data') or {}).get('markets')) or []


def price(mk, mid, want, spec=None):
    for m in mk:
        if str(m.get('id')) != mid or m.get('status') not in (0, None):
            continue
        if spec is not None and (m.get('specifier') or '') != spec:
            continue
        for o in m.get('outcomes', []):
            if o['desc'] == want and o.get('isActive', 1):
                return float(o['odds']), o['id'], (m.get('specifier') or '')
    return None, None, None


def live_stats(fid):
    """1H stats from the live df_st feed: {stat: (home, away)}"""
    try:
        raw = F3.fetch(f'df_st_1_{fid}', ttl=20)
    except Exception:
        return {}
    out = {}
    sec = raw.split('~SE÷')
    blk = next((s for s in sec if s.startswith('1st Half') or s.startswith('Match')), '')
    for name, h, a in re.findall(r'SG÷([^¬]*)¬SH÷([^¬]*)¬SI÷([^¬]*)', blk):
        key = {'Shots on target': 'sot', 'Total shots': 'shots', 'Corner kicks': 'corners', 'Fouls': 'fouls',
               'Offsides': 'offsides', 'Yellow cards': 'yellow', 'Ball possession': 'poss'}.get(name)
        if key:
            try:
                out[key] = (int(h.rstrip('%')), int(a.rstrip('%')))
            except ValueError:
                pass
    return out


def trailing_2h(rich):
    tot = []
    for side in ('home', 'away'):
        gf = rich.get(f'{side}_2h_gf_series') or []; ga = rich.get(f'{side}_2h_ga_series') or []
        tot += [x + y for x, y in zip(gf[:7], ga[:7])]
    return tot


def run(until_h=23, dry=False, poll=POLL):
    gate = LD.gate_events(until_h)
    fx = [f for off in (0, 1) for f in F2.get_fixtures(off)]
    LOG(f"live HT watcher: {len(gate)} draw-gate games; {len(fx)} flashscore fixtures for joining; polling every {poll}s until {until_h}:00")
    if SEND:
        SEND(f"live HT watcher on. Draw on {len(gate)} gate games; 2H Over 0.5 / Under 1.5 on any game whose trailing second halves say so. Codes land here at half-time.")
    done, seen = set(), set()
    end = dt.datetime.now(tz=A.WAT).replace(hour=until_h, minute=59, second=0, microsecond=0)
    while dt.datetime.now(tz=A.WAT) < end:
        try:
            board = LD.live_board()
        except Exception as ex:
            LOG(f"board error {type(ex).__name__}: {ex}"); time.sleep(poll); continue
        for e in board:
            eid = e.get('eventId')
            if eid in done or not LD.at_half_time(e):
                continue
            name = f"{e.get('homeTeamName')} v {e.get('awayTeamName')}"
            ets = int(e.get('estimateStartTime', 0)) / 1000
            f = next((f for f in fx if abs(f['ts'] - ets) <= 3600 * 2
                      and D._score(e.get('homeTeamName', ''), f['home']) >= D.JOIN_MIN_SCORE
                      and D._score(e.get('awayTeamName', ''), f['away']) >= D.JOIN_MIN_SCORE), None)
            if not f:
                done.add(eid); continue
            done.add(eid)                       # one look per game, at the whistle
            sc = e.get('setScore') or '0:0'
            try:
                h, a = (int(x) for x in sc.split(':'))
            except ValueError:
                continue
            banked = h + a
            rich = DRW._rich(f['id'])
            t2 = trailing_2h(rich) if rich else []
            st = live_stats(f['id'])
            try:
                mk = live_markets(eid)
            except Exception as ex:
                LOG(f"markets error {name}: {ex}"); continue
            legs = []
            # 1) the draw, gate games only
            if eid in gate and h == a:
                p, oid, sp = price(mk, '1', 'Draw')
                if p and p >= DRAW_MIN:
                    legs.append(('DRAW', f"1X2 / Draw", p, dict(marketId='1', specifier=sp, outcomeId=oid), f"gate game level at HT (49.3%)"))
                else:
                    LOG(f"HT {sc} {name}: gate game, Draw {p} - no bet")
            # 2) second-half goals from the trailing second halves
            if len(t2) >= 12:
                o05 = sum(x >= 1 for x in t2); u15 = sum(x <= 1 for x in t2)
                # the same 2H event is sold three ways; lower-tier games only carry the FT
                # ladder at the break, so try 2H O/U, then FT O/U, then Rest of Match
                def same_event(want_over, k):
                    tries = ((('90', f'{"Over" if want_over else "Under"} {k:g}', f'total={k:g}'), f'2nd Half {"Over" if want_over else "Under"} {k:g}'),
                             (('18', f'{"Over" if want_over else "Under"} {banked + k:g}', f'total={banked + k:g}'), f'FT {"Over" if want_over else "Under"} {banked + k:g}'),
                             (('900028', f'{"Over" if want_over else "Under"} {k:g}', f'total={k:g}|score={sc}'), f'Rest of Match {"Over" if want_over else "Under"} {k:g}'))
                    found = []
                    for (mid, want, spec), lab in tries:
                        pp, oo, ss = price(mk, mid, want, spec)
                        if pp:
                            found.append((pp, oo, ss, mid, lab))
                    return max(found, key=lambda x: x[0], default=None)
                if o05 == len(t2):
                    best = same_event(True, 0.5)
                    if best and best[0] >= O05_MIN:
                        legs.append(('2H O0.5', best[4], best[0], dict(marketId=best[3], specifier=best[2], outcomeId=best[1]),
                                     f"trailing 2H halves scored {o05}/{len(t2)} (83.6%)"))
                if u15 >= 12:
                    best = same_event(False, 1.5)
                    if best and best[0] >= U15_MIN:
                        legs.append(('2H U1.5', best[4], best[0], dict(marketId=best[3], specifier=best[2], outcomeId=best[1]),
                                     f"trailing 2H halves <=1 goal {u15}/{len(t2)} (66.1%)"))
            rec = dict(ts=time.time(), event=eid, fixture=f['id'], match=name, league=f.get('league'), ht=sc,
                       stats=st, trailing_2h=t2, legs=[(l[0], l[1], l[2]) for l in legs], gate=eid in gate)
            try:
                with open(LOGFILE, 'a') as fh:
                    fh.write(json.dumps(rec) + '\n')
            except Exception:
                pass
            if not legs:
                LOG(f"HT {sc} {name}: 2H halves {t2} - nothing"); continue
            for kind, label, p, sel, why in legs:
                if dry:
                    LOG(f"DRY HT {sc} {name}: {label} @{p:.2f}  [{why}]"); continue
                bk = A.book([dict(eventId=eid, productId=1, **sel)])
                code = (bk or {}).get('code')
                msg = f"LIVE {kind}  {name}  HT {sc}\n{label} @{p:.2f}  [{why}]\ncode {code}  {(bk or {}).get('url')}"
                LOG(msg)
                if code:
                    A.log_booking(code, bk.get('url'), f"LIVE {kind} at HT {sc} @{p:.2f}",
                                  [(ets, name, f"{label} (live, HT {sc})", p, [why, f"1H stats {st}", f"trailing 2H totals {t2}"])])
                    if SEND:
                        SEND(msg)
        time.sleep(poll)
    LOG("live HT watcher finished")


if __name__ == '__main__':
    a = sys.argv
    run(until_h=int(a[a.index('--until') + 1]) if '--until' in a else 23, dry='--dry' in a,
        poll=int(a[a.index('--poll') + 1]) if '--poll' in a else POLL)
