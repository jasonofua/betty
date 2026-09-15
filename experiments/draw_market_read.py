#!/usr/bin/env python3
"""First honest read of the draw question WITH the market in the room.
odds_history.jsonl (football-data.co.uk, 2015/16 on): for every match the
closing draw price (Pinnacle closing > Pinnacle > average > Bet365) and the
same venue-form features the gate uses, built from PRIOR matches in the same
file. Questions:
  1. Return on betting every draw at the closing price, by implied-probability band.
  2. The gate's own shape (quiet game) replayed against the market: rate and return.
  3. Where form says 'draw' and the market does not: the disagreement bands.
Return = (sum of odds on winners - number of bets) / number of bets."""
import json, collections, statistics as S
rows = [json.loads(l) for l in open('experiments/odds_history.jsonl')]
rows.sort(key=lambda r: r['ts'])
def price(r):
    for k in ('pscd', 'psd', 'avgd', 'b365d'):
        if r.get(k):
            return r[k]
    return None
def ph(r):
    for k in ('psch', 'psh', 'avgh', 'b365h'):
        if r.get(k): return r[k]
def pa(r):
    for k in ('psca', 'psa', 'avga', 'b365a'):
        if r.get(k): return r[k]

hist = collections.defaultdict(list)   # (lg, team) -> [(ts, venue, gf, ga)]
def venue(lg, team, ts, v, n=7):
    return [(gf, ga) for t, vv, gf, ga in hist[(lg, team)] if t < ts and vv == v][-n:]

B = collections.Counter(); G = collections.Counter(); D = collections.Counter(); L = collections.Counter()
def acc(C, key, draw, od):
    C[(key, 'n')] += 1; C[(key, 'draw')] += draw; C[(key, 'ret')] += (od if draw else 0.0)

for r in rows:
    od = price(r)
    if od and od > 1.5:
        draw = r['hg'] == r['ag']
        imp = 1 / od
        band = f"{int(imp * 100) // 3 * 3:02d}-{int(imp * 100) // 3 * 3 + 2:02d}%"
        acc(B, band, draw, od)
        hp, ap = venue(r['lg'], r['home'], r['ts'], 'H'), venue(r['lg'], r['away'], r['ts'], 'A')
        if len(hp) >= 5 and len(ap) >= 5:
            xg = (S.mean(gf + ga for gf, ga in hp) + S.mean(gf + ga for gf, ga in ap)) / 2
            cd = sum(gf == ga for gf, ga in hp) + sum(gf == ga for gf, ga in ap)
            mm = abs(S.mean(gf - ga for gf, ga in hp) - S.mean(gf - ga for gf, ga in ap))
            quiet = xg < 2.4 and cd >= 3 and mm <= 1.0
            acc(G, 'quiet gate' if quiet else 'not gate', draw, od)
            if quiet:
                acc(G, f"quiet gate, price {'< 3.0' if od < 3.0 else '3.0-3.4' if od < 3.4 else '3.4+'}", draw, od)
            # disagreement: form-implied draw share vs market
            form_draw = cd / (len(hp) + len(ap))
            gap = form_draw - imp
            gb = 'form >= market + 15pts' if gap >= 0.15 else 'form >= market + 5pts' if gap >= 0.05 else 'form ~ market' if gap > -0.05 else 'form < market'
            acc(D, gb, draw, od)
            acc(L, (r['lg'] if '_' not in r['src'] else r['src'][5:-4], 'all'), draw, od)
    hist[(r['lg'], r['home'])].append((r['ts'], 'H', r['hg'], r['ag']))
    hist[(r['lg'], r['away'])].append((r['ts'], 'A', r['ag'], r['hg']))

def show(C, keys, title):
    print(f"\n{title}")
    print(f"   {'':34}{'n':>7}{'draw%':>8}{'return':>9}")
    for k in keys:
        n = C[(k, 'n')]
        if n >= 200:
            print(f"   {str(k):34}{n:7}{C[(k,'draw')]/n:8.1%}{(C[(k,'ret')]-n)/n:+9.1%}")

bands = sorted({k[0] for k in B if k[1] == 'n'})
show(B, bands, "1. every draw at the closing price, by the market's implied draw probability")
show(G, ['not gate', 'quiet gate', 'quiet gate, price < 3.0', 'quiet gate, price 3.0-3.4', 'quiet gate, price 3.4+'], "2. the quiet gate against the market")
show(D, ['form < market', 'form ~ market', 'form >= market + 5pts', 'form >= market + 15pts'], "3. venue-form draw share versus the market's implied draw probability")
lgs = sorted({k[0] for k in L if k[1] == 'n'}, key=lambda k: -(L[(k, 'ret')] - L[(k, 'n')]) / max(1, L[(k, 'n')]))
show(L, lgs[:8] + lgs[-4:], "4. leagues: best and worst draw return at closing prices (all draws bet)")
