#!/usr/bin/env python3
"""Dynamic market evaluator - no market handlers written in advance.

The old design had ~20 hardcoded blocks, one per market family, so any market
not anticipated was invisible. Atlas v Sacachispas offered "1st Half - Home O/U"
with a 5/5+4/4 record behind Under 2.5, and the engine bet an untallied 1H 1X
because no block existed for it.

Flow here:
    SportyBet match  ->  that match's OWN market list (15 to 142 of them)
                     ->  Flashscore per-game record for both teams
                     ->  count EVERY offered outcome against the record
                     ->  return the three best-supported

A market is evaluated because the match offers it, not because someone wrote
code for it. Anything the record cannot answer is skipped rather than guessed.
"""
import re

# Both sides must clear this independently, over at least this many games.
# Dropped from 4 to 3 on 4 Aug: after the World Cup break teams have played
# three matches, so any higher bar cannot fire for anyone until late August.
MIN_GAMES = 3
MIN_HITS  = 0.80
TOP_N     = 3

# The selector is now the modelled probability of the outcome and its edge over
# the book's own price, not the raw tally. A tally cannot tell a real certainty
# from a market that is simply lopsided: Veranopolis read 3/3 on "will not win
# both halves" off a series of [0,0,0] and lost, while the book had it at 97%
# and was right. Requiring the model to BEAT the price removes every 1.01-1.05
# near-certainty at a stroke, because the book prices those correctly.
MIN_PROB = 0.70     # do not bet what the model itself thinks is a coin flip
MIN_EDGE = 0.02     # and only when we disagree with the price in our favour


# ─────────────────────────────────────────────────────────────────────────────
# The record: per-game series, most recent first, own venue only.
# ─────────────────────────────────────────────────────────────────────────────
class Record:
    """One team's recent games at its own venue.

    There is NO fixed list of quantities. Whatever series the fetcher returns
    for this match is what the record holds - if Flashscore carries xG, big
    chances, saves and possession for that league, they are all in here; if it
    carries only goals, that is what is here. Nothing is dropped because it was
    not anticipated, and nothing is invented because it was expected."""

    def __init__(self, series=None):
        # {quantity: ([for, ...], [against, ...])}, most recent game first
        self.series = {k: (list(v[0] or []), list(v[1] or []))
                       for k, v in (series or {}).items() if v}

    def quantities(self):
        """Every quantity this record can actually answer a question about."""
        return sorted(k for k, (f, a) in self.series.items() if f and a)

    def pairs(self, quantity):
        """[(for, against), ...] - empty when this record cannot answer it."""
        v = self.series.get(quantity)
        if not v or not v[0] or not v[1]:
            return []
        return list(zip(v[0], v[1]))

    def __repr__(self):
        return "Record(" + ", ".join(
            f"{k}:{len(self.series[k][0])}g" for k in self.quantities()) + ")"


# ─────────────────────────────────────────────────────────────────────────────
# Parsing a market into (quantity, period, side)
# ─────────────────────────────────────────────────────────────────────────────
# The ONLY interpretation kept: which word in a market name names a quantity we
# hold a per-game series for. There is deliberately no allow-list of permitted
# markets - the match's own option list is the universe. A market drops out for
# one of two structural reasons only: no test can be built from its outcome
# text, or no series exists for its quantity.
# Betting language differs from Flashscore's field names in a handful of places.
# This is translation, not a filter: it never decides whether a market is allowed,
# only which of the record's OWN quantities a market name is referring to.
# market_quantity picks the LONGEST matching phrase, which is what separates
# "Home Team Highest Scoring Half" (hsh) from the bare "Highest Scoring Half"
# (hsh_match), and "Both Halves Over 1.5" (bh_line) from "to Score In Both
# Halves" (both_halves).
ALIASES = {
    'hsh':         ('team highest scoring half',),
    'hsh_match':   ('highest scoring half',),
    'bh_line':     ('both halves over', 'both halves under'),
    'win_both':    ('to win both halves',),
    'run':         ('goals in a row',),
    'both_halves': ('both halves',),
    'yellow':   ('card', 'booking'),
    'sot':      ('shots on', 'shot on', 'on target'),
    'offsides': ('offside',),
    'fouls':    ('foul',),
    'corners':  ('corner',),
}


def market_quantity(name, available):
    """Which of THIS record's quantities the market is about.

    `available` comes from the record, so the set of answerable quantities is
    whatever the fetcher returned for this match - never a list kept in here.
    Longest name first so 'shots on target' is not captured by 'shots'."""
    n = name.lower()
    best = None
    for q in available:
        base = q.rsplit('_h', 1)[0] if q.endswith(('_h1', '_h2')) else q
        for word in (base,) + ALIASES.get(base, ()):
            if word in n and (best is None or len(word) > best[1]):
                best = (base, len(word))
    return best[0] if best else 'goals'


def _names_team(market_name, team):
    """Does this market name refer to that team by name?

    SportyBet writes team markets with the TEAM'S NAME, not "Home"/"Away":
    "CD Real Tomayapo Over/Under", "1st half - El Salvador Over/Under". Matching
    only the literal words home/away made those read as MATCH totals, so the
    count was taken from the wrong quantity entirely."""
    if not team:
        return False
    import sportybet as SB
    mt, tt = SB.toks(market_name), SB.toks(team)
    if not tt:
        return False
    return len(mt & tt) >= max(1, len(tt) - 1)


def parse_market(name, available=(), teams=(None, None)):
    """(quantity, 'ft'|'h1'|'h2', 'home'|'away'|'match')."""
    n = name.lower()
    q = market_quantity(name, available)

    if n.startswith('1st half') or n.startswith('ht ') or '1st h ' in n:
        p = 'h1'
    elif n.startswith('2nd half') or n.startswith('2nd '):
        p = 'h2'
    else:
        p = 'ft'

    # Combination markets ("Draw Or GG/NG", "Away Or Any Clean Sheet") name a
    # RESULT, not a team's quantity, so they must be read in the home frame -
    # letting "Away" flip the pair made the predicate evaluate backwards.
    if (re.search(r'\bor\b', n) and 'double chance' not in n
            and 'in a row' not in n):
        # "...2 or More Goals in a Row" is not a combination market - the "or"
        # belongs to the line, not to two joined conditions. Letting it fall
        # through here forced side='match', which reads the AWAY variant off the
        # mirrored home record and counts the opponent's run instead.
        return q, p, 'match'
    if 'no draw' in n:
        return q, p, 'match'
    # side: whose count is being bet - by the word home/away, or by team name
    if re.search(r'\bhome\b', n):   s = 'home'
    elif re.search(r'\baway\b', n): s = 'away'
    elif _names_team(name, teams[0]): s = 'home'
    elif _names_team(name, teams[1]): s = 'away'
    else:                           s = 'match'
    return q, p, s


