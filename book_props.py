#!/usr/bin/env python3
"""NBA PLAYER PROPS - one prop per game, the line the player's own games back most.

  python3 book_props.py [--from 'YYYY-MM-DD HH:MM'] [--until 'YYYY-MM-DD HH:MM'] [--floor 1.30] [--min 0.70] [--each N] [--dry]

23 Oct, user: "nba is back , time for props booking". SportyBet prices four player stats
on NBA games (event group 'Players'): points, rebounds, assists and 3-pointers made, each
as Over/Under on one line (921-924) and as an "N+" ladder (768/770/772/774). Every option
is read against the player's own games from ESPN's public game logs (no login), regular
season and playoffs only - pre-season is out:
    L10   his last 10 games (last season's run-in until he has 10 this season)
    SEAS  last season, every game (2025-26 regular season + playoffs)
    NOW   this season so far (2026-27 regular season), at least 1 game
support = the LOWEST of the three. An option goes on only when every read is --min (70%)
or better (user's 14 Oct rule: all stats must agree, else no leg) and it is priced
--floor or more. One option per player and stat (highest support, ties to the longer
price). 23 Oct, user: "I need multiple probs for a single game" - every agreed prop on a
game goes on that game's own code (--each N caps it), then every game on one code.
The window starts an hour out (standing rule) and runs to the next 06:00 WAT at least
12 hours away, so a run on Friday covers Friday night's US games.
"""
import sys, re, json, math, unicodedata, urllib.request, datetime as dt, collections
from concurrent.futures import ThreadPoolExecutor
import os as _o; sys.path.insert(0, _o.path.dirname(_o.path.abspath(__file__)))
import acca as A
A.FINDINGS_EXEMPT = True      # the findings gate is football only

SB = 'https://www.sportybet.com/api/ng/factsCenter/'
SB_H = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36',
        'Accept': 'application/json', 'Referer': 'https://www.sportybet.com/ng/sport/basketball'}
ESPN = 'https://site.api.espn.com/apis/site/v2/sports/basketball/nba/'
ESPN_LOG = 'https://site.web.api.espn.com/apis/common/v3/sports/basketball/nba/athletes/{}/gamelog?season={}'
THIS, LAST = 2027, 2026       # ESPN names a season by the year it ends: 2027 = 2026-27
OU = {'921': 'points', '922': 'assists', '923': 'rebounds', '924': '3-pointers'}
LADDER = {'768': 'points', '770': 'assists', '772': 'rebounds', '774': '3-pointers'}
COL = {'points': 'PTS', 'assists': 'AST', 'rebounds': 'REB', '3-pointers': '3PT'}


def _get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {'User-Agent': SB_H['User-Agent']})
    return json.load(urllib.request.urlopen(req, timeout=30))


def norm(s):
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode().lower()
    return [w for w in re.sub(r'[^a-z ]', ' ', s).split() if w not in ('jr', 'sr', 'ii', 'iii', 'iv')]


def sb_name(s):
    """'Bagley III, Marvin' -> ['marvin', 'bagley']"""
    last, _, first = s.partition(',')
    return norm(f"{first} {last}")


def events(start, cut):
    out, page = [], 1
    while True:
        d = _get(SB + f'pcUpcomingEvents?sportId=sr:sport:2&marketId=219&pageSize=100&pageNum={page}&option=1', SB_H)['data']
        for t in d['tournaments']:
            if t['name'].strip() != 'NBA':
                continue
            for e in t['events']:
                ko = dt.datetime.fromtimestamp(int(e['estimateStartTime']) / 1000, tz=A.WAT)
                if start < ko <= cut:
                    out.append((ko, e))
        if page * 100 >= d.get('totalNum', 0):
            return sorted(out, key=lambda x: x[0])
        page += 1


