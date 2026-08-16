#!/usr/bin/env python3
"""
book_v3.py  —  Deep-market pattern booker using fetcher_v3 rich stats
======================================================================
Extends book_patterns.py with first-half, second-half, timed-goal,
GG/NG per-half, and team-split markets that were previously invisible.

Key additions vs book_patterns.py:
  - Fetches ALL SportyBet market IDs (1H, 2H, GG/NG-1H, HT-1x2, etc.)
  - Uses fetcher_v3 rich stats: home_ht_avg, away_ht_avg, home_2h_avg,
    home_early_goal, home_btts_ht, home_first_goal, home_late_goal etc.
  - Market picker tries 12 market families in priority order per fixture
  - Each pick is verified to actually exist and is active on SportyBet

Usage:
  python3 book_v3.py [--until HH] [--days N] [--top N] [--dry]
  python3 book_v3.py --until 23 --days 2

Market families attempted (highest confidence first):
  1. 1H Over/Under 0.5 (HT goals prob from ht_avg)
  2. 1H GG/NG (home_btts_ht + away_btts_ht)
  3. 1H Double Chance (when one team dominates HT)
  4. 2H Over/Under (2h_avg per team)
  5. 2H GG/NG
  6. First Goal (home_first_goal vs away_first_goal)
  7. FT Double Chance 1X / X2 (existing proven pattern layer)
  8. FT Over1.5 / Over0.5 (standard layer)
  9. FT GG/NG
 10. 2H 1x2
 11. FT DNB
 12. Handicap safety net

All picks respect a MAX_ODDS = 1.75 cap (avoid inflated odds traps).
"""

import re, sys, time, json, urllib.request, urllib.error
from datetime import datetime, timedelta, timezone
from math import exp as mexp, factorial

# ── local imports ──────────────────────────────────────────────────────────────
sys.path.insert(0, '/Users/apple/Downloads/draw')
import acca    as A
import fetcher_v2 as F
import fetcher_v3 as F3

# ── SportyBet API ──────────────────────────────────────────────────────────────
SB_BASE = 'https://www.sportybet.com/api/ng/factsCenter/'
SB_HDRS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0 Safari/537.36',
           'Accept': 'application/json',
           'Referer': 'https://www.sportybet.com/ng/sport/football'}

# All market IDs: FT + 1H + 2H + next-goal + GG halves + Highest Scoring Half + Exact Goals
ALL_MIDS = ('1,9,10,11,16,18,19,20,21,27,29,41,45,'
            '52,53,54,'
            '60,62,63,64,65,66,68,69,70,71,74,75,76,77,78,79,'
            '83,84,85,86,87,88,90,91,92,93,94,95,'
            '900036,900037,900069,'
            # Combo markets - Result+BTTS, Result+Total, DC+BTTS, HT/FT (mid 47)
            '14,15,23,24,25,26,28,30,31,32,33,34,35,36,37,'
            '46,47,48,49,50,51,55,56,57,58,59,'
            '81,96,97,98,'
            '540,541,542,543,544,545,546,547,548,549,550,551,552,553,'
            '818,819,820,854,855,856,857,858,859,860,861,862,863,864,865,'
            '1179,'
            # ── STAT MARKETS. Restored 16 Aug. These were dropped when
            # fetch_events_rich was rewritten to use a six-id FT filter, and
            # with them went every corner, foul, offside, shots, booking and
            # saves market the engine could see. A whole Sunday board came back
            # as 36 match goal totals and nothing else because these six ids
            # were the only thing being requested.
            '900300,900301,900302,900303,'              # corners
            '900304,900305,900306,900307,900312,'        # bookings
            '900342,900393,900394,900396,'               # fouls / SoT / shots / offsides
            '900539,900544,900545,900546,900547,'        # team fouls / team SoT
            '900552,900553,900568,900569,900570,'        # team shots / team offsides
            '900609,900610,900625,900626,'               # saves
            '900633,900634,900666,900669,'               # 1H saves
            '900550,900556,900678,900679,900652,900680,' # 1H shots / SoT
            '900580,900674,900675,'                      # 1H offsides
            '900541,900686,900687,'                      # 1H fouls
            '136,137,138,139,142,143,144,152,'           # bookings totals / exact
            '165,176,'                                   # corner handicap + range
            '450004,450005,450006,810002')               # excluded number of goals

# ── constants ──────────────────────────────────────────────────────────────────
MAX_ODDS      = 3.50   # reject any bet where bookie odds > this (trap signal)
MIN_ODDS      = 1.04   # floor — too cheap legs add risk for near-zero reward
MIN_PROB      = 0.62   # minimum model probability to book a leg
MAX_LEGS      = 50

# Half-time goal fraction (1H ≈ 42% of full-match expected goals)
HT_FRACTION   = 0.42

def pois(k, lam):
    return mexp(-lam) * lam**k / factorial(k)

def p_over(lam, line):
    """P(goals > line) under Poisson(lam)."""
    return 1 - sum(pois(k, lam) for k in range(int(line) + 1))

def p_under(lam, line):
    return sum(pois(k, lam) for k in range(int(line) + 1))

# ── SportyBet fetch ────────────────────────────────────────────────────────────
def sb_get(url):
    req = urllib.request.Request(url, headers=SB_HDRS)
    return json.loads(urllib.request.urlopen(req, timeout=25).read().decode('utf-8', 'replace'))

# Small filter - same one sportybet.py uses - gets 900+ events today while the
# broad ALL_MIDS filter caps us at ~68. SportyBet filters events SERVER-SIDE by
# which of the requested market IDs they actually carry, so asking for 107
# markets leaves only the most-covered matches (Italian Coppa Italia tonight).
# Asking for just the 6 FT markets returns every match with FT lines, and the
# per-event `markets` array still contains the full set on each match that has
# them. parse_all_markets therefore keeps working unchanged - it reads from each
# event's own markets array, not from the request filter.
SB_BROAD_MIDS = '1,16,18,10,29,11'

MID_CHUNK = 40


def _mid_chunks():
    """ALL_MIDS split into request-sized groups.

    SportyBet filters events SERVER-SIDE by which requested ids a match carries,
    so one request naming 150 ids returns only the handful of matches covered by
    all of them. Asking in chunks and MERGING gets both: the FT chunk brings back
    every match with basic lines, the stat chunks add corners/fouls/offsides on
    the matches that have them. Asking for six ids only, as this did between 12
    and 16 Aug, returns plenty of events but SIX markets each - the per-event
    array holds only what was requested, not the full set."""
    ids = [x.strip() for x in ALL_MIDS.replace('\n', '').split(',') if x.strip()]
    return [','.join(ids[i:i + MID_CHUNK]) for i in range(0, len(ids), MID_CHUNK)]


def _page(mids, size, num):
    """One page of one chunk, retried."""
    url = (SB_BASE + f'pcUpcomingEvents?sportId=sr:sport:1'
           f'&marketId={mids}&pageSize={size}&pageNum={num}&option=1')
    for attempt in range(3):
        try:
            return sb_get(url)['data']
        except Exception as e:
            if attempt == 2:
                raise
            print(f"  ! sportybet page {num}/{size} attempt {attempt + 1}: {e}"
                  f" - retrying", file=sys.stderr)
            time.sleep(2 * (attempt + 1))


def _fetch_events_for(mids):
    """Page through the board for one chunk of market ids."""
    out = []
    for pg in range(1, 14):
        try:
            d = _page(mids, 100, pg)
            blocks = [d]
        except Exception as e:
            # A page too heavy for their gateway 504s at pageSize=100 but comes
            # back fine as two pages of 50. Halve it rather than lose the chunk.
            print(f"  ! page {pg} dead at pageSize=100 ({e}) - splitting into 2x50",
                  file=sys.stderr)
            try:
                blocks = [_page(mids, 50, pg * 2 - 1), _page(mids, 50, pg * 2)]
                d = blocks[-1]
            except Exception:
                print(f"  ! page {pg} failed at both sizes - chunk truncated",
                      file=sys.stderr)
                break
        tours = [t for b in blocks for t in (b.get('tournaments') or [])]
        if not tours:
            break
        for t in tours:
            for e in t.get('events', []):
                e['_tournament'] = t['name']
                out.append(e)
        if pg * 100 >= d.get('totalNum', 0):
            break
    return out


def fetch_events_rich():
    """Every upcoming event, with every market id we ask for, merged.

    Chunked because of the server-side filtering described in _mid_chunks. Each
    chunk contributes its events AND its markets; an event seen in two chunks
    has the two market lists concatenated."""
    merged = {}
    for chunk in _mid_chunks():
        try:
            evs = _fetch_events_for(chunk)
        except Exception as e:
            print(f"  ! chunk failed entirely: {e}", file=sys.stderr)
            continue
        for ev in evs:
            k = ev.get('eventId')
            if not k:
                continue
            if k in merged:
                merged[k].setdefault('markets', []).extend(ev.get('markets') or [])
            else:
                merged[k] = ev
    return list(merged.values())


