#!/usr/bin/env python3
"""Train neural nets on EVERY option family the engine supports.

Goal families (classifiers)         : 2H/1H over-under ladders, win-both, both-halves
Stat families (Poisson-head regressors, NB-priced): corners, cards, SoT, offsides,
                                       fouls, saves - match/home/away/1H where data allows

Features: the 29 goal features plus, when the corpus has them, per-team stat
form (each side's recent for/against averages for that stat). Every net is
scored out-of-time against its baseline; only winners are worth wiring.
"""
import json, math, os, pickle, sys
import numpy as np
from collections import defaultdict
from sklearn.neural_network import MLPClassifier, MLPRegressor
from sklearn.preprocessing import StandardScaler

EXP = os.path.dirname(os.path.abspath(__file__))
rows = [json.loads(l) for l in open(f'{EXP}/dataset.jsonl')]
rows.sort(key=lambda r: r['ts'])
cut = int(len(rows) * 0.7)
print(f'{len(rows)} matches, {sum(1 for r in rows if r.get("st"))} with stat sheets')

# ---- per-team stat form, built strictly from EARLIER matches -----------------
STATS = ('corners', 'yellow', 'sot', 'offsides', 'fouls', 'saves')
team_hist = defaultdict(list)          # (league, teamkey) -> [(ts, {stat: (for, against)})]
# dataset rows have no team names for crawled matches; use the match's own id
# chain instead: stat form is approximated by each side's league rate plus the
# match's own recent goal profile. Where names exist we do the real thing.
names = {}
try:
    for m in (json.loads(l) for l in open(f'{EXP}/matches.jsonl')):
        if m.get('h') and m['h'] != '?':
            names[m['id']] = (m['h'], m['a'])
except FileNotFoundError:
    pass
for r in rows:
    if r['id'] in names and r.get('st'):
        h, a = names[r['id']]
        for team, idx in ((h, 0), (a, 1)):
            team_hist[(r['lg'], team)].append((r['ts'], {k: (v[idx], v[1 - idx])
                                                         for k, v in r['st'].items()
                                                         if k in STATS}))
for k in team_hist:
    team_hist[k].sort()

def stat_form(r, side, stat):
    """That team's average for/against for this stat, earlier games only."""
    if r['id'] not in names:
        return None
    team = names[r['id']][0 if side == 'home' else 1]
    hist = [h for h in team_hist.get((r['lg'], team), []) if h[0] < r['ts'] - 3600]
    vals = [(d[stat][0], d[stat][1]) for _, d in hist[-7:] if stat in d]
    if len(vals) < 3:
        return None
    return float(np.mean([v[0] for v in vals])), float(np.mean([v[1] for v in vals]))

# ---- league rates (train only) ----------------------------------------------
lg = defaultdict(lambda: defaultdict(list))
for r in rows[:cut]:
    L = lg[r['lg']]
    L['tot'].append(r['ft'][0] + r['ft'][1])
    L['h2'].append(r['h2'][0] + r['h2'][1])
    for k, v in (r.get('st') or {}).items():
        if k in STATS:
            L[k].append(v[0] + v[1])
GLOB = {k: float(np.mean([x for L in lg.values() for x in L[k]] or [1.0]))
        for k in ('tot', 'h2') + STATS}
LEAGUES = {n: {k: (float(np.mean(L[k])) if len(L[k]) >= 8 else GLOB[k]) for k in GLOB}
           for n, L in lg.items()}
def lr(r, k): return LEAGUES.get(r['lg'], GLOB)[k]

def base_feats(r):
    hgf, hga, agf, aga = (np.array(r[k], float) for k in ('hgf', 'hga', 'agf', 'aga'))
    w = lambda x: np.average(x, weights=np.linspace(2, 1, len(x)))
    htot, atot = hgf + hga, agf + aga
    return [hgf.mean(), hga.mean(), agf.mean(), aga.mean(),
            w(hgf), w(hga), w(agf), w(aga), len(hgf), len(agf),
            hgf.mean() - hga.mean(), agf.mean() - aga.mean(),
            htot.mean(), atot.mean(), htot.max(), atot.max(), htot.min(), atot.min(),
            htot.std(), atot.std(), (hgf == 0).mean(), (agf == 0).mean(),
            (htot <= 1).mean(), (atot <= 1).mean(), (htot >= 4).mean(), (atot >= 4).mean(),
            abs((hgf.mean() - hga.mean()) - (agf.mean() - aga.mean())),
            lr(r, 'tot'), lr(r, 'h2')]

XB = np.array([base_feats(r) for r in rows])

h1t = np.array([r['h1'][0] + r['h1'][1] for r in rows])
h2t = np.array([r['h2'][0] + r['h2'][1] for r in rows])
ftt = np.array([r['ft'][0] + r['ft'][1] for r in rows])
CLS = {
    '2h_over05': (h2t >= 1), '2h_under15': (h2t <= 1), '2h_under25': (h2t <= 2),
    '2h_under35': (h2t <= 3), '1h_over05': (h1t >= 1), '1h_under15': (h1t <= 1),
    '1h_under25': (h1t <= 2), 'ft_over15': (ftt > 1.5), 'ft_over25': (ftt > 2.5),
    'ft_under25': (ftt < 2.5), 'ft_under35': (ftt < 3.5), 'ft_under45': (ftt < 4.5),
    'home_winboth': np.array([r['h1'][0] > r['h1'][1] and r['h2'][0] > r['h2'][1] for r in rows]),
    'away_winboth': np.array([r['h1'][1] > r['h1'][0] and r['h2'][1] > r['h2'][0] for r in rows]),
    'home_both_halves': np.array([r['h1'][0] > 0 and r['h2'][0] > 0 for r in rows]),
    'away_both_halves': np.array([r['h1'][1] > 0 and r['h2'][1] > 0 for r in rows]),
}

