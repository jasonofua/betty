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

    python3 book_draw.py --until 23 [--days N] [--dry] [--half]   (--half = half-time draw, market 60)
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
# 9 Sep: MODEL CUT REMOVED on the user's instruction ("remove the model cut
# and book again"). The gate alone is the selection; the model's p is still
# computed and recorded on every leg. Over five days the cut kept 1 of 6
# gate games (corpus: 2.1 gate games/day -> 0.5 after the cut).
MODEL_CUT = False
MEASURED = _B['test_precision'] if MODEL_CUT else _B.get('rule_precision', _B['test_precision'])
# The price floor prices against the GATE'S OWN RATE over the whole pocket
# (871 matches, 34.6%), not the held-out slices - test_precision is measured
# on 52 rows and rule_precision on 68, far too few to set a price against.
# Our live record agrees with the pocket: 7 of 20 settled draw legs = 35%.
RATE = _B.get('pocket_rate') or 0.346
FAIR = 1.0 / RATE
MARGIN = 0.05
try:
    LG_DRAW = json.load(open(os.path.join(ROOT, 'experiments', 'league_draw_rates.json')))
except OSError:
    LG_DRAW = {}


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def venue_form(fixture_id, kickoff_s):
    """Home side's HOME games and away side's AWAY games as [(gf, ga), ...],
    most recent first, from the history feed's own Home/Away tabs, cut at
    kickoff. This is exactly how accumulate.py builds the corpus rows the
    gate was tuned on. dynamic_v4.records_for was used for this since 6 Sep,
    but its goal series come from the deep list, which MIXES venues - OFK
    Beograd v IMT scored xg 2.50 on the mixed series and 1.86 on the split
    one, and the gate verdict flipped (found 8 Sep)."""
    raw = F2.fetch(f"df_hh_1_{fixture_id}")
    out = {'home': [], 'away': []}
    tab = blk = None
    for sec in F2.sections(raw or ''):
        if 'KA' in sec:
            tab = sec['KA']
        if 'KB' in sec:
            blk = sec['KB']
            continue
        if 'KJ' in sec and 'KK' in sec and blk and tab and 'Head' not in blk:
            try:
                kc = int(sec.get('KC', '0')); hg = int(sec.get('KU', '')); ag = int(sec.get('KT', ''))
            except ValueError:
                continue
            if kickoff_s and kc >= kickoff_s:
                continue
            gf, ga = (hg, ag) if sec.get('KS') == 'home' else (ag, hg)
            if 'Home' in tab and sec.get('KS') == 'home':
                out['home'].append((gf, ga))
            if 'Away' in tab and sec.get('KS') == 'away':
                out['away'].append((gf, ga))
    return out['home'][:7], out['away'][:7]


class _Rec:
    """Minimal record: only the goal series, from venue_form."""
    def __init__(self, pairs):
        self._p = pairs
    def pairs(self, q):
        return self._p if q == 'goals' else []


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
            return _mean((st.get(k) or {}).get('series_for') or [])
        def allowed(k):
            return _mean((st.get(k) or {}).get('series_against') or [])
        for k in ('sot', 'corners', 'yellow', 'offsides', 'fouls', 'saves'):
            f[f'{p}_{k}'] = own(k)             # what this side produces
            f[f'{p}_{k}_ag'] = allowed(k)      # what this side allows
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
    for k, pre in (('sot', 's'), ('corners', 'c')):
        ha, hd, aa, ad = f.get(f'h_{k}'), f.get(f'h_{k}_ag'), f.get(f'a_{k}'), f.get(f'a_{k}_ag')
        if None in (ha, hd, aa, ad):
            f[f'{pre}xg'] = f[f'{pre}mis'] = f[f'{pre}_att_gap'] = f[f'{pre}_def_gap'] = None
        else:
            f[f'{pre}xg'] = (ha + ad) / 2 + (aa + hd) / 2
            f[f'{pre}mis'] = abs((ha - hd) - (aa - ad))
            f[f'{pre}_att_gap'] = abs(ha - aa)
            f[f'{pre}_def_gap'] = abs(hd - ad)
    _lg = re.sub(r'^[A-Z][A-Z \-&.]+:\s*', '', league or '').strip()
    f['lg_draw'] = (LG_DRAW.get(_lg) or LG_DRAW.get(league or '') or {}).get('draw')
    for k in ('sot', 'corners', 'yellow', 'offsides', 'fouls', 'saves',
              'htdraw', 'htgoals', 'shgoals', 'btts'):
        x, y = f.get(f'h_{k}'), f.get(f'a_{k}')
        f[f'sum_{k}'] = (x + y) if x is not None and y is not None else None
        f[f'gap_{k}'] = abs(x - y) if x is not None and y is not None else None
    return f


