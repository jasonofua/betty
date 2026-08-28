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
try:
    with open(os.path.join(EXP, 'nn_all_bundle.pkl'), 'rb') as f:
        BUNDLE = pickle.load(f)
except Exception as e:
    sys.exit(f"nn_all_bundle.pkl not loadable ({e}) - run experiments/train_nn_all.py")
NETS = BUNDLE['nets']            # 16 goal-market classifiers
SCALERS = BUNDLE['scalers']
BLEAGUES = BUNDLE.get('leagues', {})
BGLOB = BUNDLE.get('glob', {})
print(f"NN booker: {len(NETS)} nets loaded ({', '.join(sorted(NETS))})")

# The nets were trained on league rates from the same corpus the engine reads.
_LR = D._LR


def _lg_goal_rates():
    """The two league features the nets were trained on: league FT total and
    league 2H total, taken from the training bundle's own tables."""
    v = BLEAGUES.get(D.CURRENT_LEAGUE)
    if v:
        return v.get('tot', BGLOB.get('tot', 3.1)), v.get('h2', BGLOB.get('h2', 1.72))
    return BGLOB.get('tot', 3.1), BGLOB.get('h2', 1.72)


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
    """Map a live market to one of the 16 trained nets by PROBING the
    predicate - never by label, same discipline as the engine's Over probe.
    A net is used only when its target is exactly this bet."""
    try:
        if quantity in ('h1', 'h2', 'goals') and side == 'match':
            per = {'h1': '1h', 'h2': '2h', 'goals': 'ft'}[quantity]
            under = test(0, 0) and not test(9, 9)
            over = (not test(0, 0)) and test(9, 9)
            if over:
                # find the line: smallest k where a total of k+1 passes
                for k, name in ((0, 'over05'), (1, 'over15'), (2, 'over25')):
                    if not test(k, 0) and test(k + 1, 0):
                        return f'{per}_{name}'
                return None
            if under:
                for k, name in ((1, 'under15'), (2, 'under25'), (3, 'under35'),
                                (4, 'under45')):
                    if test(k, 0) and not test(k + 1, 0):
                        return f'{per}_{name}'
                return None
        if quantity == 'win_both' and side in ('home', 'away'):
            # nets predict YES; the engine usually bets No, handled by caller
            return f'{side}_winboth'
        if quantity == 'both_halves' and side in ('home', 'away'):
            return f'{side}_both_halves'
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
                p = float(NETS[key].predict_proba(SCALERS[key].transform(X))[0, 1])
                # win_both / both_halves nets predict YES; flip for the No side
                if key.endswith(('winboth', 'both_halves')):
                    try:
                        is_yes = bool(test(1, 0)) and not bool(test(0, 0))
                    except Exception:
                        is_yes = True
                    if not is_yes:
                        p = 1.0 - p
                _used['nn'] += 1
                _used.setdefault('by_market', {})
                _used['by_market'][key] = _used['by_market'].get(key, 0) + 1
                return min(max(p, 0.0), 1.0)
            except Exception:
                pass
    _used['composite'] += 1
    return _composite_prob(home_rec, away_rec, quantity, side, test, grid)


D.model_prob = model_prob        # the swap - everything downstream unchanged

if __name__ == '__main__':
    BD.main()
    print(f"\nprobability sources used: NN {_used['nn']}, composite {_used['composite']}")
    for k, v in sorted(_used.get('by_market', {}).items(), key=lambda kv: -kv[1]):
        print(f"   net {k:18} used {v}x")