# ─────────────────────────────────────────────────────────────────────────────
# Turning an outcome into a yes/no test over (for, against)
# ─────────────────────────────────────────────────────────────────────────────
# Specifier parameters this evaluator can actually interpret. A market carrying
# anything else is refused, because ignoring a parameter silently changes what
# the bet means: "Total Goals from 1 to X min" has specifier 'minute=15|total=1.5'
# and reading only the total turned "over 1.5 in the first 15 minutes" into "over
# 1.5 in the match" - always true, a fake 3/3+3/3 at 100%, priced by the book at
# 17.0. 13,092 live markets carry minute=, so this was not a corner case.
KNOWN_SPEC = {'total', 'variant'}


def spec_ok(spec):
    for part in (spec or '').split('|'):
        if '=' in part and part.split('=', 1)[0] not in KNOWN_SPEC:
            return False
    return True


def parse_outcome(name, desc, spec, available=(), teams=(None, None)):
    """Return test(for, against) -> bool, or None when not countable.

    `for`/`against` are always from the perspective of the team whose record is
    being walked, so the SAME test is applied to both teams by handing the away
    team its mirrored pair (see evaluate)."""
    n, d = name.lower(), (desc or '').strip()
    dl = d.lower()
    _, _, side = parse_market(name, available, teams)
    line = None
    m = re.search(r'total=([\d.]+)', spec or '')
    if m:
        line = float(m.group(1))

    def total(f, a):
        return (f + a) if side == 'match' else f

    # "To Score N or More Goals in a Row" - MUST come before the combination
    # branch below, which sees the " or " in "2 or more goals" and refuses it.
    # "To Score N or More Goals in a Row". Series is (our longest run, theirs).
    # ANY team -> either side reaching N; HOME/AWAY -> that side only, and
    # parse_market has already put the named side's run in `f`.
    mm = re.search(r'(\d+)\s+or more goals in a row', n)
    if mm:
        k = int(mm.group(1))
        anyteam = n.strip().startswith('any team')
        yes = (lambda f, a: max(f, a) >= k) if anyteam else (lambda f, a: f >= k)
        if dl == 'yes': return yes
        if dl == 'no':  return lambda f, a: not yes(f, a)
        return None

    # ── combination markets: "<result> Or <condition>" ───────────────────────
    # These read as ONE of their halves before: "Draw Or GG/NG" was evaluated as
    # plain GG, so a 0-0 (a draw, therefore a win) counted as a loss. Both live
    # bookings of it settled correctly only by coincidence.
    def _result_part(txt):
        t = txt.strip().lower()
        if t in ('draw',):                       return lambda f, a: f == a
        if t in ('home', 'home team'):           return lambda f, a: f > a
        if t in ('away', 'away team'):           return lambda f, a: f < a
        return None

    def _cond_part(txt):
        t = txt.strip().lower()
        if 'gg/ng' in t or 'both teams to score' in t:
            return lambda f, a: f > 0 and a > 0
        if 'any clean sheet' in t:
            return lambda f, a: f == 0 or a == 0
        mm = re.match(r'^(over|under)\s*([\d.]+)$', t)
        if mm:
            ln = float(mm.group(2))
            return (lambda f, a: f + a > ln) if mm.group(1) == 'over' \
                   else (lambda f, a: f + a < ln)
        return None

    if dl in ('yes', 'no') or side != 'match':
        mm = re.match(r'^(.*?)\s+or\s+(.*)$', n)
        if mm and 'double chance' not in n:
            left, right = _result_part(mm.group(1)), _cond_part(mm.group(2))
            if left is None:
                left = _cond_part(mm.group(1))
            if right is None:
                right = _result_part(mm.group(2))
            if left and right:
                yes = lambda f, a: left(f, a) or right(f, a)
                return yes if dl != 'no' else (lambda f, a: not yes(f, a))
            return None                  # half of it we cannot express - refuse

    # "No Draw Both Teams To Score" - an AND, not an OR
    if 'no draw' in n and 'both teams to score' in n:
        yes = lambda f, a: f != a and f > 0 and a > 0
        if dl == 'yes': return yes
        if dl == 'no':  return lambda f, a: not yes(f, a)
        return None

    # "Excluded Number of Goals" - SportyBet's guide: pick the goal count that
    # will NOT happen. Outcomes are bare numbers with a "3+" bucket, same shape
    # as Exact Goals but negated.
    if 'excluded number of goals' in n:
        if d.endswith('+'):
            try:    k = int(d[:-1]); return lambda f, a: not (total(f, a) >= k)
            except ValueError: return None
        try:        k = int(d);      return lambda f, a: total(f, a) != k
        except ValueError: return None


    # ── half-comparison markets, read off their derived quantity ─────────────
    # These must come BEFORE the generic Over/Under and Yes/No branches, which
    # would otherwise swallow them and count the wrong thing.
    if 'highest scoring half' in n:
        # series is (1st-half count, 2nd-half count)
        if dl in ('1st half', 'first half'):  return lambda f, a: f > a
        if dl in ('2nd half', 'second half'): return lambda f, a: f < a
        if dl == 'equal':                     return lambda f, a: f == a
        return None

    if 'to win both halves' in n:
        # series is (won both halves 1/0, opponent won both 1/0)
        if dl == 'yes': return lambda f, a: f > 0
        if dl == 'no':  return lambda f, a: f == 0
        return None

    mm = re.search(r'both halves (over|under)\s*([\d.]+)', n)
    if mm:
        # series is (lowest-scoring half's total, highest-scoring half's total).
        # "Over" needs the LOWEST half above the line; "Under" needs the HIGHEST
        # half below it - a single half breaches either one.
        ln = float(mm.group(2))
        yes = (lambda f, a: f > ln) if mm.group(1) == 'over' else (lambda f, a: a < ln)
        if dl == 'yes': return yes
        if dl == 'no':  return lambda f, a: not yes(f, a)
        return None

    # Over / Under  (line from the outcome text, falling back to the specifier)
    m = re.match(r'^(over|under)\s+([\d.]+)$', dl)
    if m:
        ln = float(m.group(2))
        if m.group(1) == 'over':
            return lambda f, a: total(f, a) > ln
        return lambda f, a: total(f, a) < ln

    # GG / NG and its Yes/No spellings
    if 'gg/ng' in n or 'both teams' in n:
        if dl in ('yes', 'gg'):  return lambda f, a: f > 0 and a > 0
        if dl in ('no', 'ng'):   return lambda f, a: not (f > 0 and a > 0)

    # Clean sheet - the named side concedes nothing
    if 'clean sheet' in n:
        if dl == 'yes': return lambda f, a: a == 0
        if dl == 'no':  return lambda f, a: a > 0

    # Win to nil
    if 'win to nil' in n or 'win to nil' in dl:
        if dl.startswith('yes'): return lambda f, a: f > a and a == 0
        if dl.startswith('no'):  return lambda f, a: not (f > a and a == 0)

    # 1X2
    if n in ('1x2', '1st half - 1x2', '2nd half - 1x2'):
        if dl in ('home', '1'): return lambda f, a: f > a
        if dl in ('away', '2'): return lambda f, a: f < a
        if dl in ('draw', 'x'): return lambda f, a: f == a

    # Double chance
    if 'double chance' in n:
        if dl == 'home or draw': return lambda f, a: f >= a
        if dl == 'draw or away': return lambda f, a: f <= a
        if dl == 'home or away': return lambda f, a: f != a

    # Draw no bet / No bet - a push is not a loss, so count it as a hit
    if 'draw no bet' in n:
        if dl == 'home': return lambda f, a: f >= a
        if dl == 'away': return lambda f, a: f <= a
    if n == 'home no bet':   return (lambda f, a: f <= a) if dl == 'away' else None
    if n == 'away no bet':   return (lambda f, a: f >= a) if dl == 'home' else None

    # Odd / Even
    if 'odd/even' in n:
        if dl == 'odd':  return lambda f, a: total(f, a) % 2 == 1
        if dl == 'even': return lambda f, a: total(f, a) % 2 == 0

    # Exact goals, including the "3+" bucket
    if 'exact goals' in n:
        if d.endswith('+'):
            try:    k = int(d[:-1]); return lambda f, a: total(f, a) >= k
            except ValueError: return None
        try:        k = int(d);      return lambda f, a: total(f, a) == k
        except ValueError: return None

    # Multigoals / Goal Range also offer "No goal" and a "4+" top bucket, which
    # the band regex below cannot match. Without these the half-Multigoals family
    # could only ever be read on its middle bands.
    if 'multigoal' in n or 'range' in n:
        if dl in ('no goal', 'no goals'):
            return lambda f, a: total(f, a) == 0
        mm = re.match(r'^(\d+)\+$', d.strip())
        if mm:
            k = int(mm.group(1))
            return lambda f, a: total(f, a) >= k

    # Bands: Multigoals "1-3", Goal Range "2-3"
    m = re.match(r'^(\d+)\s*-\s*(\d+)$', d)
    if m and ('multigoal' in n or 'range' in n):
        lo, hi = int(m.group(1)), int(m.group(2))
        return lambda f, a: lo <= total(f, a) <= hi

    # Correct score
    m = re.match(r'^(\d+):(\d+)$', d)
    if m and 'correct score' in n:
        h, aw = int(m.group(1)), int(m.group(2))
        return lambda f, a: f == h and a == aw

    # To score (in a period / at all)
    if 'to score' in n:
        # "in both halves" is only answerable off the derived both_halves series
        # (the per-game MIN of the two halves). If this match has no half data,
        # market_quantity falls back to 'goals' and the test below would silently
        # become "scored at all" again - refuse instead of counting the wrong
        # thing, which is what put two mis-tallied legs on MLWH8U.
        if 'both halves' in n and market_quantity(name, available) != 'both_halves':
            return None
        if dl in ('yes', '1'): return lambda f, a: f > 0
        if dl in ('no', '2'):  return lambda f, a: f == 0

    return None


