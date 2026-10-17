#!/usr/bin/env python3
"""EVERY MATCH WITH STATS, PREDICTED FROM BOTH TEAMS' STATS - READ AGAINST WHO THEY PLAYED.

  python3 predict_stats.py [--until HH] [--days N] [--floor 1.15] [--rule all|stats|both|price]
                           [--book] [--best N] [--json out.json] [--lead MIN]

13 Oct, user: "every match that has stats must be predicted according to the stats", then,
after the first version lost four legs the same night: "Why didn't you take into account the
opponent stats? ... if you consider both teams stats and take them into account it would have
been better". The first version counted each side's own games at face value (four columns:
home side's last 7 home, away side's last 7 away, each side's last 10; support = the lowest
rate). Austria W's quiet home second halves came against Germany, Norway and the Netherlands,
Kosovo W's tight away games against Gibraltar, Bulgaria and Latvia; both columns said 7/7 for
"Austria 2nd-half goals Under 2.5" and it lost 4-0. A count cannot see who the games were
against, and the highest of ~200 small-sample counts per game is a fluke more often than not.

Now (ratings.py):
  1. RATINGS   every team has an attack and a defence number, fitted on ~100k games from every
               head-to-head feed fetched (stored by Flashscore team id, so leagues, cups,
               continental games and play-offs join into one table). A team's goals count
               against the defence it scored them on; recent games count more.
  2. MATCHUP   expected goals for each side = base x home edge x its attack / the other side's
               defence, split into halves by the first-half share of goals in each side's games.
  3. CHANCES   every goal and result option SportyBet offers is read off the joint chance of the
               four half-scores: 1X2, double chance, draw no bet, match / team / half totals,
               GG/NG, win to nil, clean sheet, to score in both halves, highest-scoring half,
               win both halves. Cards, corners and shots have no tested model and are not picked.
  4. PICK      --rule all    (default, 14 Oct) the option with the highest LOWEST of (four-column
                             count, stats chance, book's chance): every stat has to back it. Cards,
                             corners and shots come back here, read from the counts and the book,
                             only when all four columns hold STAT_MIN+ games of that stat.
               --rule stats  the option the stats rate most likely, priced --floor or more
               --rule both   the option with the highest LOWER of (stats chance, book's chance):
                             the stats and the price both have to back it
               --rule price  the option the book rates most likely (for comparison)

14 Oct, after Z2RWYA (rule both) lost Athletic MG v Londrina 1H GG/NG No - stats 81%, book 82%,
but both sides' venue records had both teams scoring in the first half in 3 of 7 - the user:
"I told you to use every stats ... you left out things", and chose "Every stat must back it".
What that rule tested at, before it was built:
    football-data 2019-26, one pick per game at about 1.16+    won      book said
      both: min(stats, book)                                 81.9 / 82.0   81.8
      all:  min(counts, stats, book)                         79.7 / 80.4   79.8
    12-13 Oct live legs (536, records cut at kick-off): legs whose count reached the price won
    76% (book 80%), legs below it 81% (book 83%). Card legs: under 7 games of card data in a
    column won 4/10 (book 77%), 7+ games 19/20 (book 85%) - one day, 30 legs.

14 Oct afternoon: QGVT0F lost Kashiwa Reysol v Port FC (AFC Champions League), Kashiwa 1st-half goals
Under 2.5 @1.16 - 5-0 at half-time. Counts 100% (Kashiwa's last 7 home games all against Japanese
sides, Port's last 7 away all against Thai and Indonesian sides), ratings 97% (1.86 v 1.66 expected
goals: the store held 7 Japan v Thailand club games in a year against 1,279 Japan-only and 556
Thai-only, so each league was rated against itself), book 79% (3.05 v 0.83). Measured, Sep 2025 -
Oct 2026, 1,733 games between clubs from different countries v 4,495 domestic:
    options at 90%+          cross-country          domestic
      counts                 said 93.1, won 87.6    said 93.7, won 91.9
      ratings                said 93.1, won 88.8    said 93.5, won 91.5
The user chose: no pick on a game whose two sides play in different leagues (different_leagues) -
continental cups and cup ties between divisions. Not a win-rate test: server legs in continental
cups won 43/49 (book 83%) 6-13 Oct; the rule removes the games the counts and ratings can't read.

14 Oct evening: NA7N0Y lost Tropical Coriano v Pontedera, Under 2.5 @1.69, booked at 29% - Coriano's
games said Under (9/10), Pontedera's said Over (5/7 away), and the old code took the least-bad option
on a game where no option was agreed. The user: "for every match, first fetch the option available
for that match on sporty, then for every option check if the stats agree, if they dont move to the
next option until all the options are done ... if there is no option then skip the match". Now:
  - every option on the game's SportyBet event page is read (about 1,000 markets: corners, bookings,
    minutes, combos, no-bet markets), merged with the board fetch (full_markets);
  - an option is AGREED when every read is MIN_AGREE (70%) or more: each of the four columns (so at
    least 5/7 and 7/10), the ratings' chance and the book's chance;
  - the game's pick is the agreed option with the highest lowest read; no agreed option, no leg.

14 Oct night: the audit of Y8M37R (booked 16:14) flagged 13 of its 30 legs on things the picker never
looked at, and the legs were already on the code. The user: "why flag it and still pick that option",
then "make the flags part of the pick rule". Now an agreed option must also pass flags():
  - FORM      no column lost 2 of its 3 most recent games, and the two sides' most recent games did not both lose;
  - H2H       full-time goal options: the last 10 meetings back it MIN_AGREE+ (3+ meetings needed for a read);
  - BLOWOUT   Under lines: no column holds 2+ games that went BLOWOUT (3) or more past the line;
and a game is not predicted when a side's columns hold league games from another division (old_division:
promoted and relegated sides). On Y8M37R's 30 legs: 6 games out for old-division games, 6 picks stopped
by a flag (the game moves to its next option), 18 pass. Not a win-rate test.

14 Oct night, later: Verl v Regensburg (HT Away win to nil No) killed GMGCTJ, Y8M37R and LHN1NP, and the picks
kept landing on the same few "No" options priced 1.15-1.20 - the book was the lowest read on 78 of 115 picks, so
the pick was in effect the shortest price over 1.15. The user: "leave this percent of a thing", "if you go through
each match you would see which half goals mostly comes", and chose:
  - rule 'games' (default): ONLY the Flashscore games decide - no ratings, no book chance. An option passes when it
    happened in MOST of every column's games (more than half: 4 of 7, 6 of 10, both teams) and no flag fails
    (head-to-head: most meetings); the pick is the option with the most games behind it, then the longer price;
  - BLOCKED, never picked: win to nil, GG/NG, clean sheet, win both halves, double chance, no draw, multigoals.
Found the same evening: the away side's columns were counted with the halves swapped for Highest Scoring Half and
Both Halves Over/Under (dynamic_v4.oriented), and "Home/Away Highest Scoring Half" was read on both teams' goals.

Tested on football-data 2019-26, 26,326 games in 22 divisions, ~100 options a game, leak-free
(scratchpad opp/bt2.py, ana2.py, ana3.py). Pick per game among options priced about 1.15+:
    four-column counts (the first version)   won 79.7% / 80.3%   (2019-22 / 2023-26)
    stats (opponent-rated)                   won 81.3% / 81.5%
    both must back it                        won 81.9% / 82.0%
    price                                    won 83.2% / 82.6%
The stats model is calibrated (said 85-90% -> won 87.1%); the counts were not (said 100% -> 95.6%).
"""
import sys, math, re, collections, datetime as dt
import os as _o; sys.path.insert(0, _o.path.dirname(_o.path.abspath(__file__)))
import acca as A
import book_v3 as B
import dynamic_v4 as D
import fetcher_v2 as F2
import fetcher_v3 as F3
import ratings as R