# ── market parser  ─────────────────────────────────────────────────────────────
def parse_all_markets(ev):
    """
    Parse every market on a SportyBet event into a structured dict.
    Returns:
      mk['ft_1x2']      = {home, draw, away}
      mk['ft_dc']       = {'Home or Draw', 'Draw or Away', 'Home or Away'}
      mk['ft_ou']       = {line_str: {over_odds, under_odds, over_oid, under_oid, mid, spec}}
      mk['ft_gg']       = {yes_odds, no_odds, yes_oid, no_oid}
      mk['ft_dnb']      = {home, away}
      mk['ht_1x2']      = {home, draw, away}   (mid=60)
      mk['ht_dc']       = {same as ft_dc}       (mid=63)
      mk['ht_ou']       = same as ft_ou         (mid=68)
      mk['ht_gg']       = {yes, no}             (mid=75)
      mk['ht_dnb']      = {home, away}          (mid=64)
      mk['2h_1x2']      = {home, draw, away}    (mid=83)
      mk['2h_ou']       = same as ft_ou         (mid=90)
      mk['2h_gg']       = {yes, no}             (mid=95)
      mk['2h_dnb']      = {home, away}          (mid=86)
      mk['first_goal']  = {home_odds, none_odds, away_odds}  (mid=62 goalnr=1)
      mk['last_goal']   = {home, none, away}    (mid=9)
      mk['ht_home_ou']  = {line: over/under}    (mid=69)
      mk['ht_away_ou']  = {line: over/under}    (mid=70)
      mk['2h_home_ou']  = {line: over/under}    (mid=91)
      mk['2h_away_ou']  = {line: over/under}    (mid=92)
      mk['ah']          = {hcp: {home_odds, away_odds}}
    """
    mk = {k: {} for k in ['ft_1x2','ft_dc','ft_ou','ft_gg','ft_dnb',
                           'ht_1x2','ht_dc','ht_ou','ht_gg','ht_dnb',
                           '2h_1x2','2h_ou','2h_gg','2h_dnb',
                           'first_goal','last_goal',
                           'ht_home_ou','ht_away_ou','2h_home_ou','2h_away_ou',
                           'exact_goals', 'ah', '1h_ah', '2h_ah', '2h_dc',
                           'home_ou','away_ou','1h_home_cs','1h_away_cs','1h_1x2_total',
                           '1h_home_nil','1h_away_nil','interval_1x2',
                           # New markets added 27 Jul - HT/FT, combos, odds/even, team goals
                           'htft',                     # mid 47 - HT/FT combination (9 outcomes)
                           'result_btts',              # mid 35/78/540 - Result+BTTS (6 outcomes)
                           'result_total',             # mid 37/544 - Result+Total (9 outcomes)
                           'dc_btts',                  # mid 546 - DC+BTTS (6 outcomes)
                           'dc_total',                 # mid 547 - DC+Total (9 outcomes)
                           'odd_even',                 # mid 26/27/28 - Odd/Even total goals
                           'winning_margin',           # mid 15 - winning margin
                           'home_both_halves',         # mid 48/56 - home scores in both halves
                           'away_both_halves',         # mid 49/57 - away scores in both halves
                           'home_score_first',         # mid 50 - home to score first
                           'away_score_first',         # mid 51 - away to score first
                           'either_score_first',       # mid 31 - either team to score first
                           'btts_both_halves',         # mid 32/55 - both teams score in both halves
                           'first_goal_yes_no',        # mid 9 - first goal yes/no
                           'goal_ranges',              # mid 71 - exact goals 0/1/2/3+
                           'team_score_first_match',   # mid 96/97 - team scores first + winner
                           'htft_total',               # mid 818/819 - HT/FT + Total combos
                           ]}
    def fnum(x):
        try: return float(x)
        except: return None

    def is_active(m, o):
        if m.get('status') not in (0, '0'): return False
        if o.get('isActive') == 0: return False
        return True

    for m in ev.get('markets', []):
        mid  = str(m.get('id', ''))
        spec = m.get('specifier', '')
        outs = m.get('outcomes', [])

        if mid == '1':    # FT 1x2
            for o in outs:
                if not is_active(m, o): continue
                d = o.get('desc', '')
                if d == 'Home':  mk['ft_1x2']['home'] = {'odds': fnum(o['odds']), 'oid': o['id'], 'mid': mid, 'spec': spec}
                elif d == 'Draw': mk['ft_1x2']['draw'] = {'odds': fnum(o['odds']), 'oid': o['id'], 'mid': mid, 'spec': spec}
                elif d == 'Away': mk['ft_1x2']['away'] = {'odds': fnum(o['odds']), 'oid': o['id'], 'mid': mid, 'spec': spec}

        elif mid == '10':  # FT Double Chance
            for o in outs:
                if not is_active(m, o): continue
                mk['ft_dc'][o.get('desc', '')] = {'odds': fnum(o['odds']), 'oid': o['id'], 'mid': mid, 'spec': spec}

        elif mid == '63':  # 1H Double Chance
            for o in outs:
                if not is_active(m, o): continue
                mk['ht_dc'][o.get('desc', '')] = {'odds': fnum(o['odds']), 'oid': o['id'], 'mid': mid, 'spec': spec}

        elif mid == '84':  # 2H Double Chance
            for o in outs:
                if not is_active(m, o): continue
                mk['2h_dc'][o.get('desc', '')] = {'odds': fnum(o['odds']), 'oid': o['id'], 'mid': mid, 'spec': spec}

        elif mid == '29':  # FT GG/NG
            for o in outs:
                if not is_active(m, o): continue
                d = o.get('desc', '')
                key = 'yes' if d == 'Yes' else 'no'
                mk['ft_gg'][key] = {'odds': fnum(o['odds']), 'oid': o['id'], 'mid': mid, 'spec': spec}

        elif mid == '11':  # FT DNB
            for o in outs:
                if not is_active(m, o): continue
                d = o.get('desc', '')
                if d == 'Home': mk['ft_dnb']['home'] = {'odds': fnum(o['odds']), 'oid': o['id'], 'mid': mid, 'spec': spec}
                elif d == 'Away': mk['ft_dnb']['away'] = {'odds': fnum(o['odds']), 'oid': o['id'], 'mid': mid, 'spec': spec}

        elif mid == '18':  # FT O/U
            line = spec.split('=')[1] if '=' in spec else spec
            for o in outs:
                if not is_active(m, o): continue
                d = o.get('desc', '')
                if line not in mk['ft_ou']: mk['ft_ou'][line] = {}
                key = 'over' if d.startswith('Over') else 'under'
                mk['ft_ou'][line][key] = {'odds': fnum(o['odds']), 'oid': o['id'], 'mid': mid, 'spec': spec}

        elif mid == '19':  # FT Home O/U
            line = spec.split('=')[1] if '=' in spec else spec
            for o in outs:
                if not is_active(m, o): continue
                d = o.get('desc', '')
                if line not in mk['home_ou']: mk['home_ou'][line] = {}
                key = 'over' if d.startswith('Over') else 'under'
                mk['home_ou'][line][key] = {'odds': fnum(o['odds']), 'oid': o['id'], 'mid': mid, 'spec': spec}

        elif mid == '20':  # FT Away O/U
            line = spec.split('=')[1] if '=' in spec else spec
            for o in outs:
                if not is_active(m, o): continue
                d = o.get('desc', '')
                if line not in mk['away_ou']: mk['away_ou'][line] = {}
                key = 'over' if d.startswith('Over') else 'under'
                mk['away_ou'][line][key] = {'odds': fnum(o['odds']), 'oid': o['id'], 'mid': mid, 'spec': spec}

        elif mid == '900036': # 1H Home Win to Nil
            for o in outs:
                if not is_active(m, o): continue
                key = 'yes' if o.get('desc') == 'Yes' else 'no'
                mk['1h_home_nil'][key] = {'odds': fnum(o['odds']), 'oid': o['id'], 'mid': mid, 'spec': spec}

        elif mid == '900037': # 1H Away Win to Nil
            for o in outs:
                if not is_active(m, o): continue
                key = 'yes' if o.get('desc') == 'Yes' else 'no'
                mk['1h_away_nil'][key] = {'odds': fnum(o['odds']), 'oid': o['id'], 'mid': mid, 'spec': spec}

        elif mid == '900069': # Match Result after X Minutes
            minute = spec.split('=')[1] if '=' in spec else spec
            if minute not in mk['interval_1x2']: mk['interval_1x2'][minute] = {}
            for o in outs:
                if not is_active(m, o): continue
                d = o.get('desc', '')
                if d == 'Home': mk['interval_1x2'][minute]['home'] = {'odds': fnum(o['odds']), 'oid': o['id'], 'mid': mid, 'spec': spec}
                elif d == 'Draw': mk['interval_1x2'][minute]['draw'] = {'odds': fnum(o['odds']), 'oid': o['id'], 'mid': mid, 'spec': spec}
                elif d == 'Away': mk['interval_1x2'][minute]['away'] = {'odds': fnum(o['odds']), 'oid': o['id'], 'mid': mid, 'spec': spec}

        elif mid == '60':  # 1H 1x2
            for o in outs:
                if not is_active(m, o): continue
                d = o.get('desc', '')
                if d == 'Home':  mk['ht_1x2']['home'] = {'odds': fnum(o['odds']), 'oid': o['id'], 'mid': mid, 'spec': spec}
                elif d == 'Draw': mk['ht_1x2']['draw'] = {'odds': fnum(o['odds']), 'oid': o['id'], 'mid': mid, 'spec': spec}
                elif d == 'Away': mk['ht_1x2']['away'] = {'odds': fnum(o['odds']), 'oid': o['id'], 'mid': mid, 'spec': spec}

        elif mid == '63':  # 1H DC
            for o in outs:
                if not is_active(m, o): continue
                mk['ht_dc'][o.get('desc', '')] = {'odds': fnum(o['odds']), 'oid': o['id'], 'mid': mid, 'spec': spec}

        elif mid == '68':  # 1H O/U
            line = spec.split('=')[1] if '=' in spec else spec
            for o in outs:
                if not is_active(m, o): continue
                d = o.get('desc', '')
                if line not in mk['ht_ou']: mk['ht_ou'][line] = {}
                key = 'over' if d.startswith('Over') else 'under'
                mk['ht_ou'][line][key] = {'odds': fnum(o['odds']), 'oid': o['id'], 'mid': mid, 'spec': spec}

        elif mid == '75':  # 1H GG/NG
            for o in outs:
                if not is_active(m, o): continue
                d = o.get('desc', '')
                key = 'yes' if d == 'Yes' else 'no'
                mk['ht_gg'][key] = {'odds': fnum(o['odds']), 'oid': o['id'], 'mid': mid, 'spec': spec}

        elif mid == '76':  # 1H Home Clean Sheet
            for o in outs:
                if not is_active(m, o): continue
                d = o.get('desc', '')
                key = 'yes' if d == 'Yes' else 'no'
                mk['1h_home_cs'][key] = {'odds': fnum(o['odds']), 'oid': o['id'], 'mid': mid, 'spec': spec}

        elif mid == '77':  # 1H Away Clean Sheet
            for o in outs:
                if not is_active(m, o): continue
                d = o.get('desc', '')
                key = 'yes' if d == 'Yes' else 'no'
                mk['1h_away_cs'][key] = {'odds': fnum(o['odds']), 'oid': o['id'], 'mid': mid, 'spec': spec}

        elif mid == '79':  # 1H 1x2 & Total
            line = spec.split('=')[1] if '=' in spec else spec
            for o in outs:
                if not is_active(m, o): continue
                d = o.get('desc', '')
                if line not in mk['1h_1x2_total']: mk['1h_1x2_total'][line] = {}
                mk['1h_1x2_total'][line][d] = {'odds': fnum(o['odds']), 'oid': o['id'], 'mid': mid, 'spec': spec}

        elif mid == '64':  # 1H DNB
            for o in outs:
                if not is_active(m, o): continue
                d = o.get('desc', '')
                if d == 'Home': mk['ht_dnb']['home'] = {'odds': fnum(o['odds']), 'oid': o['id'], 'mid': mid, 'spec': spec}
                elif d == 'Away': mk['ht_dnb']['away'] = {'odds': fnum(o['odds']), 'oid': o['id'], 'mid': mid, 'spec': spec}

        elif mid == '69':  # 1H Home O/U
            line = spec.split('=')[1] if '=' in spec else spec
            for o in outs:
                if not is_active(m, o): continue
                d = o.get('desc', '')
                if line not in mk['ht_home_ou']: mk['ht_home_ou'][line] = {}
                key = 'over' if d.startswith('Over') else 'under'
                mk['ht_home_ou'][line][key] = {'odds': fnum(o['odds']), 'oid': o['id'], 'mid': mid, 'spec': spec}

        elif mid == '70':  # 1H Away O/U
            line = spec.split('=')[1] if '=' in spec else spec
            for o in outs:
                if not is_active(m, o): continue
                d = o.get('desc', '')
                if line not in mk['ht_away_ou']: mk['ht_away_ou'][line] = {}
                key = 'over' if d.startswith('Over') else 'under'
                mk['ht_away_ou'][line][key] = {'odds': fnum(o['odds']), 'oid': o['id'], 'mid': mid, 'spec': spec}

        elif mid == '83':  # 2H 1x2
            for o in outs:
                if not is_active(m, o): continue
                d = o.get('desc', '')
                if d == 'Home':  mk['2h_1x2']['home'] = {'odds': fnum(o['odds']), 'oid': o['id'], 'mid': mid, 'spec': spec}
                elif d == 'Draw': mk['2h_1x2']['draw'] = {'odds': fnum(o['odds']), 'oid': o['id'], 'mid': mid, 'spec': spec}
                elif d == 'Away': mk['2h_1x2']['away'] = {'odds': fnum(o['odds']), 'oid': o['id'], 'mid': mid, 'spec': spec}

        elif mid == '85':  # 2H DC
            for o in outs:
                if not is_active(m, o): continue
                mk['2h_dc'] = mk.get('2h_dc', {})
                mk['2h_dc'][o.get('desc', '')] = {'odds': fnum(o['odds']), 'oid': o['id'], 'mid': mid, 'spec': spec}

        elif mid == '90':  # 2H O/U
            line = spec.split('=')[1] if '=' in spec else spec
            for o in outs:
                if not is_active(m, o): continue
                d = o.get('desc', '')
                if line not in mk['2h_ou']: mk['2h_ou'][line] = {}
                key = 'over' if d.startswith('Over') else 'under'
                mk['2h_ou'][line][key] = {'odds': fnum(o['odds']), 'oid': o['id'], 'mid': mid, 'spec': spec}

        elif mid == '95':  # 2H GG/NG
            for o in outs:
                if not is_active(m, o): continue
                d = o.get('desc', '')
                key = 'yes' if d == 'Yes' else 'no'
                mk['2h_gg'][key] = {'odds': fnum(o['odds']), 'oid': o['id'], 'mid': mid, 'spec': spec}

        elif mid == '86':  # 2H DNB
            for o in outs:
                if not is_active(m, o): continue
                d = o.get('desc', '')
                if d == 'Home': mk['2h_dnb']['home'] = {'odds': fnum(o['odds']), 'oid': o['id'], 'mid': mid, 'spec': spec}
                elif d == 'Away': mk['2h_dnb']['away'] = {'odds': fnum(o['odds']), 'oid': o['id'], 'mid': mid, 'spec': spec}

        elif mid == '91':  # 2H Home O/U
            line = spec.split('=')[1] if '=' in spec else spec
            for o in outs:
                if not is_active(m, o): continue
                d = o.get('desc', '')
                if line not in mk['2h_home_ou']: mk['2h_home_ou'][line] = {}
                key = 'over' if d.startswith('Over') else 'under'
                mk['2h_home_ou'][line][key] = {'odds': fnum(o['odds']), 'oid': o['id'], 'mid': mid, 'spec': spec}

        elif mid == '92':  # 2H Away O/U
            line = spec.split('=')[1] if '=' in spec else spec
            for o in outs:
                if not is_active(m, o): continue
                d = o.get('desc', '')
                if line not in mk['2h_away_ou']: mk['2h_away_ou'][line] = {}
                key = 'over' if d.startswith('Over') else 'under'
                mk['2h_away_ou'][line][key] = {'odds': fnum(o['odds']), 'oid': o['id'], 'mid': mid, 'spec': spec}

        elif mid == '62':  # 1H Next Goal (First Goal of match, typically goalnr=1)
            for o in outs:
                if not is_active(m, o): continue
                d = o.get('desc', '')
                if d == 'Home': mk['first_goal']['home'] = {'odds': fnum(o['odds']), 'oid': o['id'], 'mid': mid, 'spec': spec}
                elif d == 'Away': mk['first_goal']['away'] = {'odds': fnum(o['odds']), 'oid': o['id'], 'mid': mid, 'spec': spec}
                elif d == 'None': mk['first_goal']['none'] = {'odds': fnum(o['odds']), 'oid': o['id'], 'mid': mid, 'spec': spec}

        elif mid == '9':   # Last Goal
            for o in outs:
                if not is_active(m, o): continue
                d = o.get('desc', '')
                if d == 'Home': mk['last_goal']['home'] = {'odds': fnum(o['odds']), 'oid': o['id'], 'mid': mid, 'spec': spec}
                elif d == 'Away': mk['last_goal']['away'] = {'odds': fnum(o['odds']), 'oid': o['id'], 'mid': mid, 'spec': spec}

        elif mid == '16':  # Asian Handicap FT
            mm = re.match(r'hcp=([+-]?\d+(?:\.\d+)?)', spec)
            if mm:
                hcp = float(mm.group(1))
                for o in outs:
                    if not is_active(m, o): continue
                    d = o.get('desc', '')
                    if hcp not in mk['ah']: mk['ah'][hcp] = {}
                    if 'Home' in d: mk['ah'][hcp]['home'] = {'odds': fnum(o['odds']), 'oid': o['id'], 'mid': mid, 'spec': spec}
                    elif 'Away' in d: mk['ah'][hcp]['away'] = {'odds': fnum(o['odds']), 'oid': o['id'], 'mid': mid, 'spec': spec}

        elif mid in ('65', '88'):  # Asian Handicap 1H / 2H
            mm = re.match(r'hcp=([+-]?\d+(?:\.\d+)?)', spec)
            if mm:
                hcp = float(mm.group(1))
                t = '1h_ah' if mid == '65' else '2h_ah'
                if hcp not in mk[t]: mk[t][hcp] = {}
                for o in outs:
                    if not is_active(m, o): continue
                    d = o.get('desc', '')
                    if 'Home' in d: mk[t][hcp]['home'] = {'odds': fnum(o['odds']), 'oid': o['id'], 'mid': mid, 'spec': spec}
                    elif 'Away' in d: mk[t][hcp]['away'] = {'odds': fnum(o['odds']), 'oid': o['id'], 'mid': mid, 'spec': spec}

        elif mid == '52':  # Highest Scoring Half: 1st half / 2nd half / Equal
            pass

        elif mid == '21':  # FT Exact Goals: 0, 1, 2, 3, 4+
            for o in outs:
                if not is_active(m, o): continue
                d = o.get('desc', '')
                mk['exact_goals'][d] = {'odds': fnum(o['odds']), 'oid': o['id'], 'mid': mid, 'spec': spec}

        # ── NEW MARKETS added 27 Jul: HT/FT, combos, Odd/Even, team-both-halves ──

        elif mid == '47':  # HT/FT combination: Home/Home, Home/Draw, ..., Away/Away
            # desc format: "Home/Home", "Home/Draw", "Home/Away", "Draw/Home", etc.
            for o in outs:
                if not is_active(m, o): continue
                d = o.get('desc', '')
                if '/' in d:  # confirm it's an HT/FT combo
                    mk['htft'][d] = {'odds': fnum(o['odds']), 'oid': o['id'], 'mid': mid, 'spec': spec}

        elif mid in ('35', '78', '540', '541', '542', '543', '545'):  # Result + BTTS combos
            # desc format: "Home & Yes", "Home & No", "Draw & Yes", "Draw & No", "Away & Yes", "Away & No"
            for o in outs:
                if not is_active(m, o): continue
                d = o.get('desc', '')
                if '&' in d:
                    mk['result_btts'][d] = {'odds': fnum(o['odds']), 'oid': o['id'], 'mid': mid, 'spec': spec}

        elif mid in ('37', '544'):  # Result + Total combos
            # specifier='total=1.5' or 'total=2.5'; desc: "Home & Over 1.5", "Draw & Under 2.5", etc.
            line = spec.split('=')[1] if '=' in spec else '2.5'
            for o in outs:
                if not is_active(m, o): continue
                d = o.get('desc', '')
                if '&' in d:
                    if line not in mk['result_total']: mk['result_total'][line] = {}
                    mk['result_total'][line][d] = {'odds': fnum(o['odds']), 'oid': o['id'], 'mid': mid, 'spec': spec}

        elif mid == '546':  # Double Chance + BTTS combos (6 outcomes)
            # desc: "Home/Draw & Yes", "Home/Away & Yes", "Draw/Away & Yes", "Home/Draw & No", etc.
            for o in outs:
                if not is_active(m, o): continue
                d = o.get('desc', '')
                if '&' in d:
                    mk['dc_btts'][d] = {'odds': fnum(o['odds']), 'oid': o['id'], 'mid': mid, 'spec': spec}

        elif mid == '547':  # Double Chance + Total combos
            line = spec.split('=')[1] if '=' in spec else '1.5'
            for o in outs:
                if not is_active(m, o): continue
                d = o.get('desc', '')
                if '&' in d:
                    if line not in mk['dc_total']: mk['dc_total'][line] = {}
                    mk['dc_total'][line][d] = {'odds': fnum(o['odds']), 'oid': o['id'], 'mid': mid, 'spec': spec}

        elif mid in ('26', '27', '28'):  # FT Odd/Even total goals
            for o in outs:
                if not is_active(m, o): continue
                d = o.get('desc', '')
                key = 'odd' if 'Odd' in d else ('even' if 'Even' in d else None)
                if key:
                    mk['odd_even'][key] = {'odds': fnum(o['odds']), 'oid': o['id'], 'mid': mid, 'spec': spec}

        elif mid == '15':  # Winning Margin: Home by 1/2/3+, Away by 1/2/3+
            for o in outs:
                if not is_active(m, o): continue
                d = o.get('desc', '')
                if 'Home' in d or 'Away' in d:
                    mk['winning_margin'][d] = {'odds': fnum(o['odds']), 'oid': o['id'], 'mid': mid, 'spec': spec}

        elif mid in ('48', '56'):  # Home scores in both halves (Yes/No)
            for o in outs:
                if not is_active(m, o): continue
                d = o.get('desc', '')
                key = 'yes' if d == 'Yes' else ('no' if d == 'No' else None)
                if key:
                    mk['home_both_halves'][key] = {'odds': fnum(o['odds']), 'oid': o['id'], 'mid': mid, 'spec': spec}

        elif mid in ('49', '57'):  # Away scores in both halves
            for o in outs:
                if not is_active(m, o): continue
                d = o.get('desc', '')
                key = 'yes' if d == 'Yes' else ('no' if d == 'No' else None)
                if key:
                    mk['away_both_halves'][key] = {'odds': fnum(o['odds']), 'oid': o['id'], 'mid': mid, 'spec': spec}

        elif mid == '50':  # Home to score first
            for o in outs:
                if not is_active(m, o): continue
                d = o.get('desc', '')
                key = 'yes' if d == 'Yes' else ('no' if d == 'No' else None)
                if key:
                    mk['home_score_first'][key] = {'odds': fnum(o['odds']), 'oid': o['id'], 'mid': mid, 'spec': spec}

        elif mid == '51':  # Away to score first
            for o in outs:
                if not is_active(m, o): continue
                d = o.get('desc', '')
                key = 'yes' if d == 'Yes' else ('no' if d == 'No' else None)
                if key:
                    mk['away_score_first'][key] = {'odds': fnum(o['odds']), 'oid': o['id'], 'mid': mid, 'spec': spec}

        elif mid == '31':  # Either team to score first
            for o in outs:
                if not is_active(m, o): continue
                d = o.get('desc', '')
                key = 'yes' if d == 'Yes' else ('no' if d == 'No' else None)
                if key:
                    mk['either_score_first'][key] = {'odds': fnum(o['odds']), 'oid': o['id'], 'mid': mid, 'spec': spec}

        elif mid in ('32', '55'):  # BTTS both halves
            for o in outs:
                if not is_active(m, o): continue
                d = o.get('desc', '')
                if d in ('Yes', 'No'):
                    mk['btts_both_halves'][d.lower()] = {'odds': fnum(o['odds']), 'oid': o['id'], 'mid': mid, 'spec': spec}

        elif mid == '71':  # Exact Goals 0/1/2/3+ (sometimes variant=sr:exact_goals:3+)
            # desc: "0", "1", "2", "3+"
            for o in outs:
                if not is_active(m, o): continue
                d = o.get('desc', '')
                if d in ('0', '1', '2', '3+', '4+'):
                    mk['goal_ranges'][d] = {'odds': fnum(o['odds']), 'oid': o['id'], 'mid': mid, 'spec': spec}

        elif mid in ('96', '97'):  # Team scores first + match winner combo
            for o in outs:
                if not is_active(m, o): continue
                d = o.get('desc', '')
                key = 'yes' if d == 'Yes' else ('no' if d == 'No' else None)
                if key:
                    mk['team_score_first_match'][key] = {'odds': fnum(o['odds']), 'oid': o['id'], 'mid': mid, 'spec': spec}

        elif mid in ('818', '819'):  # HT/FT + Total combos (high-odds markets)
            line = spec.split('=')[1] if '=' in spec else '1.5'
            for o in outs:
                if not is_active(m, o): continue
                d = o.get('desc', '')
                if line not in mk['htft_total']: mk['htft_total'][line] = {}
                mk['htft_total'][line][d] = {'odds': fnum(o['odds']), 'oid': o['id'], 'mid': mid, 'spec': spec}

    return mk


