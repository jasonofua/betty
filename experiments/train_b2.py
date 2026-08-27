#!/usr/bin/env python3
"""Stage B, done properly: rich features, league encoding, recency weights,
hyperparameter search, XGBoost if available. Same out-of-time test."""
import json, math
import numpy as np
rows=[json.loads(l) for l in open('/private/tmp/claude-501/-Users-apple-Downloads-draw/dfc8926f-fbfd-46e0-943c-cd17aa8625bd/scratchpad/dataset.jsonl')]
rows.sort(key=lambda r: r['ts'])
cut=int(len(rows)*0.7)

# league encoding from TRAIN ONLY: avg total goals and 2H-goal rate per league
from collections import defaultdict
lg_tot=defaultdict(list); lg_h2=defaultdict(list)
for r in rows[:cut]:
    lg_tot[r['lg']].append(r['ft'][0]+r['ft'][1])
    lg_h2[r['lg']].append(r['h2'][0]+r['h2'][1])
g_tot=np.mean([r['ft'][0]+r['ft'][1] for r in rows[:cut]])
g_h2=np.mean([r['h2'][0]+r['h2'][1] for r in rows[:cut]])
def lgf(r):
    t=lg_tot.get(r['lg']); h=lg_h2.get(r['lg'])
    return [np.mean(t) if t and len(t)>=8 else g_tot,
            np.mean(h) if h and len(h)>=8 else g_h2]

def feats(r):
    hgf,hga,agf,aga=(np.array(r[k],float) for k in ('hgf','hga','agf','aga'))
    w=lambda x: np.average(x, weights=np.linspace(2,1,len(x)))  # recent games weigh more
    htot,atot=hgf+hga,agf+aga
    f=[hgf.mean(),hga.mean(),agf.mean(),aga.mean(),
       w(hgf),w(hga),w(agf),w(aga),
       len(hgf),len(agf),
       hgf.mean()-hga.mean(),agf.mean()-aga.mean(),
       htot.mean(),atot.mean(),htot.max(),atot.max(),htot.min(),atot.min(),
       htot.std(),atot.std(),
       (hgf==0).mean(),(agf==0).mean(),        # blank rates
       (htot<=1).mean(),(atot<=1).mean(),      # quiet-game rates
       (htot>=4).mean(),(atot>=4).mean(),      # blowup rates
       abs((hgf.mean()-hga.mean())-(agf.mean()-aga.mean()))]
    return f+lgf(r)
X=np.array([feats(r) for r in rows])
h2tot=np.array([r['h2'][0]+r['h2'][1] for r in rows])
targets={'2H Over 0.5':(h2tot>=1).astype(int),
         '2H Under 2.5':(h2tot<=2).astype(int),
         'home wins both halves':np.array([1 if (r['h1'][0]>r['h1'][1] and r['h2'][0]>r['h2'][1]) else 0 for r in rows])}

try:
    from xgboost import XGBClassifier as GB
    def mk(d,lr,ne): return GB(max_depth=d,learning_rate=lr,n_estimators=ne,
        subsample=0.8,colsample_bytree=0.8,reg_lambda=2.0,eval_metric='logloss')
    kind='XGBoost'
except ImportError:
    from sklearn.ensemble import HistGradientBoostingClassifier as GB
    def mk(d,lr,ne): return GB(max_depth=d,learning_rate=lr,max_iter=ne,l2_regularization=2.0,random_state=7)
    kind='HistGBM'
print(f'model: {kind}, {X.shape[1]} features, train {cut} / test {len(rows)-cut} out-of-time\n')

val=int(cut*0.8)   # inner validation split for tuning
mu_h=(X[:,0]+X[:,3])/2; mu_a=(X[:,1]+X[:,2])/2; mu2=(mu_h+mu_a)*0.52
def pcdf(k,m): return sum(math.exp(-m)*m**i/math.factorial(i) for i in range(k+1))
pois={'2H Over 0.5':1-np.exp(-mu2),'2H Under 2.5':np.array([pcdf(2,m) for m in mu2])}
pw=[]
for i in range(len(rows)):
    def pwin(mh,ma): return sum((math.exp(-mh)*mh**f/math.factorial(f))*pcdf(f-1,ma) for f in range(1,9))
    pw.append(min(1,pwin(mu_h[i]*0.48,mu_a[i]*0.48)*pwin(mu_h[i]*0.52,mu_a[i]*0.52)*1.15))
pois['home wins both halves']=np.array(pw)

print(f'{"target":24} {"base":>7} {"poisson":>8} {"tunedML":>8}  verdict')
for tgt,yy in targets.items():
    best=None
    for d in (2,3,4):
        for lr in (0.03,0.06,0.1):
            m=mk(d,lr,300); m.fit(X[:val],yy[:val])
            p=m.predict_proba(X[val:cut])[:,1]
            b=np.mean((p-yy[val:cut])**2)
            if best is None or b<best[0]: best=(b,d,lr)
    _,d,lr=best
    m=mk(d,lr,300); m.fit(X[:cut],yy[:cut])
    p=m.predict_proba(X[cut:])[:,1]
    yte=yy[cut:]
    b_gbm=np.mean((p-yte)**2)
    b_base=np.mean((np.full(len(yte),yy[:cut].mean())-yte)**2)
    b_pois=np.mean((pois[tgt][cut:]-yte)**2)
    win=min(b_base,b_pois,b_gbm)
    v=f'{kind} WINS' if b_gbm==win else ('poisson wins' if b_pois==win else 'BASE RATE wins')
    print(f'{tgt:24} {b_base:7.4f} {b_pois:8.4f} {b_gbm:8.4f}  {v}  (depth {d}, lr {lr})')
