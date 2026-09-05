#!/usr/bin/env python3
"""Train the draw model on experiments/draw_dataset.jsonl.

Time-ordered split: the model never sees a match older than the ones it is
scored on. Reports precision by threshold, because a draw bet only matters if
precision beats the price - break-even at 3.20 is 31.3%.
"""
import json, os, sys, math, collections
import numpy as np

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, 'draw_dataset.jsonl')
OUT = os.path.join(ROOT, 'draw_model.pkl')

BASE = ['h_att','h_def','a_att','a_def','xg','mismatch','h_gd','a_gd',
        'h_draws','a_draws','cd','h_low','a_low','low','h_blank','a_blank',
        'blank','h_cs','a_cs','cs','lg_draw']
# every stat the corpus actually carries, as trailing team history plus the
# paired sum/gap terms - draws want two SIMILAR, quiet sides. `shots` is
# excluded: 2% coverage and those rows are miscoded.
STATK = ['sot','corners','yellow','offsides','fouls','saves',
         'htdraw','htgoals','shgoals','btts']
FEATS = (BASE
         + [f'{s}_{k}' for k in STATK for s in ('h','a')]
         + [f'{p}_{k}' for k in STATK for p in ('sum','gap')]
         + ['h_cs2','a_cs2'])


def load():
    rows=[json.loads(l) for l in open(DATA, encoding='utf-8')]
    rows.sort(key=lambda r: r['ts'] or 0)
    X=np.full((len(rows), len(FEATS)), np.nan)
    for i,r in enumerate(rows):
        for j,f in enumerate(FEATS):
            v=r.get(f)
            if v is not None: X[i,j]=v
    y=np.array([r['draw'] for r in rows])
    return rows, X, y


def report(name, p, y, prices=(3.0, 3.2, 3.5)):
    order=np.argsort(-p)
    print(f"\n--- {name} ---")
    print(f"test base draw rate: {y.mean():.1%}   n={len(y)}")
    print(f"{'top':>8}{'n':>7}{'precision':>11}{'fair':>8}   verdict @3.20")
    for frac in (0.01,0.02,0.05,0.10,0.20,0.30,0.50):
        k=max(1,int(len(y)*frac))
        sel=order[:k]
        prec=y[sel].mean()
        edge=prec*3.20-1
        print(f"{frac:>7.0%}{k:>7}{prec:>11.1%}{(1/prec if prec else 0):>8.2f}"
              f"   {'+' if edge>0 else ''}{edge:>6.1%} ROI")
    # threshold view
    print(f"{'thresh':>8}{'n':>7}{'precision':>11}   ROI@3.20")
    for t in (0.30,0.33,0.35,0.38,0.40,0.45):
        sel=p>=t
        if sel.sum()<20: continue
        prec=y[sel].mean()
        print(f"{t:>8.2f}{sel.sum():>7}{prec:>11.1%}   {prec*3.20-1:>+7.1%}")


def main():
    rows,X,y=load()
    cut=int(len(y)*0.8)
    Xtr,Xte,ytr,yte=X[:cut],X[cut:],y[:cut],y[cut:]
    print(f"train {len(ytr)} (draw {ytr.mean():.1%})   test {len(yte)} (draw {yte.mean():.1%})")

    from sklearn.impute import SimpleImputer
    from sklearn.preprocessing import StandardScaler
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.metrics import roc_auc_score, brier_score_loss

    lr=make_pipeline(SimpleImputer(strategy='median'), StandardScaler(),
                     LogisticRegression(max_iter=2000, C=0.5))
    lr.fit(Xtr,ytr)
    plr=lr.predict_proba(Xte)[:,1]
    print(f"\nlogistic  AUC {roc_auc_score(yte,plr):.4f}  Brier {brier_score_loss(yte,plr):.4f}")
    report("LOGISTIC", plr, yte)

    best=(lr,'logistic',plr)
    try:
        import xgboost as xgb
        m=xgb.XGBClassifier(n_estimators=400, max_depth=4, learning_rate=0.05,
                            subsample=0.8, colsample_bytree=0.8,
                            eval_metric='logloss', reg_lambda=2.0)
        m.fit(Xtr,ytr)
        pxg=m.predict_proba(Xte)[:,1]
        print(f"\nxgboost   AUC {roc_auc_score(yte,pxg):.4f}  Brier {brier_score_loss(yte,pxg):.4f}")
        report("XGBOOST", pxg, yte)
        imp=sorted(zip(FEATS, m.feature_importances_), key=lambda x:-x[1])[:12]
        print("\ntop features:")
        for f,v in imp: print(f"   {f:<12}{v:.4f}")
        if roc_auc_score(yte,pxg) > roc_auc_score(yte,plr):
            best=(m,'xgboost',pxg)
    except ImportError:
        print("\nxgboost not available - logistic only")

    import pickle
    pickle.dump({'model':best[0],'kind':best[1],'feats':FEATS}, open(OUT,'wb'))
    print(f"\nsaved {best[1]} -> {OUT}")


if __name__=='__main__':
    main()
