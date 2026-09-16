#!/usr/bin/env python3
"""Draws v2 (15 Sep): the draw edge is in the PRICE, not the pick.

football-data.co.uk, 78k priced matches 2015-2026, judged on two halves of
the sample: backing the draw where a soft book's price sits 5%+ above the
market average and the market has the draw at 30-36% returned +14.6% and
+10.6% (n 321 / 379); with Pinnacle's closing price as the reference and
the 32-36% band, +5.7% / +11.4% (n 1,351 / 1,320). Everything the old gate
read (venue form, expected goals, combined draws) added nothing over the
price: quiet-gate games returned -3.7% at closing against -4.1% for the rest.

So: the market's average draw price for today's main-league fixtures comes
from football-data's fixtures.csv; SportyBet's board is matched to it; a
draw is booked as a SINGLE where SportyBet's price is >= 1.05 x the market
average and the market-implied draw probability is 30-36%.

    python3 book_draw_market.py [--dry] [--ratio 1.05]
"""
import csv, json, os, re, sys, datetime as dt
import acca as A
import dynamic_v4 as D
import fetcher_v2 as F2
import book_winners as BW
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'experiments'))
import odds_snapshot as SNAP

DIV = {'E0': 'England', 'E1': 'England', 'E2': 'England', 'E3': 'England', 'EC': 'England',
       'SC0': 'Scotland', 'SC1': 'Scotland', 'SC2': 'Scotland', 'SC3': 'Scotland',
       'D1': 'Germany', 'D2': 'Germany', 'I1': 'Italy', 'I2': 'Italy', 'SP1': 'Spain', 'SP2': 'Spain',
       'F1': 'France', 'F2': 'France', 'N1': 'Netherlands', 'B1': 'Belgium', 'P1': 'Portugal', 'T1': 'Turkey', 'G1': 'Greece'}
RATIO, LO, HI = 1.05, 0.30, 0.36
BAND_CAP = 12
UNDER_MAX = 1.70         # 16 Sep, user's call, measured: draw band 30%+ with Under 2.5 at 1.51-1.70 -> 33.3% draws
                         # v 31.4% priced, +2.7% at closing on both halves (+6.7% at best price, n 9,632);
                         # Under 1.71-1.90 in the same band -> 29.2%, -8.1%. Under 1.50 or shorter is
                         # already priced as a draw (32.9% v 32.9%).
PPG_GAP = 0.2            # 16 Sep, user's call: sides close to each other IN THE TABLE - the tight bucket.
                         # football-data 2015-26, 27%+ band, live standings after 8+ rounds: points-per-game
                         # gap < 0.2 -> 30.6% draws v 29.7% market (+2.6% at best price, positive on both
                         # halves), 0.2-0.5 -> 30.1% (+1.4%, flat on the second half), 0.5+ -> 29.3%.
TABLE_MIN = 8            # 16 Sep, user: "use the real table points not last season form". Both sides need
                         # 8+ league games this season (the measured cut); a league two rounds old has no
                         # table and is skipped rather than read off last season's form.
BAND_MIN = 0.27          # 16 Sep: games with no reference price - SportyBet's own implied draw (overround
                         # removed) 28%+ is the market at ~30%+, where draws at the market's best price pay
                         # (+2.8% at 30-32%, +6% at 32-34%); SportyBet has priced draws at or above the
                         # market's best on every game checked. Tracked as its own band on Results.


def fixtures():
    # 16 Sep: urllib, not curl - the Railway image has no curl and every server run
    # of this script had died here (the 09:20 slot never booked a draws slip)
    import urllib.request, io
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'experiments', 'odds_raw', 'fixtures.csv')
    try:
        raw = urllib.request.urlopen(urllib.request.Request('https://www.football-data.co.uk/fixtures.csv', headers={'User-Agent': 'Mozilla/5.0'}), timeout=60).read()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, 'wb').write(raw)
    except Exception as e:
        print(f"fixtures.csv: {type(e).__name__}: {e}" + (' - using the cached copy' if os.path.exists(path) else ' - no reference prices today'))
        if not os.path.exists(path):
            return []
    out = []
    for r in csv.DictReader(open(path, encoding='utf-8-sig', errors='replace')):
        try:
            avgh, avgd, avga = float(r['AvgH']), float(r['AvgD']), float(r['AvgA'])
        except (KeyError, ValueError, TypeError):
            continue
        s = 1 / avgh + 1 / avgd + 1 / avga
        out.append(dict(div=r['Div'], country=DIV.get(r['Div']), date=r['Date'], time=r.get('Time'), home=r['HomeTeam'], away=r['AwayTeam'],
                        avgd=avgd, maxd=float(r['MaxD']) if r.get('MaxD') else None, imp=(1 / avgd) / s))
    return out