# ─────────────────────────────────────────────────────────────────────────────
# Counting
# ─────────────────────────────────────────────────────────────────────────────
def reads(test):
    """Which of (for, against) the predicate actually depends on.

    Probing the predicate beats keeping a list of which markets are about a
    team's own production and which are about what it concedes:

      "Home Team Corners Over 4.5"  -> test is f > 4.5     reads FOR
      "Home Clean Sheet / No"       -> test is a > 0       reads AGAINST
      "Home Win To Nil / Yes"       -> f > a and a == 0    reads BOTH

    That distinction decides which team's record settles the bet. Gating
    "Home Clean Sheet No" on the HOME team's conceding record let a pick through
    on Ranheim 6/7 while the away side that has to score sat at 4/7 - the away
    team's scoring IS the event, not corroboration for it."""
    # The grid has to span every line a market can carry - corner lines reach
    # 12.5, shots lines higher. A narrow probe made "Corners Over 4.5" look as
    # though it read neither column, because f never crossed the line.
    rf = ra = False
    try:
        for f in range(0, 26):
            for a in range(0, 26):
                if not rf and test(f, a) != test(f + 1, a): rf = True
                if not ra and test(f, a) != test(f, a + 1): ra = True
                if rf and ra:
                    return True, True
    except Exception:
        return True, True
    return rf, ra


def _mirror(pairs):
    """Flip (for, against) so an away team's record answers the same question
    from the home team's side of the fixture."""
    return [(a, f) for f, a in pairs]


