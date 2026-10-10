#!/usr/bin/env python3
"""THE WINNERS' FINDING - an option goes on a code only when the match's own record backs it.

  python3 findings.py [--warm]      # --warm: compute today's records for every game on the board

10 Oct, user, after the winners were profiled option by option: "Using this make
sure all the games booked today follows this finding". The finding, from 866 won
football legs 25 Sep - 10 Oct: every option won in the matches whose recent record
was already doing that thing -
    draw after 5 minutes    teams whose games almost never have a goal by minute 5
    0-0 at 10 minutes       ... by minute 10
    first-half Unders       both sides' first halves stay at or under the line, and
                            each side has a goalless first half in its window
    Overs                   both sides' games clear the line
    Home or Draw            a home side that rarely loses at home, an away side that
                            rarely wins away (Draw or Away the reverse)
    Home or Away            teams that rarely draw
    team first-half Unders  the named team rarely scores early

The record is the home side's last 7 home games and the away side's last 7 away
games before kickoff (friendlies out, cut at a season break). Each of those games is
read in the role the side plays today - a home-side game as it stands, an away-side
game with the away side in the away column - and the option is scored on it as if it
were today's match. The share of those games the option would have won is its HIT
RATE, and an option is allowed when the hit rate reaches the floor its own winners
reached (FLOORS: the lower quartile of the won legs' hit rates, so three in four of
the winners clear it). An option with no record - no Flashscore match, too few
games, or a market with no measured finding (corners, cards, draws) - is not allowed.

Only active on ON_DAYS. The records for a day are computed once (--warm, or the
first script that needs them) and kept in findings_<day>.json, so every product
reads the same numbers.
"""
import sys, os, re, json, math, datetime as dt, threading
from concurrent.futures import ThreadPoolExecutor
import os as _o; sys.path.insert(0, _o.path.dirname(_o.path.abspath(__file__)))
import acca as A
import fetcher_v2 as F2
import fetcher_v3 as F3

ON_DAYS = {'2026-10-10'}     # user, 10 Oct: "all the games booked today"
# 10 Oct evening, user: "Apply it" - the minute-market record check (draw after 5 min,
# 0-0 at 10 min) stays on every day from here, with the three fixes in verdict().
MINUTE_FROM = '2026-10-10'
ROOT = os.path.dirname(os.path.abspath(__file__))
WINDOW = 7                   # venue games a side
MIN_SIDE = 3                 # games a side needed to read a record at all
MIN_SIDE_TIMED = 3           # minute markets: games EACH side whose early-goal question is answered
DEFAULT_FLOOR = 0.75         # a line with no won legs of its own to set a floor
# Lower quartile of the won legs' hit rates, 25 Sep - 10 Oct (findings_floors in
# the scratch analysis). Three in four winners of each option cleared these.
FLOORS = {
    'draw after 5 min': 0.83, '0-0 at 10 min': 0.73,
    '1H Under 2.5': 0.89, '1H Under 1.5': 0.64, '1H Over 0.5': 0.71,
    'team 1H Under 1.5': 0.89, 'team 1H Under 0.5': 0.79,
    'DC Home or Draw': 0.67, 'DC Draw or Away': 0.57, 'DC Home or Away': 0.79,
    '1X2 Home': 0.50, '1X2 Away': 0.50,            # 1X2 Away mirrors Home (one won leg of its own)
    'FT Over 0.5': 0.91, 'FT Over 1.5': 0.75,
    'FT Under 3.5': 0.71, 'FT Under 4.5': 0.67, 'FT Under 5.5': 0.79,
    '2H Under 2.5': 0.64, '2H Over 0.5': 0.71,
}


def active():
    return dt.datetime.now(A.WAT).date().isoformat() in ON_DAYS


MINUTE_FAMS = ('draw after', '0-0 at')


def active_for(fam):
    """Whether the check applies to this option today: minute markets every day from
    MINUTE_FROM, everything else only on ON_DAYS."""
    if fam and fam.startswith(MINUTE_FAMS):
        return dt.datetime.now(A.WAT).date().isoformat() >= MINUTE_FROM
    return active()