FLOOR = 2.00                 # 14 Oct night, user: "I don't want small odds anymore, only big odds" - chose 2.00+
MAX_LEGS = 50
RULE = 'games'               # 14 Oct night, user: "leave this percent" - the Flashscore games decide (rule 'games')
STAT_MIN = 7                 # cards/corners/shots: games of that stat needed in EACH of the four columns
SKIP_NAMES = ('odd/even', 'odd or even')
# 14 Oct night, user's blocked options (after Colon Reserve's HT Away win to nil No): never picked by any rule
BLOCKED = ('win to nil', 'gg/ng', 'clean sheet', 'win both halves', 'double chance', 'no draw', 'multigoal',
           'no bet')                # Draw/Home/Away No Bet: a refund pays nothing - user: "this doesn't bring big odds"
# 14 Oct: no pick when the two sides play in different leagues (user's choice after Kashiwa v Port FC)
CONTINENTAL = {'world', 'europe', 'asia', 'africa', 'south america', 'north & central america', 'australia & oceania'}
LEAGUE_GAMES = 10            # a side's league = the competition of most of its last 10 home-country games
LEAGUE_HERE = 3              # a domestic game is a league game for a side with 3+ of its last 10 in it
# 14 Oct, user: "for every match, first fetch the option available for that match on sporty, then for every
# option check if the stats agree, if they dont move to the next option until all the options are done ...
# if there is no option then skip the match". The stats AGREE on an option when every read is at MIN_AGREE or
# more: each of the four columns, the ratings' chance (goal markets) and the book's chance.
MIN_AGREE = 0.70
EARLY_PAYOUT = range(60000, 61000)   # 1UP / 2UP / Never Down / GG/NG 2+: settle differently from the plain market
# 14 Oct, user, after the Y8M37R audit: "make the flags part of the pick rule" (see flags(), old_division())
H2H_GAMES = 10               # meetings read
H2H_MIN = 3                  # fewer meetings than this: no head-to-head read
BLOWOUT = 3                  # an Under line's miss counts as a blowout this many goals (or more) past the line
EVENT_URL = 'https://www.sportybet.com/api/ng/factsCenter/event?eventId={}&productId=3'