def count_outcome(home_rec, away_rec, quantity, side, test):
    """(hits_h, n_h, hits_a, n_a, primary) or None when the records are too thin.

    `primary` is the index (0 or 1) of the series that actually decides the bet:

      "Home Team Total Corners Under 6.5" settles on the HOME team's corner
      count. That team's own record is the evidence; the opponent's concession
      record only corroborates it. Requiring BOTH to clear the bar discarded the
      picks with the strongest direct evidence - a side going 5/5 on its own
      count was thrown out because its opponent sat at 3/5.

    For match totals neither series is primary: the line depends on both teams,
    so both still have to clear the bar."""
    H = home_rec.pairs(quantity)
    A = away_rec.pairs(quantity)
    rf, ra = reads(test)
    if side == 'home':
        seq_h, seq_a = H, _mirror(A)
        # reads FOR only -> the home team's own production decides.
        # reads AGAINST only -> the AWAY team's production decides (mirrored into
        # seq_a's `against` slot), so gate there instead.
        primary = 0 if (rf and not ra) else (1 if (ra and not rf) else None)
    elif side == 'away':
        seq_h, seq_a = _mirror(H), A
        primary = 1 if (rf and not ra) else (0 if (ra and not rf) else None)
    else:
        # Match markets are written from the HOME side ("Home or Draw" is f >= a
        # with f = home goals), so the away team's own record has to be mirrored
        # before the same predicate is applied to it. Without this, "Home or Draw"
        # asked of the away team's games counted the games they WON as successes,
        # when those are exactly the games where the bet would have lost.
        # Symmetric markets (Over/Under, GG/NG, Odd/Even) are unchanged by the
        # mirror, so this is safe for all of them.
        seq_h, seq_a, primary = H, _mirror(A), None
    if len(seq_h) < MIN_GAMES or len(seq_a) < MIN_GAMES:
        return None
    hh = sum(1 for f, a in seq_h if test(f, a))
    ha = sum(1 for f, a in seq_a if test(f, a))
    return hh, len(seq_h), ha, len(seq_a), primary


# Families excluded by explicit instruction, not by the evaluator's own logic.
# Odd/Even is structurally near-random: most first halves finish 0-0 or 1-0, so
# "Even" scores a high tally simply because nobody scored. The count is real but
# it is measuring "low-scoring half" under another name, and the market itself is
# a coin flip. Removed on request 5 Aug after it filled 8 legs across two slips.
#
# Multigoals, removed 6 Aug: 3W-6L (33%) for -48% ROI across 12 codes, against
# 84-94% for every other family. FIVE of the six losses were the team scoring
# ZERO. Every band SportyBet offers starts at 1 - 1-2, 1-3, 2-3, 1-4 .. 3-6 -
# so there is no band containing 0, and a Multigoals bet is a "needs a goal"
# bet with a ceiling added: it dies to a blank at the bottom AND a blowout at
# the top, for less money than the plain team Over 0.5 that carries only the
# first risk. The tallies behind the losers were sound ([6/7+7/7], [6/7+6/7]);
# the counter cannot see that the band's floor is where the risk lives.
#
# Clean Sheet, removed on request 7 Aug. Hwaseong 0:0 Seoul E-Land took out a
# "Clean Sheet / No" on two different slips in one game - one needed Hwaseong to
# score, the other needed Seoul E-Land to, and the goalless draw beat both. The
# whole family is blacklisted here, Yes and No alike, not just the goal-dependent
# side.
# Matched against the market name. 'multigoal' is NOT here: the user's blacklist
# was aimed at the full-match Multigoals family (3W-8L, five of six losses being
# the team scoring zero, and every band starting at 1 so you cannot bet the
# blank). The 1st/2nd Half variants DO offer a "No goal" outcome, so that
# objection does not apply to them - they are freed by EXCLUDE_EXACT below.

# ─────────────────────────────────────────────────────────────────────────────
# What is LIKELY to happen, as opposed to what has happened
# ─────────────────────────────────────────────────────────────────────────────
from math import exp as _e, factorial as _fa


def _symmetric(test, grid=8):
    """True when the predicate treats the two teams interchangeably.

    This is what separates a goal TOTAL from a goal RESULT without keeping a
    list of market names. "Under 3.5" and "Goal Range 2-3" and "Excluded Number
    of Goals / 3" all ask about f + a, so swapping the sides cannot change the
    answer. "Double Chance", "Draw No Bet" and "1X2" ask who won, so it does."""
    try:
        for f in range(grid):
            for a in range(grid):
                if test(f, a) != test(a, f):
                    return False
    except Exception:
        return False
    return True


def _pois(k, mu):
    return _e(-mu) * mu ** k / _fa(k) if mu > 0 else (1.0 if k == 0 else 0.0)


def model_prob(home_rec, away_rec, quantity, side, test, grid=16):
    """P(this outcome) for THIS match, not "how often did it happen".

    The tally cannot distinguish cases it should. Sirius had first-half corners
    of [7,2,3,3,6,3,5] against a 1.5 line: a flawless 7/7, but the mean is 4.1
    and the floor is 2, so the true chance was ~88%, not 100% - and it came in
    at 1. Veranopolis had win_both [0,0,0], a 3/3 on a series with no variation
    at all, and won both halves. Both read as certainties to a counter.

    Method: take the mean of the deciding series, blended with the opponent's
    matching column (a team's corner count depends on who it plays), then push
    the SAME predicate through a Poisson grid instead of through seven past
    games. Works for any predicate - thresholds, bands, exact counts, half
    comparisons - because it only ever asks test(f, a).

    Series that are pure 0/1 indicators (win_both, and any derived flag) are not
    Poisson; those fall back to a Laplace-smoothed rate, which for an all-zeros
    sample gives ~1/(n+2) rather than 0 and stops the certainty."""
    H, A = home_rec.pairs(quantity), away_rec.pairs(quantity)
    if len(H) < MIN_GAMES or len(A) < MIN_GAMES:
        return None
    hf = [f for f, _ in H]; ha = [a for _, a in H]
    af = [f for f, _ in A]; aa = [a for _, a in A]

    binary = all(v in (0, 1) for v in hf + ha + af + aa)
    if binary:
        seq_h = H if side != 'away' else _mirror(H)
        seq_a = _mirror(A) if side != 'away' else A
        hits = sum(1 for f, a in seq_h if test(f, a)) + \
               sum(1 for f, a in seq_a if test(f, a))
        return (hits + 1) / (len(seq_h) + len(seq_a) + 2)

    def blend(own, opp):
        m = (sum(own) / len(own) + sum(opp) / len(opp)) / 2 if own and opp else 0.0
        return max(m, 0.02)

    if side == 'away':
        mu_f, mu_a = blend(af, ha), blend(aa, hf)
    else:
        mu_f, mu_a = blend(hf, aa), blend(ha, af)

    p = 0.0
    for f in range(grid):
        pf = _pois(f, mu_f)
        if pf < 1e-9:
            continue
        for a in range(grid):
            pa = _pois(a, mu_a)
            if pa < 1e-9:
                continue
            try:
                if test(f, a):
                    p += pf * pa
            except Exception:
                return None
    return min(max(p, 0.0), 1.0)


