#!/usr/bin/env python3
"""Book from the dynamic evaluator.

  python3 book_dynamic.py [--until HH] [--days N] [--target Nx] [--rollover] [--dry] [--floor X] [--legs N] [--rank 1,2,3]

Slips are split into parts of at most MAX_LEGS (SportyBet's cap); --legs N
makes them smaller, which is usually what you want - a 50-leg accumulator
needs all 50.

Each match contributes up to three options, ranked by how well its own stat
record supports them. Slip 1 takes every match's BEST option, slip 2 the second,
slip 3 the third - so the three slips are independent reads of the same board
rather than three shuffles of one list.
"""
import sys, datetime as dt, collections

import os as _o; sys.path.insert(0, _o.path.dirname(_o.path.abspath(__file__)))
import acca as A
import book_v3 as B
import fetcher_v2 as F2
import dynamic_v4 as D


def build(until_h=10, floor=None, verbose=True, days=0):
    """`days` pushes the cutoff that many extra days out - `--until 23 --days 2`
    means 23:00 the day after tomorrow. Without it the window can never exceed
    24 hours, because the cutoff is the next occurrence of that hour."""
    now = dt.datetime.now(A.WAT)
    # The window opens an hour out (standing rule, 21 Aug): a build can take
    # an hour on a big board, and a leg that kicks off before the code is
    # played is dead weight - SportyBet drops in-play legs without live
    # coverage and returns them at settlement.
    start = now + dt.timedelta(hours=1)
    cutoff = now.replace(hour=until_h, minute=0, second=0, microsecond=0)
    if cutoff <= now:
        cutoff += dt.timedelta(days=1)
    cutoff += dt.timedelta(days=days)
    if verbose:
        print(f"window {start:%a %H:%M} -> {cutoff:%a %d %H:%M} WAT", flush=True)

    evs = [e for e in B.fetch_events_rich()
           if start < dt.datetime.fromtimestamp(int(e.get('estimateStartTime', 0)) / 1000,
                                                tz=A.WAT) <= cutoff]
    # Flashscore fixture days must cover the whole window or the far end joins to
    # nothing. book_v3 scales this with --days; here it is derived from the cutoff
    # itself, so a late-evening run that rolls the cutoff forward still fetches
    # far enough. Offsets 0..N inclusive, never fewer than three days.
    seen, fx = set(), []
    span = max(2, (cutoff.date() - now.date()).days)
    for off in range(span + 1):
        for f in F2.get_fixtures(off):
            if f['id'] not in seen:
                seen.add(f['id'])
                fx.append(f)

    pairs = D.join(evs, fx,
                   lambda e: dt.datetime.fromtimestamp(int(e['estimateStartTime']) / 1000, tz=A.WAT),
                   lambda f: dt.datetime.fromtimestamp(f['ts'], tz=A.WAT))
    if verbose:
        print(f"sportybet in window {len(evs)}  |  joined to flashscore {len(pairs)}", flush=True)

    board, st = [], collections.Counter()
    for ev, f, _s in pairs:
        h, a = D.records_for(f['id'])
        if not h or not h.quantities() or not a.quantities():
            st['no record'] += 1
            continue
        kw = {'min_odds': floor} if floor else {}
        # team names, so markets written as "CD Real Tomayapo Over/Under" resolve
        # to that team instead of being read as a match total
        kw['teams'] = (ev.get('homeTeamName'), ev.get('awayTeamName'))
        D.set_league(f.get('league'))
        picks = D.best_three(ev.get('markets') or [], h, a, **kw)
        if not picks:
            st['nothing supported'] += 1
            continue
        st['with picks'] += 1
        ts = dt.datetime.fromtimestamp(int(ev['estimateStartTime']) / 1000, tz=A.WAT)
        # The fixture's 1X2 favourite price, logged with every leg: all five
        # second-half blow-ups this week had a strong favourite grinding a
        # 0:0 into a late avalanche, but the losers' prices are unrecoverable
        # once settled - so record them at booking time and set any favourite
        # gate from a measured week, not from a guess.
        fav = None
        for _m in (ev.get('markets') or []):
            if str(_m.get('id')) == '1':
                try:
                    fav = min(float(o['odds']) for o in (_m.get('outcomes') or [])
                              if o.get('desc') in ('Home', 'Away') and o.get('isActive', 1))
                except (ValueError, KeyError, TypeError):
                    pass
                break
        board.append({
            'ts': ts, 'eid': ev['eventId'], 'fx': f, 'rec': h, 'rec_a': a,
            'games': len(h.pairs('goals')), 'picks': picks, 'fav': fav,
        })
    board.sort(key=lambda x: x['ts'])
    if verbose:
        for k, v in st.most_common():
            print(f"   {k}: {v}", flush=True)
    return board