def under25(eid):
    """SportyBet's Under 2.5 price for the event (market 18, total=2.5), or None."""
    import urllib.request
    try:
        d = json.loads(urllib.request.urlopen(urllib.request.Request(f"https://www.sportybet.com/api/ng/factsCenter/event?eventId={eid}&productId=3", headers=A.HDRS), timeout=25).read().decode())
    except Exception:
        return None
    for m in (d.get('data') or {}).get('markets', []):
        if str(m.get('id')) == '18' and (m.get('specifier') or '') == 'total=2.5':
            o = next((o for o in m.get('outcomes', []) if o['desc'] == 'Under 2.5' and o.get('isActive', 1)), None)
            return float(o['odds']) if o else None
    return None


def match(fx, board):
    best = None
    for s in board:
        if fx['country'] and not s['comp'].startswith(fx['country']):
            continue
        sh, sa = D._score(fx['home'], s['home']), D._score(fx['away'], s['away'])
        if sh >= D.JOIN_MIN_SCORE and sa >= D.JOIN_MIN_SCORE and (best is None or sh + sa > best[0]):
            best = (sh + sa, s)
    return best[1] if best else None


def league_rounds(path):
    """{match id: round number} for a league, from the results/fixtures feeds the
    league's Flashscore results page embeds (the daily feed carries no round).
    One page per league, cached six hours. Cups return nothing - no table."""
    import fetcher_v3 as F3, urllib.request, hashlib, time
    if not path:
        return {}
    cf = os.path.join(F3.CACHE, 'lr_' + hashlib.md5(path.encode()).hexdigest())
    if os.path.exists(cf) and time.time() - os.path.getmtime(cf) < 6 * 3600:
        return json.load(open(cf))
    out = {}
    try:
        html = urllib.request.urlopen(urllib.request.Request('https://www.flashscore.com/football' + path + 'results/',
                                                             headers={'User-Agent': 'Mozilla/5.0'}), timeout=30).read().decode('utf-8', 'replace')
        for m in re.finditer(r'initialFeeds\["summary-(?:results|fixtures)"\] = \{\s*data: `([^`]*)`', html):
            for blk in m.group(1).split('~AA÷')[1:]:
                d = dict(re.findall(r'([A-Z]{2,3})÷([^¬]*)', 'AA÷' + blk))
                r = re.match(r'Round (\d+)$', d.get('ER', ''))
                if r:
                    out[d['AA']] = int(r.group(1))
        if out:                                   # a game the page does not list (in play) is in the current round
            out['_next:' + path] = max(out.values()) + (0 if 'summary-fixtures' in html else 1)
    except Exception as e:
        print(f"  league page {path}: {type(e).__name__}: {e}")
    json.dump(out, open(cf, 'w'))
    return out


