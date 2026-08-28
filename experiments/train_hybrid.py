#!/usr/bin/env python3
"""Train the HYBRID bundle: NN for goal markets, XGBoost for stat markets.

Measured on the 19.5k-match corpus (28 Aug):
  goal markets - the nets beat baseline on 12 of 16 (FT/1H totals strongest)
  stat markets - XGBoost beats the league average on 11 of 17, and every win
                 is a TEAM-level market (corners/SoT/cards/fouls/saves home &
                 away). Match totals stay with the league rate, which beats
                 both learners.

Writes experiments/hybrid_bundle.pkl - consumed by book_hybrid.py.
"""
import json, os, pickle, sys
import numpy as np
from collections import defaultdict
from sklearn.neural_network import MLPClassifier
from sklearn.preprocessing import StandardScaler
from xgboost import XGBRegressor

EXP = os.path.dirname(os.path.abspath(__file__))
rows = [json.loads(l) for l in open(f'{EXP}/dataset.jsonl')]
rows.sort(key=lambda r: r['ts'])
cut = int(len(rows) * 0.7)
STATS = ('corners', 'yellow', 'sot', 'offsides', 'fouls', 'saves')
print(f'{len(rows)} matches, {sum(1 for r in rows if r.get("st"))} sheeted')

# ---------- league rates (train slice only) ----------
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
def lrate(r, k): return LEAGUES.get(r['lg'], GLOB)[k]

# ---------- per-team stat history (chronological, leak-free) ----------
hist = defaultdict(list)
for r in rows:
    if r.get('st') and r.get('h'):
        for team, idx in ((r['h'], 0), (r['a'], 1)):
            hist[(r['lg'], team)].append(
                (r['ts'], {k: (v[idx], v[1 - idx]) for k, v in r['st'].items() if k in STATS}))
for k in hist:
    hist[k].sort(key=lambda x: x[0])

def form(r, side, stat):
    team = r['h'] if side == 'home' else r['a']
    prior = [d for t, d in hist.get((r['lg'], team), []) if t < r['ts'] - 3600]
    vals = [(d[stat][0], d[stat][1]) for d in prior[-7:] if stat in d]
    if len(vals) < 3:
        return None
    return float(np.mean([v[0] for v in vals])), float(np.mean([v[1] for v in vals])), len(vals)

def goal_feats(r):
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
            lrate(r, 'tot'), lrate(r, 'h2')]

XG = np.array([goal_feats(r) for r in rows])
h1t = np.array([r['h1'][0] + r['h1'][1] for r in rows])
h2t = np.array([r['h2'][0] + r['h2'][1] for r in rows])
ftt = np.array([r['ft'][0] + r['ft'][1] for r in rows])
CLS = {'2h_over05': (h2t >= 1), '2h_under15': (h2t <= 1), '2h_under25': (h2t <= 2),
       '2h_under35': (h2t <= 3), '1h_over05': (h1t >= 1), '1h_under15': (h1t <= 1),
       '1h_under25': (h1t <= 2), 'ft_over15': (ftt > 1.5), 'ft_over25': (ftt > 2.5),
       'ft_under25': (ftt < 2.5), 'ft_under35': (ftt < 3.5), 'ft_under45': (ftt < 4.5),
       'home_both_halves': np.array([r['h1'][0] > 0 and r['h2'][0] > 0 for r in rows]),
       'away_both_halves': np.array([r['h1'][1] > 0 and r['h2'][1] > 0 for r in rows])}
# win_both nets are EXCLUDED: they over-fit rare compound events and lost to
# base on the 28 Aug retrain (home .1404 vs .1384, away .1300 vs .1008).

bundle = {'goal_nets': {}, 'goal_scalers': {}, 'stat_models': {}, 'stat_disp': {},
          'leagues': LEAGUES, 'glob': GLOB, 'eval': {}}