# SportyBet caps a slip; anything past this is silently dropped rather than
# refused - slip 1 asked for 62 legs, the code came back with 60, and the booker
# reported no shortfall. Split into parts instead of losing legs.
MAX_LEGS = 50


def _fmt(series):
    """Compact per-game list: whole numbers stay whole."""
    return "[" + ", ".join(f"{v:g}" for v in series) + "]"


def stat_lines(m, pick):
    """EVERY series behind a pick, for bookings.md.

    The log used to keep one line - "5 games, support 86%" - which is unusable
    later: you cannot re-check a pick, re-grade it by hand, or see what the
    engine was looking at when it chose. This writes the whole record for both
    teams, so a booked slip stays auditable after the caches expire."""
    out = []
    q, per, side = D.parse_market(pick['market'],
                                  set(m['rec'].quantities()) | set(m['rec_a'].quantities()))
    qk = q if per == 'ft' else (per if q == 'goals' else f'{q}_{per}')
    out.append(f"support {pick['rate']:.0%}  tally {pick['tally']}  "
               f"reads '{qk}'  book implies {1 / pick['odds']:.0%}"
               + (f"  1X2 fav @{m['fav']:.2f}" if m.get('fav') else ""))
    out.append(f"DECIDED BY  {qk}:")
    for tag, rec in (('home', m['rec']), ('away', m['rec_a'])):
        pr = rec.pairs(qk)
        if pr:
            out.append(f"   {tag} for {_fmt([f for f, _ in pr])}  "
                       f"against {_fmt([a for _, a in pr])}")
    out.append("FULL RECORD:")
    for tag, rec in (('home', m['rec']), ('away', m['rec_a'])):
        for quant in rec.quantities():
            pr = rec.pairs(quant)
            out.append(f"   {tag} {quant:<16} for {_fmt([f for f, _ in pr])}  "
                       f"against {_fmt([a for _, a in pr])}")
    return out



# ── target-payout selection ──────────────────────────────────────────────────
# Measured on 2,225 settled legs: what a booked price ACTUALLY wins at, and the
# margin the book takes at that price. De-vigged, the book is calibrated to
# within two points at every band, while our Poisson model was 13 points
# optimistic - so the price is the probability estimate, not the model.
#
#   book implied   actual    effective margin
#     >=0.95        94.3%        1.039
#     0.90-0.95     88.3%        1.044
#     0.85-0.90     80.0%        1.094
#     0.80-0.85     76.4%        1.081
#      <0.80        64.1%        1.106
# One leg per fixture unless raised - see pick_for_target.
MAX_PER_MATCH = 1

CALIB = ((0.95, 0.943), (0.90, 0.883), (0.85, 0.800), (0.80, 0.764), (0.0, 0.641))


def true_prob(odds):
    """What a leg at this price really wins at, from the calibration table."""
    implied = 1.0 / odds
    for lo, w in CALIB:
        if implied >= lo:
            return w
    return CALIB[-1][1]