# ── pick engine ────────────────────────────────────────────────────────────────
def pick_best(ev, pf, rich, hr, ar, league):
    """
    Try markets in priority order using both standard pf stats AND rich HT/goal stats.
    Returns (leg_dict, label, prob) or (None, None, None).
    leg_dict = {eventId, productId, marketId, specifier, outcomeId}
    """
    EXCLUDED_LEAGUES = ["USA: MLS"]
    if any(ex in league for ex in EXCLUDED_LEAGUES):
        return None

    mk = parse_all_markets(ev)
    eid = ev['eventId']

    def leg(entry, label, prob, max_odds=None):
        """Build a bookable leg from a market entry dict."""
        if entry is None: return None
        o = entry.get('odds')
        cap = max_odds if max_odds is not None else MAX_ODDS
        if o is None or o < MIN_ODDS or o > cap: return None
        if prob < MIN_PROB: return None
        return dict(
            label=label, prob=prob, odds=o,
            bs=dict(eventId=eid, productId=3,
                    marketId=entry['mid'], specifier=entry['spec'],
                    outcomeId=entry['oid'])
        )

    h_exp  = pf['h_exp']
    a_exp  = pf['a_exp']
    gap    = pf['gap']     # h_exp - a_exp

    # ── rich stats (may be None if deep fetch unavailable) ──────────────────
    h_ht   = rich.get('home_ht_avg')    # home goals in 1H at home
    a_ht   = rich.get('away_ht_avg')    # away goals in 1H away
    h_2h   = rich.get('home_2h_avg')    # home goals in 2H at home
    a_2h   = rich.get('away_2h_avg')    # away goals in 2H away
    h_bht  = rich.get('home_btts_ht')   # % home games where BTTS in HT
    a_bht  = rich.get('away_btts_ht')   # % away games where BTTS in HT
    h_fg   = rich.get('home_first_goal') # % home games home team scored first
    a_fg   = rich.get('away_first_goal') # % away games away team scored first
    h_eg   = rich.get('home_early_goal') # % home games, goal before 30min
    a_eg   = rich.get('away_early_goal')
    h_lg   = rich.get('home_late_goal')  # % home games, goal after 75min
    a_lg   = rich.get('away_late_goal')

    # Fallback HT expected goals from Poisson full-time model
    ht_lh = h_ht if h_ht is not None else h_exp * HT_FRACTION
    ht_la = a_ht if a_ht is not None else a_exp * HT_FRACTION
    sh_lh = h_2h if h_2h is not None else h_exp * (1 - HT_FRACTION)
    sh_la = a_2h if a_2h is not None else a_exp * (1 - HT_FRACTION)

    candidates = []  # list of (priority, leg_dict, label, prob)
    total_ht = ht_lh + ht_la
    total_2h = sh_lh + sh_la
    total_exp = h_exp + a_exp
    hMax = pf.get('h_max', 0)
    aMax = pf.get('a_max', 0)

    # ══════════════════════════════════════════════════════════════════════════
    # PRIORITY 1 — 1H UNDERS (most reliable: first half is quiet in most games)
    # ══════════════════════════════════════════════════════════════════════════
    safe_1h_btts = (h_bht or 0) < 0.20 and (a_bht or 0) < 0.20
    
    # Variance check (last 7 games)
    h_ga_max = max((r.get('ga', 0) for r in hr[:7]), default=0)
    a_ga_max = max((r.get('ga', 0) for r in ar[:7]), default=0)
    safe_under_variance = (hMax < 4 and aMax < 4 and h_ga_max < 4 and a_ga_max < 4)
    
    # Drought check (last 7 games)
    hh_scored = sum(1 for x in hr[:7] if x.get('gf', 0) >= 1) / max(1, len(hr[:7]))
    aa_scored = sum(1 for x in ar[:7] if x.get('gf', 0) >= 1) / max(1, len(ar[:7]))
    h_zero_games = sum(1 for x in hr[:7] if x.get('gf', 0) == 0)
    a_zero_games = sum(1 for x in ar[:7] if x.get('gf', 0) == 0)
    safe_over_drought = (h_zero_games < 4 and a_zero_games < 4)
    
    # H2H check
    h2h_data = pf.get('h2h') or {}
    h2h_n = h2h_data.get('n', 0)
    safe_1x_h2h = True if h2h_n < 4 else (h2h_data.get('hwin', 0) > 0.15)
    safe_x2_h2h = True if h2h_n < 4 else (h2h_data.get('awin', 0) > 0.15)
    
    # Recent momentum check (last 2 overall games)
    h_recent_gf = sum(r.get('gf', 0) for r in hr[:2]) if len(hr) >= 2 else 0
    a_recent_gf = sum(r.get('gf', 0) for r in ar[:2]) if len(ar) >= 2 else 0
    h_hot = h_recent_gf >= 3  # Home scored 3+ goals in last 2 games
    a_hot = a_recent_gf >= 3  # Away scored 3+ goals in last 2 games
    safe_from_momentum = not h_hot and not a_hot

    if total_ht <= 1.4 and total_exp <= 2.6 and pf.get('o25', 0) < 0.60 and safe_1h_btts and safe_from_momentum and safe_under_variance:
        prob = p_under(total_ht, 2.5)
        e = mk['ht_ou'].get('2.5', {}).get('under')
        r = leg(e, '1H Under2.5', prob)
        if r: candidates.append((1, r))
    
    if total_ht <= 1.0 and total_exp <= 2.2 and pf.get('o25', 0) < 0.50 and safe_1h_btts and safe_from_momentum and safe_under_variance:
        prob = p_under(total_ht, 1.5)
        e = mk['ht_ou'].get('1.5', {}).get('under')
        r = leg(e, '1H Under1.5', prob)
        if r: candidates.append((1, r))

    if total_ht <= 0.7 and total_exp <= 2.0 and pf.get('o25', 0) < 0.40 and safe_1h_btts and safe_from_momentum and safe_under_variance:
        prob = p_under(total_ht, 0.5)
        e = mk['ht_ou'].get('0.5', {}).get('under')
        r = leg(e, '1H Under0.5', prob)
        if r: candidates.append((1, r))

    # ══════════════════════════════════════════════════════════════════════════
    # PRIORITY 1b — Draw 0-15 Min
    # ══════════════════════════════════════════════════════════════════════════
    if total_exp < 2.2 and total_ht <= 0.8 and safe_from_momentum:
        e = mk['interval_1x2'].get('15', {}).get('draw')
        if e:
            # high confidence for 15m draw when goals are expected low
            prob_15m_00 = pois(0, total_ht * (15/45))
            r = leg(e, 'Draw 0-15 Min', prob_15m_00)
            if r: candidates.append((1, r))

    # ══════════════════════════════════════════════════════════════════════════
    # PRIORITY 2 — 2H UNDERS
    # ══════════════════════════════════════════════════════════════════════════
    if total_2h <= 1.5 and total_exp <= 2.6 and pf.get('o25', 0) < 0.60 and safe_from_momentum and safe_under_variance:
        prob = p_under(total_2h, 2.5)
        e = mk['2h_ou'].get('2.5', {}).get('under')
        r = leg(e, '2H Under2.5', prob)
        if r: candidates.append((2, r))

    if total_2h <= 1.1 and total_exp <= 2.2 and pf.get('o25', 0) < 0.50 and safe_from_momentum and safe_under_variance:  # quiet 2H → Under 1.5
        prob = p_under(total_2h, 1.5)
        e = mk['2h_ou'].get('1.5', {}).get('under')
        r = leg(e, '2H Under1.5', prob)
        if r: candidates.append((2, r))

    if total_2h <= 0.9 and total_exp < 2.2 and pf.get('o25', 0) < 0.40 and safe_from_momentum and safe_under_variance:  # very quiet 2H → Under 0.5
        prob = p_under(total_2h, 0.5)
        e = mk['2h_ou'].get('0.5', {}).get('under')
        r = leg(e, '2H Under0.5', prob)
        if r: candidates.append((2, r))

    # ══════════════════════════════════════════════════════════════════════════
    # PRIORITY 3c — 2H Over 0.5
    # ══════════════════════════════════════════════════════════════════════════
    safe_2h_offense = safe_over_drought
    h2h_avg = (pf.get('h2h') or {}).get('avg', 99.0)
    vital_offense = (hh_scored >= 0.40 and aa_scored >= 0.40) or (total_exp >= 2.8)
    h2h_safe = (h2h_avg >= 2.0) or (hh_scored >= 0.70 and aa_scored >= 0.70 and total_2h >= 1.70)

    if total_2h >= 1.65 and vital_offense and h2h_safe and safe_2h_offense and total_exp >= 2.60 and pf.get('o25', 0) >= 0.55:
        prob = 1 - p_under(total_2h, 0.5)
        e = mk['2h_ou'].get('0.5', {}).get('over')
        r = leg(e, '2H Over0.5', prob)
        if r: candidates.append((3, r))

    # ══════════════════════════════════════════════════════════════════════════
    # PRIORITY 3c — 2H GG No (No — neither/only one team scores in 2H)
    # Triggered when 2H expected goals per team are both low
    # ══════════════════════════════════════════════════════════════════════════
    prob_2h_gg = (1 - p_under(sh_lh, 0.5)) * (1 - p_under(sh_la, 0.5))
    if prob_2h_gg <= 0.25 and total_exp <= 2.6 and total_2h <= 1.4 and (pf.get('h2h') or {}).get('avg', 0) < 3.0 and pf.get('btts', 0) < 0.60 and safe_from_momentum:  # strong evidence only 0 or 1 team scores in 2H
        e = mk['2h_gg'].get('no')
        r = leg(e, '2H GG No', 1 - prob_2h_gg)
        if r: candidates.append((3, r))

    # ══════════════════════════════════════════════════════════════════════════
    # PRIORITY 4a — 1H Double Chance
    # ══════════════════════════════════════════════════════════════════════════
    if total_ht < 0.9 and safe_from_momentum:
        if ht_lh >= ht_la * 1.5 and safe_1x_h2h:
            prob_1x = sum(pois(h, ht_lh) * sum(pois(a, ht_la) for a in range(h + 1)) for h in range(5))
            e = mk['ht_dc'].get('Home or Draw')
            r = leg(e, '1H 1X', prob_1x, 1.60)
            if r: candidates.append((4, r))
        elif ht_la >= ht_lh * 1.5 and safe_x2_h2h:
            prob_x2 = sum(pois(a, ht_la) * sum(pois(h, ht_lh) for h in range(a + 1)) for a in range(5))
            e = mk['ht_dc'].get('Draw or Away')
            r = leg(e, '1H X2', prob_x2, 1.60)
            if r: candidates.append((4, r))

    # ══════════════════════════════════════════════════════════════════════════
    # PRIORITY 4c — 2H Double Chance
    # ══════════════════════════════════════════════════════════════════════════
    if total_2h < 0.9 and safe_from_momentum:
        if sh_lh >= sh_la * 1.5 and safe_1x_h2h:
            prob_1x = sum(pois(h, sh_lh) * sum(pois(a, sh_la) for a in range(h + 1)) for h in range(5))
            e = mk['2h_dc'].get('Home or Draw')
            r = leg(e, '2H 1X', prob_1x, 1.60)
            if r: candidates.append((4, r))
        elif sh_la >= sh_lh * 1.5 and safe_x2_h2h:
            prob_x2 = sum(pois(a, sh_la) * sum(pois(h, sh_lh) for h in range(a + 1)) for a in range(5))
            e = mk['2h_dc'].get('Draw or Away')
            r = leg(e, '2H X2', prob_x2, 1.60)
            if r: candidates.append((4, r))

    # ══════════════════════════════════════════════════════════════════════════
    # PRIORITY 4b — 1H GG No
    # ══════════════════════════════════════════════════════════════════════════
    prob_1h_gg = (1 - p_under(ht_lh, 0.5)) * (1 - p_under(ht_la, 0.5))
    if prob_1h_gg <= 0.20 and total_exp <= 2.6 and total_ht <= 1.4 and (pf.get('h2h') or {}).get('avg', 0) < 3.0 and pf.get('btts', 0) < 0.60 and safe_from_momentum:  # at least one team very unlikely to score in 1H
        e = mk['ht_gg'].get('no')
        r = leg(e, '1H GG No', 1 - prob_1h_gg)
        if r: candidates.append((4, r))

    if h_bht is not None and a_bht is not None:
        combined_btts_ht = (h_bht + a_bht) / 2
        if combined_btts_ht <= 0.12 and total_exp <= 2.6 and total_ht <= 1.4 and (pf.get('h2h') or {}).get('avg', 0) < 3.0 and pf.get('btts', 0) < 0.60 and safe_from_momentum:  # strong historical signal
            prob_ng_ht = p_under(ht_lh, 0.5) + p_under(ht_la, 0.5) - p_under(ht_lh, 0.5) * p_under(ht_la, 0.5)
            e = mk['ht_gg'].get('no')
            r = leg(e, '1H GG No', max(prob_ng_ht, 1 - combined_btts_ht * 1.2))
            if r: candidates.append((4, r))

    # ══════════════════════════════════════════════════════════════════════════
    # PRIORITY 5a — FT Draw No Bet
    # ══════════════════════════════════════════════════════════════════════════
    if gap >= 1.2 and a_exp < 0.8 and safe_1x_h2h:
        # p_win / (p_win + p_loss) = rough DNB prob
        prob_win = sum(pois(h, h_exp) * sum(pois(a, a_exp) for a in range(h)) for h in range(1, 10))
        prob_loss = sum(pois(a, a_exp) * sum(pois(h, h_exp) for h in range(a)) for a in range(1, 10))
        dnb_prob = prob_win / (prob_win + prob_loss) if (prob_win + prob_loss) > 0 else 0
        e = mk['ft_dnb'].get('home')
        r = leg(e, 'FT Home DNB', dnb_prob, 1.60)
        if r: candidates.append((5, r))
    elif gap <= -1.2 and h_exp < 0.8 and safe_x2_h2h:
        prob_win = sum(pois(a, a_exp) * sum(pois(h, h_exp) for h in range(a)) for a in range(1, 10))
        prob_loss = sum(pois(h, h_exp) * sum(pois(a, a_exp) for a in range(h)) for h in range(1, 10))
        dnb_prob = prob_win / (prob_win + prob_loss) if (prob_win + prob_loss) > 0 else 0
        e = mk['ft_dnb'].get('away')
        r = leg(e, 'FT Away DNB', dnb_prob, 1.60)
        if r: candidates.append((5, r))

    # ══════════════════════════════════════════════════════════════════════════
    # PRIORITY 5b — 1H Draw No Bet
    # ══════════════════════════════════════════════════════════════════════════
    if ht_lh >= 0.8 and ht_la <= 0.3:
        prob_win = sum(pois(h, ht_lh) * sum(pois(a, ht_la) for a in range(h)) for h in range(1, 5))
        e = mk['ht_dnb'].get('home')
        r = leg(e, '1H Home DNB', prob_win + 0.3, 1.60) # +0.3 for draw void push
        if r: candidates.append((5, r))
    elif ht_la >= 0.8 and ht_lh <= 0.3:
        prob_win = sum(pois(a, ht_la) * sum(pois(h, ht_lh) for h in range(a)) for a in range(1, 5))
        e = mk['ht_dnb'].get('away')
        r = leg(e, '1H Away DNB', prob_win + 0.3, 1.60) # +0.3 for draw void push
        if r: candidates.append((5, r))



    # ══════════════════════════════════════════════════════════════════════════
    # PRIORITY 6b — EXACT GOALS ≤ 2 (Goals in a row 3 No)
    # mid=21 FT Exact Goals: outcomes '0','1','2','3','4+'
    # P(goals ≤ 2) = P(0)+P(1)+P(2) under Poisson
    # ══════════════════════════════════════════════════════════════════════════
    if total_exp <= 2.0 and safe_from_momentum and safe_under_variance:  # low-scoring game → likely 0,1 or 2 goals total
        prob_le2 = sum(pois(k, total_exp) for k in range(3))  # P(FT goals ≤ 2)
        # Book as FT Under 2.5 (which is equivalent but we try exact goals market first)
        eg_mk = mk.get('exact_goals', {})
        e = mk['ft_ou'].get('2.5', {}).get('under')  # fallback to standard Under 2.5
        r = leg(e, 'Under2.5 (goals ≤ 2)', prob_le2)
        if r: candidates.append((6, r))

    # 2H Exact Goals ≤ 1 — 2H total very low → P(0 or 1 goal in 2H)
    if total_2h <= 1.2 and safe_from_momentum and safe_under_variance:
        prob_2h_le1 = sum(pois(k, total_2h) for k in range(2))  # P(2H goals ≤ 1)
        e = mk['2h_ou'].get('1.5', {}).get('under')  # 2H Under 1.5
        r = leg(e, '2H Under1.5 (≤1 goal)', prob_2h_le1)
        if r: candidates.append((6, r))

    # ══════════════════════════════════════════════════════════════════════════
    # PRIORITY 7 — FT UNDERS
    # ══════════════════════════════════════════════════════════════════════════
    safe_max_goals = hMax < 4 and aMax < 4
    
    if total_exp <= 2.2 and safe_max_goals and safe_from_momentum and safe_under_variance:
        prob_u25 = p_under(total_exp, 2.5)
        e = mk['ft_ou'].get('2.5', {}).get('under')
        r = leg(e, 'FT Under2.5', prob_u25)
        if r: candidates.append((7, r))

    if total_exp <= 1.5 and safe_max_goals and safe_from_momentum and safe_under_variance:
        prob_u15 = p_under(total_exp, 1.5)
        e = mk['ft_ou'].get('1.5', {}).get('under')
        r = leg(e, 'FT Under1.5', prob_u15)
        if r: candidates.append((7, r))

    # ══════════════════════════════════════════════════════════════════════════
    # PRIORITY 8 — FT Double Chance (proven pattern layer)
    # ══════════════════════════════════════════════════════════════════════════
    if gap >= 1.0:
        safe_1x = ((h_fg or 0) >= (a_fg or 0) - 0.10) and (ht_lh >= ht_la - 0.20)
        prob_1x = sum(pois(h, h_exp) * sum(pois(a, a_exp) for a in range(h + 1)) for h in range(10))
        if aMax <= hMax and safe_1x and safe_1x_h2h:
            e = mk['ft_dc'].get('Home or Draw')
            r = leg(e, '1X', prob_1x)
            if r: candidates.append((8, r))
    elif gap <= -1.0:
        safe_x2 = ((a_fg or 0) >= (h_fg or 0) - 0.10) and (ht_la >= ht_lh - 0.20)
        prob_x2 = sum(pois(a, a_exp) * sum(pois(h, h_exp) for h in range(a + 1)) for a in range(10))
        if safe_x2 and safe_x2_h2h:
            e = mk['ft_dc'].get('Draw or Away')
            r = leg(e, 'X2', prob_x2)
            if r: candidates.append((8, r))

    # ══════════════════════════════════════════════════════════════════════════
    # PRIORITY 8b — 1H Draw & Under 1.5
    # ══════════════════════════════════════════════════════════════════════════
    if total_ht <= 0.6 and safe_from_momentum and safe_under_variance:
        prob = pois(0, total_ht) # exactly 0-0
        e = mk['1h_1x2_total'].get('1.5', {}).get('Draw & Under 1.5')
        r = leg(e, '1H Draw & Under 1.5', prob)
        if r: candidates.append((8, r))

    # ══════════════════════════════════════════════════════════════════════════
    # PRIORITY 9 — 2H GG Yes (only when both halves clearly goal-heavy)
    # ══════════════════════════════════════════════════════════════════════════
    safe_gg_offense = (h_zero_games <= 1) and (a_zero_games <= 1)
    if prob_2h_gg >= 0.60 and total_2h >= 1.8 and safe_gg_offense and safe_over_drought:
        e = mk['2h_gg'].get('yes')
        r = leg(e, '2H GG Yes', prob_2h_gg, max_odds=1.45)
        if r: candidates.append((9, r))

    # ══════════════════════════════════════════════════════════════════════════
    # PRIORITY 10 — 1H GG Yes (only when strong HT scoring history)
    # ══════════════════════════════════════════════════════════════════════════
    if h_bht is not None and a_bht is not None:
        combined_btts_ht = (h_bht + a_bht) / 2
        if combined_btts_ht >= 0.45 and total_ht >= 1.6 and safe_gg_offense and safe_over_drought:
            prob_gg_ht = 1 - p_under(ht_lh, 0.5) - p_under(ht_la, 0.5) + p_under(ht_lh, 0.5) * p_under(ht_la, 0.5)
            e = mk['ht_gg'].get('yes')
            r = leg(e, '1H GG Yes', max(prob_gg_ht, combined_btts_ht * 0.9), max_odds=1.45)
            if r: candidates.append((10, r))

    # ══════════════════════════════════════════════════════════════════════════
    # PRIORITY 11 — 2H Over 0.5 (switched from 1H Over 1.5 for high HT expectation matches)
    # ══════════════════════════════════════════════════════════════════════════
    if (total_ht >= 2.0 or total_exp >= 3.2) and total_2h >= 1.60 and safe_over_drought:
        # Prefer 2H Over 0.5 (much higher hit rate than 1H Over 1.5)
        prob_2h = 1 - p_under(total_2h, 0.5)
        e_2h = mk['2h_ou'].get('0.5', {}).get('over')
        r_2h = leg(e_2h, '2H Over0.5', prob_2h)
        if r_2h:
            candidates.append((11, r_2h))
        else:
            prob_1h = p_over(total_ht, 0.5)
            e_1h = mk['ht_ou'].get('0.5', {}).get('over')
            r_1h = leg(e_1h, '1H Over0.5', prob_1h)
            if r_1h: candidates.append((11, r_1h))

    h_fg_rate = (h_fg or 0)
    a_fg_rate = (a_fg or 0)
    safe_1h_fg = (h_fg_rate >= 0.50 or a_fg_rate >= 0.50)
    early_goal_ok = (h_eg is None or h_eg >= 0.25) and (a_eg is None or a_eg >= 0.25)
    if total_ht >= 1.4 and early_goal_ok and safe_1h_fg and safe_over_drought and pf.get('o25', 0) >= 0.50 and total_exp >= 2.5:
        prob = p_over(total_ht, 0.5)
        e = mk['ht_ou'].get('0.5', {}).get('over')
        r = leg(e, '1H Over0.5', prob)
        if r: candidates.append((11, r))

    # ══════════════════════════════════════════════════════════════════════════
    # PRIORITY 11b — NEW MARKETS (27 Jul): HT/FT combos, Result+BTTS, Result+Total,
    #                                  Odd/Even, team both-halves. Higher-odds legs
    #                                  priced from the same per-half Poisson rates.
    # ══════════════════════════════════════════════════════════════════════════

    # Build per-half Poisson grids (used for HT/FT, both-halves, etc.)
    grid_ht = {(h, a): pois(h, ht_lh) * pois(a, ht_la) for h in range(6) for a in range(6)}
    grid_2h = {(h, a): pois(h, sh_lh) * pois(a, sh_la) for h in range(6) for a in range(6)}

    # HT/FT (mid 47) - 9 outcomes (H/H, H/D, H/A, D/H, D/D, D/A, A/H, A/D, A/A)
    # Take when model gives >=55% to a single combo - pays 3.0-7.0x
    if mk.get('htft'):
        htft_probs = {}
        for (h1, a1), pa in grid_ht.items():
            for (h2, a2), pb in grid_2h.items():
                ht = 'Home' if h1 > a1 else 'Away' if h1 < a1 else 'Draw'
                ft = 'Home' if h1 + h2 > a1 + a2 else 'Away' if h1 + h2 < a1 + a2 else 'Draw'
                combo = f"{ht}/{ft}"
                htft_probs[combo] = htft_probs.get(combo, 0) + pa * pb
        # Take the highest-probability HT/FT combo priced at >=2.5x if model gives >=55%
        best_combo, best_prob = max(htft_probs.items(), key=lambda kv: kv[1])
        if best_prob >= 0.55:
            e = mk['htft'].get(best_combo)
            r = leg(e, f'HT/FT {best_combo}', best_prob)
            if r and r['odds'] >= 2.5:  # only take high-odds HT/FT
                candidates.append((11, r))

    # Result + BTTS (mid 35/78/540) - 6 outcomes priced from P(1x2) * P(BTTS|score)
    # Take "Home & Yes" or "Away & Yes" when both sigs agree (>=50% joint prob)
    if mk.get('result_btts'):
        prob_gg_ft = (1 - mexp(-h_exp)) * (1 - mexp(-a_exp))
        # "Home & Yes" = P(home wins) * P(BTTS | home wins)
        g = grid if 'grid' in dir() else {(h, a): pois(h, h_exp) * pois(a, a_exp) for h in range(10) for a in range(10)}
        # Build probability table for Result+BTTS
        p_home_win_gg = sum(p for (h, a), p in g.items() if h > a and h >= 1 and a >= 1) / max(1e-9, sum(p for (h, a), p in g.items() if h > a))
        p_home_win = sum(p for (h, a), p in g.items() if h > a)
        p_away_win_gg = sum(p for (h, a), p in g.items() if a > h and h >= 1 and a >= 1) / max(1e-9, sum(p for (h, a), p in g.items() if a > h))
        p_away_win = sum(p for (h, a), p in g.items() if a > h)
        # Joint probabilities
        p_home_yes = p_home_win * p_home_win_gg
        p_away_yes = p_away_win * p_away_win_gg
        if p_home_yes >= 0.50 and not safe_1h_btts and gap >= 0.5:
            e = mk['result_btts'].get('Home & Yes')
            r = leg(e, 'Home & BTTS Yes', p_home_yes)
            if r and r['odds'] >= 1.80:
                candidates.append((11, r))
        if p_away_yes >= 0.50 and not safe_1h_btts and gap <= -0.5:
            e = mk['result_btts'].get('Away & Yes')
            r = leg(e, 'Away & BTTS Yes', p_away_yes)
            if r and r['odds'] >= 1.80:
                candidates.append((11, r))

    # Result + Total (mid 37/544) - 9 outcomes - "Home & Over 2.5", etc.
    # Take when joint prob >= 50% and odds >= 1.80
    if mk.get('result_total'):
        g = {(h, a): pois(h, h_exp) * pois(a, a_exp) for h in range(10) for a in range(10)}
        for line in ('1.5', '2.5'):
            bucket = mk['result_total'].get(line)
            if not bucket: continue
            for combo_desc, joint_label in [
                ('Home & Over ' + line, 'Home_O'),
                ('Away & Over ' + line, 'Away_O'),
                ('Draw & Under ' + line, 'Draw_U'),
            ]:
                # Compute joint probability
                if 'Home_O' in joint_label:
                    ln = float(line)
                    p = sum(p for (h, a), p in g.items() if h > a and h + a > ln)
                elif 'Away_O' in joint_label:
                    ln = float(line)
                    p = sum(p for (h, a), p in g.items() if a > h and h + a > ln)
                else:
                    ln = float(line)
                    p = sum(p for (h, a), p in g.items() if h == a and h + a < ln)
                if p >= 0.45:
                    e = bucket.get(combo_desc)
                    if e:
                        r = leg(e, f'Result+Total [{combo_desc}]', p)
                        if r and r['odds'] >= 1.80:
                            candidates.append((11, r))
                            break

    # FT Odd/Even (mid 26/27/28) - take the parity model prefers when lambda clear
    if mk.get('odd_even') and total_exp >= 1.5:
        # P(total is odd) depends on sum parity of two Poissons
        # Approximation: if (lh*la) is large enough, parity is ~50/50
        # But if one side has 0 lambda, parity is predictable
        if h_exp > 0.5 and a_exp > 0.5:
            # both score often - parity hard to call, skip unless model says >=65%
            pass
        # Take Odd if P(odd) >= 0.65
        # Compute by enumerating total goals 0-9
        p_odd = sum(pois(k, h_exp + a_exp) for k in range(10) if k % 2 == 1)
        if p_odd >= 0.65:
            e = mk['odd_even'].get('odd')
            if e:
                r = leg(e, 'FT Odd', p_odd)
                if r: candidates.append((11, r))
        elif (1 - p_odd) >= 0.65:
            e = mk['odd_even'].get('even')
            if e:
                r = leg(e, 'FT Even', 1 - p_odd)
                if r: candidates.append((11, r))

    # Home/Away scores in BOTH halves (mid 48/49/56/57)
    # Yes prob = P(home scores in 1H) * P(home scores in 2H)
    if mk.get('home_both_halves'):
        p_home_ht_score = 1 - p_under(ht_lh, 0.5)
        p_home_2h_score = 1 - p_under(sh_lh, 0.5)
        p_home_both = p_home_ht_score * p_home_2h_score
        if p_home_both >= 0.55 and h_exp >= 1.2:
            e = mk['home_both_halves'].get('yes')
            r = leg(e, 'Home scores both halves', p_home_both)
            if r and r['odds'] >= 1.80:
                candidates.append((11, r))
    if mk.get('away_both_halves'):
        p_away_ht_score = 1 - p_under(ht_la, 0.5)
        p_away_2h_score = 1 - p_under(sh_la, 0.5)
        p_away_both = p_away_ht_score * p_away_2h_score
        if p_away_both >= 0.55 and a_exp >= 1.2:
            e = mk['away_both_halves'].get('yes')
            r = leg(e, 'Away scores both halves', p_away_both)
            if r and r['odds'] >= 1.80:
                candidates.append((11, r))

    # ══════════════════════════════════════════════════════════════════════════
    # PRIORITY 12 — FT Over (removed)
    # ══════════════════════════════════════════════════════════════════════════

    # FT GG / NG as last safety-net pick
    prob_gg = (1 - mexp(-h_exp)) * (1 - mexp(-a_exp))
    if prob_gg <= 0.28 and pf.get('btts', 0) <= 0.32:
        e = mk['ft_gg'].get('no')
        r = leg(e, 'FT GG No', 1 - prob_gg)
        if r: candidates.append((13, r))

    # ──────────────────────────────────────────────────────────────────────────
    # Return the HIGHEST PRIORITY candidate that passes all gates
    # Within same priority, prefer highest probability
    # ──────────────────────────────────────────────────────────────────────────
    if not candidates:
        return None
    candidates.sort(key=lambda x: (x[0], -x[1]['prob']))
    return candidates[0][1]   # the winning leg dict