def options(ev):
    """Every priced player option on the event: dict(player, stat, sel, line, over, odds, ids)."""
    ms = _get(SB + f"event?eventId={ev['eventId']}&productId=3", SB_H)['data']['markets']
    out = []
    for m in ms:
        if m.get('group') != 'Players' or m.get('status') not in (0, None):
            continue
        if m['id'] in OU:
            player = m['desc'].split(' total ')[0]
            for o in m['outcomes']:
                if not o.get('isActive'):
                    continue
                side, line = o['desc'].split()
                out.append(dict(player=player, stat=OU[m['id']], line=float(line), over=side == 'Over',
                                sel=f"{player} {OU[m['id']]} {o['desc']}", odds=float(o['odds']),
                                ids=dict(eventId=ev['eventId'], productId=3, marketId=m['id'], specifier=m['specifier'], outcomeId=o['id'])))
        elif m['id'] in LADDER:
            for o in m['outcomes']:
                if not o.get('isActive'):
                    continue
                player, n = o['desc'].rsplit(' ', 1)
                out.append(dict(player=player, stat=LADDER[m['id']], line=int(n.rstrip('+')) - 0.5, over=True,
                                sel=f"{player} {LADDER[m['id']]} {n}", odds=float(o['odds']),
                                ids=dict(eventId=ev['eventId'], productId=3, marketId=m['id'], specifier=m['specifier'], outcomeId=o['id'])))
    return out


_teams = None


def roster(team_name):
    global _teams
    if _teams is None:
        _teams = {t['team']['displayName']: t['team']['id'] for t in _get(ESPN + 'teams')['sports'][0]['leagues'][0]['teams']}
        _teams['LA Clippers'] = _teams.get('LA Clippers') or _teams.get('Los Angeles Clippers')
    tid = _teams.get(team_name)
    if not tid:
        return []
    return [(a['id'], a['displayName']) for a in _get(ESPN + f'teams/{tid}/roster').get('athletes', [])]


def games(aid):
    """[(date, opp, minutes, {PTS, REB, AST, 3PT}, season)] newest first, pre-season out."""
    rows = {}
    for season in (THIS, LAST):
        try:
            d = _get(ESPN_LOG.format(aid, season))
        except Exception:
            continue
        lab, evs = d.get('labels') or [], d.get('events') or {}
        for st in d.get('seasonTypes', []):
            if 'Preseason' in st.get('displayName', ''):
                continue
            for c in st.get('categories', []):
                for ev in c.get('events', []):
                    s = dict(zip(lab, ev['stats']))
                    meta = evs.get(ev['eventId'], {})
                    try:
                        vals = dict(PTS=int(s['PTS']), REB=int(s['REB']), AST=int(s['AST']), **{'3PT': int(s['3PT'].split('-')[0])})
                        mins = int(float(s.get('MIN') or 0))
                    except (KeyError, ValueError):
                        continue
                    rows[ev['eventId']] = (meta.get('gameDate', ''), (meta.get('opponent') or {}).get('abbreviation', ''), mins, vals, season)
    return sorted(rows.values(), key=lambda r: r[0], reverse=True)


def reads(gs, o):
    col = COL[o['stat']]
    hit = lambda g: (g[3][col] > o['line']) == o['over']
    out = {}
    for k, sel in (('L10', gs[:10]), ('SEAS', [g for g in gs if g[4] == LAST]), ('NOW', [g for g in gs if g[4] == THIS])):
        out[k] = (sum(map(hit, sel)), len(sel))
    return out


def stat_lines(o, r, gs, sup):
    col = COL[o['stat']]
    seas = [g for g in gs if g[4] == LAST]
    avg = sum(g[3][col] for g in seas) / len(seas)
    return [f"reads: last 10 {r['L10'][0]} of {r['L10'][1]}, last season {r['SEAS'][0]} of {r['SEAS'][1]}, "
            f"this season {r['NOW'][0]} of {r['NOW'][1]} -> support {sup:.2f}",
            f"last 10 {o['stat']}: {' '.join(str(g[3][col]) for g in gs[:10])}   (minutes {' '.join(str(g[2]) for g in gs[:10])})",
            f"last season average {avg:.1f} over {len(seas)} games; this season "
            + ', '.join(f"{g[3][col]} v {g[1]} in {g[2]} min" for g in gs if g[4] == THIS)]


def book_code(legs, label, dry):
    combo = math.prod(l['odds'] for l in legs)
    print(f"\n=== {label}: {len(legs)} legs, {combo:,.2f}x, every leg landing about 1 in "
          f"{1 / math.prod(l['chance'] for l in legs):,.1f} by the reads")
    for l in legs:
        print(f"   {dt.datetime.fromtimestamp(l['ts'], tz=A.WAT):%a %H:%M}  {l['match'][:40]:40} {l['sel'][:46]:46} @{l['odds']}")
    if dry:
        return
    bk = A.book([l['ids'] for l in legs])
    if bk and bk.get('code'):
        print(f"   >> code {bk['code']}   {bk['url']}   ({bk.get('booked')}/{bk.get('req')} on the code, "
              f"{bk.get('verified')} read back)", flush=True)
        A.log_booking(bk['code'], bk['url'], f"basketball nba props - {label} - {combo:,.2f}x ({len(legs)} legs)",
                      [(l['ts'], l['match'], l['sel'], l['odds'], l['stats']) for l in legs])
    else:
        print(f"   >> booking failed: {bk.get('msg') if bk else 'no selections'}", flush=True)