def season_record(fid, ts, rnd):
    """Both sides' league record THIS SEASON: games in the same competition as their
    most recent one, newest first, capped at the round number from the fixture feed
    (Round 12 = 11 played). A gap of 90+ days is the off-season; a gap of 35+ days
    with most of the season already collected is too (FNL's 7-week summer), while a
    mid-season break (Georgia's 7 weeks in July) is walked through. Returns
    {side: (pts, played, ppg)} keyed 'h'/'a', or None when the feed is empty."""
    import fetcher_v3 as F3
    try:
        raw = F3.fetch(f"df_hh_1_{fid}", ttl=3600)
    except Exception:
        return None
    rows = {}; tab = blk = None
    for f in F3.sections(raw):
        if 'KA' in f: tab = f['KA']
        if 'KB' in f: blk = f['KB']; continue
        if not blk or not blk.startswith('Last matches') or tab != 'Overall' or 'KJ' not in f: continue
        try: hg, ag, kc = int(f.get('KU', '')), int(f.get('KT', '')), int(f.get('KC', 0))
        except ValueError: continue
        if kc >= ts - 3600: continue
        gf, ga = (hg, ag) if f.get('KS') == 'home' else (ag, hg)
        rows.setdefault(blk, []).append((kc, f.get('KF') or f.get('KI') or '', 3 if gf > ga else 1 if gf == ga else 0))
    out = {}
    for side, key in zip('ha', list(rows)[:2]):
        g = rows[key]
        if not g: return None
        comp = g[0][1]; pts = played = 0; prev = None; cap = (rnd - 1) if rnd else 60
        for kc, c, p in g:
            if c != comp: continue
            if prev is not None:
                gap = (prev - kc) / 86400
                if gap >= 90 or (gap >= 35 and played >= 0.7 * cap): break
            if played >= cap: break
            pts += p; played += 1; prev = kc
        out[side] = (pts, played, pts / played if played else 0.0)
    return out if len(out) == 2 else None


