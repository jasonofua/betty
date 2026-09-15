#!/usr/bin/env python3
"""15 Sep: the draw edge on 78k priced matches is in the PRICE, not the pick -
draws at the top of the market in the 32%+ band return +5-9%, at the average
price -2%. So: is SportyBet top of the market on draws? Snapshot its 1X2 for
every upcoming football game each morning; football-data's closing prices for
the covered leagues arrive after the round and the comparison is done offline.
Runs from the Railway scheduler; the file lives next to bookings.md."""
import json, os, sys, time, urllib.request
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import acca as A

def board():
    BASE = 'https://www.sportybet.com/api/ng/factsCenter/'
    out = []
    for pg in range(1, 15):
        url = BASE + f'pcUpcomingEvents?sportId=sr:sport:1&marketId=1&pageSize=100&pageNum={pg}&option=1'
        try:
            d = json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=A.HDRS), timeout=25).read().decode())['data']
        except Exception:
            break
        evs = [e for t in (d.get('tournaments') or []) for e in t.get('events', [])]
        if not evs:
            break
        for e in evs:
            m = next((m for m in e.get('markets', []) if str(m.get('id')) == '1' and not m.get('specifier')), None)
            if not m:
                continue
            o = {x['desc']: float(x['odds']) for x in m.get('outcomes', []) if x.get('odds')}
            if {'Home', 'Draw', 'Away'} <= set(o):
                sp = e.get('sport') or {}; cat = sp.get('category') or {}
                out.append(dict(snap=int(time.time()), eid=e['eventId'], ko=int(e['estimateStartTime']) // 1000,
                                comp=f"{cat.get('name', '')}: {(cat.get('tournament') or {}).get('name', '')}",
                                home=e['homeTeamName'], away=e['awayTeamName'], o1=o['Home'], ox=o['Draw'], o2=o['Away']))
    return out

def snapshot():
    vol = os.environ.get('BOOKINGS_PATH')
    path = os.path.join(os.path.dirname(vol) if vol else os.path.dirname(os.path.abspath(__file__)), 'sporty_odds.jsonl')
    rows = board()
    with open(path, 'a') as fh:
        for r in rows:
            fh.write(json.dumps(r) + '\n')
    return dict(rows=len(rows), path=path)

if __name__ == '__main__':
    print(snapshot())
