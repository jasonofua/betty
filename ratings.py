#!/usr/bin/env python3
"""OPPONENT-RATED EXPECTED GOALS - every option's chance read off WHO each team played.

13 Oct, user: "Why didn't you take into account the opponent stats? ... if you consider both
teams stats and take them into account it would have been better than this". The four-column
counts (predict_stats first version) read each side's own games at face value: Austria W's
quiet home second halves came against Germany, Norway and the Netherlands, Kosovo W's tight
away games against Gibraltar, Bulgaria and Latvia. Neither record held a game like Austria v
Kosovo; both said 7/7, and the pick (Austria 2nd-half goals Under 2.5) lost 4-0.

Here every past game is read against the opponent it was played against:
  1. RATINGS   every team gets an ATTACK and a DEFENCE number, fitted on all of its games and
               all of its opponents' games, so an opponent's level is itself measured against
               the teams IT played:
                   goals X scores v Y  ~  Poisson( base x home edge x attack(X) / defence(Y) )
               Recent games count more (half-life HALF_LIFE days); a team with few games is
               pulled toward average by PRIOR games' worth of average goals.
  2. MATCHUP   today's expected goals for each side = base x home edge x its attack / the
               opponent's defence, split into halves by the first-half share of goals.
  3. CHANCES   every goal and result option is read off the joint distribution of the four
               half-scores (home 1H, away 1H, home 2H, away 2H): 1X2, double chance, draw no bet,
               totals for the match / either team / either half, GG/NG, win to nil, clean sheet,
               to score in both halves, highest-scoring half, win both halves.
"""
import math
import numpy as np

HALF_LIFE = 365.0      # days - a game a year old counts half as much as today's
PRIOR = 3.0            # games' worth of average goals every team is pulled toward
H1_SHARE = 0.445       # first-half share of goals when a side's games give no half scores
SHARE_PRIOR = 20.0     # goals' worth of H1_SHARE every side's own share is pulled toward
MAXG = 9               # goals per side per half on the grid (P(10+) is negligible)


class Model:
    def __init__(self, teams, att, dfn, mu, home, n_games):
        self.ix = {t: i for i, t in enumerate(teams)}
        self.att, self.dfn, self.mu, self.home, self.n_games = att, dfn, mu, home, n_games

    def rating(self, team):
        i = self.ix.get(team)
        return (0.0, 0.0) if i is None else (float(self.att[i]), float(self.dfn[i]))

    def expect(self, home, away, neutral=False):
        """(expected goals home side, expected goals away side)."""
        ah, dh = self.rating(home)
        aa, da = self.rating(away)
        edge = 0.0 if neutral else self.home
        return math.exp(self.mu + edge + ah - da), math.exp(self.mu + aa - dh)


def fit(games, ref_ts, half_life=HALF_LIFE, prior=PRIOR, iters=60):
    """games: (ts, home, away, home_goals, away_goals, neutral) - only games before ref_ts count.
    Returns a Model, or None with no games."""
    games = [g for g in games if g[0] < ref_ts]
    if not games:
        return None
    teams = sorted({g[1] for g in games} | {g[2] for g in games})
    ix = {t: i for i, t in enumerate(teams)}
    n = len(teams)
    H = np.array([ix[g[1]] for g in games])
    A = np.array([ix[g[2]] for g in games])
    hg = np.array([g[3] for g in games], float)
    ag = np.array([g[4] for g in games], float)
    on = np.array([0.0 if g[5] else 1.0 for g in games])          # 1 = a real home side
    w = 0.5 ** (((ref_ts - np.array([g[0] for g in games], float)) / 86400.0) / half_life)
    base = max(0.2, float(w @ (hg + ag)) / (2 * float(w.sum())))
    mu, home = math.log(base), 0.2
    att, dfn = np.zeros(n), np.zeros(n)
    k = prior * base
    for _ in range(iters):
        lh = np.exp(mu + home * on + att[H] - dfn[A])
        la = np.exp(mu + att[A] - dfn[H])
        # attack: goals scored against what an average attack would have scored there
        sc = np.bincount(H, w * hg, n) + np.bincount(A, w * ag, n)
        ex = (np.bincount(H, w * lh, n) + np.bincount(A, w * la, n)) * np.exp(-att)
        att = np.log((sc + k) / (ex + k))
        lh = np.exp(mu + home * on + att[H] - dfn[A])
        la = np.exp(mu + att[A] - dfn[H])
        # defence: goals conceded against what an average defence would have conceded
        cn = np.bincount(A, w * hg, n) + np.bincount(H, w * ag, n)
        ex = (np.bincount(A, w * lh, n) + np.bincount(H, w * la, n)) * np.exp(dfn)
        dfn = -np.log((cn + k) / (ex + k))
        # keep the averages at zero; base and home edge from the totals
        mu += float(att.mean()) - float(dfn.mean())
        att -= att.mean()
        dfn -= dfn.mean()
        lh = np.exp(mu + home * on + att[H] - dfn[A])
        la = np.exp(mu + att[A] - dfn[H])
        mu += math.log(float(w @ (hg + ag)) / float(w @ (lh + la)))
        hw = w * on
        if hw.sum() > 0:
            lh = np.exp(mu + home * on + att[H] - dfn[A])
            home += 0.5 * math.log(max(1e-6, float(hw @ hg)) / max(1e-6, float(hw @ lh)))
            home = min(max(home, 0.0), 0.6)
    return Model(teams, att, dfn, mu, home, len(games))