def main():
    dry = '--dry' in sys.argv
    ratio = float(sys.argv[sys.argv.index('--ratio') + 1]) if '--ratio' in sys.argv else RATIO
    replaces = sys.argv[sys.argv.index('--replaces') + 1] if '--replaces' in sys.argv else None   # a hand rebook names the code it retires
    only = [x.strip().lower() for x in sys.argv[sys.argv.index('--only') + 1].split(',') if x.strip()] if '--only' in sys.argv else []   # user's call: these games, checks waived
    now = dt.datetime.now(tz=A.WAT)
    fx = fixtures(); board = SNAP.board()
    print(f"fixtures.csv {len(fx)} priced main-league fixtures | SportyBet board {len(board)} games")
    picks, seen = [], 0
    for f in fx:
        s = match(f, board)
        if not s:
            continue
        ko = dt.datetime.fromtimestamp(s['ko'], tz=A.WAT)
        if ko <= now + dt.timedelta(minutes=30):
            continue
        seen += 1
        edge = s['ox'] / f['avgd']
        tag = ''
        if not (LO <= f['imp'] < HI):
            tag = f"market draw {f['imp']:.0%} outside {LO:.0%}-{HI:.0%}"
        elif edge < ratio:
            tag = f"SportyBet {s['ox']:.2f} is {edge:.3f}x the market average {f['avgd']:.2f} (needs {ratio:.2f})"
        print(f"  {ko:%a %H:%M}  {f['div']:3} {s['home'][:22]:22} v {s['away'][:22]:22} sporty {s['ox']:.2f} avg {f['avgd']:.2f} max {f['maxd'] or 0:.2f} imp {f['imp']:.0%}  {'PICK' if not tag else 'skip: ' + tag}")
        if not tag:
            picks.append(dict(f=f, s=s, edge=edge, ko=ko))
    # second pass: everything SportyBet prices that football-data does not (South America,
    # Asia, Africa, cups) - the band rule on SportyBet's own implied draw probability
    matched_eids = {p['s']['eid'] for p in picks} | {match(f, board)['eid'] for f in fx if match(f, board)}
    cut = now + dt.timedelta(hours=14)
    if '--until' in sys.argv:                     # --until HH: the window ends at that hour (tomorrow if already past)
        hh = int(sys.argv[sys.argv.index('--until') + 1])
        cut = now.replace(hour=hh, minute=0, second=0, microsecond=0)
        if cut <= now:
            cut += dt.timedelta(days=1)
    band_picks = []
    for s in board:
        if s['eid'] in matched_eids or s['eid'] in {p['s']['eid'] for p in picks}:
            continue
        ko = dt.datetime.fromtimestamp(s['ko'], tz=A.WAT)
        if not (now + dt.timedelta(minutes=30) < ko <= cut):
            continue
        if any(x in s['comp'] for x in ('Simulated', 'eSoccer', 'Women', 'U19', 'U20', 'U21', 'U23', 'Youth', 'Reserve', 'Amateur')):
            continue
        o = 1 / s['o1'] + 1 / s['ox'] + 1 / s['o2']; imp = (1 / s['ox']) / o
        if imp >= BAND_MIN:
            band_picks.append(dict(f=dict(div='-', avgd=None, maxd=None, imp=imp), s=s, edge=None, ko=ko))
    # the two sides must be close in the TABLE: points a game this season, this
    # competition (16 Sep, user: real table points, not last season's form)
    import fetcher_v3 as F3
    fxs, paths = [], {}
    for off in (0, 1):
        fxs += F2.get_fixtures(off)
        cur = None
        for sct in F3.sections(F3.fetch(f'f_1_{off}_1_en-ng_1')):
            if 'ZA' in sct: cur = sct.get('ZL')
            elif 'AA' in sct and cur: paths[sct['AA']] = cur
    rounds = {}
    evs = [dict(eventId=p['s']['eid'], homeTeamName=p['s']['home'], awayTeamName=p['s']['away'], estimateStartTime=p['s']['ko'] * 1000) for p in band_picks]
    joined = {e['eventId']: f for e, f, sc in D.join(evs, fxs, lambda e: dt.datetime.fromtimestamp(int(e['estimateStartTime']) / 1000, tz=A.WAT),
                                                          lambda f: dt.datetime.fromtimestamp(f['ts'], tz=A.WAT))}
    kept = []
    for p in band_picks:
        if only:
            if any(k in (p['s']['home'] + ' v ' + p['s']['away']).lower() for k in only):
                p['user'] = True; p['gap'] = 0; p['table'] = (0, 0, 0, 0); p['ppg'] = (0, 0); p['under'] = under25(p['s']['eid']) or 0
                kept.append(p)
            else:
                p['why'] = "not on the user's list"
            continue
        f = joined.get(p['s']['eid'])
        if not f:
            p['why'] = 'no form feed'; continue
        if f['id'] not in rounds:
            rounds.update(league_rounds(paths.get(f['id'])))
        rnd = rounds.get(f['id']) or rounds.get('_next:' + str(paths.get(f['id'])))
        if not rnd:
            p['why'] = 'no league round (cup or unknown stage) - no table'; continue
        rec = season_record(f['id'], f['ts'], rnd)
        if not rec:
            p['why'] = 'no season record'; continue
        (hp, hn, ph), (ap, an, pa) = rec['h'], rec['a']
        p['table'] = (hp, hn, ap, an); p['ppg'] = (round(ph, 2), round(pa, 2)); p['gap'] = abs(ph - pa)
        if min(hn, an) < TABLE_MIN:
            p['why'] = f"no table yet: {hn} and {an} league games this season (round {rnd})"; continue
        if p['gap'] >= PPG_GAP:
            p['why'] = f"apart in the table: {hp} pts/{hn} ({ph:.2f}) v {ap} pts/{an} ({pa:.2f})"; continue
        # 16 Sep: an away side the market makes 10+ points the stronger draws LESS than
        # priced (27.8% v 29.0%, -7.1% at closing); the home side being the stronger
        # one is where the band pays (30.5% v 29.3%, +3.5% at best price)
        s_ = p['s']; tot = 1 / s_['o1'] + 1 / s_['ox'] + 1 / s_['o2']
        if (1 / s_['o2']) / tot - (1 / s_['o1']) / tot >= 0.10:
            p['why'] = f"away side clearly stronger ({s_['o1']:.2f} v {s_['o2']:.2f})"; continue
        u = under25(s_['eid']); p['under'] = u
        if u is None:
            p['why'] = 'no Under 2.5 price'; continue
        if u > UNDER_MAX:
            p['why'] = f"market expects goals: Under 2.5 at {u:.2f}"; continue
        kept.append(p)
    kept.sort(key=lambda p: (-p['f']['imp'], p['gap']))
    for p in band_picks:
        s, ko, imp = p['s'], p['ko'], p['f']['imp']
        tag = ("PICK (user's call, Under 2.5 at %.2f)" % p['under'] if p.get('user') else 'PICK (band, table: %d pts/%d v %d pts/%d = %.2f v %.2f a game, Under 2.5 at %.2f)' % (*p['table'], p['ppg'][0], p['ppg'][1], p['under'])) if p in kept[:BAND_CAP] else ('skip: ' + p.get('why', 'over the cap'))
        print(f"  {ko:%a %H:%M}  {'-':3} {s['home'][:22]:22} v {s['away'][:22]:22} sporty {s['ox']:.2f} own implied {imp:.0%}  {tag}")
    picks += kept[:BAND_CAP]
    print(f"\n{seen} fixtures matched, {len(picks)} draw singles" + (' (dry run)' if dry else ''))
    if dry or not picks:
        return
    # 16 Sep, user's call: ONE slip, not singles. Every pick's Draw on one code.
    import urllib.request
    sels, legs, done = [], [], set()
    for p in picks:
        s, f = p['s'], p['f']
        if s['eid'] in done:                      # 16 Sep: LG4TB5 carried Caxias v Pelotas twice
            continue
        done.add(s['eid'])
        oid = None
        try:
            d = json.loads(urllib.request.urlopen(urllib.request.Request(f"https://www.sportybet.com/api/ng/factsCenter/event?eventId={s['eid']}&productId=3", headers=A.HDRS), timeout=25).read().decode())
            for m in (d.get('data') or {}).get('markets', []):
                if str(m.get('id')) == '1' and not m.get('specifier'):
                    oid = next((o['id'] for o in m.get('outcomes', []) if o['desc'] == 'Draw' and o.get('isActive', 1)), None)
        except Exception:
            pass
        if not oid:
            print(f"  no Draw outcome for {s['home']} v {s['away']}"); continue
        sels.append(dict(eventId=s['eid'], productId=3, marketId='1', specifier='', outcomeId=oid))
        if p['edge'] is not None:
            why = [f"market average draw {f['avgd']:.2f} (max {f['maxd']}), implied {f['imp']:.0%}; SportyBet {s['ox']:.2f} = {p['edge']:.3f}x average",
                   "rule: soft price >= 1.05x market average, market draw 30-36% -> +12% on 700 matches (football-data 2015-26, both halves positive)"]
        elif p.get('user'):
            why = [f"SportyBet {s['o1']:.2f}/{s['ox']:.2f}/{s['o2']:.2f} -> draw {f['imp']:.0%} after the overround (band 27%+); Under 2.5 at {p['under']:.2f}",
                   "user's call: picked by hand from the band, the table / Under checks waived"]
        else:
            why = [f"SportyBet {s['o1']:.2f}/{s['ox']:.2f}/{s['o2']:.2f} -> draw {f['imp']:.0%} after the overround (band 27%+); Under 2.5 at {p['under']:.2f}",
                   f"table this season: {p['table'][0]} pts from {p['table'][1]} v {p['table'][2]} pts from {p['table'][3]} = {p['ppg'][0]} v {p['ppg'][1]} a game (sides close)",
                   "rule: the draw band + sides close in the table (8+ rounds) + home not weaker + Under 2.5 at 1.70 or shorter (each measured on football-data 2015-26, positive on both halves)"]
        legs.append((s['ko'], f"{s['home']} v {s['away']}", '1X2 / Draw', s['ox'], why))
    if not sels:
        return
    bk = A.book(sels[:A.MAX_CODE])
    code = (bk or {}).get('code'); combo = 1.0
    for l in legs[:A.MAX_CODE]:
        combo *= l[3]
    print(f"\nbooked {bk}")
    print(f"code {code}  {(bk or {}).get('url')}   ({(bk or {}).get('booked')}/{len(sels)} legs, verified {(bk or {}).get('verified')})")
    if code:
        A.log_booking(code, bk.get('url'), f"draw slip ({'user' + chr(39) + 's call' if only else 'market band'}) {combo:,.1f}x ({len(legs)} legs){' ' + replaces + ' with' if replaces else ''} - " + ("picked by hand from the draw band" if only else "draw band, sides close in the table, home not weaker, Under 2.5 <= 1.70"), legs[:A.MAX_CODE])


if __name__ == '__main__':
    main()
