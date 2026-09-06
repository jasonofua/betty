#!/usr/bin/env python3
"""Draw mode - the TRAINED model picks, the price floor decides.

The model is experiments/draw_model.pkl, a logistic regression trained on
experiments/draw_dataset.jsonl (51,368 corpus matches, every feature strictly
pre-match: goal form, half-time and second-half history, and trailing shots on
target, corners, cards, offsides, fouls and saves for both sides, plus paired
sum/gap terms and the league's draw rate).

A classifier fitted to the whole corpus lost to a hand-picked rule three times
(draws are close to random across all football, AUC ~0.56). Fitted INSIDE the
draw-prone region instead - the wide pocket xg < 2.4, combined draws >= 3,
mismatch <= 1.0 (7,479 matches, 29.2% draws) - its top decile on held-out rows
hits 36.9% (n=149) where the old four-term rule hit 32.8% (n=305) on the same
rows. That decile is what this module bets: pocket -> model probability at or
above the saved cut -> price at or above fair.

Live features are rebuilt from fetcher_v3's deep history to the same
definitions the corpus builder used. Where a stat is missing live, the
pipeline's median imputer fills it, exactly as in training.

    python3 book_draw.py --until 23 [--days N] [--dry] [--margin 0.05]
"""
import sys, os, re, json, pickle, datetime as dt, collections
import numpy as np
import acca as A
import book_v3 as B
import dynamic_v4 as D
import fetcher_v2 as F2
import fetcher_v3 as F3

ROOT = os.path.dirname(os.path.abspath(__file__))
_B = pickle.load(open(os.path.join(ROOT, 'experiments', 'draw_model.pkl'), 'rb'))
MODEL, FEATS, POCKET, P_CUT = _B['model'], _B['feats'], _B['pocket'], _B['p_cut']
MEASURED = _B['test_precision']          # held-out precision of the bet slice
FAIR = 1.0 / MEASURED
MARGIN = 0.05
try:
    LG_DRAW = json.load(open(os.path.join(ROOT, 'experiments', 'league_draw_rates.json')))
except OSError:
    LG_DRAW = {}


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _rich(fixture_id):
    out = F3.fetch_rich_history(fixture_id)
    for x in (out if isinstance(out, tuple) else (out,)):
        if isinstance(x, dict) and any(k.endswith('_ft_gf_series') for k in x):
            return x
    return None


def features(rich, league, home_rec, away_rec):
    """Every model feature, built the way experiments/build_draw_dataset.py built it.

    GOAL FORM IS VENUE-SPLIT, because the corpus is: accumulate.py builds hgf/hga
    from the home side's HOME games and agf/aga from the away side's AWAY games,
    last 7, cut at kickoff. dynamic_v4.records_for returns exactly those two
    records. The first cut of this file fed the model the deep list's venue-
    MIXED series instead - trained on one distribution, scored on another.
    The trailing stat / half-time block is per-team over all games in both the
    corpus and the deep history, so that part stays on `rich`."""
    f = {}
    side_ok = True
    for s in ('home', 'away'):
        rec = home_rec if s == 'home' else away_rec
        pairs = rec.pairs('goals')[:7] if rec else []
        gf, ga = [x for x, _ in pairs], [y for _, y in pairs]
        if len(pairs) < 5:
            side_ok = False
        p = 'h' if s == 'home' else 'a'
        f[f'{p}_att'], f[f'{p}_def'] = _mean(gf), _mean(ga)
        f[f'{p}_draws'] = sum(1 for x, y in zip(gf, ga) if x == y)
        f[f'{p}_low'] = sum(1 for x, y in zip(gf, ga) if x + y <= 2)
        f[f'{p}_blank'] = sum(1 for x in gf if x == 0)
        f[f'{p}_cs'] = sum(1 for x in ga if x == 0)
        # trailing history block (corpus WIN=10; the deep list carries ~7)
        ht, hta = rich.get(f'{s}_ht_gf_series') or [], rich.get(f'{s}_ht_ga_series') or []
        h2, h2a = rich.get(f'{s}_2h_gf_series') or [], rich.get(f'{s}_2h_ga_series') or []
        st = rich.get(f'{s}_stats') or {}
        def own(k):
            v = (st.get(k) or {}).get('series_for') or []
            return _mean(v)
        f[f'{p}_sot'], f[f'{p}_corners'] = own('sot'), own('corners')
        f[f'{p}_yellow'], f[f'{p}_offsides'] = own('yellow'), own('offsides')
        f[f'{p}_fouls'], f[f'{p}_saves'] = own('fouls'), own('saves')
        f[f'{p}_htdraw'] = _mean([1.0 if x == y else 0.0 for x, y in zip(ht, hta)])
        f[f'{p}_htgoals'] = _mean([x + y for x, y in zip(ht, hta)])
        f[f'{p}_shgoals'] = _mean([x + y for x, y in zip(h2, h2a)])
        f[f'{p}_btts'] = _mean([1.0 if x > 0 and y > 0 else 0.0 for x, y in zip(gf, ga)])
        f[f'{p}_cs2'] = _mean([1.0 if y == 0 else 0.0 for y in ga])
    if not side_ok or None in (f['h_att'], f['h_def'], f['a_att'], f['a_def']):
        return None
    f['xg'] = (f['h_att'] + f['a_def']) / 2 + (f['a_att'] + f['h_def']) / 2
    f['h_gd'], f['a_gd'] = f['h_att'] - f['h_def'], f['a_att'] - f['a_def']
    f['mismatch'] = abs(f['h_gd'] - f['a_gd'])
    f['cd'] = f['h_draws'] + f['a_draws']
    f['low'] = f['h_low'] + f['a_low']
    f['blank'] = f['h_blank'] + f['a_blank']
    f['cs'] = f['h_cs'] + f['a_cs']
    # the corpus carries both 'Gaucho 2' and 'BRAZIL: Gaucho 2'; the table is
    # keyed on the bare name, so strip the country prefix before looking up
    _lg = re.sub(r'^[A-Z][A-Z \-&.]+:\s*', '', league or '').strip()
    f['lg_draw'] = (LG_DRAW.get(_lg) or LG_DRAW.get(league or '') or {}).get('draw')
    for k in ('sot', 'corners', 'yellow', 'offsides', 'fouls', 'saves',
              'htdraw', 'htgoals', 'shgoals', 'btts'):
        x, y = f.get(f'h_{k}'), f.get(f'a_{k}')
        f[f'sum_{k}'] = (x + y) if x is not None and y is not None else None
        f[f'gap_{k}'] = abs(x - y) if x is not None and y is not None else None
    return f


