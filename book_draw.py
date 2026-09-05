#!/usr/bin/env python3
"""Draw mode - book the draw, only where the measured pattern fires.

Trained on experiments/draw_dataset.jsonl (51,368 corpus matches, every feature
strictly pre-match). Time-ordered 80/20 split; the gate below was selected on
the train half and held on the test half:

    xg < 2.1  and  cd >= 4  and  mismatch <= 0.6  and  btts <= 0.55
        train  1,528 matches  32.3%
        test     215 matches  34.4%
        base draw rate 22.2%   ->  +12 points, fair price 2.91

A logistic model over the full feature set (including trailing sot/corners/
cards/offsides/fouls/saves and half-time history) reached AUC 0.568 and did NOT
beat this gate; xgboost overfit outright (AUC 0.551, top-1% precision 21.6%,
below base). So the gate is the model - four terms, each monotonic on its own.

The features here are built from OVERALL last-7 form, matching how the corpus
rows were built. dynamic_v4's records are venue-filtered, which is a different
distribution, so this module parses its own history rather than reusing them.

    python3 book_draw.py --until 23 [--days N] [--dry] [--margin 0.05]
"""
import sys, datetime as dt, collections
import acca as A
import book_v3 as B
import dynamic_v4 as D
import fetcher_v2 as F2

# gate, measured - see docstring
XG_MAX, CD_MIN, MM_MAX, BTTS_MAX = 2.1, 4, 0.6, 0.55
MEASURED = 0.333            # combined train+test precision of the gate
FAIR = 1.0 / MEASURED       # 3.00
MARGIN = 0.05               # require the book to beat fair by this much
WINDOW = 7


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def form(fixture_id):
    """(home rows, away rows) as [(gf, ga), ...] overall, most recent first."""
    raw = F2.fetch(f"df_hh_1_{fixture_id}")
    if not raw:
        return [], []
    h, a = F2.parse_history(raw)
    return ([(r['gf'], r['ga']) for r in h][:WINDOW],
            [(r['gf'], r['ga']) for r in a][:WINDOW])


def features(hrows, arows):
    if len(hrows) < 5 or len(arows) < 5:
        return None
    h_att, h_def = _mean([g for g, _ in hrows]), _mean([a for _, a in hrows])
    a_att, a_def = _mean([g for g, _ in arows]), _mean([a for _, a in arows])
    if None in (h_att, h_def, a_att, a_def):
        return None
    btts_h = _mean([1.0 if g > 0 and a > 0 else 0.0 for g, a in hrows])
    btts_a = _mean([1.0 if g > 0 and a > 0 else 0.0 for g, a in arows])
    return dict(
        xg=(h_att + a_def) / 2 + (a_att + h_def) / 2,
        mismatch=abs((h_att - h_def) - (a_att - a_def)),
        cd=sum(1 for g, a in hrows if g == a) + sum(1 for g, a in arows if g == a),
        btts=(btts_h + btts_a) / 2,
        h_att=h_att, h_def=h_def, a_att=a_att, a_def=a_def,
    )


def fires(f):
    return (f['xg'] < XG_MAX and f['cd'] >= CD_MIN
            and f['mismatch'] <= MM_MAX and f['btts'] <= BTTS_MAX)


def draw_price(ev):
    """(odds, outcomeId) for the 1X2 Draw, or (None, None)."""
    for m in (ev.get('markets') or []):
        if str(m.get('id')) != '1':
            continue
        for o in (m.get('outcomes') or []):
            if o.get('desc') == 'Draw' and o.get('isActive', 1):
                try:
                    return float(o['odds']), str(o.get('id'))
                except (ValueError, KeyError, TypeError):
                    return None, None
    return None, None


def build(until_h=23, days=0, margin=MARGIN, verbose=True):
    now = dt.datetime.now(A.WAT)
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

    need = FAIR * (1 + margin)
    out, st = [], collections.Counter()
    for ev, f, _s in pairs:
        try:
            h, a = form(f['id'])
        except Exception:
            st['no history'] += 1
            continue
        ft = features(h, a)
        if not ft:
            st['no history'] += 1
            continue
        if not fires(ft):
            st['pattern did not fire'] += 1
            continue
        odds, oid = draw_price(ev)
        if not odds or not oid:
            st['no draw price'] += 1
            continue
        if odds < need:
            st[f'priced under {need:.2f}'] += 1
            continue
        out.append({
            'ts': dt.datetime.fromtimestamp(int(ev['estimateStartTime']) / 1000, tz=A.WAT),
            'match': f"{ev.get('homeTeamName')} v {ev.get('awayTeamName')}",
            'league': f.get('league'), 'odds': odds, 'ft': ft,
            'label': f"1X2 / Draw  [xg {ft['xg']:.2f} cd {ft['cd']} mm {ft['mismatch']:.2f} btts {ft['btts']:.2f}]",
            'stats': [f"xg {ft['xg']:.2f}  mismatch {ft['mismatch']:.2f}  "
                      f"combined draws {ft['cd']}  btts {ft['btts']:.0%}"],
            'bs': dict(eventId=ev['eventId'], productId=3, marketId='1',
                       specifier='', outcomeId=oid),
        })
    if verbose:
        for k, v in st.most_common():
            print(f"   {k}: {v}", flush=True)
        print(f"   DRAW CANDIDATES: {len(out)}", flush=True)
    out.sort(key=lambda x: x['ts'])
    return out


def main():
    until = int(sys.argv[sys.argv.index('--until') + 1]) if '--until' in sys.argv else 23
    days = int(sys.argv[sys.argv.index('--days') + 1]) if '--days' in sys.argv else 0
    margin = float(sys.argv[sys.argv.index('--margin') + 1]) if '--margin' in sys.argv else MARGIN
    dry = '--dry' in sys.argv
    legs = build(until_h=until, days=days, margin=margin)
    if not legs:
        print("\n>> no fixture clears the draw gate and the price floor today")
        return
    combo = 1.0
    for l in legs:
        combo *= l['odds']
    print(f"\n=== DRAW MODE — {len(legs)} legs, combined ~{combo:,.1f}x "
          f"(gate hits {MEASURED:.1%}, fair {FAIR:.2f}, need {FAIR*(1+margin):.2f})")
    for l in legs:
        print(f"   {l['ts']:%a %H:%M}  {l['match'][:40]:<40} @{l['odds']:<6} {l['stats'][0]}")
    if dry:
        print("\n(dry run - nothing booked)")
        return
    bk = A.book([l['bs'] for l in legs])
    if bk and bk.get('code'):
        print(f"\ncode {bk['code']}  {bk['url']}")
        A.log_booking(bk['code'], bk['url'], f"draw mode {len(legs)} legs until {until}:00",
                      [(l['ts'].timestamp(), l['match'], l['label'], l['odds'], l['stats'])
                       for l in legs])
    else:
        print("\nbooking failed")


if __name__ == '__main__':
    main()