def book_prices(ev):
    """(home, draw, away, over 2.5, under 2.5) from the event's own markets, None where missing."""
    x, ou = {}, {}
    for m in ev.get('markets') or []:
        outs = {o.get('desc'): float(o['odds']) for o in m.get('outcomes') or [] if o.get('odds') and o.get('isActive', 1)}
        if str(m.get('id')) == '1' and not m.get('specifier'):
            x = outs
        elif str(m.get('id')) == '18' and (m.get('specifier') or '') == 'total=2.5':
            ou = outs
    return x.get('Home'), x.get('Draw'), x.get('Away'), ou.get('Over 2.5'), ou.get('Under 2.5')


def side_share(rec):
    """First-half share of the goals in this side's games (its deep half scores), pulled to average."""
    h1 = rec.pairs('h1') if 'h1' in rec.quantities() else []
    g = rec.pairs('goals')
    return R.half_share([p[0] for p in h1], [p[1] for p in h1], [p[0] for p in g], [p[1] for p in g])


def matchup(f, ev, h10, a10, model):
    """The game's grid and what it was built from: stats expected goals (opponent ratings),
    the book's expected goals (its 1X2 and Over/Under 2.5 prices) and the two sides' half shares."""
    _, ids = R.feed_games(F2.fetch(f"df_hh_1_{f['id']}"))
    names = list(ids)[:2]
    if len(names) < 2 or not model:
        return None
    hid, aid = ids[names[0]], ids[names[1]]
    if hid not in model.ix or aid not in model.ix:
        return None
    stats = model.expect(hid, aid)
    p = book_prices(ev)
    book = R.market_lam(*p) if all(p[:3]) else None
    sh, sa = side_share(h10), side_share(a10)
    return dict(stats=stats, book=book, share=(sh, sa), grid=R.Grid(stats[0], stats[1], sh, sa),
                rating=(model.rating(hid), model.rating(aid)))


def counts_rate(cv, ct):
    """Lowest of the four column rates (home side's last 7 home, away side's last 7 away, each
    side's last 10), or None when a column is missing or empty."""
    if not (cv and ct) or not all((cv[1], cv[3], ct[1], ct[3])):
        return None
    return min(cv[0] / cv[1], cv[2] / cv[3], ct[0] / ct[1], ct[2] / ct[3])


def full_markets(ev):
    """Every market SportyBet offers on the event: its event page (about 1,000 markets - corners, bookings,
    minutes, combos and no-bet markets the board fetch never asks for) merged with the board's own list."""
    import json, urllib.request
    try:
        page = json.loads(urllib.request.urlopen(urllib.request.Request(EVENT_URL.format(ev['eventId']), headers=A.HDRS),
                                                 timeout=25).read().decode('utf-8', 'replace'))['data']['markets'] or []
    except Exception:
        return ev
    seen = {(str(m.get('id')), m.get('specifier') or '') for m in ev.get('markets') or []}
    ev['markets'] = list(ev.get('markets') or []) + [m for m in page if (str(m.get('id')), m.get('specifier') or '') not in seen]
    return ev


CUP = re.compile(r'\b(cup|copa|coppa|coupe|pokal|ta[c\u00e7]a|trophy|trofeo|shield|beker|kupa|puchar|cupa|pohar|kubok|'
                 r'super ?cup|supercopa|federaci[o\u00f3]n)\b', re.I)
