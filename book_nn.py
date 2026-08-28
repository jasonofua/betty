#!/usr/bin/env python3
"""NN booker - the shipped engine with the neural net swapped in as the
probability source for the goal markets it was trained on.

    python3 book_nn.py [--until HH] [--days N] [--target Nx] [--rollover] [--dry]

Everything else is identical to book_dynamic: same board build, same rulebook
(spotless, depth, favourite gates, mismatch flip, agreement band), same target
selection. ONLY model_prob changes, and only for the targets the nets cover:
2H Over 0.5 and 2H Under 2.5. Every other market falls through to the shipped
composite, so a slip's differences are attributable to the net alone.

The nets live in experiments/model_nn_*.pkl (trained by the overnight
pipeline). They are BENCHED in the shipped engine because the composite still
beats them out-of-sample (0.1843 vs 0.1859 on 1,814 unseen matches, 28 Aug);
this file exists to run them live in parallel and settle it with real slips.
"""
import os, pickle, sys
import numpy as np

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import dynamic_v4 as D
import book_dynamic as BD

EXP = os.path.join(ROOT, 'experiments')
NETS = {}
for tgt, fn in (('h2_over05', 'model_nn_2h_over05.pkl'),
                ('h2_under25', 'model_nn_2h_under25.pkl')):
    try:
        with open(os.path.join(EXP, fn), 'rb') as f:
            NETS[tgt] = pickle.load(f)
    except Exception as e:
        print(f"!! could not load {fn}: {e}")
if not NETS:
    sys.exit("no nets available - run the overnight pipeline first")
print(f"NN booker: {len(NETS)} nets loaded ({', '.join(NETS)})")

# The nets were trained on league rates from the same corpus the engine reads.
_LR = D._LR


def _lg_goal_rates():
    v = _LR.get('leagues', {}).get(D.CURRENT_LEAGUE)
    tot = v[0] if v else _LR.get('global_total', 3.1)
    # corpus features used per-side league means; split the total evenly
    return tot / 2, tot / 2


def _features(home_rec, away_rec):
    """The 29 features the nets were trained on, rebuilt from live Records."""
    H, A = home_rec.pairs('goals'), away_rec.pairs('goals')
    if len(H) < 4 or len(A) < 4:
        return None
    hgf = np.array([f for f, _ in H], float); hga = np.array([a for _, a in H], float)
    agf = np.array([f for f, _ in A], float); aga = np.array([a for _, a in A], float)
    w = lambda x: np.average(x, weights=np.linspace(2, 1, len(x)))
    htot, atot = hgf + hga, agf + aga
    lh, la = _lg_goal_rates()
    return np.array([[hgf.mean(), hga.mean(), agf.mean(), aga.mean(),
                      w(hgf), w(hga), w(agf), w(aga),
                      len(hgf), len(agf),
                      hgf.mean() - hga.mean(), agf.mean() - aga.mean(),
                      htot.mean(), atot.mean(), htot.max(), atot.max(),
                      htot.min(), atot.min(), htot.std(), atot.std(),
                      (hgf == 0).mean(), (agf == 0).mean(),
                      (htot <= 1).mean(), (atot <= 1).mean(),
                      (htot >= 4).mean(), (atot >= 4).mean(),
                      abs((hgf.mean() - hga.mean()) - (agf.mean() - aga.mean())),
                      lh, la]])


def _which_net(quantity, side, test):
    """Only the two markets the nets were trained on, identified by predicate
    probe rather than by label - same discipline as the engine's Over probe."""
    if quantity != 'h2' or side != 'match':
        return None
    try:
        if test(0, 0) and not test(9, 9):          # an Under
            return 'h2_under25' if not test(2, 1) and test(1, 1) else None
        if not test(0, 0) and test(9, 9):          # an Over
            return 'h2_over05' if test(1, 0) else None
    except Exception:
        return None
    return None


_composite_prob = D.model_prob
_used = {'nn': 0, 'composite': 0}


def model_prob(home_rec, away_rec, quantity, side, test, grid=16):
    key = _which_net(quantity, side, test)
    if key and key in NETS:
        X = _features(home_rec, away_rec)
        if X is not None:
            try:
                bundle = NETS[key]
                p = float(bundle['net'].predict_proba(bundle['scaler'].transform(X))[0, 1])
                _used['nn'] += 1
                return min(max(p, 0.0), 1.0)
            except Exception:
                pass
    _used['composite'] += 1
    return _composite_prob(home_rec, away_rec, quantity, side, test, grid)


D.model_prob = model_prob        # the swap - everything downstream unchanged

if __name__ == '__main__':
    BD.main()
    print(f"\nprobability sources used: NN {_used['nn']}, composite {_used['composite']}")