def pick_for_target(legs, target):
    """Shortest slip whose combined odds reach `target`, chosen to maximise the
    chance it lands.

    Maximising P(land) subject to prod(odds) >= T means maximising sum(log w)
    subject to sum(log price) >= log T. That is a knapsack, and the greedy
    ordering is by payout bought per unit of survival spent:

        ratio = log(price) / -log(true_prob)

    Ranking by price alone would buy the longest odds regardless of how much
    survival they cost; ranking by probability alone is what the engine used to
    do, and it needed 114 legs to reach 10x."""
    import math
    scored = []
    for l in legs:
        # Cheap corner legs are excluded from target slips (20 Aug, on request).
        # Klaksvik 1H corners @1.13 killed a 52x slip while contributing 13% of
        # its payout - and Kansas City corners @1.15 did the same to T2ZS81.
        # A corners leg at that price buys almost nothing and still carries the
        # fat-tail risk the Poisson model cannot see (series [8,0,1,1,0,1,3]
        # read as 93%). The match's other markets stay eligible, so the slot
        # falls through to a different option rather than vanishing.
        if l['odds'] < 1.20 and 'corner' in l['label'].lower():
            continue
        # CHEAP GOAL-DEPENDENT LEGS, banned 30 Aug - the corner rule applied to
        # its twin. FT Over 0.5 legs ran 95W-7L across the weekend's codes, and
        # all seven losses were 0-0 draws: Uruguay Montevideo, Pineto, Gamba
        # Osaka, Correcaminos, Yenisey, Gangwon, Odra Opole. At 1.03-1.10 each
        # one multiplies the slip by 3-10% while carrying a ~6% chance of
        # killing it outright - the same bad trade the Klaksvik corner leg made.
        # They were on nearly every slip in bulk, so they were the single
        # biggest source of dead tickets by volume. The match's other markets
        # stay eligible; only this price/market combination is refused.
        if l['odds'] < 1.15 and _is_cheap_goal_leg(l['label']):
            continue
        w = true_prob(l['odds'])
        cost = -math.log(w)
        if cost <= 0 or l['odds'] <= 1.0:
            continue
        scored.append((math.log(l['odds']) / cost, w, l))
    scored.sort(key=lambda x: -x[0])
    # At most MAX_PER_MATCH legs from one fixture. Deduping on market alone put
    # three legs on Internacional v Remo and three on Pachuca v Puebla - six of
    # twelve from three games. Those are not independent: if one side dominates,
    # its corners, bookings and win-both-halves legs fail together, so the
    # survival estimate below (a plain product) would be badly overstated.
    out, combo, surv = [], 1.0, 1.0
    per_match = collections.Counter()
    for _, w, l in scored:
        if combo >= target:
            break
        # SportyBet carries at most MAX_LEGS selections; past that the platform
        # silently keeps the first 50 and drops the rest, so a 59-leg "1030x"
        # is really an unknown 50-leg slip at unknown odds. Stop at the cap and
        # let the caller warn, exactly as it does for an unreachable target.
        if len(out) >= MAX_LEGS:
            break
        ev = l['bs']['eventId']
        if per_match[ev] >= MAX_PER_MATCH:
            continue
        per_match[ev] += 1
        out.append(l); combo *= l['odds']; surv *= w
    return out, combo, surv


def _is_cheap_goal_leg(label):
    """A goal-dependent Over that needs an event to HAPPEN - the fragile class."""
    l = label.lower()
    if any(w in l for w in ('corner', 'booking', 'card', 'offside', 'shot', 'foul', 'save')):
        return False
    # 'Over/Under / Under 5.5' contains the word 'over' in the MARKET name, so
    # match on the outcome side only - the bit after the final slash.
    outcome = l.rsplit('/', 1)[-1].strip()
    return outcome.startswith('over')


def pick_max_odds(legs, cap=None):
    """The biggest multiplier the board can produce inside SportyBet's cap.

    pick_for_target stops as soon as it clears the number, which is right when
    you want a specific payout at the best odds of landing. This is the other
    request: 'the highest odds you can get under 50 games'. The pool is already
    quality-gated by the rulebook, so this simply takes the longest prices in
    it, one per fixture, up to the cap."""
    cap = cap or MAX_LEGS
    ranked = sorted(legs, key=lambda l: -l['odds'])
    out, combo, surv = [], 1.0, 1.0
    per_match = collections.Counter()
    for l in ranked:
        if len(out) >= cap:
            break
        if l['odds'] < 1.20 and 'corner' in l['label'].lower():
            continue
        if l['odds'] < 1.15 and _is_cheap_goal_leg(l['label']):
            continue
        ev = l['bs']['eventId']
        if per_match[ev] >= MAX_PER_MATCH:
            continue
        per_match[ev] += 1
        out.append(l); combo *= l['odds']; surv *= true_prob(l['odds'])
    return out, combo, surv


def slip(board, rank):
    """Every match's option at this rank, as bookable selections."""
    legs, seen_ev = [], set()
    for m in board:
        if len(m['picks']) <= rank:
            continue
        if m['eid'] in seen_ev:
            continue                     # same event twice - SportyBet merges them
        seen_ev.add(m['eid'])
        p = m['picks'][rank]
        legs.append({
            'ts': m['ts'], 'match': f"{m['fx']['home']} v {m['fx']['away']}",
            'league': m['fx']['league'], 'games': m['games'],
            'label': f"{p['market']} / {p['outcome']}  [{p['tally']}]",
            'odds': p['odds'], 'rate': p['rate'], 'stats': stat_lines(m, p),
            'bs': dict(eventId=m['eid'], productId=3, marketId=str(p['mid']),
                       specifier=p['spec'], outcomeId=str(p['oid'])),
        })
    return legs