CUP_NAMED_LEAGUES = {'copa de primera', 'copa pacena'}     # Paraguay's top flight, Bolivia's La Paz league


NAMED_CUPS = {('canada', 'championship')}                   # the Canadian Championship: MLS sides' domestic cup


def is_cup(comp, country=''):
    c = (comp or '').strip().lower()
    return (bool(CUP.search(c)) and c not in CUP_NAMED_LEAGUES) or ((country or '').strip().lower(), c) in NAMED_CUPS


def different_leagues(f):
    """(home side, away side) descriptions when the two sides do not play in the same league, else None.
    The counts only hold each side's games in its own league, and the ratings rate a league mostly
    against itself, so neither can see the gap between two leagues (14 Oct: both read Kashiwa v Port FC
    as close; the book had Kashiwa 3.05 v 0.83 goals).
      - A LEAGUE game (domestic, not a cup) counts when both sides have LEAGUE_HERE+ of their last
        LEAGUE_GAMES in it; a side new to the league (promoted, relegated) brings another division's record.
      - A CUP or continental game counts only when both sides' CURRENT league - the competition of
        their most recent home-country league game - is the same. 14 Oct evening, two holes fixed:
        Ethnikos Achnas (relegated: 3 second-division games, 7 old top-flight ones in their last 10) were
        read as top-flight by "most games in the last 10", and Copa Federacion ties between different
        Segunda RFEF groups were read as league games because both sides had played 3 cup games."""
    games, ids = R.feed_games(F2.fetch(f"df_hh_1_{f['id']}"))
    names = list(ids)[:2]
    if len(names) < 2:
        return None
    country, _, comp = (f.get('league') or '').partition(':')
    country, comp = country.strip().lower(), comp.strip().lower()
    league_game = country not in CONTINENTAL and not is_cup(comp)
    here = lambda g: (g[6] or '').lower() == country and (comp == g[5].lower() or comp.startswith(g[5].lower() + ' - '))
    current, in_this = [], []
    for name in names:
        tid = ids[name]
        rows = sorted((g for g in games.values() if tid in (g[1], g[2]) and g[0] < f['ts'] - 3600
                       and not R.is_friendly(g[5], g[6])), key=lambda g: -g[0])[:LEAGUE_GAMES]
        lg = next(((g[6], g[5]) for g in rows if (g[6] or '').lower() not in CONTINENTAL and not is_cup(g[5], g[6])), None)
        current.append(lg)
        in_this.append(sum(1 for g in rows if g[5] and here(g)))
    label = lambda l: f'{l[1]} ({l[0]})' if l else 'no league games'
    if league_game:
        if min(in_this) >= LEAGUE_HERE:
            return None
        return tuple(f"{label(l)}, {n} of last {LEAGUE_GAMES} in this league" for l, n in zip(current, in_this))
    if current[0] == current[1]:
        return None
    return label(current[0]), label(current[1])


def _division(g):
    """(country, league) of a domestic league game, the stage dropped ('Liga Pro - Clausura' is 'liga pro');
    None for cups, continental games and friendlies."""
    country, comp = (g[6] or '').lower(), (g[5] or '').lower()
    if not comp or country in CONTINENTAL or is_cup(comp, country) or R.is_friendly(g[5], g[6]):
        return None
    return country, comp.split(' - ')[0].strip()


def old_division(f):
    """A description per side whose four columns hold league games from a division it no longer plays in
    (promoted or relegated), else None. 14 Oct, user, after Y8M37R's audit flagged six such games and the
    picker booked them anyway: "make the flags part of the pick rule". Al-Okhdood (relegated) had 3 of
    their 7 away games in last season's Saudi Pro League - one of the two misses was 4-0 at Al Ahli - and
    the counts read them as games in the league they play now. A side's league now = the division of its
    most recent league game; the columns = its last 7 games at this venue and its last 10."""
    games, ids = R.feed_games(F2.fetch(f"df_hh_1_{f['id']}"))
    names = list(ids)[:2]
    if len(names) < 2:
        return None
    out = []
    for name, venue in zip(names, ('home', 'away')):
        tid = ids[name]
        rows = sorted((g for g in games.values() if tid in (g[1], g[2]) and g[0] < f['ts'] - 3600
                       and not R.is_friendly(g[5], g[6])), key=lambda g: -g[0])
        now = next((_division(g) for g in rows if _division(g)), None)
        at = [g for g in rows if (g[1] == tid) == (venue == 'home')][:F3.MAX_DEEP_GAMES]
        cols = {id(g): g for g in at + rows[:F3.OVERALL_GAMES]}.values()
        old = [g for g in cols if _division(g) and _division(g) != now]
        if old:
            comps = collections.Counter(g[5] for g in old)
            out.append(f"{name}: {len(old)} game{'s' if len(old) > 1 else ''} in "
                       + ', '.join(f'{c} ({n})' for c, n in comps.items()) + f", league now {now[1] if now else '?'}")
    return out or None


