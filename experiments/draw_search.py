#!/usr/bin/env python3
"""The draw search, with the market in the room. 147k matches, 2015/16-2025/26,
closing prices. Every feature is built from PRIOR matches only (time order).
The model predicts the draw residual over the market: P(draw) given the
implied probability AND the team/league/time features. Trained on 2015/16-
2021/22, judged on 2022/23-2025/26 season by season. Two verdicts per cut:
draw rate against what the market implied, and return at the closing price.
Then the same at the MAX price on the board (line shopping)."""
import json, collections, statistics as S, math
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import HistGradientBoostingClassifier

rows = [json.loads(l) for l in open('experiments/odds_history.jsonl')]
rows.sort(key=lambda r: r['ts'])
def pick(r, *ks):
    for k in ks:
        if r.get(k): return r[k]
hist = collections.defaultdict(list)          # (lg, team) -> list of dicts, time order
lg_hist = collections.defaultdict(list)       # lg -> draws (0/1), time order
X, y, meta = [], [], []
def team_feats(lg, team, ts):
    h = [g for g in hist[(lg, team)] if g['ts'] < ts]
    if len(h) < 10: return None
    l10, l30 = h[-10:], h[-30:]
    def rate(gs, f): return sum(f(g) for g in gs) / len(gs)
    f = dict(d10=rate(l10, lambda g: g['d']), d30=rate(l30, lambda g: g['d']),
             gf10=rate(l10, lambda g: g['gf']), ga10=rate(l10, lambda g: g['ga']),
             tot30=rate(l30, lambda g: g['gf'] + g['ga']), low30=rate(l30, lambda g: g['gf'] + g['ga'] <= 2),
             pts10=rate(l10, lambda g: 3 if g['gf'] > g['ga'] else 1 if g['d'] else 0),
             rest=min(30, (ts - h[-1]['ts']) / 86400))
    sot = [g['sot'] for g in l10 if g.get('sot') is not None]; sota = [g['sota'] for g in l10 if g.get('sota') is not None]
    f['sot10'] = S.mean(sot) if len(sot) >= 5 else -1; f['sota10'] = S.mean(sota) if len(sota) >= 5 else -1
    return f
for r in rows:
    od = pick(r, 'pscd', 'psd', 'avgd', 'b365d'); oh = pick(r, 'psch', 'psh', 'avgh', 'b365h'); oa = pick(r, 'psca', 'psa', 'avga', 'b365a')
    d = int(r['hg'] == r['ag'])
    if od and oh and oa and od > 1.5:
        s = 1 / oh + 1 / od + 1 / oa
        imp_d, imp_h, imp_a = (1 / od) / s, (1 / oh) / s, (1 / oa) / s
        fh, fa = team_feats(r['lg'], r['home'], r['ts']), team_feats(r['lg'], r['away'], r['ts'])
        lgd = lg_hist[r['lg']][-400:]
        if fh and fa and len(lgd) >= 100:
            mo = int(r['date'][5:7]); season_pos = len([g for g in hist[(r['lg'], r['home'])] if g['ts'] < r['ts'] and g['season'] == r['season']])
            drift = (r['pscd'] - r['psd']) / r['psd'] if r.get('pscd') and r.get('psd') else 0.0
            maxratio = (r['maxd'] / r['avgd']) if r.get('maxd') and r.get('avgd') else 1.0
            feats = [imp_d, imp_h, imp_a, abs(imp_h - imp_a), math.log(od),
                     fh['d10'], fh['d30'], fa['d10'], fa['d30'], fh['gf10'], fh['ga10'], fa['gf10'], fa['ga10'],
                     fh['tot30'], fa['tot30'], fh['low30'], fa['low30'], fh['pts10'], fa['pts10'], fh['rest'], fa['rest'],
                     fh['sot10'], fh['sota10'], fa['sot10'], fa['sota10'], S.mean(lgd), mo, season_pos, drift, maxratio]
            X.append(feats); y.append(d)
            meta.append(dict(season=r['season'], lg=r['lg'], od=od, imp=imp_d, maxd=r.get('maxd') or od, src=r['src']))
    st = r.get('st') or {}
    hist[(r['lg'], r['home'])].append(dict(ts=r['ts'], season=r['season'], gf=r['hg'], ga=r['ag'], d=d, sot=st.get('hst'), sota=st.get('ast')))
    hist[(r['lg'], r['away'])].append(dict(ts=r['ts'], season=r['season'], gf=r['ag'], ga=r['hg'], d=d, sot=st.get('ast'), sota=st.get('hst')))
    lg_hist[r['lg']].append(d)
X = np.array(X); y = np.array(y)
seasons = np.array([m['season'] for m in meta])
train = np.isin(seasons, ['1516', '1617', '1718', '1819', '1920', '2021', '2122'])
print(f"{len(y)} matches with features; train {train.sum()}, test {(~train).sum()}; base draw {y.mean():.1%}")

def evaluate(name, p):
    print(f"\n== {name} ==")
    for season in ['2223', '2324', '2425', '2526']:
        m = seasons == season
        if m.sum() < 500: continue
        imp = np.array([mm['imp'] for mm in meta])[m]; od = np.array([mm['od'] for mm in meta])[m]; mx = np.array([mm['maxd'] for mm in meta])[m]
        gap = p[m] - imp
        line = f"   {season}: n {m.sum():6}"
        for th in (0.02, 0.04, 0.06):
            sel = gap >= th
            if sel.sum() >= 50:
                ret = (od[sel] * y[m][sel]).sum() / sel.sum() - 1
                retmax = (mx[sel] * y[m][sel]).sum() / sel.sum() - 1
                line += f" | gap>={th:.2f}: n {sel.sum():5} drew {y[m][sel].mean():.1%} (mkt {imp[sel].mean():.1%}) ret {ret:+.1%} @max {retmax:+.1%}"
        print(line)

lr = LogisticRegression(C=0.1, max_iter=2000).fit(X[train], y[train])
evaluate('logistic (market + form + league + time + drift)', lr.predict_proba(X)[:, 1])
gb = HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=300, l2_regularization=1.0).fit(X[train], y[train])
evaluate('gradient boosting', gb.predict_proba(X)[:, 1])
# what the market alone gives: bet the draw whenever implied >= x (no model)
print("\n== market only, test seasons: back the draw when the implied probability is at least ==")
tst = ~train
imp = np.array([m['imp'] for m in meta]); od = np.array([m['od'] for m in meta]); mx = np.array([m['maxd'] for m in meta])
for th in (0.30, 0.32, 0.34):
    sel = tst & (imp >= th)
    print(f"   imp >= {th:.2f}: n {sel.sum():6} drew {y[sel].mean():.1%} ret {(od[sel]*y[sel]).sum()/sel.sum()-1:+.1%} @max {(mx[sel]*y[sel]).sum()/sel.sum()-1:+.1%}")
# drift alone
drift = X[:, 28]
for lab, sel in (('price shortened >= 5%', tst & (drift <= -0.05)), ('price drifted >= 5%', tst & (drift >= 0.05))):
    if sel.sum(): print(f"   {lab:24}: n {sel.sum():6} drew {y[sel].mean():.1%} (mkt {imp[sel].mean():.1%}) ret {(od[sel]*y[sel]).sum()/sel.sum()-1:+.1%}")
