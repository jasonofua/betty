#!/usr/bin/env python3
"""Book from the dynamic evaluator.

  python3 book_dynamic.py [--until HH] [--days N] [--target Nx] [--dry] [--floor X] [--legs N] [--rank 1,2,3]

Slips are split into parts of at most MAX_LEGS (SportyBet's cap); --legs N
makes them smaller, which is usually what you want - a 50-leg accumulator
needs all 50.

Each match contributes up to three options, ranked by how well its own stat
record supports them. Slip 1 takes every match's BEST option, slip 2 the second,
slip 3 the third - so the three slips are independent reads of the same board
rather than three shuffles of one list.
"""
import sys, datetime as dt, collections

sys.path.insert(0, '/Users/apple/Downloads/draw')
import acca as A
import book_v3 as B
import fetcher_v2 as F2
import dynamic_v4 as D


def build(until_h=10, floor=None, verbose=True, days=0):
    """`days` pushes the cutoff that many extra days out - `--until 23 --days 2`
    means 23:00 the day after tomorrow. Without it the window can never exceed
    24 hours, because the cutoff is the next occurrence of that hour."""
    now = dt.datetime.now(A.WAT)
    cutoff = now.replace(hour=until_h, minute=0, second=0, microsecond=0)
    if cutoff <= now:
        cutoff += dt.timedelta(days=1)
    cutoff += dt.timedelta(days=days)
    if verbose:
        print(f"window {now:%a %H:%M} -> {cutoff:%a %d %H:%M} WAT", flush=True)

    evs = [e for e in B.fetch_events_rich()
           if now < dt.datetime.fromtimestamp(int(e.get('estimateStartTime', 0)) / 1000,
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
        picks = D.best_three(ev.get('markets') or [], h, a, **kw)
        if not picks:
            st['nothing supported'] += 1
            continue
        st['with picks'] += 1
        ts = dt.datetime.fromtimestamp(int(ev['estimateStartTime']) / 1000, tz=A.WAT)
        board.append({
            'ts': ts, 'eid': ev['eventId'], 'fx': f, 'rec': h, 'rec_a': a,
            'games': len(h.pairs('goals')), 'picks': picks,
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
               f"reads '{qk}'  book implies {1 / pick['odds']:.0%}")
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
        ev = l['bs']['eventId']
        if per_match[ev] >= MAX_PER_MATCH:
            continue
        per_match[ev] += 1
        out.append(l); combo *= l['odds']; surv *= w
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


def main():
    dry = '--dry' in sys.argv
    until = int(sys.argv[sys.argv.index('--until') + 1]) if '--until' in sys.argv else 10
    floor = float(sys.argv[sys.argv.index('--floor') + 1]) if '--floor' in sys.argv else None
    days = int(sys.argv[sys.argv.index('--days') + 1]) if '--days' in sys.argv else 0
    board = build(until, floor, days=days)
    if not board:
        print("\n>> no supported options on this board")
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
            # Booking a slip that misses the target is not a smaller version of
            # the same bet - it is a different bet with the same risk and a
            # fraction of the payout. The 21:46 run took all 12 available legs
            # and reached 6.2x against a 25x target.
            print(f"\n>> CANNOT REACH {target:g}x on this board — best is "
                  f"{combo:,.1f}x from {len(legs)} legs (pool of {len(pool)}). "
                  f"Nothing booked.\n>> widen the window, or ask for a lower target.")
            return
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