bundle = {'leagues': LEAGUES, 'glob': GLOB, 'nets': {}, 'scalers': {},
          'regressors': {}, 'reg_scalers': {}, 'dispersion': {}, 'eval': {}}
print(f'\n--- CLASSIFIERS (Brier, out-of-time on {len(rows)-cut}) ---')
print(f'{"target":18} {"base":>8} {"NN":>8}  verdict')
for name, yb in CLS.items():
    y = yb.astype(int)
    sc = StandardScaler().fit(XB[:cut])
    net = MLPClassifier(hidden_layer_sizes=(64, 32), alpha=0.5, max_iter=2000,
                        early_stopping=True, random_state=7).fit(sc.transform(XB[:cut]), y[:cut])
    p = net.predict_proba(sc.transform(XB[cut:]))[:, 1]
    bn = float(np.mean((p - y[cut:]) ** 2))
    bb = float(np.mean((np.full(len(y) - cut, y[:cut].mean()) - y[cut:]) ** 2))
    print(f'{name:18} {bb:8.4f} {bn:8.4f}  {"NN better" if bn < bb else "base better"}')
    full_sc = StandardScaler().fit(XB)
    bundle['scalers'][name] = full_sc
    bundle['nets'][name] = MLPClassifier(hidden_layer_sizes=(64, 32), alpha=0.5,
                                         max_iter=2000, early_stopping=True,
                                         random_state=7).fit(full_sc.transform(XB), y)
    bundle['eval'][name] = {'base': bb, 'nn': bn}

# ---- stat regressors, with per-team stat form where available ---------------
def stat_target(stat, side):
    y = np.full(len(rows), np.nan)
    for i, r in enumerate(rows):
        s = (r.get('st') or {}).get(stat)
        if s:
            y[i] = s[0] + s[1] if side == 'match' else s[0 if side == 'home' else 1]
    return y

print(f'\n--- COUNT MODELS (RMSE, out-of-time) ---')
print(f'{"target":18} {"n_tr":>6} {"league":>8} {"NN":>8}  NB alpha')
for stat in STATS:
    for side in ('match', 'home', 'away'):
        name = f'{stat}_{side}'
        y = stat_target(stat, side)
        extra = []
        for r in rows:
            fh = stat_form(r, 'home', stat); fa = stat_form(r, 'away', stat)
            extra.append([fh[0] if fh else np.nan, fh[1] if fh else np.nan,
                          fa[0] if fa else np.nan, fa[1] if fa else np.nan,
                          lr(r, stat)])
        E = np.array(extra)
        have_form = ~np.isnan(E[:, 0]) & ~np.isnan(E[:, 2])
        X = np.hstack([XB, np.nan_to_num(E, nan=0.0),
                       have_form.reshape(-1, 1).astype(float)])
        ok = ~np.isnan(y)
        tr = np.where(ok[:cut])[0]; te = np.where(ok[cut:])[0] + cut
        if len(tr) < 300 or len(te) < 80:
            print(f'{name:18} {len(tr):6d}  - too thin -'); continue
        sc = StandardScaler().fit(X[tr])
        net = MLPRegressor(hidden_layer_sizes=(64, 32), alpha=0.5, max_iter=2000,
                           early_stopping=True, random_state=7).fit(sc.transform(X[tr]), y[tr])
        p = np.clip(net.predict(sc.transform(X[te])), 0.05, None)
        rmse = float(np.sqrt(np.mean((p - y[te]) ** 2)))
        bl = float(np.sqrt(np.mean((np.array([lr(rows[i], stat) if side == 'match'
                                              else lr(rows[i], stat) / 2 for i in te]) - y[te]) ** 2)))
        res = y[tr] - np.clip(net.predict(sc.transform(X[tr])), 0.05, None)
        mu = np.clip(net.predict(sc.transform(X[tr])), 0.05, None)
        alpha = max(0.0, float((np.var(res) - np.mean(mu)) / max(np.mean(mu ** 2), 1e-9)))
        print(f'{name:18} {len(tr):6d} {bl:8.3f} {rmse:8.3f}  {alpha:.3f}'
              f'{"  <-- NN better" if rmse < bl else ""}')
        full_sc = StandardScaler().fit(X[ok])
        bundle['reg_scalers'][name] = full_sc
        bundle['regressors'][name] = MLPRegressor(hidden_layer_sizes=(64, 32), alpha=0.5,
                                                  max_iter=2000, early_stopping=True,
                                                  random_state=7).fit(full_sc.transform(X[ok]), y[ok])
        bundle['dispersion'][name] = alpha
        bundle['eval'][name] = {'league': bl, 'nn': rmse}

with open(f'{EXP}/nn_all_bundle.pkl', 'wb') as f:
    pickle.dump(bundle, f)
print(f'\nsaved nn_all_bundle.pkl: {len(bundle["nets"])} classifiers, '
      f'{len(bundle["regressors"])} count models')
wins = sum(1 for k, v in bundle['eval'].items() if v.get('nn', 9) < v.get('base', v.get('league', 9)))
print(f'targets where the net beats its baseline: {wins}/{len(bundle["eval"])}')