EXCLUDE = ('odd/even', 'odd or even', 'clean sheet',
           # Result markets, re-banned 13 Aug. Freeing match goal TOTALS meant
           # switching off the primary-is-None rule, and that rule was also what
           # blocked these - they came back by accident, seven of them on one
           # slip. 84W-43L, 66% across the settled record. 'gg/ng' also catches
           # the "Home Team Or GG/NG" and "Draw Or GG/NG" combinations.
           'gg/ng', 'no draw',
           # Double Chance by name, not by the symmetry probe. "Home or Away"
           # means "not a draw", which IS symmetric - swap the teams and the
           # answer is unchanged - so the probe reads it as a total and lets it
           # through, while correctly blocking "Home or Draw" and "Draw or
           # Away". It slipped onto K9Z96G at 1.25 and lost to a 3:3 draw.
           'double chance')

# Full-match Multigoals only, by exact name. Half variants stay available.
EXCLUDE_EXACT = ('multigoals', 'home multigoals', 'away multigoals')

# The only price band in which Under bets have paid. See the table in evaluate().
UNDER_MIN, UNDER_MAX = 1.10, 1.19


def evaluate(markets, home_rec, away_rec, min_odds=1.0, max_odds=None,
             teams=(None, None)):
    """Score every offered outcome. Returns the best-supported first.

    `markets` is the event's own market list, straight from SportyBet, so the
    range of what gets considered is set by the match, not by this file."""
    out = []
    for m in markets:
        name = m.get('name') or m.get('desc') or ''
        nlow = name.strip().lower()
        if any(x in nlow for x in EXCLUDE) or nlow in EXCLUDE_EXACT:
            continue                            # excluded by instruction
        # Only OPEN markets. The events feed carries a per-market status and
        # status 0 is the tradeable one; 1, 2 and 3 are suspended or settled and
        # cannot be placed. Booking them produced codes that were 42-52%
        # unplayable - SportyBet lists a fixture days ahead but keeps its corner
        # and stat markets frozen until close to kickoff, so the further out the
        # window, the more of the slip is dead on arrival.
        if str(m.get('status', '0')) != '0' or m.get('banned'):
            continue
        spec = m.get('specifier', '') or ''
        if not spec_ok(spec):
            continue                            # parameter we cannot interpret
        avail = set(home_rec.quantities()) | set(away_rec.quantities())
        quantity, period, side = parse_market(name, avail, teams)
        # the period picks which variant of that quantity to read
        if period == 'ft':
            qkey = quantity
        elif quantity == 'goals':
            qkey = period                       # goals keep their h1 / h2 names
        else:
            qkey = f'{quantity}_{period}'       # e.g. corners_h1
        if qkey not in avail:
            continue                            # this match has no such series
        # STAT MARKETS ONLY (11 Aug). M0347R - corners, fouls and offsides, not
        # one goal leg - went 8 from 8, while the two codes from the same board
        # built on goals went 77% and 75%. This was first written as a ranking
        # preference, which did nothing: most fixtures carry no stat market, so
        # goals still filled the slips and the newly-countable "To Score In Both
        # Halves / No" took nine of slip 1's seventeen legs off a 73% base rate.
        # A preference cannot restrict a board this thin - it has to be a gate.
        # ...but goals come back as FILLER (12 Aug). Stat-only capped the board at
        # ~9% of fixtures - 271 matches over three days yielded 25 legs - because
        # SportyBet prices corners on roughly one fixture in ten and Flashscore
        # carries the series on about half. Goals are on every fixture. They stay
        # BELOW every stat market in the sort (see is_goal), so a match that
        # offers corners still leads with corners and goals only fill the ranks
        # nothing else can reach. Team goal UNDERS only: goals-Over is banned
        # below (50%), match goal totals die on the primary-is-None rule (56%),
        # and both_halves stays out entirely - it was never settled once and its
        # base rate is 73%, so a 6/7 tally says almost nothing.
        if qkey == 'both_halves':
            continue
        # Match SoT totals only. A match line of 10.5 aggregates ~20 shots and
        # holds; a TEAM line of 4.5 aggregates ~4, where one extra shot decides
        # it - Botev Plovdiv Away SoT Under 4.5 off [2, 4, 3] duly lost while
        # both match-total SoT legs on the same board won.
        if quantity == 'sot' and side in ('home', 'away'):
            continue
        for o in (m.get('outcomes') or []):
            if not o.get('isActive', 1):
                continue
            try:    odds = float(o.get('odds'))
            except (TypeError, ValueError):
                continue
            if odds < min_odds or (max_odds and odds > max_odds):
                continue
            d = (o.get('desc') or '').strip().lower()
            # Goals-Over blacklisted 7 Aug: 8W-8L (50%) across 12 codes, and six
            # of the eight losses were the team failing to score at all. It is
            # the "needs a goal" class wearing its third name, after Clean Sheet
            # and Multigoals. Corner overs are NOT touched - they ran 20W-4L.
            if qkey in GOAL_FAMILY and d.startswith('over'):
                continue
            # Team-corner Overs blacklisted 11 Aug. Both booked that day lost:
            # "1H Away Corners Over 0.5" needed Brann to win one corner in the
            # half and they won none, "Away Corners Over 1.5" needed Sparta to
            # win two in a 3-0 defeat. Same class as goals-Over and the clean
            # sheets - a bet that needs an event to HAPPEN, which is the side
            # that keeps failing (goal-dependent legs 76% against 84%). MATCH
            # corner totals are untouched: they aggregate both teams and the one
            # booked that day, Fluminense Over 6.5, won.
            if quantity == 'corners' and side in ('home', 'away') and d.startswith('over'):
                continue
            # NO PRICE BAND ON UNDERS. A 1.10-1.19 band was added 8 Aug off a
            # measurement that said those legs went 28W-1L, and REMOVED 11 Aug
            # because the measurement was a selection effect. Those legs had been
            # chosen by TALLY and merely happened to price there; filtering ON
            # price picks a different set - the narrowest line on every match,
            # because a wider line is always cheaper. It booked Norrby 1H Under
            # 1.5 @1.11, Radomlje Under 2.5 @1.10, Toronto Under 2.5 @1.13, all
            # sitting on the floor of the band, and six of that board's ten
            # losses would have won one line further out. Do not re-add it.
            test = parse_outcome(name, o.get('desc'), spec, avail, teams)
            if test is None:
                continue
            c = count_outcome(home_rec, away_rec, qkey, side, test)
            if not c:
                continue
            hh, nh, ha, na, primary = c
            if primary is None and qkey in GOAL_FAMILY and not _symmetric(test):
                # Nothing a team's own series can answer, on a GOAL quantity:
                # Double Chance, Draw No Bet, 1X2, GG/NG, match goal totals,
                # "Draw Or GG/NG". Both teams can sit at 7/7 and the bet still
                # dies, because it settles on the RESULT, not on either team's
                # count - Keflavik and KA were both 7/7 on scoring, GG/NG Yes was
                # booked, KA failed to score. Measured 5 Aug: 56-60% for -24% ROI
                # against 79% / +15% for markets one team's record settles.
                #
                # NARROWED 8 Aug to goal quantities only. The original rule swept
                # up match totals on STAT quantities too, which was never measured
                # - it just inherited the goals verdict. A match goal total turns
                # on two or three events and one blow-up ruins it; a match SoT
                # total of 10.5 aggregates ~20 events and regresses far harder,
                # so the variance that makes goal totals unbettable is what makes
                # SoT totals stable. User's own 22 SoT match-total unders went
                # 19W-3L (86%, +18.8% ROI), all of which this rule had blocked.
                continue
            if primary is None:
                # Stat match total (SoT, corners, shots, offsides): no side is
                # primary, so BOTH teams' records still have to clear the bar.
                if hh / nh < MIN_HITS or ha / na < MIN_HITS:
                    continue
            else:
                # team market - only the team the bet settles on must clear it
                ph, pn = (hh, nh) if primary == 0 else (ha, na)
                if ph / pn < MIN_HITS:
                    continue
            # A bet that dies when nobody scores needs a perfect side behind it.
            # Probing the predicate at 0-0 separates the two classes without any
            # market list. Measured over 260 settled legs:
            #     survives 0-0  82-92% at every tally strength
            #     dies to 0-0   100% both sides perfect, 77% one side,
            #                    62% neither  (-19.6% ROI, n=47)
            # The 80% gate passes [6/7+6/7], which IS the 62% bucket - Blooming
            # v Always Ready finished 0-0 and took "Home Clean Sheet / No" with
            # it, while "Blooming Under 2.5" on the same game paid.
            try:
                dies_goalless = not test(0, 0)
            except Exception:
                dies_goalless = False
            if dies_goalless and hh < nh and ha < na:
                continue
            # Laplace-smoothed, so a 3-game 3/3 does not outrank a 7-game 7/7
            # and nothing is ever reported as a certainty.
            # The opponent's record still counts toward the rate, so a pick
            # both sides agree on outranks one carried by a single team - it is
            # allowed through, it just ranks lower.
            # LIKELIHOOD, not history. The tally stays on the leg as evidence
            # but no longer decides: a 7/7 that the model reads as 89% is not a
            # certainty, and a 100% tally the book prices at 1.01 is not an edge.
            mp = model_prob(home_rec, away_rec, qkey, side, test)
            if mp is None:
                continue
            implied = 1.0 / odds
            if mp < MIN_PROB or (mp - implied) < MIN_EDGE:
                continue
            rate = mp
            edge = mp - implied
            out.append({
                'market': name, 'outcome': o.get('desc'), 'odds': odds,
                'rate': rate, 'tally': f"{hh}/{nh}+{ha}/{na}",
                'n': nh + na, 'mid': m.get('id'), 'spec': spec, 'edge': edge,
                'oid': o.get('id'), 'test': test, 'qkey': qkey, 'side': side,
                'is_goal': qkey in GOAL_FAMILY or qkey == 'both_halves',
            })
    # Best-supported first, ties to the larger sample, then to the CHEAPER
    # price. That last term used to prefer the bigger price, which inside a
    # family means the tightest line: "Inter Miami Under 3.5" and "Under 4.5"
    # both tallied 3/3+3/3, the tie went to the dearer one, and Miami scored 4.
    # Four of eight losses on 6 Aug would have won one line further out -
    # Under 3.5 -> 4.5, Multigoals 1-2 -> 1-3, 2H Under 0.5 -> 1.5,
    # Over 1.5 -> 0.5. When the evidence cannot separate two lines, take the
    # one with more room.
    # STAT MARKETS FIRST. M0347R was corners, fouls and offsides with not one
    # goal leg on it and went 8 from 8, while the two codes from the same board
    # built mostly on goals went 77% and 75%. A goal market is now only reached
    # when the match offers no stat market that clears the gates.
    out.sort(key=lambda x: (x['is_goal'], -x['rate'], -x['n'], x['odds']))
    return out


