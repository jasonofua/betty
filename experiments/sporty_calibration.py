#!/usr/bin/env python3
"""SportyBet's own draw calibration - the test that decides whether SportyBet
alone (no reference price) pays on draws. Joins every 08:55 snapshot row to
the Flashscore results feed by team names and kickoff, keeps the first price
seen for each game, and tabulates: SportyBet-implied draw (overround removed)
v the draw rate that happened, and the return at SportyBet's price.
Bet365 on its own is calibrated to within a point in every band and loses its
6% overround everywhere under 30%; SportyBet prices draws higher, and whether
that is enough is exactly this table."""
import json, os, sys, collections, datetime as dt
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import acca as A, fetcher_v3 as F3, dynamic_v4 as D
ROOT = os.path.dirname(os.path.abspath(__file__))
SNAP = os.path.join(os.path.dirname(os.environ['BOOKINGS_PATH']), 'sporty_odds.jsonl') if os.environ.get('BOOKINGS_PATH') else os.path.join(ROOT, 'sporty_odds.jsonl')
OUT = os.path.join(ROOT, 'sporty_results.jsonl')

def results(days=(-2, -1, 0)):
    out = []
    for off in days:
        cur = None
        for sct in F3.sections(F3.fetch(f'f_1_{off}_1_en-ng_1', ttl=900)):
            if 'ZA' in sct: cur = sct['ZA']
            elif 'AA' in sct and sct.get('AG') is not None and sct.get('AH') is not None and str(sct.get('AC', '')) == '3':
                out.append(dict(comp=cur, h=sct.get('AE'), a=sct.get('AF'), hg=int(sct['AG']), ag=int(sct['AH']), ts=int(sct.get('AD', 0))))
    return out

def main():
    first = {}
    for line in open(SNAP):
        try: r = json.loads(line)
        except ValueError: continue
        if r['eid'] not in first: first[r['eid']] = r        # first price seen = the morning price
    done = set()
    if os.path.exists(OUT):
        for line in open(OUT):
            try: done.add(json.loads(line)['eid'])
            except ValueError: pass
    res = results(); n = 0
    with open(OUT, 'a') as fh:
        for eid, s in first.items():
            if eid in done or s['ko'] > dt.datetime.now().timestamp() - 2 * 3600: continue
            best = None
            for r in res:
                if abs(r['ts'] - s['ko']) > 2 * 3600: continue
                sc = min(D._score(s['home'], r['h']), D._score(s['away'], r['a']))
                if sc >= D.JOIN_MIN_SCORE and (best is None or sc > best[0]): best = (sc, r)
            if not best: continue
            r = best[1]; o = 1/s['o1'] + 1/s['ox'] + 1/s['o2']
            fh.write(json.dumps(dict(eid=eid, home=s['home'], away=s['away'], comp=s['comp'], ko=s['ko'], o1=s['o1'], ox=s['ox'], o2=s['o2'],
                                     imp=(1/s['ox'])/o, over=o - 1, hg=r['hg'], ag=r['ag'], draw=int(r['hg'] == r['ag']))) + '\n'); n += 1
    print(f"{n} new results joined")
    rows = [json.loads(l) for l in open(OUT)] if os.path.exists(OUT) else []
    C = collections.defaultdict(collections.Counter)
    for r in rows:
        band = f"{int(r['imp']*100)//3*3:02d}-{int(r['imp']*100)//3*3+2:02d}%"
        for b in (band, 'all'):
            c = C[b]; c['n'] += 1; c['d'] += r['draw']; c['ret'] += r['ox'] if r['draw'] else 0; c['imp'] += r['imp']; c['over'] += r['over']
    print(f"\nSportyBet draws: {len(rows)} settled games on record\n{'band':9}{'n':>6}{'implied':>9}{'drew':>7}{'return':>9}{'overround':>10}")
    for b in sorted(C):
        c = C[b]
        if c['n'] >= 20: print(f"{b:9}{c['n']:6}{c['imp']/c['n']:9.1%}{c['d']/c['n']:7.1%}{c['ret']/c['n']-1:+9.1%}{c['over']/c['n']:10.1%}")

if __name__ == '__main__':
    main()