def column_hits(hrec, arec, qkey, side, test):
    """Game by game, most recent first, whether the option would have won in each of the two series -
    the same orientation as D.count_outcome."""
    sh, sa = D.oriented(hrec, arec, qkey, side)
    return [(x, y, bool(test(x, y))) for x, y in sh], [(x, y, bool(test(x, y))) for x, y in sa]


def h2h_pairs(raw, home):
    """The last H2H_GAMES meetings as (fixture home's goals, fixture away's goals), most recent first."""
    import sportybet as SB
    th, out = SB.toks(home), []
    for m in A.recent_kc(raw)[2]:
        if SB.toks(m['home']) & th:
            out.append((m['hg'], m['ag']))
        elif SB.toks(m['away']) & th:
            out.append((m['ag'], m['hg']))
        if len(out) == H2H_GAMES:
            break
    return out


def h2h_count(h2h, qkey, side, test):
    """(backed, meetings) for a full-time goal option, or None (no read: other markets, under H2H_MIN meetings)."""
    if not h2h or qkey != 'goals' or len(h2h) < H2H_MIN:
        return None
    return sum(1 for x, y in h2h if (test(y, x) if side == 'away' else test(x, y))), len(h2h)


def flags(cols, desc, spec, qkey, side, test, h2h, majority=False):
    """The audit's flags on one option, as reasons it fails (empty list = it passes). 14 Oct, user: "make the
    flags part of the pick rule" - each was found on a Y8M37R leg after the code was booked:
      FORM      a column lost 2 of its 3 most recent games (Waldhof Mannheim won both halves in 2 of their
                last 3 against Home win both halves No; Cornella's last 2 at home went over 3.5), or both
                sides' most recent game lost (Viktoria Koln 2-5 and Grossaspach 1-5 against Under 5.5);
      H2H       full-time goal options: the last 10 meetings, when there are H2H_MIN+, must back it MIN_AGREE+
                like every other read (Montanesa v L'Escala 3/6 against Under 3.5);
      BLOWOUT   Under lines: a column holding 2+ games that went BLOWOUT+ past the line (Raufoss's away
                column 5/7 for Under 5.5, the two misses 9-1 and 7-2)."""
    why = []
    hits = [[r[2] for r in c] for c in cols]
    if any(sum(1 for x in h[:3] if not x) >= 2 for h in hits):
        why.append('form')
    elif hits[2] and hits[3] and not hits[2][0] and not hits[3][0]:
        why.append('form')
    hh = h2h_count(h2h, qkey, side, test)
    if hh and (2 * hh[0] <= hh[1] if majority else hh[0] / hh[1] < MIN_AGREE):
        why.append('h2h')                    # rule 'games': most meetings must back it, like every column
    line = re.search(r'total=([\d.]+)', spec or '')
    if line and (desc or '').lower().startswith('under'):
        top = float(line.group(1)) + BLOWOUT
        if any(sum(1 for x, y, ok in c if not ok and (x if side in ('home', 'away') else x + y) >= top) >= 2 for c in cols):
            why.append('blowout')
    return why