from math import exp as _exp, factorial as _fact

# Scoreline weights for comparing two bets, roughly a league-average match.
_SCORE_W = {(f, a): (_exp(-1.35) * 1.35 ** f / _fact(f)) *
                    (_exp(-1.35) * 1.35 ** a / _fact(a))
            for f in range(7) for a in range(7)}
MAX_OVERLAP = 0.70
GOAL_FAMILY = {'goals', 'h1', 'h2'}


def overlap(t1, t2):
    """How often two predicates return the same verdict, weighted by how likely
    each scoreline is. Two bets that agree on nearly every plausible score are
    the same bet under different names."""
    s = w = 0.0
    for (f, a), p in _SCORE_W.items():
        w += p
        try:
            s += p * (bool(t1(f, a)) == bool(t2(f, a)))
        except Exception:
            return 1.0                       # cannot compare - assume duplicate
    return s / w if w else 1.0


def _match_frame(rec):
    """The candidate's predicate rewritten from the HOME side of the fixture.

    Two picks can be the SAME event and still look unrelated to overlap(),
    because each predicate is written from the side that settles it. "Cavalier
    Over 0.5" is `f > 0.5` read against the AWAY record; "Cibao Clean Sheet /
    No" is `a > 0` read against the HOME record. Compared as written they agree
    on 62% of scorelines - under the 70% guard - so both were booked, at the
    same 1.53, on two different slips, and the 0-0 killed both. Mirrored into
    one frame they agree on 100%. Same story for "2H Tulsa Under 0.5" against
    "2H Away Clean Sheet / Yes" the same night."""
    t = rec['test']
    if rec.get('side') == 'away':
        return lambda f, a: t(a, f)
    return t