def pick_rollover(pool, floor=0.30,
                  targets=(10, 8, 6, 5, 4, 3, 2.5, 2, 1.75, 1.5, 1.3)):
    """The rollover standard (26 Aug): floor the ticket by WINNING CHANCE,
    not by multiplier. Walk the target ladder downward and book the biggest
    slip whose estimated landing chance still clears the floor - 6x on a fat
    Saturday, 1.5x on a thin Tuesday. The three rollover tickets that won
    (NFSGA4, LSVNNJ, WJJMKK) were all 1.4-1.8x at 40%+; the one that died
    was forced to 5.5x/10% by a multiplier floor. Size comes from chaining
    days, not from any single ticket."""
    best = (None, 0.0, 0.0)
    for t in targets:
        legs, combo, surv = pick_for_target(pool, t)
        if not legs:
            continue
        if surv >= floor:
            return legs, combo, surv
        if surv > best[2]:
            best = (legs, combo, surv)
    return None, best[1], best[2]


def main():
    dry = '--dry' in sys.argv
    until = int(sys.argv[sys.argv.index('--until') + 1]) if '--until' in sys.argv else 10
    floor = float(sys.argv[sys.argv.index('--floor') + 1]) if '--floor' in sys.argv else None
    days = int(sys.argv[sys.argv.index('--days') + 1]) if '--days' in sys.argv else 0
    board = build(until, floor, days=days)
    if not board:
        print("\n>> no supported options on this board")
        return

    if '--maxodds' in sys.argv:
        pool, seen_ev = [], set()
        for rank in range(D.TOP_N):
            for l in slip(board, rank):
                k = (l['bs']['eventId'], l['bs']['marketId'], l['bs']['specifier'])
                if k in seen_ev:
                    continue
                seen_ev.add(k)
                pool.append(l)
        legs, combo, surv = pick_max_odds(pool)
        if not legs:
            print("\n>> no bookable legs on this board")
            return
        legs.sort(key=lambda l: l['ts'])
        print(f"\n=== MAX ODDS — {len(legs)} legs, combined ~{combo:,.1f}x, "
              f"estimated {surv:.2%} chance of landing   (pool of {len(pool)})")
        for l in legs:
            print(f"   {l['ts']:%a %H:%M}  {l['match'][:38]:<38} {l['games']}g  "
                  f"{true_prob(l['odds']):>4.0%}  {l['label']}  @{l['odds']}")
        if dry:
            return
        bk = A.book([l['bs'] for l in legs])
        if bk and bk.get('code'):
            got = bk.get('verified') or bk['booked']
            print(f"   >> CODE {bk['code']}   {bk['url']}")
            if got != bk['req']:
                print(f"   >> short: booked {got}/{bk['req']}")
            A.log_booking(bk['code'], bk['url'], f"max odds {combo:,.1f}x",
                          [(l['ts'].timestamp(), l['match'], l['label'],
                            l['odds'], l['stats']) for l in legs])
        else:
            print(f"   >> booking failed: {bk.get('msg') if bk else 'no selections'}")
        return

    if '--rollover' in sys.argv:
        pool, seen_ev = [], set()
        for rank in range(D.TOP_N):
            for l in slip(board, rank):
                k = (l['bs']['eventId'], l['bs']['marketId'], l['bs']['specifier'])
                if k in seen_ev:
                    continue
                seen_ev.add(k)
                pool.append(l)
        legs, combo, surv = pick_rollover(pool)
        if not legs:
            print(f"\n>> no rollover today: even the smallest slip only lands "
                  f"{surv:.0%} - the board does not clear the 30% floor")
            return
        legs.sort(key=lambda l: l['ts'])
        print(f"\n=== ROLLOVER — {len(legs)} legs, combined ~{combo:,.2f}x, "
              f"estimated {surv:.0%} chance of landing   (pool of {len(pool)})")
        for l in legs:
            print(f"   {l['ts']:%a %H:%M}  {l['match'][:38]:<38} {l['games']}g  "
                  f"{true_prob(l['odds']):>4.0%}  {l['label']}  @{l['odds']}")
        if dry:
            return
        bk = A.book([l['bs'] for l in legs])
        if bk and bk.get('code'):
            got = bk.get('verified') or bk['booked']
            print(f"   >> CODE {bk['code']}   {bk['url']}")
            if got != bk['req']:
                print(f"   >> short: booked {got}/{bk['req']}")
            A.log_booking(bk['code'], bk['url'],
                          f"rollover {combo:.2f}x est {surv:.0%}",
                          [(l['ts'].timestamp(), l['match'], l['label'],
                            l['odds'], l['stats']) for l in legs])
        else:
            print(f"   >> booking failed: {bk.get('msg') if bk else 'no selections'}")
        return

    if '--target' in sys.argv:
        target = float(sys.argv[sys.argv.index('--target') + 1])
        pool, seen_ev = [], set()
        for rank in range(D.TOP_N):
            for l in slip(board, rank):
                k = (l['bs']['eventId'], l['bs']['marketId'], l['bs']['specifier'])
                if k in seen_ev:
                    continue
                seen_ev.add(k)
                pool.append(l)
        legs, combo, surv = pick_for_target(pool, target)
        if not legs:
            print(f"\n>> nothing on the board can reach {target}x")
            return
        if combo < target:
            # Changed 21 Aug on request: warn loudly, then book the reachable
            # best instead of refusing. The old refuse-outright behaviour cost
            # a usable 235x board because 300x was asked for.
            print(f"\n>> WARNING: cannot reach {target:g}x — booking the best "
                  f"available instead: {combo:,.1f}x from {len(legs)} legs "
                  f"(pool of {len(pool)}).")
        legs.sort(key=lambda l: l['ts'])
        print(f"\n=== TARGET {target:g}x — {len(legs)} legs, combined ~{combo:,.1f}x, "
              f"estimated {surv:.1%} chance of landing   (pool of {len(pool)})")
        for l in legs:
            print(f"   {l['ts']:%a %H:%M}  {l['match'][:38]:<38} {l['games']}g  "
                  f"{true_prob(l['odds']):>4.0%}  {l['label']}  @{l['odds']}")
        if dry:
            return
        bk = A.book([l['bs'] for l in legs])
        if bk and bk.get('code'):
            got = bk.get('verified') or bk['booked']
            extra = "" if got == bk['req'] else f"  (booked {got}/{bk['req']})"
            print(f"   >> CODE {bk['code']}   {bk['url']}{extra}")
            A.log_booking(bk['code'], bk['url'],
                          f"dynamic_v4 target {target:g}x until {until}:00",
                          [(l['ts'].timestamp(), l['match'], l['label'], l['odds'],
                            l['stats']) for l in legs])
        else:
            print(f"   >> booking failed: {bk.get('msg') if bk else 'no selections'}")
        return

    only = None
    if '--rank' in sys.argv:
        only = {int(x) - 1 for x in sys.argv[sys.argv.index('--rank') + 1].split(',')}
    for rank in range(D.TOP_N):
        if only is not None and rank not in only:
            continue
        allpicks = slip(board, rank)
        if not allpicks:
            print(f"\n=== SLIP {rank + 1}: no match had a #{rank + 1} option")
            continue
        size = int(sys.argv[sys.argv.index('--legs') + 1]) if '--legs' in sys.argv else MAX_LEGS
        size = max(1, min(size, MAX_LEGS))
        # ONE code per rank. When the board has more than `size` picks, keep the
        # best-supported `size` of them and drop the rest - do not spill into a
        # second code. Ranked by support rate, ties to the larger sample.
        dropped = max(0, len(allpicks) - size)
        legs = sorted(allpicks, key=lambda l: (-l['rate'], -l['games']))[:size]
        legs.sort(key=lambda l: l['ts'])          # back into kickoff order to read
        combo = 1.0
        for l in legs:
            combo *= l['odds']
        print(f"\n=== SLIP {rank + 1} — each match's #{rank + 1} option — "
              f"{len(legs)} legs, combined ~{combo:,.0f}x"
              + (f"   ({dropped} weakest dropped from {len(allpicks)})" if dropped else ""))
        for l in legs:
            print(f"   {l['ts']:%a %H:%M}  {l['match'][:40]:<40} {l['games']}g  "
                  f"{l['rate']:>4.0%}  {l['label']}  @{l['odds']}")
        if dry:
            continue
        bk = A.book([l['bs'] for l in legs])
        if bk and bk.get('code'):
            # `booked` is the count SportyBet echoes in the POST reply; `verified`
            # is what re-reading the finished code actually returns. They can
            # disagree - R3KM21 echoed 25 and held 23, and comparing only `booked`
            # reported a full slip while Bidhannagar and EL Nacional were gone.
            got = bk.get('verified') or bk['booked']
            extra = "" if got == bk['req'] else f"  (booked {got}/{bk['req']})"
            print(f"   >> CODE {bk['code']}   {bk['url']}{extra}")
            A.log_booking(bk['code'], bk['url'],
                          f"dynamic_v4 slip{rank + 1} until {until}:00" + (f" +{days}d" if days else ""),
                          [(l['ts'].timestamp(), l['match'], l['label'], l['odds'],
                            l['stats']) for l in legs])
        else:
            print(f"   >> booking failed: {bk.get('msg') if bk else 'no selections'}")


if __name__ == '__main__':
    main()