# ── match fixture ──────────────────────────────────────────────────────────────
def match_fixture(ev, fixtures):
    return A.match_fixture(ev, fixtures)


# ── booking ────────────────────────────────────────────────────────────────────
def main():
    dry      = '--dry' in sys.argv
    until_str = sys.argv[sys.argv.index('--until') + 1] if '--until' in sys.argv else '9'
    if ':' in until_str:
        until_h, until_m = map(int, until_str.split(':'))
    else:
        until_h, until_m = int(until_str), 0
    top      = int(sys.argv[sys.argv.index('--top')   + 1]) if '--top'   in sys.argv else None
    days     = int(sys.argv[sys.argv.index('--days')  + 1]) if '--days'  in sys.argv else None
    deep     = '--deep' not in sys.argv   # deep fetch is ON by default, use --no-deep to skip

    now      = time.time()
    now_wat  = datetime.fromtimestamp(now, tz=A.WAT)
    cutoff_wat = now_wat.replace(hour=until_h, minute=until_m, second=0, microsecond=0)
    if days is not None:
        cutoff_wat += timedelta(days=days)
    elif cutoff_wat <= now_wat:
        cutoff_wat += timedelta(days=1)
    cutoff = cutoff_wat.timestamp()
    print(f"window: {now_wat:%a %H:%M} -> {cutoff_wat:%a %d %H:%M} WAT  (book_v3 deep-market)", flush=True)

    print("loading SportyBet (all markets)...", flush=True)
    events = fetch_events_rich()
    print(f"  {len(events)} SportyBet events loaded", flush=True)

    print("loading Flashscore fixtures...", flush=True)
    fixtures = []
    for d in range((days if days is not None else 1) + 1):
        fixtures += F.get_fixtures(d)
    fixtures = [f for f in fixtures if now + 300 < f['ts'] <= cutoff]

    pairs, seen = [], set()
    for ev in events:
        et = ev['estimateStartTime'] / 1000
        if not (now + 300 < et <= cutoff): continue
        f = A.match_fixture(ev, fixtures)
        if not f or f['id'] in seen: continue
        seen.add(f['id']); pairs.append((ev, f))
    print(f"{len(pairs)} bettable fixtures in window", flush=True)

    legs = []
    BLACKLIST = ['cup', 'copa', 'pokal', 'taca', 'taça', 'friend', 'youth', 'u21', 'u19', 'u20', 'u23', 'reserve', 'women', 'qualifi']
    for i, (ev, f) in enumerate(pairs):
        if any(b in f.get('league', '').lower() for b in BLACKLIST): continue
        # ── standard form data (v2) ──────────────────────────────────────────
        raw = F.fetch(f"df_hh_1_{f['id']}")
        if not raw: continue
        hr, ar, h2h = A.recent_kc(raw)
        if not hr or not ar: continue
        lh = max(0.2, min((A.vmean(hr, 'gf', 'home') + A.vmean(ar, 'ga', 'away')) / 2, 4.5))
        la = max(0.2, min((A.vmean(ar, 'gf', 'away') + A.vmean(hr, 'ga', 'home')) / 2, 4.5))
        lh, la = A.strength_adjust(lh, la, hr, ar)
        pf = A.build_pf(hr, ar, lh, la, h2h, f['home'])

        # ── rich HT/goal-minute data (v3 deep fetch) ─────────────────────────
        rich = {}
        if deep:
            try:
                _, _, rich = F3.fetch_rich_history(f['id'])
            except Exception:
                rich = {}

        # ── pick best market ──────────────────────────────────────────────────
        result = pick_best(ev, pf, rich, hr, ar, f['league'])
        if not result: continue

        # ── stat block for display ────────────────────────────────────────────
        sb = A.stat_block(pf, hr, ar)
        # Add rich stats if available
        if rich.get('deep_games', 0) > 0:
            def pct(v): return f"{v*100:.0f}%" if v is not None else 'n/a'
            def avg(v): return f"{v:.2f}" if v is not None else 'n/a'
            sb.append(
                f"1H: home {avg(rich.get('home_ht_avg'))} / away {avg(rich.get('away_ht_avg'))}"
                f"  2H: home {avg(rich.get('home_2h_avg'))} / away {avg(rich.get('away_2h_avg'))}"
                f"  BTTS-HT: {pct(rich.get('home_btts_ht'))}/{pct(rich.get('away_btts_ht'))}"
                f"  FG: {pct(rich.get('home_first_goal'))}/{pct(rich.get('away_first_goal'))}"
            )

        legs.append((result['prob'], f['ts'], f"{f['home']} v {f['away']}",
                     result['label'], result['odds'], f['league'], sb, result['bs']))

        if (i + 1) % 10 == 0:
            print(f"  ...{i+1}/{len(pairs)}, {len(legs)} legs", flush=True)
        time.sleep(0.05)

    legs.sort(key=lambda x: -x[0])
    cap = min(top, MAX_LEGS) if top else MAX_LEGS
    if len(legs) > cap:
        print(f"  (capping {len(legs)} -> {cap})")
    legs = legs[:cap]

    out = [f"\n{len(legs)} LEGS  —  {now_wat:%a %H:%M} -> {cutoff_wat:%a %d %H:%M} WAT  (book_v3)"]
    combo = p_all = 1.0; sels = []
    for prob, ts, match, label, odds, league, sb, bs in legs:
        combo *= odds; p_all *= prob
        ko = datetime.fromtimestamp(ts, tz=A.WAT).strftime('%H:%M')
        out.append(f"\n[{prob*100:3.0f}%] {ko}  {match}   {label} @{odds:.2f}   {league}")
        out += [f"        {line}" for line in sb]
        sels.append(bs)
    out.append(f"\n  -> {len(legs)} legs, combined ~{combo:,.0f}x, all-land ~{p_all*100:.2f}%")
    print("\n".join(out))

    if not dry and sels:
        bk = A.book(sels)
        if bk and bk['code']:
            extra = "" if bk['booked'] == bk['req'] else f" (booked {bk['booked']}/{bk['req']})"
            print(f"\n>> BOOKING CODE: {bk['code']}  ({bk['verified']} load back)  {bk['url']}{extra}")
            A.log_booking(bk['code'], bk['url'],
                          f"book_v3 window {now_wat:%a %H:%M}->{cutoff_wat:%a %d %H:%M} WAT",
                          [(ts, match, label, odds, sb)
                           for (prob, ts, match, label, odds, league, sb, bs) in legs])
        else:
            print(f"\n>> booking failed: {bk['msg'] if bk else 'no selections'}")
    elif dry:
        print("\n(dry run - not booked)")


if __name__ == '__main__':
    main()
