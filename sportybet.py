#!/usr/bin/env python3
"""Bridge between the flashscore-based calibrated picks and SportyBet's own market.
Pulls SportyBet's unauthenticated JSON API (factsCenter), matches each calibrated
top pick to its SportyBet event by team name + kickoff, and tags it with:
  - bettable?  (is the game on SportyBet at all)
  - real odds  (SportyBet's price for that exact pick)
  - true EV    (model probability x real odds)
  - cushion    (for AH picks, the handicap lines SportyBet actually offers)
No auth/signature - just a browser UA + Referer. See memory reference-sportybet-api.
Usage: python3 sportybet.py            # tags today's picks_calibrated.md
"""
import urllib.request, json, re, sys
from datetime import datetime, timezone

BASE = 'https://www.sportybet.com/api/ng/factsCenter/'
HDRS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0 Safari/537.36',
        'Accept': 'application/json', 'Referer': 'https://www.sportybet.com/ng/sport/football'}
STOP = {'fc', 'sc', 'afc', 'cf', 'bk', 'jk', 'ii', 'united', 'city', 'club', 'the', 'de', 'el'}

def _get(url):
    return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=HDRS), timeout=25).read().decode('utf-8', 'replace'))

def norm(s): return re.sub(r'[^a-z0-9 ]', ' ', s.lower())
def toks(s): return set(w for w in norm(s).split() if len(w) >= 3 and w not in STOP)
def fnum(x):
    try: return float(x)
    except (TypeError, ValueError): return None

def fetch_events():
    """All upcoming football events with FT markets, flattened with tournament name."""
    out = []
    for pg in range(1, 12):
        url = BASE + f'pcUpcomingEvents?sportId=sr:sport:1&marketId=1,16,18,10,29,11&pageSize=100&pageNum={pg}&option=1'
        try:
            d = _get(url)['data']
        except Exception:
            break
        tours = d.get('tournaments', [])
        if not tours:
            break
        for t in tours:
            for e in t.get('events', []):
                e['_tournament'] = t['name']
                out.append(e)
        if pg * 100 >= d.get('totalNum', 0):
            break
    return out

def parse_markets(e):
    """-> {'1x2':{h,d,a}, 'ah_home':{hcp:odds}, 'ah_away':{hcp:odds}, 'ou':{line:{over,under}}, 'dc':{}, 'btts':{}, 'dnb':{}}"""
    M = {'1x2': {}, 'ah_home': {}, 'ah_away': {}, 'ou': {}, 'dc': {}, 'btts': {}, 'dnb': {}}
    for m in e.get('markets', []):
        mid = m.get('id')
        outs = {o.get('desc'): o.get('odds') for o in m.get('outcomes', [])}
        if mid == '1':
            M['1x2'] = {'home': outs.get('Home'), 'draw': outs.get('Draw'), 'away': outs.get('Away')}
        elif mid == '16':
            for desc, odd in outs.items():
                hm = re.search(r'\(([+-]?\d+(?:\.\d+)?)\)', desc)
                if not hm: continue
                hcp = float(hm.group(1))
                (M['ah_home'] if desc.startswith('Home') else M['ah_away'])[hcp] = odd
        elif mid == '18':
            spec = m.get('specifier', '')
            line = spec.split('=')[1] if '=' in spec else spec
            M['ou'][line] = {'over': next((v for k, v in outs.items() if k.startswith('Over')), None),
                             'under': next((v for k, v in outs.items() if k.startswith('Under')), None)}
        elif mid == '10':
            M['dc'] = outs
        elif mid == '29':
            M['btts'] = outs
        elif mid == '11':
            M['dnb'] = outs
    return M

# calibrated-pick name -> (function(markets) -> odds or None)
def pick_odds(pick, mk):
    if pick == 'AH+1 Home': return mk['ah_home'].get(1.0)
    if pick == 'AH+1 Away': return mk['ah_away'].get(1.0)
    if pick == '1 Home': return mk['1x2'].get('home')
    if pick == '2 Away': return mk['1x2'].get('away')
    if pick == '1X': return mk['dc'].get('Home or Draw')
    if pick == 'X2': return mk['dc'].get('Draw or Away')
    if pick == '12': return mk['dc'].get('Home or Away')
    if pick == 'Over1.5': return mk['ou'].get('1.5', {}).get('over')
    if pick == 'Over2.5': return mk['ou'].get('2.5', {}).get('over')
    if pick == 'Under2.5': return mk['ou'].get('2.5', {}).get('under')
    return None  # 2H/1H/halves/combo markets not in this feed

def match_event(home, away, hhmm, events):
    th, ta = toks(home), toks(away)
    try:
        pk = datetime.strptime(hhmm, '%H:%M')
    except Exception:
        pk = None
    for e in events:
        if not (th & toks(e.get('homeTeamName', '')) and ta & toks(e.get('awayTeamName', ''))):
            continue
        if pk:
            et = datetime.fromtimestamp(e['estimateStartTime'] / 1000, tz=timezone.utc).strftime('%H:%M')
            try:
                diff = abs((datetime.strptime(et, '%H:%M') - pk).total_seconds()) / 60
                if diff > 25 and diff < 1415:  # allow ~midnight wrap
                    continue
            except Exception:
                pass
        return e
    return None

