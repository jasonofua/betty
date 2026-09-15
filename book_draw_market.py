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
import csv, json, os, re, subprocess, sys, datetime as dt
import acca as A
import dynamic_v4 as D
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'experiments'))
import odds_snapshot as SNAP

DIV = {'E0': 'England', 'E1': 'England', 'E2': 'England', 'E3': 'England', 'EC': 'England',
       'SC0': 'Scotland', 'SC1': 'Scotland', 'SC2': 'Scotland', 'SC3': 'Scotland',
       'D1': 'Germany', 'D2': 'Germany', 'I1': 'Italy', 'I2': 'Italy', 'SP1': 'Spain', 'SP2': 'Spain',
       'F1': 'France', 'F2': 'France', 'N1': 'Netherlands', 'B1': 'Belgium', 'P1': 'Portugal', 'T1': 'Turkey', 'G1': 'Greece'}
RATIO, LO, HI = 1.05, 0.30, 0.36


def fixtures():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'experiments', 'odds_raw', 'fixtures.csv')
    subprocess.run(['curl', '-s', '-L', '--max-time', '60', '-A', 'Mozilla/5.0', '-o', path, 'https://www.football-data.co.uk/fixtures.csv'])
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


def match(fx, board):
    best = None
    for s in board:
        if fx['country'] and not s['comp'].startswith(fx['country']):
            continue
        sh, sa = D._score(fx['home'], s['home']), D._score(fx['away'], s['away'])
        if sh >= D.JOIN_MIN_SCORE and sa >= D.JOIN_MIN_SCORE and (best is None or sh + sa > best[0]):
            best = (sh + sa, s)
    return best[1] if best else None


def main():
    dry = '--dry' in sys.argv
    ratio = float(sys.argv[sys.argv.index('--ratio') + 1]) if '--ratio' in sys.argv else RATIO
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
    print(f"\n{seen} fixtures matched, {len(picks)} draw singles" + (' (dry run)' if dry else ''))
    for p in picks:
        s, f = p['s'], p['f']
        if dry:
            continue
        oid = None
        if True:
            import urllib.request
            d = json.loads(urllib.request.urlopen(urllib.request.Request(f"https://www.sportybet.com/api/ng/factsCenter/event?eventId={s['eid']}&productId=3", headers=A.HDRS), timeout=25).read().decode())
            for m in (d.get('data') or {}).get('markets', []):
                if str(m.get('id')) == '1' and not m.get('specifier'):
                    oid = next((o['id'] for o in m.get('outcomes', []) if o['desc'] == 'Draw'), None)
        if not oid:
            print(f"  no Draw outcome for {s['home']} v {s['away']}"); continue
        bk = A.book([dict(eventId=s['eid'], productId=3, marketId='1', specifier='', outcomeId=oid)])
        code = (bk or {}).get('code')
        print(f"  {p['ko']:%a %H:%M} {s['home']} v {s['away']} Draw @{s['ox']:.2f}  code {code}  {(bk or {}).get('url')}")
        if code:
            A.log_booking(code, bk.get('url'), f"draw single (market) {s['ox']:.2f}x - SportyBet {p['edge']:.3f}x the market average, market draw {f['imp']:.0%}",
                          [(s['ko'], f"{s['home']} v {s['away']}", '1X2 / Draw', s['ox'],
                            [f"market average draw {f['avgd']:.2f} (max {f['maxd']}), implied {f['imp']:.0%}; SportyBet {s['ox']:.2f} = {p['edge']:.3f}x average",
                             "rule: soft price >= 1.05x market average, market draw 30-36% -> +12% on 700 matches (football-data 2015-26, both halves positive)"])])


if __name__ == '__main__':
    main()
