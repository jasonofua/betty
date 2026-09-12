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


BATCH_S = 120        # legs found within this window go on ONE slip (half-times of a kickoff wave land together)
STOP = {'flag': False}


def book_batch(pending, dry):
    """One code for the batch: a single if one leg, one slip if two or more."""
    if not pending:
        return
    legs = pending[:A.MAX_CODE]
    if dry:
        for l in legs:
            LOG(f"DRY HT {l['sc']} {l['name']}: {l['label']} @{l['p']:.2f}  [{l['why']}]")
        return
    bk = A.book([dict(eventId=l['eid'], productId=1, **l['sel']) for l in legs])
    code = (bk or {}).get('code')
    combo = 1.0
    for l in legs:
        combo *= l['p']
    head = (f"LIVE {'SLIP' if len(legs) > 1 else legs[0]['kind']}  {len(legs)} leg{'s' if len(legs) > 1 else ''}  ~{combo:.2f}x\n"
            if len(legs) > 1 else f"LIVE {legs[0]['kind']}\n")
    body = "\n".join(f"{l['name']}  HT {l['sc']}\n  {l['label']} @{l['p']:.2f}  [{l['why']}]" for l in legs)
    msg = f"{head}{body}\ncode {code}  {(bk or {}).get('url')}"
    LOG(msg)
    if code:
        A.log_booking(code, bk.get('url'), f"LIVE {'slip' if len(legs) > 1 else legs[0]['kind']} at HT ({len(legs)} legs, {combo:.2f}x)",
                      [(l['ets'], l['name'], f"{l['label']} (live, HT {l['sc']})", l['p'], [l['why'], f"1H stats {l['st']}", f"trailing 2H totals {l['t2']}"]) for l in legs])
        if SEND:
            SEND(msg)


def run(until_h=None, dry=False, poll=POLL):
    STOP['flag'] = False
    import threading
    state = dict(gate={}, fx=[f for off in (0, 1) for f in F2.get_fixtures(off)], built=0.0, building=False)

    def build_gate():
        # the draw-gate list needs every fixture's history - an hour or two on a
        # cold server. The 2H rules need none of it, so this runs in the
        # background and the draw rule switches on when it lands.
        state['building'] = True
        try:
            g = LD.gate_events(23, days=0)
            state['gate'] = g; state['built'] = time.time()
            LOG(f"draw gate ready: {len(g)} games today")
            if SEND:
                SEND(f"draw gate ready: {len(g)} gate games today - the half-time Draw is now armed on them")
        except Exception as ex:
            LOG(f"gate build failed: {type(ex).__name__}: {ex}"); state['built'] = time.time()
        finally:
            state['building'] = False
    threading.Thread(target=build_gate, daemon=True).start()
    LOG(f"live HT watcher: polling every {poll}s; {'until ' + str(until_h) + ':00' if until_h else 'until /stop'}; "
        f"{len(state['fx'])} fixtures for joining; 2+ legs in a {BATCH_S}s window go on one slip; draw gate building in the background")
    if SEND:
        SEND("live watcher on. 2H Over 0.5 / Under 1.5 from the trailing second halves on any game, from now. "
             "The half-time Draw on gate games arms itself once the gate list is built (up to an hour). "
             "Codes land here at half-time; two or more together go on one slip. /stop ends it, /livelog shows what it has seen.")
    done, seen = set(), set()
    pending, pending_since = [], None
    end = (dt.datetime.now(tz=A.WAT).replace(hour=until_h, minute=59, second=0, microsecond=0) if until_h
           else dt.datetime.now(tz=A.WAT) + dt.timedelta(days=30))
    while dt.datetime.now(tz=A.WAT) < end and not STOP['flag']:
        gate, fx = state['gate'], state['fx']
        if state['built'] and time.time() - state['built'] > 6 * 3600 and not state['building']:
            state['fx'] = [f for off in (0, 1) for f in F2.get_fixtures(off)]
            threading.Thread(target=build_gate, daemon=True).start()
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
                # 12 Sep evening, user's call: bar dropped from 14/14 (83.6%) to 13/14
                # (80.2%) for volume; the book prices this at 1.20-1.30, so at 13/14
                # the price floor moves to 1.25 to stay at or above fair.
                if o05 >= len(t2) - 1 and len(t2) >= 13:
                    best = same_event(True, 0.5)
                    floor = O05_MIN if o05 == len(t2) else 1.25
                    if best and best[0] >= floor:
                        legs.append(('2H O0.5', best[4], best[0], dict(marketId=best[3], specifier=best[2], outcomeId=best[1]),
                                     f"trailing 2H halves scored {o05}/{len(t2)} ({'83.6' if o05 == len(t2) else '80.2'}%)"))
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
                pending.append(dict(kind=kind, label=label, p=p, sel=sel, why=why, eid=eid, name=name, sc=sc, ets=ets, st=st, t2=t2))
                pending_since = pending_since or time.time()
                LOG(f"queued HT {sc} {name}: {label} @{p:.2f}")
        if pending and time.time() - pending_since >= BATCH_S:
            book_batch(pending, dry); pending, pending_since = [], None
        time.sleep(poll)
    if pending:
        book_batch(pending, dry)
    LOG("live HT watcher stopped" if STOP['flag'] else "live HT watcher finished")
    if SEND:
        SEND("live watcher stopped")


if __name__ == '__main__':
    a = sys.argv
    run(until_h=int(a[a.index('--until') + 1]) if '--until' in a else None, dry='--dry' in a,
        poll=int(a[a.index('--poll') + 1]) if '--poll' in a else POLL)