def _quiet(f):
    """The 7-9 Sep gate: quiet on goals AND quiet/even on SoT, both sides blank
    and blank alike. Corpus 33.7% (n=614), 34.7% since Jun 2026, ~2 games/day."""
    if not (f['xg'] < POCKET['xg_max'] and f['cd'] >= POCKET['cd_min']
            and f['mismatch'] <= POCKET['mm_max']):
        return False
    if f.get('sxg') is None or f.get('smis') is None:
        return False
    # 9 Sep: the evenness cap is a FLOOR, not a tuned parameter. The 8 Sep
    # retrain let the grid drop it (smis 99) to keep volume and Damac v
    # Al-Ula walked through with a 4.0 SoT gap (17 shots to 6, 0:1).
    if f['sxg'] > POCKET['sxg_max'] or f['smis'] > min(POCKET['smis_max'], 2.0):
        return False
    # both sides blank, and blank alike (7 Sep)
    if f['blank'] < POCKET.get('blank_min', 0):
        return False
    if abs(f['h_blank'] - f['a_blank']) > POCKET.get('bgap_max', 99):
        return False
    return True


def home_profile(f):
    """10 Sep: the shape of the draws that WON on 6 Sep. The home side draws
    at home (3+ of 7), concedes little at home (<= 1.0/game), both sides blank
    (5+), in a league that draws (>= 30%). Corpus 33.5% (n=651), 35.2% since
    Jun 2026, ~2.2/day, almost disjoint from _quiet (overlap 81). The union is
    33.4% (n=1,184), every 2026 month 30-39%. Without the league term this
    profile is only 29% - the league rate is what makes it bet-worthy."""
    if f.get('h_def') is None or f.get('lg_draw') is None:
        return False
    # 15 Sep: mismatch capped at 2.0. Petrovac v Tivat came through here with a
    # 2.14-goal venue gap (the quiet branch caps at 1.0) and lost 1-2; on the draw
    # dataset the branch draws 33.7% at mismatch <= 1.0, 32.5% at 1-2, 27.3% above 2 (n 22).
    return (f['h_draws'] >= 3 and f['h_def'] <= 1.0 and f['blank'] >= 5
            and f['lg_draw'] >= 0.30 and (f.get('mismatch') or 0) <= 2.0)


def in_pocket(f):
    if POCKET.get('mode') == 'all':          # no gate - every fixture is scored
        return True
    if POCKET.get('mode') == 'both':         # quiet game  OR  home-side draw profile
        return _quiet(f) or home_profile(f)
    if POCKET.get('mode', 'goals') == 'goals':
        return (f['xg'] < POCKET['xg_max'] and f['cd'] >= POCKET['cd_min']
                and f['mismatch'] <= POCKET['mm_max'])
    if f.get('smis') is None or f.get('sxg') is None:
        return False
    return (f['smis'] <= POCKET['smis_max'] and f['sxg'] <= POCKET['sxg_max']
            and f['cd'] >= POCKET['cd_min'])


def prob(f):
    X = np.full((1, len(FEATS)), np.nan)
    for j, k in enumerate(FEATS):
        v = f.get(k)
        if v is not None:
            X[0, j] = v
    return float(MODEL.predict_proba(X)[0, 1])


# HALF-TIME DRAW (9 Sep). Corpus: inside the gate the HT draw lands 47.9%
# (fair 2.09) vs 40.0% for all matches; with league draw rate >= 34% it is
# 54.2% (fair 1.85). Only 48.7% of gate matches level at HT stay level to
# FT - the gate finds games that stay level for an hour, so bet the hour.
# SportyBet market 60 '1st Half - 1X2', priced 2.00-2.10 on the quiet games.
HALF = False                      # set by --half / API half:true
HT_MEASURED = 0.479
FLOOR = True                      # refuse a draw priced under 1/MEASURED


def draw_price(ev):
    want = '60' if HALF else '1'
    for m in (ev.get('markets') or []):
        if str(m.get('id')) != want:
            continue
        for o in (m.get('outcomes') or []):
            if o.get('desc') == 'Draw' and o.get('isActive', 1):
                try:
                    return float(o['odds']), str(o.get('id'))
                except (ValueError, KeyError, TypeError):
                    return None, None
    return None, None