def any_active():
    return active() or dt.datetime.now(A.WAT).date().isoformat() >= MINUTE_FROM


def _early(g, lim):
    """A record game's answer to 'a goal by minute lim?': True early, False clean, None unknown.
    A known early goal counts even when another goal in that game has no minute
    (10 Oct: Bristol Rovers 5-3 Newport had a 1' goal and was dropped whole)."""
    if g.get('first') is not None:
        return g['first'] <= lim
    known = g.get('known')
    if known is not None and known <= lim:
        return True
    return None


# ── which option a selection is ─────────────────────────────────────────────
PERIOD = {'18': 'FT', '68': '1H', '90': '2H'}
TEAM = {'19': ('FT', 'home'), '20': ('FT', 'away'), '69': ('1H', 'home'), '70': ('1H', 'away'),
        '91': ('2H', 'home'), '92': ('2H', 'away')}


def option_of(mid, spec, desc):
    """(family, test) for a market id / specifier / outcome, or None when no finding covers it.
    test(g) scores one record game g = dict(h, a, hh, ah, first) in today's roles."""
    mid, spec, desc = str(mid), spec or '', desc or ''
    m = re.match(r'(Over|Under) ([\d.]+)$', desc)
    if mid == '1':
        if desc == 'Home': return '1X2 Home', lambda g: g['h'] > g['a']
        if desc == 'Away': return '1X2 Away', lambda g: g['h'] < g['a']
        return None
    if mid == '10':
        return {'Home or Draw': ('DC Home or Draw', lambda g: g['h'] >= g['a']),
                'Draw or Away': ('DC Draw or Away', lambda g: g['h'] <= g['a']),
                'Home or Away': ('DC Home or Away', lambda g: g['h'] != g['a'])}.get(desc)
    if mid == '29':
        return None                       # GG/NG: no won legs to read a finding from (0 of 2)
    if mid == '900069' and spec == 'minute=5' and desc == 'Draw':
        return 'draw after 5 min', lambda g: (lambda e: None if e is None else not e)(_early(g, 5))
    if mid == '900313' and m and m.group(1) == 'Under' and m.group(2) == '0.5':
        mm = re.match(r'minute=(\d+)\|total=0\.5$', spec)
        if mm:
            x = int(mm.group(1))
            return f'0-0 at {x} min', lambda g, x=x: (lambda e: None if e is None else not e)(_early(g, x))
        return None
    if not m or not m.group(2).endswith('.5'):
        return None
    side, line = m.group(1), float(m.group(2))
    over = side == 'Over'
    if mid in PERIOD:
        per = PERIOD[mid]
        def tot(g, per=per):
            if per == 'FT': return g['h'] + g['a']
            if g['hh'] is None: return None
            return g['hh'] + g['ah'] if per == '1H' else (g['h'] - g['hh']) + (g['a'] - g['ah'])
        fam = f"{per} {side} {line}"
        return fam, (lambda g: None if tot(g) is None else (tot(g) > line) == over)
    if mid in TEAM:
        per, who = TEAM[mid]
        def goals(g, per=per, who=who):
            full = g['h'] if who == 'home' else g['a']
            if per == 'FT': return full
            half = g['hh'] if who == 'home' else g['ah']
            if half is None: return None
            return half if per == '1H' else full - half
        fam = f"team {per} {side} {line}" if per != 'FT' else f"team {side} {line}"
        return fam, (lambda g: None if goals(g) is None else (goals(g) > line) == over)
    return None


# ── the record ──────────────────────────────────────────────────────────────
def _venue(rows, venue, ko):
    rs = [r for r in rows if r['venue'] == venue and not r.get('friendly') and 0 < r['kc'] < ko - 3600]
    return F3.trim_at_season_gap(rs)[:WINDOW]