def _base_quantity(qk):
    """'corners_h1' -> 'corners', 'h2' -> 'goals'. Period variants of one
    quantity are the same underlying thing and must be compared as such."""
    if qk in GOAL_FAMILY or qk == 'both_halves':
        return 'goals'
    return qk[:-3] if qk.endswith(('_h1', '_h2')) else qk


def correlated(cand, chosen):
    """True when this candidate is effectively a repeat of one already taken.

    Viitorul Cluj v SCM Zalau produced "Under 3.5", "No Draw Both Teams To
    Score / No" and "2nd Half Away Clean Sheet / Yes" as its three picks - three
    markets, one claim: a low-scoring game where somebody fails to score. They
    agreed on 74% of likely scorelines, went onto three different slips as if
    they were independent, and went wrong together. Comparing only market NAMES
    cannot see that; comparing what the bets actually pay on does."""
    for c in chosen:
        if _base_quantity(cand['qkey']) != _base_quantity(c['qkey']):
            continue                          # corners vs goals: different bets
        # SAME quantity on the SAME team is one claim wearing two lines, whatever
        # the period. Klaksvik went onto slip 1 as "1H Away Corners Over 1.5" and
        # slip 2 as "Away Corners Over 4.5" - both are "this team wins corners",
        # both off the same 3/7 record - because qkey was 'corners_h1' against
        # 'corners' and the old family test never compared them at all.
        # The overlap() fallback cannot catch it either: its scoreline grid is a
        # Poisson GOALS grid over 0-6, which says nothing about a corner line at
        # 4.5. For anything that is not goals, the structural test is the only
        # one that means anything.
        if cand.get('side') == c.get('side'):
            return True
        if overlap(_match_frame(cand), _match_frame(c)) >= MAX_OVERLAP:
            return True
    return False


def best_three(markets, home_rec, away_rec, **kw):
    """The three best-supported outcomes, ONE PER QUANTITY, so a match spreads
    across what it measures instead of repeating one thing three ways.

    This deduped on the market NAME until 11 Aug, which counted "Home Team Total
    Corners", "1st Half - Home Total Corners" and "Away Team Total Corners" as
    three different families - so a match could contribute three corner bets and
    routinely did, and every slip came back as corners end to end. Deduping on
    the quantity instead makes rank 2 reach for offsides, rank 3 for fouls or
    shots. MCY729 was the accidental version of this and was the best code of
    the day at 5 from 6, carrying corners, offsides, fouls, SoT and a match
    total rather than one market repeated.

    It also closes the hedge the correlated() guard cannot see. That blocks the
    same quantity on the same TEAM; this blocks it on either team. Apollon v
    Brann lost a leg on all three slips, two of them opposite sides of the same
    first-half corner count - one needed Brann to win a corner, the other needed
    Apollon not to win three, and a one-sided half beat both."""
    seen, picked = set(), []
    for r in evaluate(markets, home_rec, away_rec, **kw):
        if not plausible(r):
            continue                    # market flatly disagrees - take the next
        fam = _base_quantity(r['qkey'])
        if fam in seen:
            continue
        if correlated(r, picked):
            continue                          # same bet as one already taken
        seen.add(fam)
        picked.append(r)
        if len(picked) >= TOP_N:
            break
    return picked