def build(until_h=23, days=0, margin=MARGIN, verbose=True, lead_h=1.0, floor=None):
    # 14 Sep: the floor is a PARAMETER. live_draw used to flip the module global
    # FLOOR off while it built its gate in the watcher thread, and the 09:20
    # draws run in the same process booked Fenix Pilar at 2.75 under the 2.89 floor.
    use_floor = FLOOR if floor is None else bool(floor)
    now = dt.datetime.now(A.WAT)
    start = now + dt.timedelta(hours=lead_h)      # live watcher passes a negative lead to keep in-play games
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
        if MODEL_CUT:
            print(f"model: {_B['kind']}  p_cut {P_CUT:.3f}  (held-out precision at the cut {MEASURED:.1%}; "
                  f"{'price floor ' + format(FAIR, '.2f') if use_floor else 'no price floor'})", flush=True)
        else:
            print(f"gate only - model cut OFF (gate rate {RATE:.1%}; price floor {FAIR:.2f})" if use_floor else
                  f"gate only - model cut OFF (gate rate {RATE:.1%}; no price floor)", flush=True)

    need = FAIR * (1 + margin)
    out, st = [], collections.Counter()
    for ev, f, _s in pairs:
        try:
            _hp, _ap = venue_form(f['id'], int(ev['estimateStartTime']) / 1000)
            hrec, arec = _Rec(_hp), _Rec(_ap)        # true venue-split, cut at kickoff
            rich = _rich(f['id'])                    # halves + stats (per-team, all games)
        except Exception:
            hrec = arec = rich = None
        ft = features(rich, f.get('league'), hrec, arec) if (rich and hrec and arec) else None
        if not ft:
            st['no history'] += 1; continue
        if not in_pocket(ft):
            st['outside pocket'] += 1; continue
        p = prob(ft)
        if MODEL_CUT and p < P_CUT:
            st['model below cut'] += 1; continue
        odds, oid = draw_price(ev)
        if not odds or not oid:
            st['no draw price'] += 1; continue
        # 11 Sep: a BREAK-EVEN floor, not a selection filter. The 6 Sep floor
        # was fair*(1+margin) and it rejected almost everything, which is what
        # the user threw out. This one refuses only prices that lose money at
        # our OWN measured gate rate: 34.6% -> 2.89. Cumbaya @2.70 (10 Sep,
        # 0:1) was such a bet - wrong even if the pick had landed. 3 of the 11
        # priced legs booked since 6 Sep were below fair. Nothing about which
        # GAME gets picked changes; this only declines a bad number.
        if use_floor and odds < FAIR:
            st[f'price below fair {FAIR:.2f}'] += 1; continue
        out.append({
            'ts': dt.datetime.fromtimestamp(int(ev['estimateStartTime']) / 1000, tz=A.WAT),
            'match': f"{ev.get('homeTeamName')} v {ev.get('awayTeamName')}",
            'league': f.get('league'), 'odds': odds, 'p': p, 'ft': ft,
            'label': f"{'1st Half - 1X2' if HALF else '1X2'} / Draw  [p {p:.2f} xg {ft['xg']:.2f} cd {ft['cd']} mm {ft['mismatch']:.2f}]",
            'stats': [f"model p {p:.2f}  xg {ft['xg']:.2f}  mismatch {ft['mismatch']:.2f}  "
                      f"combined draws {ft['cd']}  btts {(ft.get('sum_btts') or 0)/2:.0%}  "
                      f"2H goals {ft.get('sum_shgoals')}  league draw {ft.get('lg_draw')}  "
                      f"gate {'quiet' if _quiet(ft) else 'home-profile'}"],
            'bs': dict(eventId=ev['eventId'], productId=3, marketId=('60' if HALF else '1'),
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
    global HALF
    if '--half' in sys.argv:
        HALF = True                                  # half-time draw instead of full-time
    legs = build(until_h=until, days=days, margin=margin)
    if not legs:
        print("\n>> no fixture clears the gate today"); return
    print(f"\n=== DRAW MODE — {len(legs)} gate candidates ({'model cut ' + format(P_CUT, '.3f') if MODEL_CUT else 'no model cut'}, {'floor ' + format(FAIR, '.2f') if FLOOR else 'no price floor'})")
    for l in legs:
        print(f"   {l['ts']:%a %H:%M}  {l['match'][:40]:<40} @{l['odds']:<6} {l['stats'][0]}")
    if dry:
        print("\n(dry run - nothing booked)"); return
    # ONE SLIP (user's instruction, 6 Sep) - every candidate on the same code
    combo = 1.0
    for l in legs:
        combo *= l['odds']
    bk = A.book([l['bs'] for l in legs])
    if bk and bk.get('code'):
        print(f"\ncode {bk['code']}  {bk['url']}   ({len(legs)} legs, ~{combo:,.1f}x)")
        A.log_booking(bk['code'], bk['url'], f"{'HT ' if HALF else ''}draw slip {combo:,.1f}x ({len(legs)} legs) until {until}:00",
                      [(l['ts'].timestamp(), l['match'], l['label'], l['odds'], l['stats']) for l in legs])
    else:
        print("\nbooking failed")


if __name__ == '__main__':
    main()