def cushion_menu(mk, side):
    d = mk['ah_home'] if side == 'home' else mk['ah_away']
    plus = sorted(h for h in d if h >= 0)
    return " ".join(f"{'+' if h>=0 else ''}{h:g}@{d[h]}" for h in plus) or "(no + lines)"

def acc_line(pick, mk):
    """Safest PLACEABLE leg on SportyBet for this pick -> (market_label, odds_float, flag) or None.
    For an accumulator we want every leg to land, so prefer a real cushion >= +1; if SportyBet
    doesn't list one, fall back to the Double-Chance cover; only then to a thin +0.5."""
    if pick in ('AH+1 Home', 'AH+1 Away'):
        side = 'home' if 'Home' in pick else 'away'
        d = mk['ah_home'] if side == 'home' else mk['ah_away']
        big = sorted(h for h in d if h >= 1.0)          # +1, +1.5, +2 ... safe cushions
        if big:
            h = big[0]; return (f"AH +{h:g} {side.title()}", fnum(d[h]), 'safe')
        dc_key = 'Home or Draw' if side == 'home' else 'Draw or Away'
        dc = fnum(mk['dc'].get(dc_key))
        if dc:
            return (f"{'1X' if side == 'home' else 'X2'} (Double Chance)", dc, 'safe')
        half = fnum(d.get(0.5))
        if half:
            return (f"AH +0.5 {side.title()}", half, 'thin')   # 1-goal loss = no push, riskier
        return None
    od = fnum(pick_odds(pick, mk))
    return (pick, od, 'ok') if od else None

def main():
    print("fetching SportyBet catalog + odds...", flush=True)
    events = fetch_events()
    cat = set(toks(e['_tournament']) and e['_tournament'] for e in events)
    print(f"{len(events)} SportyBet events loaded\n", flush=True)
    blocks = open('/Users/apple/Downloads/draw/picks_calibrated.md').read().strip().split('\n\n')
    on = off = 0
    legs = []  # (pct, match, ko, label, odds, flag) - placeable accumulator legs
    for b in blocks:
        L = b.split('\n')
        m = re.match(r'(\d\d:\d\d)  (.+?)  \| ', L[0])
        if not m: continue
        ko, match = m.group(1), m.group(2)
        home, _, away = match.partition(' v ')
        tm = re.match(r'(.+?) (\d+)%', L[1].strip())
        if not tm: continue
        pick, pct = tm.group(1).strip(), int(tm.group(2))
        e = match_event(home, away, ko, events)
        if not e:
            off += 1
            continue
        on += 1
        al = acc_line(pick, parse_markets(e))
        if al:
            label, odds, flag = al
            legs.append((pct, match, ko, label, odds, flag))
    print(f"{on} of {on+off} model picks are on SportyBet; {len([x for x in legs])} have a placeable line.\n")

    safe = sorted([x for x in legs if x[5] == 'safe' or x[5] == 'ok'], reverse=True)
    thin = sorted([x for x in legs if x[5] == 'thin'], reverse=True)

    print("ACCUMULATOR LEGS - on SportyBet, real line, safest cover, highest confidence first")
    print(f"{'conf':>4}  {'time':5}  {'match':34}  {'bet (the line to tap on SportyBet)':30}  {'odds':>5}")
    print("-" * 92)
    combo = 1.0
    for pct, match, ko, label, odds, flag in safe:
        combo *= odds
        print(f"{pct:3}%  {ko:5}  {match[:34]:34}  {label:30}  {odds:>5.2f}")
    print("-" * 92)
    p_all = 1.0
    for pct, *_ in safe:
        p_all *= pct / 100
    print(f"{len(safe)} legs - combined odds about {combo:.1f}x "
          f"(1000 stake returns ~{combo*1000:,.0f}) - but all {len(safe)} land only ~{p_all*100:.0f}% of the time.")
    # what the safest subsets look like (accumulator = every leg must win)
    print("\nsafest subsets (take the top N by confidence to raise the all-land chance):")
    for n in (5, 8, 10, 12, 15):
        if n <= len(safe):
            sub = safe[:n]
            c = 1.0; p = 1.0
            for pct, mt, ko, lb, od, fl in sub:
                c *= od; p *= pct / 100
            print(f"  top {n:2}: ~{c:5.1f}x  all-land ~{p*100:2.0f}%  (1000 -> ~{c*1000:,.0f})")

    if thin:
        print("\nTHIN legs (only +0.5 on SportyBet, a 1-goal loss kills them - add at your own risk):")
        for pct, match, ko, label, odds, flag in thin:
            print(f"  {pct:3}%  {ko:5}  {match[:34]:34}  {label:24}  {odds:.2f}")

if __name__ == '__main__':
    main()
