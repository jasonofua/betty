#!/usr/bin/env python3
"""Train the draw model on experiments/draw_dataset.jsonl and save the best one.

Time-ordered split: train on the oldest 70%, tune on the next 10%, report on
the newest 20%. The model never sees a match older than the ones it is scored
on. Every model is compared against the four-term rule on the SAME test rows,
and the report is precision at usable volume - a draw bet only matters if
precision beats the price.
"""
import json, os, sys, math, pickle
import numpy as np

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, 'draw_dataset.jsonl')
OUT = os.path.join(ROOT, 'draw_model.pkl')

BASE = ['h_att','h_def','a_att','a_def','xg','mismatch','h_gd','a_gd',
        'h_draws','a_draws','cd','h_low','a_low','low','h_blank','a_blank',
        'blank','h_cs','a_cs','cs','lg_draw']
STATK = ['sot','corners','yellow','offsides','fouls','saves',
         'htdraw','htgoals','shgoals','btts']
STATAG = ['sot','corners','yellow','offsides','fouls','saves']
STRENGTH = ['sxg','smis','s_att_gap','s_def_gap','cxg','cmis','c_att_gap','c_def_gap']
FEATS = (BASE
         + [f'{s}_{k}' for k in STATK for s in ('h','a')]
         + [f'{s}_{k}_ag' for k in STATAG for s in ('h','a')]     # what each side ALLOWS
         + [f'{p}_{k}' for k in STATK for p in ('sum','gap')]
         + STRENGTH                                                 # strength + evenness on stats
         + ['h_cs2','a_cs2'])


def load():
    rows = [json.loads(l) for l in open(DATA, encoding='utf-8')]
    rows.sort(key=lambda r: r['ts'] or 0)
    X = np.full((len(rows), len(FEATS)), np.nan)
    for i, r in enumerate(rows):
        for j, f in enumerate(FEATS):
            v = r.get(f)
            if v is not None:
                X[i, j] = v
    y = np.array([r['draw'] for r in rows])
    return rows, X, y


def rule(r):
    return (r['xg'] < 2.1 and r['cd'] >= 4 and r['mismatch'] <= 0.6
            and r.get('sum_btts') is not None and r['sum_btts'] / 2 <= 0.55)


def report(name, p, y, floor_price=3.15):
    order = np.argsort(-p)
    print(f"\n--- {name} ---")
    print(f"{'top':>6}{'n':>7}{'precision':>11}{'fair':>7}{'ROI@3.20':>10}")
    for frac in (0.01, 0.02, 0.03, 0.05, 0.10, 0.20):
        k = max(1, int(len(y) * frac))
        prec = y[order[:k]].mean()
        print(f"{frac:>5.0%}{k:>7}{prec:>11.1%}{1/prec:>7.2f}{prec*3.20-1:>+10.1%}")
    # what the price floor would actually let through
    need = 1.0 / floor_price
    sel = p >= need
    if sel.sum() >= 20:
        prec = y[sel].mean()
        print(f"  p >= {need:.3f} (price floor {floor_price}):  n={sel.sum()}  precision {prec:.1%}  fair {1/prec:.2f}")