def score_game(ev, h, a, h10, a10, mu, floor=FLOOR, rule=RULE, tally=None, h2h=None):
    """Every option on the game with its chances, best first by `rule`.
    Rule 'games' (default, 14 Oct night): only the Flashscore games decide - an option passes when it happened in
    MOST of every column's games (more than half: 4 of 7, 6 of 10) and no flag fails; the pick is the one with the
    most games behind it (lowest column first, then all four together), then the longer price. Rule 'all' also reads
    card/corner/shot markets (counts and book only - the ratings are goals) and keeps only the options
    every read agrees on (MIN_AGREE+) and no flag fails (flags()). `tally` (a dict) gets offered /
    readable / agreed counts and how many agreed options each flag stopped. `h2h`: h2h_pairs()."""
    teams = (ev.get('homeTeamName'), ev.get('awayTeamName'))
    avail = set(h.quantities()) | set(a.quantities()) | R.GOAL_QUANTITIES
    out = []
    t = tally if tally is not None else {}
    for k in ('offered', 'readable', 'agreed', 'form', 'h2h', 'blowout'):
        t.setdefault(k, 0)
    for m in ev.get('markets') or []:
        name = m.get('name') or m.get('desc') or ''
        t['offered'] += sum(1 for o in m.get('outcomes') or [] if o.get('isActive', 1) and o.get('odds')
                            and float(o['odds']) >= floor)
        if str(m.get('status', '0')) != '0' or any(x in name.lower() for x in SKIP_NAMES + BLOCKED):
            continue
        try:
            if int(m.get('id')) in EARLY_PAYOUT:
                continue
        except (TypeError, ValueError):
            pass
        spec = m.get('specifier') or ''
        if not D.spec_ok(spec):
            continue
        quantity, period, side = D.parse_market(name, avail, teams)
        qkey = quantity if period == 'ft' else (period if quantity == 'goals' else f'{quantity}_{period}')
        goal = qkey in R.GOAL_QUANTITIES
        if not goal and (rule not in ('all', 'games') or qkey not in h.quantities() or qkey not in h10.quantities()):
            continue                                  # cards, corners, shots: only rules 'all' and 'games' read them
        outs = [o for o in m.get('outcomes') or [] if o.get('isActive', 1) and o.get('odds')]
        try:
            inv = sum(1 / float(o['odds']) for o in outs)
        except (TypeError, ValueError, ZeroDivisionError):
            continue
        for o in outs:
            odds = float(o['odds'])
            if odds < floor:
                continue
            test = D.parse_outcome(name, o.get('desc'), spec, avail, teams)
            if test is None:
                continue
            chance = mu['grid'].chance(qkey, side, test) if goal and mu else None
            if goal and chance is None and rule != 'games':
                continue
            book = (1 / odds) / inv if inv else None
            cv = D.count_outcome(h, a, qkey, side, test) if qkey in h.quantities() else None
            ct = D.count_outcome(h10, a10, qkey, side, test) if qkey in h10.quantities() else None
            tally = (f"HH {cv[0]}/{cv[1]}  AA {cv[2]}/{cv[3]}  H10 {ct[0]}/{ct[1]}  A10 {ct[2]}/{ct[3]}" if cv and ct else 'no count')
            counts = counts_rate(cv, ct)
            if rule == 'games':
                if counts is None or (not goal and min(cv[1], cv[3], ct[1], ct[3]) < STAT_MIN):
                    continue
                t['readable'] += 1
                if not all(2 * hit > n for hit, n in ((cv[0], cv[1]), (cv[2], cv[3]), (ct[0], ct[1]), (ct[2], ct[3]))):
                    continue                          # the games don't mostly back it: next option
                t['agreed'] += 1
                why = flags(column_hits(h, a, qkey, side, test) + column_hits(h10, a10, qkey, side, test),
                            o.get('desc'), spec, qkey, side, test, h2h, majority=True)
                if why:
                    for w in why:
                        t[w] += 1
                    continue
                hh = h2h_count(h2h, qkey, side, test)
                if hh:
                    tally += f"  H2H {hh[0]}/{hh[1]}"
                key = counts
            elif rule == 'all':
                if counts is None or book is None:
                    continue                          # every stat has to back it: no count, no pick
                if not goal and min(cv[1], cv[3], ct[1], ct[3]) < STAT_MIN:
                    continue
                t['readable'] += 1
                key = min(x for x in (counts, chance, book) if x is not None)
                if key < MIN_AGREE:
                    continue                          # a read disagrees: next option
                t['agreed'] += 1
                why = flags(column_hits(h, a, qkey, side, test) + column_hits(h10, a10, qkey, side, test),
                            o.get('desc'), spec, qkey, side, test, h2h)
                if why:
                    for w in why:
                        t[w] += 1
                    continue                          # a flag fails: next option
            else:
                key = chance if rule == 'stats' else (book or 0) if rule == 'price' else min(chance, book or 0)
            out.append(dict(market=name, outcome=o.get('desc'), odds=odds, stats=chance, chance=book, counts=counts,
                            support=key, tally=tally, games=(cv[0] + cv[2] + ct[0] + ct[2]) / (cv[1] + cv[3] + ct[1] + ct[3]) if cv and ct else 0,
                            ids=dict(eventId=ev['eventId'], productId=3, marketId=str(m.get('id')),
                                     specifier=spec or None, outcomeId=str(o.get('id')))))
    if rule == 'games':
        out.sort(key=lambda r: (-r['support'], -r['games'], -r['odds']))
    else:
        out.sort(key=lambda r: (-r['support'], -(r['stats'] or 0)))
    return out