def in_pocket(f):
    return (f['xg'] < POCKET['xg_max'] and f['cd'] >= POCKET['cd_min']
            and f['mismatch'] <= POCKET['mm_max'])


def prob(f):
    X = np.full((1, len(FEATS)), np.nan)
    for j, k in enumerate(FEATS):
        v = f.get(k)
        if v is not None:
            X[0, j] = v
    return float(MODEL.predict_proba(X)[0, 1])


def draw_price(ev):
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
                seen.add(f['id']); fx.append(f)
    pairs = D.join(evs, fx,
                   lambda e: dt.datetime.fromtimestamp(int(e['estimateStartTime']) / 1000, tz=A.WAT),
                   lambda f: dt.datetime.fromtimestamp(f['ts'], tz=A.WAT))
    if verbose:
        print(f"sportybet in window {len(evs)}  |  joined to flashscore {len(pairs)}", flush=True)
        print(f"model: {_B['kind']}  slice precision {MEASURED:.1%}  fair {FAIR:.2f}  "
              f"need >= {FAIR*(1+margin):.2f}  p_cut {P_CUT:.3f}", flush=True)

    need = FAIR * (1 + margin)
    out, st = [], collections.Counter()
    for ev, f, _s in pairs:
        try:
            hrec, arec = D.records_for(f['id'])      # venue-split, as the corpus
            rich = _rich(f['id'])                    # cached by the call above
        except Exception:
            hrec = arec = rich = None
        ft = features(rich, f.get('league'), hrec, arec) if (rich and hrec and arec) else None
        if not ft:
            st['no history'] += 1; continue
        if not in_pocket(ft):
            st['outside pocket'] += 1; continue
        p = prob(ft)
        if p < P_CUT:
            st['model below cut'] += 1; continue
        odds, oid = draw_price(ev)
        if not odds or not oid:
            st['no draw price'] += 1; continue
        if odds < need:
            st[f'priced under {need:.2f}'] += 1; continue
        out.append({
            'ts': dt.datetime.fromtimestamp(int(ev['estimateStartTime']) / 1000, tz=A.WAT),
            'match': f"{ev.get('homeTeamName')} v {ev.get('awayTeamName')}",
            'league': f.get('league'), 'odds': odds, 'p': p, 'ft': ft,
            'label': f"1X2 / Draw  [p {p:.2f} xg {ft['xg']:.2f} cd {ft['cd']} mm {ft['mismatch']:.2f}]",
            'stats': [f"model p {p:.2f}  xg {ft['xg']:.2f}  mismatch {ft['mismatch']:.2f}  "
                      f"combined draws {ft['cd']}  btts {(ft.get('sum_btts') or 0)/2:.0%}  "
                      f"2H goals {ft.get('sum_shgoals')}  league draw {ft.get('lg_draw')}"],
            'bs': dict(eventId=ev['eventId'], productId=3, marketId='1',
                       specifier='', outcomeId=oid),
        })
    if verbose:
        for k, v in st.most_common():
            print(f"   {k}: {v}", flush=True)
        print(f"   DRAW CANDIDATES: {len(out)}", flush=True)
    out.sort(key=lambda x: -x['p'])
    return out


def main():
    until = int(sys.argv[sys.argv.index('--until') + 1]) if '--until' in sys.argv else 23
    days = int(sys.argv[sys.argv.index('--days') + 1]) if '--days' in sys.argv else 0
    margin = float(sys.argv[sys.argv.index('--margin') + 1]) if '--margin' in sys.argv else MARGIN
    dry = '--dry' in sys.argv
    legs = build(until_h=until, days=days, margin=margin)
    if not legs:
        print("\n>> no fixture clears the model cut and the price floor today"); return
    print(f"\n=== DRAW MODE — {len(legs)} candidates (slice hits {MEASURED:.1%}, fair {FAIR:.2f}, need {FAIR*(1+margin):.2f})")
    for l in legs:
        print(f"   {l['ts']:%a %H:%M}  {l['match'][:40]:<40} @{l['odds']:<6} {l['stats'][0]}")
    if dry:
        print("\n(dry run - nothing booked)"); return
    # a 37% instrument is a SINGLES instrument: one code per fixture
    for l in legs:
        bk = A.book([l['bs']])
        if bk and bk.get('code'):
            print(f"\n{l['match'][:40]} @{l['odds']}  ->  code {bk['code']}  {bk['url']}")
            A.log_booking(bk['code'], bk['url'], f"draw model single @{l['odds']} until {until}:00",
                          [(l['ts'].timestamp(), l['match'], l['label'], l['odds'], l['stats'])])
        else:
            print(f"\n{l['match'][:40]}  booking failed")


if __name__ == '__main__':
    main()