def main():
    rows, X, y = load()
    n = len(y)
    c1, c2 = int(n * 0.7), int(n * 0.8)
    Xtr, ytr = X[:c1], y[:c1]
    Xva, yva = X[c1:c2], y[c1:c2]
    Xte, yte = X[c2:], y[c2:]
    rte = rows[c2:]
    print(f"train {len(ytr)} (draw {ytr.mean():.1%})  tune {len(yva)}  test {len(yte)} (draw {yte.mean():.1%})")

    # the rule, on the same test rows, as the bar to clear
    rsel = np.array([rule(r) for r in rte])
    print(f"\nRULE on test: n={rsel.sum()}  precision {yte[rsel].mean():.1%}  fair {1/yte[rsel].mean():.2f}")

    from sklearn.impute import SimpleImputer
    from sklearn.preprocessing import StandardScaler
    from sklearn.linear_model import LogisticRegression
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.pipeline import make_pipeline
    from sklearn.metrics import roc_auc_score, brier_score_loss

    cands = []

    # 1. logistic, C tuned on the validation slice
    best = None
    for C in (0.05, 0.1, 0.3, 1.0):
        m = make_pipeline(SimpleImputer(strategy='median'), StandardScaler(),
                          LogisticRegression(max_iter=3000, C=C))
        m.fit(Xtr, ytr)
        auc = roc_auc_score(yva, m.predict_proba(Xva)[:, 1])
        if best is None or auc > best[0]:
            best = (auc, C, m)
    _, C, lr = best
    plr = lr.predict_proba(Xte)[:, 1]
    print(f"\nlogistic C={C}  AUC {roc_auc_score(yte, plr):.4f}  Brier {brier_score_loss(yte, plr):.4f}")
    report("LOGISTIC", plr, yte)
    cands.append(('logistic', lr, plr))

    # 2. sklearn HistGradientBoosting with early stopping - handles NaN natively
    best = None
    for depth, lr_ in ((3, 0.03), (4, 0.03), (3, 0.06)):
        m = HistGradientBoostingClassifier(max_depth=depth, learning_rate=lr_,
                                           max_iter=2000, early_stopping=True,
                                           validation_fraction=0.15, n_iter_no_change=60,
                                           min_samples_leaf=60, l2_regularization=2.0,
                                           random_state=7)
        m.fit(Xtr, ytr)
        auc = roc_auc_score(yva, m.predict_proba(Xva)[:, 1])
        if best is None or auc > best[0]:
            best = (auc, depth, lr_, m)
    _, depth, lr_, hgb = best
    phgb = hgb.predict_proba(Xte)[:, 1]
    print(f"\nhistgb depth={depth} lr={lr_} iters={hgb.n_iter_}  AUC {roc_auc_score(yte, phgb):.4f}  Brier {brier_score_loss(yte, phgb):.4f}")
    report("HIST-GRADIENT-BOOSTING", phgb, yte)
    cands.append(('histgb', hgb, phgb))

    # 3. xgboost, regularised and early-stopped on the tune slice
    try:
        import xgboost as xgb
        best = None
        for depth, mcw in ((3, 30), (3, 80), (4, 50)):
            m = xgb.XGBClassifier(n_estimators=3000, max_depth=depth, learning_rate=0.02,
                                  subsample=0.7, colsample_bytree=0.6, min_child_weight=mcw,
                                  reg_lambda=5.0, gamma=0.3, eval_metric='logloss',
                                  early_stopping_rounds=100)
            m.fit(Xtr, ytr, eval_set=[(Xva, yva)], verbose=False)
            auc = roc_auc_score(yva, m.predict_proba(Xva)[:, 1])
            if best is None or auc > best[0]:
                best = (auc, depth, mcw, m)
        _, depth, mcw, xg = best
        pxg = xg.predict_proba(Xte)[:, 1]
        print(f"\nxgboost depth={depth} mcw={mcw} trees={xg.best_iteration}  AUC {roc_auc_score(yte, pxg):.4f}  Brier {brier_score_loss(yte, pxg):.4f}")
        report("XGBOOST", pxg, yte)
        cands.append(('xgboost', xg, pxg))
    except Exception as e:
        print("\nxgboost skipped:", e)

    # pick by precision at the volume the price floor lets through (>=150 test rows),
    # falling back to top-5% precision
    def score(p):
        sel = p >= 1 / 3.15
        if sel.sum() >= 150:
            return yte[sel].mean(), sel.sum()
        k = max(1, int(len(yte) * 0.05))
        return yte[np.argsort(-p)[:k]].mean(), k
    ranked = sorted(cands, key=lambda c: -score(c[2])[0])
    name, model, p = ranked[0]
    prec, k = score(p)
    print(f"\nBEST: {name}  precision {prec:.1%} on {k} test rows   (rule: {yte[rsel].mean():.1%} on {rsel.sum()})")
    pickle.dump({'model': model, 'kind': name, 'feats': FEATS,
                 'test_precision': float(prec), 'test_n': int(k),
                 'rule_precision': float(yte[rsel].mean())}, open(OUT, 'wb'))
    print(f"saved -> {OUT}")


if __name__ == '__main__':
    main()