def predict(until_h=23, days=0, floor=FLOOR, rule=RULE, verbose=True, lead_min=60):
    now = dt.datetime.now(A.WAT)
    start = now + dt.timedelta(minutes=lead_min)             # standing rule: an hour out (--lead only when the user asks)
    cut = now.replace(hour=until_h, minute=59, second=59, microsecond=0)
    if cut <= now:
        cut += dt.timedelta(days=1)
    cut += dt.timedelta(days=days)
    evs = [e for e in B.fetch_events_rich()
           if start < dt.datetime.fromtimestamp(int(e.get('estimateStartTime', 0)) / 1000, tz=A.WAT) <= cut
           and 'SRL' not in (e.get('homeTeamName') or '').upper()]
    seen, fx = set(), []
    for off in range(max(2, (cut.date() - now.date()).days) + 1):
        for f in F2.get_fixtures(off):
            if f['id'] not in seen:
                seen.add(f['id']); fx.append(f)
    pairs = D.join(evs, fx, lambda e: dt.datetime.fromtimestamp(int(e['estimateStartTime']) / 1000, tz=A.WAT),
                   lambda f: dt.datetime.fromtimestamp(f['ts'], tz=A.WAT))
    if verbose:
        print(f"window {start:%a %H:%M} -> {cut:%a %H:%M}  |  {len(evs)} games on the board, {len(pairs)} matched to Flashscore", flush=True)
    # pass 1: every game's records (this also fetches each game's head-to-head feed)
    recs, no_stats, other_league, old_div = [], [], [], []
    for ev, f, _ in pairs:
        h, a = D.records_for(f['id'], season=False)
        h10, a10 = D.records_for(f['id'], season=False, overall=True)
        name = f"{ev['homeTeamName']} v {ev['awayTeamName']}"
        if not (h and a and h10 and a10) or min(len(h.pairs('goals')), len(a.pairs('goals')),
                                                len(h10.pairs('goals')), len(a10.pairs('goals'))) < D.MIN_GAMES:
            no_stats.append(name); continue
        diff = different_leagues(f)
        if diff:
            other_league.append(f"{name} ({diff[0]} / {diff[1]})"); continue
        old = old_division(f)
        if old:
            old_div.append(f"{name} ({'; '.join(old)})"); continue
        recs.append((ev, f, name, h, a, h10, a10))
    # the ratings: every game in every feed fetched so far, this run's included
    model = None
    if rule != 'games':                                        # rule 'games' reads the Flashscore games only
        model = R.global_model(R.harvest(), now.timestamp())
        if verbose:
            print(f"ratings: {model.n_games:,} games, {len(model.ix):,} teams", flush=True)
    # every option SportyBet offers on each game, not just the board fetch's market ids
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(6) as pool:
        list(pool.map(lambda r: full_markets(r[0]), recs))
    games = []
    for ev, f, name, h, a, h10, a10 in recs:
        mu = matchup(f, ev, h10, a10, model) if model else None
        if not mu and rule != 'games':
            no_stats.append(name + ' (no rating)'); continue
        D.set_league(f.get('league'))
        tally = {}
        opts = score_game(ev, h, a, h10, a10, mu, floor, rule, tally, h2h_pairs(F2.fetch(f"df_hh_1_{f['id']}"), f['home']))
        games.append(dict(ts=int(ev['estimateStartTime']) / 1000, match=name, league=f.get('league') or '',
                          pick=opts[0] if opts else None, options=len(opts), checked=tally,
                          expected=dict(stats=[round(x, 2) for x in mu['stats']], book=[round(x, 2) for x in mu['book']] if mu['book'] else None) if mu else None))
    games.sort(key=lambda g: g['ts'])
    return (games, no_stats, [f"{e['homeTeamName']} v {e['awayTeamName']}" for e in evs if e['eventId'] not in {p[0]['eventId'] for p in pairs}],
            other_league, old_div)


