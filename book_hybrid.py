#!/usr/bin/env python3
"""HYBRID booker - neural nets price the goal markets, XGBoost prices the
team stat markets, the shipped composite handles everything else.

    python3 book_hybrid.py [--until HH] [--days N] [--target Nx] [--rollover] [--dry]

Nothing here touches the production engine. book_dynamic.py and dynamic_v4.py
are imported unmodified; only D.model_prob is swapped for this file's router,
and only for markets whose model BEAT ITS BASELINE out-of-time on the 19.7k
corpus (see experiments/train_hybrid.py). Everything else - win-both, corner
match totals, cards away, anything a model lost on - falls straight through to
the composite, so the rulebook and its gates are unchanged.

The stat models predict an expected COUNT; the line is then priced with a
Negative Binomial using each model's fitted dispersion, which is what lets one
model answer 'Under 4.5' and 'Over 6.5' alike.
"""
import math, os, pickle, sys
import numpy as np

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import dynamic_v4 as D
import book_dynamic as BD

EXP = os.path.join(ROOT, 'experiments')
try:
    with open(os.path.join(EXP, 'hybrid_bundle.pkl'), 'rb') as f:
        B = pickle.load(f)
except Exception as e:
    sys.exit(f'hybrid_bundle.pkl not loadable ({e}) - run experiments/train_hybrid.py')

GOAL_NETS, GOAL_SC = B['goal_nets'], B['goal_scalers']
STAT_MODELS, STAT_DISP = B['stat_models'], B['stat_disp']
LEAGUES, GLOB = B['leagues'], B['glob']
print(f'hybrid booker: {len(GOAL_NETS)} goal nets + {len(STAT_MODELS)} stat models')

_used = {'goal_nn': 0, 'stat_xgb': 0, 'composite': 0, 'by': {}}


def _lg(k):
    v = LEAGUES.get(D.CURRENT_LEAGUE)
    return (v or GLOB).get(k, GLOB.get(k, 1.0))


def _goal_features(h, a):
    H, A = h.pairs('goals'), a.pairs('goals')
    if len(H) < 4 or len(A) < 4:
        return None
    hgf = np.array([f for f, _ in H], float); hga = np.array([x for _, x in H], float)
    agf = np.array([f for f, _ in A], float); aga = np.array([x for _, x in A], float)
    w = lambda x: np.average(x, weights=np.linspace(2, 1, len(x)))
    ht, at = hgf + hga, agf + aga
    return np.array([[hgf.mean(), hga.mean(), agf.mean(), aga.mean(),
                      w(hgf), w(hga), w(agf), w(aga), len(hgf), len(agf),
                      hgf.mean() - hga.mean(), agf.mean() - aga.mean(),
                      ht.mean(), at.mean(), ht.max(), at.max(), ht.min(), at.min(),
                      ht.std(), at.std(), (hgf == 0).mean(), (agf == 0).mean(),
                      (ht <= 1).mean(), (at <= 1).mean(), (ht >= 4).mean(), (at >= 4).mean(),
                      abs((hgf.mean() - hga.mean()) - (agf.mean() - aga.mean())),
                      _lg('tot'), _lg('h2')]])


def _stat_features(h, a, stat):
    """Same shape train_hybrid used: both sides' recent for/against in this
    stat, sample sizes, league rate, then the goal-form summary."""
    HP, AP = h.pairs(stat), a.pairs(stat)
    if len(HP) < 3 or len(AP) < 3:
        return None
    hf = np.mean([f for f, _ in HP]); ha_ = np.mean([x for _, x in HP])
    af = np.mean([f for f, _ in AP]); aa = np.mean([x for _, x in AP])
    G, GA = h.pairs('goals'), a.pairs('goals')
    if len(G) < 3 or len(GA) < 3:
        return None
    hgf = np.array([f for f, _ in G], float); hga = np.array([x for _, x in G], float)
    agf = np.array([f for f, _ in GA], float); aga = np.array([x for _, x in GA], float)
    return np.array([[hf, ha_, af, aa, len(HP), len(AP), _lg(stat),
                      hgf.mean(), hga.mean(), agf.mean(), aga.mean(),
                      (hgf + hga).mean(), (agf + aga).mean(),
                      abs((hgf.mean() - hga.mean()) - (agf.mean() - aga.mean()))]])


# Minimum cushion between a stat Under's line and the model's own predicted
# count, in standard deviations. Measured on 26,290 match records (28 Aug):
# a line +0.38 sd above the mean wins 69% of the time, +0.88 sd wins 84%,
# +1.38 sd wins 93% - and the relationship holds across SoT, corners and
# cards alike, so it is a property of counting statistics, not of any one
# market. The Horsens SoT Under 10.5 that lost tonight sat at +0.38 sd; the
# shots Unders at 32.5 and 35.5 that held sat far beyond +3 sd.
MIN_CUSHION_SD = 0.55     # floor only - below this the market is a coin flip
                          # (0.55 sd ~ 73% empirical; dynamic_v4's MIN_PROB of
                          # 0.70 and the overclaim check do the real filtering)