def record(fid, ko):
    """The two venue windows as record games in today's roles:
    [dict(side, h, a, hh, ah, first)] - h/a today's home/away columns, hh/ah at half-time
    (None when the half is unknown), first = minute of the first goal (99 for 0-0,
    None when the goals were not all timed), known = the earliest goal minute that IS
    timed (None when none is) - a known early goal settles the minute markets even
    when another goal in the game has no minute."""
    raw = F2.fetch(f"df_hh_1_{fid}")
    if not raw:
        return None
    hr, ar, _ = F3.parse_history(raw)
    out = []
    for rows, side in ((hr, 'home'), (ar, 'away')):
        for r in _venue(rows, side, ko):
            s = F3.parse_match_summary(r['match_id'], r['hg'], r['ag'])
            # r['hg']/r['ag'] are the past match's true home/away goals, and the side
            # played it at the same venue it plays today - so the columns line up as is.
            g = dict(side=side, h=r['hg'], a=r['ag'], hh=None, ah=None, first=None, known=None)
            if r['hg'] + r['ag'] == 0:
                g['first'] = 99                       # 0-0: no goal to time
            if s:
                g['hh'], g['ah'] = s['ht_home'], s['ht_away']
                mins = [x['minute'] for x in s['goals'] if x.get('minute') is not None]
                g['known'] = min(mins) if mins else None
                if s.get('goals_ok') and len(mins) == len(s['goals']):
                    g['first'] = min(mins) if mins else 99
            out.append(g)
    return out


def hit_rate(games, test):
    """(rate, n, per-side n) of the record games the option would have won."""
    res = [(g['side'], test(g)) for g in games]
    res = [(s, v) for s, v in res if v is not None]
    if not res:
        return None, 0, {}
    n = {s: sum(1 for x, _ in res if x == s) for s in ('home', 'away')}
    return sum(v for _, v in res) / len(res), len(res), n


def verdict(games, fam, test):
    """(ok, why) for one option on one record."""
    if fam.startswith(MINUTE_FAMS):
        # 10 Oct evening fixes, from the four 5-min draw losses (Plzen, Augsburg,
        # Bristol Rovers, Barcelona) - backtested on 88 settled 5-min draws: legs this
        # passes won 37/37, legs it stops 47/51 (0-0 at 10: 29/33 v 28/35).
        #   each side needs MIN_SIDE_TIMED answered games of its own (not 6 between them),
        #   a game with no answer counts AGAINST the option instead of vanishing.
        if not games:
            return False, 'no record'
        res = [(g['side'], test(g)) for g in games]
        per = {s: sum(1 for x, v in res if x == s and v is not None) for s in ('home', 'away')}
        if per['home'] < MIN_SIDE_TIMED or per['away'] < MIN_SIDE_TIMED:
            return False, f"only {per['home']}+{per['away']} timed games (need {MIN_SIDE_TIMED} a side)"
        rate = sum(1 for _, v in res if v is True) / len(res)
        floor = FLOORS.get(fam, DEFAULT_FLOOR)
        unk = sum(1 for _, v in res if v is None)
        why = f"record {rate:.0%} of {len(res)}" + (f" ({unk} untimed counted against)" if unk else '') + f" (floor {floor:.0%})"
        return rate >= floor, why
    rate, n, per = hit_rate(games, test)
    if rate is None:
        return False, 'no record'
    if per.get('home', 0) < MIN_SIDE or per.get('away', 0) < MIN_SIDE:
        return False, f"only {per.get('home', 0)}+{per.get('away', 0)} games"
    floor = FLOORS.get(fam, DEFAULT_FLOOR)
    why = f"record {rate:.0%} of {n} (floor {floor:.0%})"
    if rate < floor:
        return False, why
    if fam.startswith('1H Under'):
        # first-half Unders: each side has a goalless first half in its window
        for side in ('home', 'away'):
            if not any(g['side'] == side and g['hh'] is not None and g['hh'] + g['ah'] == 0 for g in games):
                return False, why + f', no 0-0 first half in the {side} window'
    return True, why


# ── the day's store ─────────────────────────────────────────────────────────
_lock = threading.Lock()
_store = None