def main():
    dry = '--dry' in sys.argv
    arg = lambda k, d: type(d)(sys.argv[sys.argv.index(k) + 1]) if k in sys.argv else d
    floor, need, each = arg('--floor', 1.30), arg('--min', 0.70), arg('--each', 0)
    now = dt.datetime.now(A.WAT); start = now + dt.timedelta(hours=1)        # standing rule: an hour out
    cut = now.replace(hour=6, minute=0, second=0, microsecond=0)
    while cut - now < dt.timedelta(hours=12):
        cut += dt.timedelta(days=1)
    if '--from' in sys.argv:
        start = max(start, dt.datetime.strptime(arg('--from', ''), '%Y-%m-%d %H:%M').replace(tzinfo=A.WAT))
    if '--until' in sys.argv:
        cut = dt.datetime.strptime(arg('--until', ''), '%Y-%m-%d %H:%M').replace(tzinfo=A.WAT)
    evs = events(start, cut)
    print(f"window {start:%a %H:%M} -> {cut:%a %H:%M}: {len(evs)} NBA games", flush=True)
    by_game = []
    for ko, ev in evs:
        match = f"{ev['homeTeamName']} v {ev['awayTeamName']}"
        opts = options(ev)
        ros = roster(ev['homeTeamName']) + roster(ev['awayTeamName'])
        idx = {}
        for aid, nm in ros:
            idx[tuple(sorted(norm(nm)))] = aid
        players = sorted({o['player'] for o in opts})
        found = {p: idx.get(tuple(sorted(sb_name(p)))) for p in players}
        with ThreadPoolExecutor(8) as ex:
            logs = dict(zip(players, ex.map(lambda p: games(found[p]) if found[p] else [], players)))
        miss = [p for p in players if not logs[p]]
        best = {}                     # one option per player and stat: the highest support, then the longer price
        for o in opts:
            gs = logs[o['player']]
            if not gs or o['odds'] < floor:
                continue
            r = reads(gs, o)
            if any(n == 0 for _, n in r.values()) or r['L10'][1] < 5:
                continue
            sup = min(h / n for h, n in r.values())
            k = (o['player'], o['stat'])
            if sup >= need and (k not in best or (round(sup, 3), o['odds']) > (round(best[k][0], 3), best[k][1])):
                best[k] = (sup, o['odds'], o, r, gs)
        agreed = sorted(best.values(), key=lambda x: (-round(x[0], 3), -x[1]))
        if each:
            agreed = agreed[:each]
        print(f"\n{ko:%a %H:%M}  {match}: {len(opts)} options on {len(players)} players, "
              f"{len(agreed)} props agreed{'; no ESPN games for ' + ', '.join(miss) if miss else ''}", flush=True)
        legs = []
        for sup, odds, o, r, gs in agreed:
            stats = stat_lines(o, r, gs, sup)
            legs.append(dict(ts=ko.timestamp(), match=match, sel=o['sel'], odds=odds, chance=sup, ids=o['ids'], stats=stats))
            print(f"   PICK {o['sel']} @{odds}", flush=True)
            for s in stats:
                print(f"        {s}", flush=True)
        if legs:
            by_game.append((match, legs))
    if not by_game:
        print("no leg: no option was backed by every read - nothing booked")
        return
    # 23 Oct, user: "I need multiple probs for a single game" - every agreed prop on a game
    # (one per player and stat) goes on that game's own code, then all games on one code.
    for match, legs in by_game:
        if len(legs) >= 2:
            book_code(legs, match, dry)
    every = sorted((l for _, ls in by_game for l in ls), key=lambda l: -l['chance'])[:50]
    if len(by_game) > 1:
        book_code(sorted(every, key=lambda l: l['ts']), 'every game', dry)


if __name__ == '__main__':
    main()
