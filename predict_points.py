#!/usr/bin/env python3
"""POINTS SPORTS, EVERY GAME PREDICTED FROM ITS STATS.

  python3 predict_points.py --sport basketball|hockey|handball|amfoot [--days N] [--floor 1.40] [--book]

13 Oct, user: "do this for the points sports" - the predict_stats.py logic on the
book_amfoot.py data. Four columns per game, THIS SEASON only (book_amfoot.SEASON_START,
user's call the same day), every competition but friendlies / pre-season, 5+ games in each:
    HH   home side's last 7 HOME games        AA   away side's last 7 AWAY games
    H10  home side's last 10 (any venue)      A10  away side's last 10 (any venue)
Hockey scores are regulation time wherever the period feed has them (that is what
the book settles totals and handicaps on). Every line SportyBet prices on the
winner, full-time total, first-period total and handicap markets is scored in each
column, read in the home side's frame (the away side's games are mirrored):
    support = the LOWEST of the four column rates
    pick    = the option with the highest support priced --floor (book_amfoot's
              MIN_PRICE, 1.40 - the user's band for these sports) or more; ties go to
              the bigger sample, then to the option the book rates more likely
A push on a whole line counts as a loss in the record.
"""
import sys, math, datetime as dt, collections
import os as _o; sys.path.insert(0, _o.path.dirname(_o.path.abspath(__file__)))
import acca as A
import book_amfoot as P

MIN_COL = 5
import re
FRIENDLY = re.compile(r'friendl|pre-season|preseason|exhibition|club friendly', re.I)
MAX_LEGS = 50


def games_for(f):
    """The four columns as lists of dicts (f, a, fh_f, fh_a) in the TEAM's own frame, or None."""
    lg = f.get('lg')
    # 13 Oct, user: "every match that has stats must be predicted according to the stats".
    # Every competitive game this season counts, league and European alike - the
    # same-competition rule left EuroLeague / EuroCup / European League sides with 1-3
    # games. Friendlies and pre-season tournaments stay out.
    real = lambda gs, k: [g for g in gs if not FRIENDLY.search(g.get('comp') or '')][:k]
    hh = real(P.venue_games(f['id'], f['ts'], f['h'], '- Home', None, n=40), 7)
    aa = real(P.venue_games(f['id'], f['ts'], f['a'], '- Away', None, n=40), 7)
    if len(hh) < MIN_COL:
        tp = [g for g in P.team_page_games(f.get('px'), f.get('wu'), f['ts'], f['h'], True, lg) if g.get('ts', 0) >= P.SEASON_START]
        if len(tp) > len(hh): hh = tp
    if len(aa) < MIN_COL:
        tp = [g for g in P.team_page_games(f.get('py'), f.get('wv'), f['ts'], f['a'], False, lg) if g.get('ts', 0) >= P.SEASON_START]
        if len(tp) > len(aa): aa = tp
    h10 = real(P.venue_games(f['id'], f['ts'], f['h'], 'Overall', None, n=40), 10)
    a10 = real(P.venue_games(f['id'], f['ts'], f['a'], 'Overall', None, n=40), 10)
    cols = [hh, aa, h10, a10]
    if min(len(c) for c in cols) < MIN_COL:
        return None, [len(c) for c in cols]
    for c in cols:
        for g in c:
            if 'fh' not in g:
                g['fh'] = P.first_half(g['id'], g['home']) if g.get('id') else None
            if P.SPORT['reg_only'] and g.get('id') and not g.get('_reg'):
                r = P.regulation(g['id'], g['home'])
                if r:
                    g['pf'], g['pa'] = r
                g['_reg'] = True
    return cols, [len(c) for c in cols]


def home_frame(cols):
    """Each column as [(f, a, fh_f, fh_a)] in the HOME side's frame: the away side's games
    are mirrored, so 'Home -5.5' asked of the away side's games is 'lost by 6+'."""
    out = []
    for i, c in enumerate(cols):
        mirror = i in (1, 3)
        rows = []
        for g in c:
            f, a = (g['pa'], g['pf']) if mirror else (g['pf'], g['pa'])
            fh = g.get('fh')
            fh = ((fh[1], fh[0]) if mirror else fh) if fh else None
            rows.append((f, a, fh))
        out.append(rows)
    return out


def test_for(key, desc, spec):
    """(test(f, a, fh) -> True/False/None, needs_first_half) for one outcome, home frame."""
    d = (desc or '').strip()
    try:
        v = float((spec or '').split('=')[-1]) if spec else None
    except ValueError:
        v = None
    if key == 'Winner':
        if d.startswith('Home') or d == '1':  return (lambda f, a, fh: f > a), False
        if d.startswith('Away') or d == '2':  return (lambda f, a, fh: a > f), False
        if d.startswith('Draw') or d == 'X':  return (lambda f, a, fh: f == a), False
    if key in ('FT O/U', '1H O/U') and v is not None:
        first = key == '1H O/U'
        tot = (lambda f, a, fh: None if fh is None else fh[0] + fh[1]) if first else (lambda f, a, fh: f + a)
        if d.startswith('Over'):
            return (lambda f, a, fh: None if tot(f, a, fh) is None else tot(f, a, fh) > v), first
        if d.startswith('Under'):
            return (lambda f, a, fh: None if tot(f, a, fh) is None else tot(f, a, fh) < v), first
    if key == 'Handicap' and v is not None:                  # v is the HOME line: 'Home (-5.5)' for hcp=-5.5
        if d.startswith('Home'):  return (lambda f, a, fh: f - a + v > 0), False
        if d.startswith('Away'):  return (lambda f, a, fh: f - a + v < 0), False
    return None, False