def _path(day=None):
    day = day or dt.datetime.now(A.WAT).date().isoformat()
    return os.path.join(ROOT, f'findings_{day}.json')


def _load():
    global _store
    if _store is None:
        try:
            _store = json.load(open(_path()))
        except Exception:
            _store = {}
    return _store


def _save():
    p = _path(); tmp = p + f'.{os.getpid()}.tmp'
    try:
        disk = json.load(open(p))
    except Exception:
        disk = {}
    disk.update(_store)
    json.dump(disk, open(tmp, 'w'))
    os.replace(tmp, p)


def _fixtures():
    out, seen = [], set()
    for off in (0, 1):
        for f in F2.get_fixtures(off):
            if f['id'] not in seen:
                seen.add(f['id']); out.append(f)
    return out


def prepare(evs, threads=8):
    """Make sure every event has its record in the day's store (one join, one fetch each)."""
    import dynamic_v4 as D
    st = _load()
    todo = [e for e in evs if str(e.get('eventId')) not in st and 'SRL' not in (e.get('homeTeamName') or '')]
    if not todo:
        return st
    fx = _fixtures()
    pairs = D.join(todo, fx, lambda e: dt.datetime.fromtimestamp(int(e['estimateStartTime']) / 1000, tz=A.WAT),
                   lambda f: dt.datetime.fromtimestamp(f['ts'], tz=A.WAT))
    joined = {str(e['eventId']): f for e, f, _ in pairs}
    for e in todo:
        if str(e['eventId']) not in joined:
            st[str(e['eventId'])] = None                 # no Flashscore match: no record
    def one(p):
        e, f, _ = p
        try:
            rec = record(f['id'], int(f['ts']))
        except Exception:
            rec = None
        with _lock:
            st[str(e['eventId'])] = None if rec is None else dict(fid=f['id'], league=f['league'], games=rec)
    with ThreadPoolExecutor(threads) as ex:
        list(ex.map(one, pairs))
    with _lock:
        _save()
    return st


def ok(ev, mid, spec, desc):
    """(allowed, why). Everything is allowed when the finding is not switched on today
    for this option (minute markets from MINUTE_FROM, the rest on ON_DAYS)."""
    opt = option_of(mid, spec, desc)
    if not active_for(opt[0] if opt else None):
        return True, 'finding off'
    if not opt:
        return False, 'no finding for this market'
    rec = _load().get(str(ev.get('eventId')))
    if rec is None:
        if str(ev.get('eventId')) not in _load() and ev.get('estimateStartTime') and ev.get('homeTeamName'):
            prepare([ev])
            rec = _load().get(str(ev.get('eventId')))
        if rec is None:
            return False, 'no record'
    return verdict(rec['games'], *opt)


def warm(until_h=23):
    """Every football game on today's board from now to until_h:59 (and the small hours)."""
    import book_v3 as B
    now = dt.datetime.now(A.WAT)
    cut = now.replace(hour=until_h, minute=59, second=59) + dt.timedelta(hours=8)
    evs = [e for e in B.fetch_events_rich()
           if now < dt.datetime.fromtimestamp(int(e.get('estimateStartTime', 0)) / 1000, tz=A.WAT) <= cut]
    for mid in ('900313',):                               # not in the rich feed
        evs += [e for e in B._fetch_events_for(mid)
                if now < dt.datetime.fromtimestamp(int(e.get('estimateStartTime', 0)) / 1000, tz=A.WAT) <= cut]
    seen, uniq = set(), []
    for e in evs:
        if str(e['eventId']) not in seen:
            seen.add(str(e['eventId'])); uniq.append(e)
    st = prepare(uniq)
    have = sum(1 for e in uniq if st.get(str(e['eventId'])))
    print(f"findings {dt.datetime.now(A.WAT):%H:%M}: {len(uniq)} games on the board, {have} with a record -> {_path()}")


if __name__ == '__main__':
    if '--warm' in sys.argv:
        warm()