def why(g):
    p, ex = g['pick'], g['expected']
    if not ex:                                               # rule 'games': the games behind it, nothing else
        return p['tally']
    bk = f"book's prices {ex['book'][0]:.2f} v {ex['book'][1]:.2f}" if ex['book'] else 'no 1X2 price'
    st = f"stats {p['stats']:.0%}" if p['stats'] is not None else 'stats: none (not a goal market)'
    ct = f"counts {p['counts']:.0%}" if p.get('counts') is not None else 'counts: none'
    return (f"{ct}, {st}, book {p['chance']:.0%}  |  expected goals from the ratings {ex['stats'][0]:.2f} v {ex['stats'][1]:.2f}, "
            f"{bk}  |  {p['tally']}")


def main():
    arg = lambda k, d: sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d
    floor = float(arg('--floor', FLOOR)); rule = arg('--rule', RULE)
    games, no_stats, unmatched, other_league, old_div = predict(int(arg('--until', 23)), int(arg('--days', 0)), floor, rule,
                                                       lead_min=int(arg('--lead', 60)))
    picked = [g for g in games if g['pick']]
    print(f"\nrule: {rule}  |  {len(picked)} games predicted  |  {len(games) - len(picked)} with no option every stat agrees on "
          f"|  {len(no_stats)} with no stats  |  {len(other_league)} between sides from different leagues  "
          f"|  {len(old_div)} with games from another division  "
          f"|  {len(unmatched)} not matched to Flashscore\n")
    for g in games:
        t = dt.datetime.fromtimestamp(g['ts'], tz=A.WAT)
        p = g['pick']
        c = g.get('checked') or {}
        seen = f"checked {c.get('readable', 0)} readable of {c.get('offered', 0)} options at {floor}+, {c.get('agreed', 0)} agreed"
        stop = [f"{c[k]} {k}" for k in ('form', 'h2h', 'blowout') if c.get(k)]
        if stop:
            seen += f" (stopped by {', '.join(stop)})"
        if not p:
            print(f"  {t:%a %H:%M}  {g['match'][:44]:44}  SKIPPED - no option passes every stat and every flag ({seen})"); continue
        print(f"  {t:%a %H:%M}  {g['match'][:44]:44}  {p['market'][:34]} / {p['outcome']:<14} @{p['odds']:<5} {why(g)}  |  {seen}")
    if no_stats:
        print(f"\nno stats ({len(no_stats)}): " + ', '.join(no_stats))
    if other_league:
        print(f"\nsides from different leagues, not predicted ({len(other_league)}):\n  " + '\n  '.join(other_league))
    if old_div:
        print(f"\ngames from another division in a side's record, not predicted ({len(old_div)}):\n  " + '\n  '.join(old_div))
    if '--json' in sys.argv:
        import json
        json.dump(games, open(arg('--json', 'predictions.json'), 'w'), indent=1, default=str)
    if '--book' not in sys.argv or len(picked) < 2:
        return
    parts = [picked[i:i + MAX_LEGS] for i in range(0, len(picked), MAX_LEGS)]
    if len(parts) > 1:                                  # balance the parts instead of 50 + a stub
        size = -(-len(picked) // len(parts))
        parts = [picked[i:i + size] for i in range(0, len(picked), size)]
    slips = [(f"part {i}" if len(parts) > 1 else 'every game', part) for i, part in enumerate(parts, 1)]
    if '--best' in sys.argv:                            # 13 Oct, user: "book the best 50"
        k = min(int(arg('--best', MAX_LEGS)), MAX_LEGS)
        best = sorted(picked, key=lambda g: (-g['pick']['support'], -(g['pick']['stats'] or 0)))[:k]
        slips.append((f"the best {len(best)}", sorted(best, key=lambda g: g['ts'])))
    for name, part in slips:
        combo = math.prod(g['pick']['odds'] for g in part)
        bk = A.book([g['pick']['ids'] for g in part])
        if bk and bk.get('code'):
            print(f"\n   >> code {bk['code']}   {bk['url']}   {name}: {combo:,.2f}x ({len(part)} games, {bk.get('booked')}/{bk.get('req')} on the code)")
            A.log_booking(bk['code'], bk['url'], (f"stats prediction (Flashscore games only: most of every column backs it, blocked options out, flags) at {floor}+ - {name} - {combo:,.2f}x ({len(part)} games)"
                           if rule == 'games' else f"stats prediction (opponent-rated, rule {rule}) at {floor}+ - {name} - {combo:,.2f}x ({len(part)} games)"),
                          [(g['ts'], g['match'], f"{g['pick']['market']} / {g['pick']['outcome']}", g['pick']['odds'], [why(g)]) for g in part])
        else:
            print(f"   >> booking failed for {name}: {bk.get('msg') if bk else 'no selections'}")


if __name__ == '__main__':
    main()