def score(mk, cols, floor):
    hf = home_frame(cols)
    out = []
    for key in ('Winner', 'FT O/U', '1H O/U', 'Handicap'):
        for m in mk.get(key, []):
            try:
                inv = sum(1 / o[1] for o in m['outs'])
            except ZeroDivisionError:
                continue
            for desc, odds, oid in m['outs']:
                if odds < floor:
                    continue
                test, _ = test_for(key, desc, m['spec'])
                if test is None:
                    continue
                rates, tallies = [], []
                for rows in hf:
                    res = [test(f, a, fh) for f, a, fh in rows]
                    res = [x for x in res if x is not None]
                    if len(res) < MIN_COL:
                        break
                    rates.append(sum(res) / len(res)); tallies.append(f"{sum(res)}/{len(res)}")
                if len(rates) < 4:
                    continue
                out.append(dict(market=key, outcome=desc, odds=odds, chance=(1 / odds) / inv if inv else None,
                                support=min(rates), n=sum(int(t.split('/')[1]) for t in tallies), tally='  '.join(tallies),
                                ids=dict(eventId=None, productId=3, marketId=m['id'], specifier=m['spec'] or None, outcomeId=str(oid))))
    out.sort(key=lambda r: (-r['support'], -r['n'], -(r['chance'] or 0)))
    return out


def main():
    arg = lambda k, d: sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d
    sport = arg('--sport', 'basketball')
    P.SPORT = P.SPORTS[sport]; P.KEEP = P.keep_map()
    days = int(arg('--days', 0)); floor = float(arg('--floor', P.MIN_PRICE))
    now = dt.datetime.now(tz=A.WAT); start = now + dt.timedelta(hours=1)
    horizon = (now + dt.timedelta(days=days)).replace(hour=23, minute=59, second=59)
    sb = [e for e in P.sb_board() if start < dt.datetime.fromtimestamp(int(e['estimateStartTime']) / 1000, tz=A.WAT) <= horizon]
    fs = P.fs_board(days)
    print(f"{P.SPORT['label']}: {len(sb)} SportyBet games  |  {len(fs)} Flashscore fixtures", flush=True)
    picked, rows = [], []
    for e in sorted(sb, key=lambda e: e['estimateStartTime']):
        ets = int(e['estimateStartTime']) // 1000
        f = next((f for f in fs if abs(f['ts'] - ets) <= 3600 * 3
                  and (P.norm(f['h'])[:8] in P.norm(e['homeTeamName']) or P.norm(e['homeTeamName'])[:8] in P.norm(f['h']))
                  and (P.norm(f['a'])[:6] in P.norm(e['awayTeamName']) or P.norm(e['awayTeamName'])[:6] in P.norm(f['a']))), None)
        t = dt.datetime.fromtimestamp(ets, tz=A.WAT); name = f"{e['homeTeamName']} v {e['awayTeamName']}"
        if not f:
            rows.append(f"  {t:%a %H:%M}  {name[:44]:44}  not matched to Flashscore"); continue
        cols, ns = games_for(f)
        if not cols:
            rows.append(f"  {t:%a %H:%M}  {name[:44]:44}  no stats: this season's games {ns[0]}/{ns[1]} venue, {ns[2]}/{ns[3]} last 10 (need {MIN_COL} each)"); continue
        opts = score(P.sb_markets(e['eventId']), cols, floor)
        if not opts:
            rows.append(f"  {t:%a %H:%M}  {name[:44]:44}  no line at {floor}+ the stats can score"); continue
        p = opts[0]; p['ids']['eventId'] = e['eventId']
        picked.append(dict(ts=ets, match=name, pick=p))
        rows.append(f"  {t:%a %H:%M}  {name[:44]:44}  {p['market']} {p['outcome']:<14} @{p['odds']:<5} support {p['support']:.0%}  [{p['tally']}]")
    print('\n'.join(rows))
    combo = math.prod(g['pick']['odds'] for g in picked) if picked else 0
    print(f"\n{len(picked)} games predicted, {combo:,.2f}x together")
    if '--book' not in sys.argv or len(picked) < 2:
        return
    for i in range(0, len(picked), MAX_LEGS):
        part = picked[i:i + MAX_LEGS]; c = math.prod(g['pick']['odds'] for g in part)
        bk = A.book([g['pick']['ids'] for g in part])
        if bk and bk.get('code'):
            print(f"   >> code {bk['code']}   {bk['url']}   {c:,.2f}x ({len(part)} games, {bk.get('booked')}/{bk.get('req')} on the code)")
            A.log_booking(bk['code'], bk['url'], f"{P.SPORT['label'].lower()} stats prediction: every game's best-supported line at {floor}+ - {c:,.2f}x ({len(part)} games)",
                          [(g['ts'], g['match'], f"{g['pick']['market']} / {g['pick']['outcome']}", g['pick']['odds'],
                            [f"support {g['pick']['support']:.0%} (lowest of HH AA H10 A10)  {g['pick']['tally']}"]) for g in part])
        else:
            print(f"   >> booking failed: {bk.get('msg') if bk else 'no selections'}")


if __name__ == '__main__':
    main()