# Empirical win rate by cushion, measured on 37,041 SoT / 37,054 corner /
# 34,558 card match records. Used as a REALITY CHECK on the model, not as a
# blanket ban: the model may not claim more than OVERCLAIM_CAP above what the
# raw distribution delivers at that cushion. Horsens SoT Under 10.5 sat at
# +0.35 sd where the distribution wins 69%, and the model claimed 81% - a
# 12-point overclaim, refused. Thursday's SoT Overs sat at +0.8 sd where the
# distribution wins ~84% and the model agreed, so they stay bettable.
CUSHION_TABLE = ((0.4, 0.66), (0.6, 0.75), (0.8, 0.80), (1.0, 0.86),
                 (1.2, 0.87), (1.4, 0.92), (2.0, 0.97), (3.0, 0.99))
OVERCLAIM_CAP = 0.08


def _base_rate_for(cushion):
    prev = 0.50
    for c, p in CUSHION_TABLE:
        if cushion <= c:
            return prev + (p - prev) * 0.5
        prev = p
    return 0.99


def _nb_sd(mu, alpha):
    """Negative-binomial standard deviation for a predicted count."""
    return math.sqrt(max(mu * (1.0 + alpha * mu), 1e-9))


def _nb_cdf(k, mu, alpha):
    if alpha < 1e-6:
        return sum(math.exp(-mu) * mu ** i / math.factorial(i) for i in range(k + 1))
    r = 1 / alpha; p = r / (r + mu); tot = 0.0; c = p ** r
    for i in range(k + 1):
        tot += c; c *= (r + i) / (i + 1) * (1 - p)
    return min(tot, 1.0)


def _goal_key(quantity, side, test):
    """Probe the predicate to name the market - never trust the label."""
    if quantity not in ('h1', 'h2', 'goals') or side != 'match':
        return None
    per = {'h1': '1h', 'h2': '2h', 'goals': 'ft'}[quantity]
    try:
        if (not test(0, 0)) and test(9, 9):
            for k, nm in ((0, 'over05'), (1, 'over15'), (2, 'over25')):
                if not test(k, 0) and test(k + 1, 0):
                    return f'{per}_{nm}'
        if test(0, 0) and not test(9, 9):
            for k, nm in ((1, 'under15'), (2, 'under25'), (3, 'under35'), (4, 'under45')):
                if test(k, 0) and not test(k + 1, 0):
                    return f'{per}_{nm}'
    except Exception:
        return None
    return None


def _stat_line(test, cap=30):
    """Recover (line, is_under) from a count predicate by scanning."""
    try:
        lo = test(0, 0)
        for k in range(cap):
            if test(k, 0) != lo:
                return k - 0.5 if lo else k - 0.5, lo
    except Exception:
        return None
    return None


_composite = D.model_prob


def model_prob(home_rec, away_rec, quantity, side, test, grid=16):
    # 1) goal markets -> neural nets
    key = _goal_key(quantity, side, test)
    if key and key in GOAL_NETS:
        X = _goal_features(home_rec, away_rec)
        if X is not None:
            try:
                p = float(GOAL_NETS[key].predict_proba(GOAL_SC[key].transform(X))[0, 1])
                _used['goal_nn'] += 1
                _used['by'][key] = _used['by'].get(key, 0) + 1
                return min(max(p, 0.0), 1.0)
            except Exception:
                pass
    # 2) team stat markets -> XGBoost count + NB line pricing
    if side in ('home', 'away') and quantity in ('corners', 'yellow', 'sot',
                                                 'offsides', 'fouls', 'saves'):
        name = f'{quantity}_{side}'
        if name in STAT_MODELS:
            found = _stat_line(test)
            X = _stat_features(home_rec, away_rec, quantity)
            if found and X is not None:
                line, is_under = found
                try:
                    mu = float(max(0.05, STAT_MODELS[name].predict(X)[0]))
                    alpha = STAT_DISP.get(name, 0.0)
                    # Cushion applies to BOTH sides - measured symmetric on
                    # 26,381 matches: SoT Over 4.5 sits +1.12 sd from the mean
                    # and wins 89.9%, SoT Under 12.5 sits +0.88 sd and wins
                    # 84.2%. Same distance, same safety, either direction.
                    # This matters because SportyBet prints the SoT ladder
                    # tightly around the mean (7.5-10.5 on 477 of 559 events,
                    # nothing above 12.5), so the bettable side there is the
                    # deep Over, not the Under.
                    cushion = ((line - mu) if is_under else (mu - line)) / _nb_sd(mu, alpha)
                    if cushion < MIN_CUSHION_SD:
                        _used['thin_line'] = _used.get('thin_line', 0) + 1
                        return 0.0
                    p_under = _nb_cdf(int(math.floor(line)), mu, alpha)
                    p_side = p_under if is_under else 1.0 - p_under
                    # overclaim check against the empirical distribution
                    if p_side - _base_rate_for(cushion) > OVERCLAIM_CAP:
                        _used['overclaim'] = _used.get('overclaim', 0) + 1
                        return 0.0
                    _used['stat_xgb'] += 1
                    _used['by'][name] = _used['by'].get(name, 0) + 1
                    return p_under if is_under else 1.0 - p_under
                except Exception:
                    pass
    _used['composite'] += 1
    return _composite(home_rec, away_rec, quantity, side, test, grid)


D.model_prob = model_prob

if __name__ == '__main__':
    BD.main()
    print(f"\nsources: goal-NN {_used['goal_nn']}, stat-XGB {_used['stat_xgb']}, "
          f"composite {_used['composite']}, thin-line {_used.get('thin_line', 0)}, "
          f"overclaim {_used.get('overclaim', 0)}")
    for k, v in sorted(_used['by'].items(), key=lambda kv: -kv[1])[:12]:
        print(f'   {k:18} {v}x')