print(f'\n--- GOAL MARKETS: neural nets (Brier, out-of-time) ---')
print(f'{"target":18} {"base":>8} {"NN":>8}  keep?')
for name, yb in CLS.items():
    y = yb.astype(int)
    sc = StandardScaler().fit(XG[:cut])
    net = MLPClassifier(hidden_layer_sizes=(64, 32), alpha=0.5, max_iter=2000,
                        early_stopping=True, random_state=7).fit(sc.transform(XG[:cut]), y[:cut])
    p = net.predict_proba(sc.transform(XG[cut:]))[:, 1]
    bn = float(np.mean((p - y[cut:]) ** 2))
    bb = float(np.mean((np.full(len(y) - cut, y[:cut].mean()) - y[cut:]) ** 2))
    keep = bn < bb
    print(f'{name:18} {bb:8.4f} {bn:8.4f}  {"KEEP" if keep else "drop"}')
    bundle['eval'][name] = {'base': bb, 'model': bn, 'kept': keep}
    if keep:
        fsc = StandardScaler().fit(XG)
        bundle['goal_scalers'][name] = fsc
        bundle['goal_nets'][name] = MLPClassifier(hidden_layer_sizes=(64, 32), alpha=0.5,
                                                  max_iter=2000, early_stopping=True,
                                                  random_state=7).fit(fsc.transform(XG), y)

print(f'\n--- STAT MARKETS: XGBoost (RMSE, out-of-time) ---')
print(f'{"target":18} {"n":>6} {"league":>8} {"XGB":>8}  keep?')
for stat in STATS:
    for side in ('home', 'away', 'match'):     # match kept for measurement only
        X, y = [], []
        for r in rows:
            s = (r.get('st') or {}).get(stat)
            if not s or not r.get('h'):
                continue
            fh = form(r, 'home', stat); fa = form(r, 'away', stat)
            if not fh or not fa:
                continue
            hgf, hga, agf, aga = (np.array(r[k], float) for k in ('hgf', 'hga', 'agf', 'aga'))
            X.append([fh[0], fh[1], fa[0], fa[1], fh[2], fa[2], lrate(r, stat),
                      hgf.mean(), hga.mean(), agf.mean(), aga.mean(),
                      (hgf + hga).mean(), (agf + aga).mean(),
                      abs((hgf.mean() - hga.mean()) - (agf.mean() - aga.mean()))])
            y.append(s[0] + s[1] if side == 'match' else s[0 if side == 'home' else 1])
        if len(y) < 400:
            print(f'{stat}_{side:12} {len(y):6d}  too thin'); continue
        X = np.array(X); y = np.array(y); c = int(len(y) * 0.7)
        base = np.array([X[i, 6] if side == 'match' else X[i, 6] / 2 for i in range(c, len(y))])
        b_lg = float(np.sqrt(np.mean((base - y[c:]) ** 2)))
        m = XGBRegressor(max_depth=3, learning_rate=0.05, n_estimators=300, subsample=0.8,
                         colsample_bytree=0.8, reg_lambda=2.0,
                         objective='count:poisson').fit(X[:c], y[:c])
        pred = np.clip(m.predict(X[c:]), 0.05, None)
        b_x = float(np.sqrt(np.mean((pred - y[c:]) ** 2)))
        keep = b_x < b_lg
        name = f'{stat}_{side}'
        print(f'{name:18} {len(y):6d} {b_lg:8.3f} {b_x:8.3f}  {"KEEP" if keep else "drop"}')
        bundle['eval'][name] = {'league': b_lg, 'model': b_x, 'kept': keep}
        if keep:
            full = XGBRegressor(max_depth=3, learning_rate=0.05, n_estimators=300,
                                subsample=0.8, colsample_bytree=0.8, reg_lambda=2.0,
                                objective='count:poisson').fit(X, y)
            tr_pred = np.clip(full.predict(X), 0.05, None)
            res = y - tr_pred
            alpha = max(0.0, float((np.var(res) - np.mean(tr_pred)) / max(np.mean(tr_pred ** 2), 1e-9)))
            bundle['stat_models'][name] = full
            bundle['stat_disp'][name] = alpha

with open(f'{EXP}/hybrid_bundle.pkl', 'wb') as f:
    pickle.dump(bundle, f)
print(f'\nsaved hybrid_bundle.pkl: {len(bundle["goal_nets"])} goal nets, '
      f'{len(bundle["stat_models"])} stat models (only those that beat their baseline)')