# ─────────────────────────────────────────────────────────────────────────────
# Live data: build both Records straight from Flashscore
# ─────────────────────────────────────────────────────────────────────────────
def records_for(fixture_id, raw=None):
    """(home_record, away_record) for a Flashscore fixture id.

    Home team's HOME games and away team's AWAY games, most recent first, cut
    at any season break. EVERY series the fetcher produced is carried through -
    goals, both halves, and every stat that league publishes, in whatever
    combination exists for this match. Nothing is selected in advance."""
    import acca as A
    import fetcher_v2 as F2
    import fetcher_v3 as F3

    raw = raw or F2.fetch(f"df_hh_1_{fixture_id}")
    if not raw:
        return None, None
    hr, ar, _h2h = A.recent_kc(raw)
    if not hr or not ar:
        return None, None
    hr = F3.trim_at_season_gap(hr)[:F3.RECENT_WINDOW]
    ar = F3.trim_at_season_gap(ar)[:F3.RECENT_WINDOW]
    rows = {'home': [r for r in hr if r.get('ks') == 'home'],
            'away': [r for r in ar if r.get('ks') == 'away']}

    try:
        _, _, rich = F3.fetch_rich_history(fixture_id, raw=raw)
    except Exception:
        rich = {}

    def build(side):
        ser = {}
        # goals, taken from the deep list so 1H / 2H / FT stay index-aligned
        ft = (rich.get(f'{side}_ft_gf_series'), rich.get(f'{side}_ft_ga_series'))
        if ft[0] and ft[1]:
            ser['goals'] = ft
        else:
            ser['goals'] = ([r.get('gf', 0) for r in rows[side]],
                            [r.get('ga', 0) for r in rows[side]])
        ser['h1'] = (rich.get(f'{side}_ht_gf_series'), rich.get(f'{side}_ht_ga_series'))
        ser['h2'] = (rich.get(f'{side}_2h_gf_series'), rich.get(f'{side}_2h_ga_series'))
        # "to score in both halves" is not answerable from any series above: the
        # full-time count says a team scored, not that it scored twice in the
        # right places. parse_outcome read it as plain `f > 0` on FT goals, so
        # the tally behind two booked legs measured the wrong event entirely.
        # The MIN of the two halves is exactly the quantity the market is about -
        # min(h1, h2) > 0 is true only when both halves had a goal - so derive it
        # and let the ordinary counting machinery answer it.
        h1f, h1a = ser['h1']
        h2f, h2a = ser['h2']
        if h1f and h2f and h1a and h2a:
            n = min(len(h1f), len(h2f), len(h1a), len(h2a))
            ser['both_halves'] = ([min(h1f[i], h2f[i]) for i in range(n)],
                                  [min(h1a[i], h2a[i]) for i in range(n)])
            # Markets that compare the two halves rather than count one of them.
            # SportyBet's own marketGuide defines each; the trick in every case is
            # to derive the quantity the market is actually about, so the ordinary
            # counter can answer it with no special case downstream.
            #
            #   Highest Scoring Half  "Predict in which Half most goals will be
            #   scored" -> (1st-half count, 2nd-half count) IS a (for, against)
            #   pair: "1st half" is f > a, "2nd half" is f < a, "Equal" is f == a.
            ser['hsh'] = ([h1f[i] for i in range(n)], [h2f[i] for i in range(n)])
            ser['hsh_match'] = ([h1f[i] + h1a[i] for i in range(n)],
                                [h2f[i] + h2a[i] for i in range(n)])
            #   Both Halves Over/Under X  "the number of goals scored in EACH
            #   half is over/under the line" -> the binding half is the min for
            #   Over and the max for Under, so carry both.
            ser['bh_line'] = ([min(h1f[i] + h1a[i], h2f[i] + h2a[i]) for i in range(n)],
                              [max(h1f[i] + h1a[i], h2f[i] + h2a[i]) for i in range(n)])
            #   Team to Win Both Halves  "will X score more goals than Y in both
            #   halves" -> a yes/no per game, and the mirror for the opponent.
            #   To Score N or More Goals in a Row - needs the ORDER goals went
            #   in, which Flashscore does carry (df_sui gives every goal's team
            #   and minute) and fetcher_v3 now emits as a per-game longest-run
            #   series. Lyon 3-0 Sparta was H20 -> H66 -> H72, a run of three; a
            #   3-0 built H-A-H-H is only a run of two, and the goal COUNT cannot
            #   tell those apart. Extra-time goals are excluded upstream.
            rf, ra = (rich.get(f'{side}_run_gf_series'), rich.get(f'{side}_run_ga_series'))
            if rf and ra:
                ser['run'] = (rf, ra)
            ser['win_both'] = (
                [1 if (h1f[i] > h1a[i] and h2f[i] > h2a[i]) else 0 for i in range(n)],
                [1 if (h1a[i] > h1f[i] and h2a[i] > h2f[i]) else 0 for i in range(n)])
        # every stat this league actually publishes, full match and per half
        for key, slot in (rich.get(f'{side}_stats') or {}).items():
            if not isinstance(slot, dict):
                continue
            ser[key]          = (slot.get('series_for'),      slot.get('series_against'))
            ser[key + '_h1']  = (slot.get('1h_series_for'),   slot.get('1h_series_against'))
            ser[key + '_h2']  = (slot.get('2h_series_for'),   slot.get('2h_series_against'))
        return Record(ser)

    return build('home'), build('away')


def summary(rec):
    """Which quantities this record can actually answer, and on how many games."""
    return {q: len(rec.series[q][0]) for q in rec.quantities()}


# ─────────────────────────────────────────────────────────────────────────────
# Joining SportyBet events to Flashscore fixtures
# ─────────────────────────────────────────────────────────────────────────────
JOIN_WINDOW_S = 15 * 60     # kickoffs must agree to within this
JOIN_MIN_SCORE = 0.34       # share of tokens that must overlap, per side


def _score(a, b):
    """Token overlap between two team names, 0..1, tolerant of the longer name
    carrying extra words: SportyBet writes "Clube do Remo PA", Flashscore "Remo"."""
    import sportybet as SB
    ta, tb = SB.toks(a), SB.toks(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


def join(events, fixtures, ev_time, fx_time):
    """[(event, fixture, score)] - each fixture used at most once.

    Exact token-set equality matched 7 of 32: it cannot survive "AD Pasto" vs
    "Dep. Pasto" or "Antigua GFC" vs "Antigua (Gtm)". Kickoff time does most of
    the work here and the names only have to corroborate it."""
    out, used = [], set()
    for ev in events:
        ets = ev_time(ev)
        best = None
        for i, fx in enumerate(fixtures):
            if i in used:
                continue
            if abs((fx_time(fx) - ets).total_seconds()) > JOIN_WINDOW_S:
                continue
            sh = _score(ev.get('homeTeamName', ''), fx['home'])
            sa = _score(ev.get('awayTeamName', ''), fx['away'])
            if sh < JOIN_MIN_SCORE or sa < JOIN_MIN_SCORE:
                continue
            s = sh + sa
            if best is None or s > best[0]:
                best = (s, i, fx)
        if best:
            used.add(best[1])
            out.append((ev, best[2], best[0] / 2))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Sanity: refuse picks the market flatly contradicts
# ─────────────────────────────────────────────────────────────────────────────
MAX_DISAGREE = 0.25


def plausible(rec):
    """A count that wildly outruns the price is usually a counting error, not an
    edge: "Away To Score In Both Halves" counted 4/5 while the book priced it at
    6.4 (16%). Skip it and take the next candidate rather than dropping the match."""
    implied = 1.0 / rec['odds']
    return (rec['rate'] - implied) <= MAX_DISAGREE