def half_share(h1_for, h1_against, ft_for, ft_against):
    """A side's first-half share of the goals in its games, pulled toward H1_SHARE."""
    g1 = sum(h1_for) + sum(h1_against)
    g = sum(ft_for[:len(h1_for)]) + sum(ft_against[:len(h1_against)])
    return (g1 + SHARE_PRIOR * H1_SHARE) / (g + SHARE_PRIOR)


def _pmf(lam):
    k = np.arange(MAXG + 1)
    p = np.exp(-lam + k * math.log(max(lam, 1e-9)) - np.array([math.lgamma(x + 1) for x in k]))
    return p / p.sum()


class Grid:
    """Joint chance of every (home 1H, away 1H, home 2H, away 2H) score."""

    def __init__(self, lam_h, lam_a, share_h=H1_SHARE, share_a=H1_SHARE):
        self.lam = (lam_h, lam_a)
        p = np.einsum('a,b,c,d->abcd', _pmf(lam_h * share_h), _pmf(lam_a * share_a),
                      _pmf(lam_h * (1 - share_h)), _pmf(lam_a * (1 - share_a)))
        g = np.indices(p.shape)
        self.p = p.ravel()
        self.h1, self.a1, self.h2, self.a2 = (x.ravel() for x in g)
        self._cache = {}

    def pairs(self, qkey, side):
        """(for, against) of quantity qkey on every cell, from `side`'s point of view -
        the same pairs dynamic_v4.Record holds for a team's past games."""
        key = (qkey, side)
        if key in self._cache:
            return self._cache[key]
        h1, a1, h2, a2 = self.h1, self.a1, self.h2, self.a2
        if qkey == 'goals':
            f, a = h1 + h2, a1 + a2
        elif qkey == 'h1':
            f, a = h1, a1
        elif qkey == 'h2':
            f, a = h2, a2
        elif qkey == 'both_halves':
            f, a = np.minimum(h1, h2), np.minimum(a1, a2)
        elif qkey == 'win_both':
            f, a = ((h1 > a1) & (h2 > a2)).astype(int), ((a1 > h1) & (a2 > h2)).astype(int)
        elif qkey == 'hsh_match':
            f, a = h1 + a1, h2 + a2
            side = 'match'
        elif qkey == 'hsh':                   # one team's own goals by half: "Home Highest Scoring Half"
            f, a = (a1, a2) if side == 'away' else (h1, h2)
            side = 'match'
        elif qkey == 'bh_line':
            f, a = np.minimum(h1 + a1, h2 + a2), np.maximum(h1 + a1, h2 + a2)
            side = 'match'
        else:
            return None
        if side == 'away':
            f, a = a, f
        code = f * 64 + a
        u, inv = np.unique(code, return_inverse=True)
        mass = np.bincount(inv, self.p)
        out = [(int(c) // 64, int(c) % 64, float(m)) for c, m in zip(u, mass)]
        self._cache[key] = out
        return out

    def chance(self, qkey, side, test):
        prs = self.pairs(qkey, side)
        if prs is None:
            return None
        try:
            return sum(m for f, a, m in prs if test(f, a))
        except Exception:
            return None


GOAL_QUANTITIES = {'goals', 'h1', 'h2', 'both_halves', 'win_both', 'hsh_match', 'hsh', 'bh_line'}


import re as _re
FRIENDLY = _re.compile(r'friendl|pre-season|preseason|exhibition|club friendly|como cup|summer series|trophy tour', _re.I)
# 14 Oct: pre-season tournaments whose names have no "friendly" in them. Recreativo's home column held
# the Trofeo Colombino (World) v Betis; Port FC's away column two Indonesian President Cup games.
PRESEASON = _re.compile(r'trofeo|copa amsterdam|atlantic cup|nextgen cup|cee cup|viareggio|emirates cup|audi cup', _re.I)
PRESEASON_BY_COUNTRY = {('indonesia', 'president cup')}


def is_friendly(comp, country=''):
    """A friendly or a pre-season tournament - not form. `country` is the feed's KH ('World' for the
    invitational club tournaments); without it the pre-season names still count."""
    comp, k = comp or '', (country or '').strip().lower()
    if FRIENDLY.search(comp):
        return True
    if PRESEASON.search(comp) and k in ('', 'world'):
        return True
    return (k, comp.strip().lower()) in PRESEASON_BY_COUNTRY
# ─────────────────────────────────────────────────────────────────────────────
# Global ratings: every game in every head-to-head feed ever fetched, by TEAM ID
# ─────────────────────────────────────────────────────────────────────────────
# One match's own records cannot tell levels apart: Kosovo W's opponents (Gibraltar,
# Bulgaria, Latvia) and Austria W's (Germany, Norway, Netherlands) barely meet, so each
# cluster is rated against itself and the gap between them is lost (13 Oct: 1.69 v 1.12
# for a side that won 4-0). Every df_hh feed carries ~50 games a side WITH the Flashscore
# team ids (UQ home, UO away), so all of them together form one connected table - play-offs,
# cups and continental games link the leagues and levels.
import os as _os, json as _json, glob as _glob
# on the server the store sits on the bookings volume so it survives deploys and keeps growing
STORE = _os.path.join(_os.path.dirname(_os.environ['BOOKINGS_PATH']) if _os.environ.get('BOOKINGS_PATH')
                      else _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'experiments'), 'fs_games.json')
WINDOW_DAYS = 1100    # games older than ~3 years are not fitted


def feed_games(raw):
    """Every game in a df_hh feed's 'Last matches' blocks: {mid: [ts, home_id, away_id, hg, ag, comp, country, home, away]}.
    Also returns {block team name: team id} so a fixture's two sides can be found."""
    import fetcher_v2 as F2
    out, ids, tab, blk = {}, {}, None, None
    for s in F2.sections(raw or ''):
        if 'KA' in s:
            tab = s['KA']
        if 'KB' in s:
            blk = s['KB']
            continue
        if tab != 'Overall' or not blk or not blk.startswith('Last matches') or 'KJ' not in s or 'KU' not in s:
            continue
        try:
            hg, ag, ts = int(s['KU']), int(s['KT']), int(s.get('KC', 0))
        except ValueError:
            continue
        hid, aid, mid = s.get('UQ'), s.get('UO'), s.get('KP')
        if not (hid and aid and mid):
            continue
        # the block's own team: KS is its venue in that game ('*' marks the WINNER, not the team -
        # reading it as the team gave a side its opponent's id whenever it had lost, 13 Oct)
        team = blk.split(':', 1)[1].strip()
        if s.get('KS') == 'home':
            ids.setdefault(team, hid)
        elif s.get('KS') == 'away':
            ids.setdefault(team, aid)
        out[mid] = [ts, hid, aid, hg, ag, s.get('KF', ''), s.get('KH', ''), s['KJ'].lstrip('*'), s['KK'].lstrip('*')]
    return out, ids


def load_store(path=STORE):
    try:
        return _json.load(open(path))
    except Exception:
        return {}


def harvest(store=None, cache_dir=None, path=STORE, save=True):
    """Fold every cached df_hh feed into the store (keyed by Flashscore match id)."""
    import fetcher_v2 as F2
    store = load_store(path) if store is None else store
    for fp in _glob.glob(_os.path.join(cache_dir or F2.CACHE, '*')):
        try:
            raw = open(fp, encoding='utf-8', errors='replace').read()
        except OSError:
            continue
        if 'Last matches' in raw[:4000]:
            store.update(feed_games(raw)[0])
    if save:
        _os.makedirs(_os.path.dirname(path), exist_ok=True)
        _json.dump(store, open(path, 'w'))
    return store


def global_model(store, ref_ts, days=WINDOW_DAYS):
    """The ratings fitted on every stored game in the `days` before ref_ts, friendlies out."""
    lo = ref_ts - days * 86400
    games = [(g[0], g[1], g[2], g[3], g[4], False) for g in store.values()
             if lo <= g[0] < ref_ts and not is_friendly(g[5], g[6])]
    return fit(games, ref_ts)


# ─────────────────────────────────────────────────────────────────────────────
# Today's prices as expected goals
# ─────────────────────────────────────────────────────────────────────────────
_TAB = None


def _table():
    global _TAB
    if _TAB is None:
        lg = np.arange(0.05, 4.5, 0.025)
        lh, la = np.meshgrid(lg, lg, indexing='ij')
        lh, la = lh.ravel(), la.ravel()
        k = np.arange(15)
        lf = np.array([math.lgamma(x + 1) for x in k])
        ph = np.exp(-lh[:, None] + k[None, :] * np.log(lh[:, None]) - lf[None, :])
        pa = np.exp(-la[:, None] + k[None, :] * np.log(la[:, None]) - lf[None, :])
        j = ph[:, :, None] * pa[:, None, :]
        diff, tot = np.subtract.outer(k, k), np.add.outer(k, k)
        _TAB = (lh, la, (j * (diff > 0)).sum((1, 2)), (j * (diff < 0)).sum((1, 2)), (j * (tot > 2.5)).sum((1, 2)))
    return _TAB


def market_lam(o1, ox, o2, over=None, under=None):
    """(home, away) expected goals whose Poisson 1X2 (and Over 2.5) match the prices with the margin removed."""
    lh, la, th, ta, to = _table()
    s = 1 / o1 + 1 / ox + 1 / o2
    err = (th - (1 / o1) / s) ** 2 + (ta - (1 / o2) / s) ** 2
    if over and under:
        err = err + (to - (1 / over) / (1 / over + 1 / under)) ** 2
    i = int(np.argmin(err))
    return float(lh[i]), float(la[i])
