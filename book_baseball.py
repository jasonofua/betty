#!/usr/bin/env python3
"""
book_baseball.py - Automated Baseball prediction and booking bot.
Uses Flashscore (Sport ID 6) for historical runs and SportyBet for booking.
"""

import sys, time, json, re, urllib.request, urllib.error
from math import exp as mexp, factorial
from collections import defaultdict
from datetime import datetime, timezone, timedelta
import os as _o; sys.path.append(_o.path.dirname(_o.path.abspath(__file__)))
import fetcher_baseball as F
import sportybet as S

HDRS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0',
        'Accept': 'application/json', 'Content-Type': 'application/json',
        'Referer': 'https://www.sportybet.com/ng/sport/baseball'}

WAT = timezone(timedelta(hours=1))

# SportyBet Baseball Markets
# 251: Winner (incl. extra innings) -> 1 (Home) or 2 (Away)
# 258: Total Runs (incl. extra innings) -> Over/Under
# 256: Handicap (Run Line, incl. extra innings)
# 260: Competitor1 (Home) Total Runs
# 261: Competitor2 (Away) Total Runs
# 275: Innings 1 to 5 - handicap
# 276: Innings 1 to 5 - total
STABLE_MKT = {'1', '251', '256', '258', '260', '261', '275', '276'}
MIN_CONF = 0.50
MIN_ODDS = 1.20
MAX_CODE = 60

# ---------------- SportyBet booking ----------------
def post(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=HDRS, method='POST')
    try:
        return json.loads(urllib.request.urlopen(req, timeout=25).read().decode('utf-8', 'replace'))
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode('utf-8', 'replace'))
    except Exception as e:
        return {'bizCode': -1, 'message': str(e)}

def verify(code):
    try:
        r = json.loads(urllib.request.urlopen(urllib.request.Request(
            'https://www.sportybet.com/api/ng/orders/share/' + code, headers=HDRS), timeout=20).read().decode())
        return len((r.get('data') or {}).get('outcomes', [])) if r.get('bizCode') == 10000 else 0
    except Exception:
        return 0

def book(selections):
    if not selections: return None
    sels = selections[:MAX_CODE]
    resp = post('https://www.sportybet.com/api/ng/orders/share', {'selections': sels})
    d = resp.get('data') or {}
    code = d.get('shareCode')
    return dict(code=code, url=d.get('shareURL'), booked=len(d.get('outcomes', [])),
                req=len(sels), msg=resp.get('message'), verified=verify(code) if code else 0)

def log_booking(code, url, label, legs):
    if not code: return
    out = [f"\n## {datetime.now(WAT):%Y-%m-%d %H:%M} WAT  |  {label}  |  code {code}"]
    if url: out.append(url)
    for lg in legs:
        ts, match, lab, odds = lg[0], lg[1], lg[2], lg[3]
        out.append(f"- {datetime.fromtimestamp(ts, tz=WAT):%a %H:%M}  {match}  -  {lab} @{odds:.2f}")
        if len(lg) > 4 and lg[4]:
            out += [f"    {line}" for line in lg[4]]
    open('/Users/apple/Downloads/draw/bookings_baseball.md', 'a', encoding='utf-8').write("\n".join(out) + "\n")

def fetch_events_baseball():
    BASE = 'https://www.sportybet.com/api/ng/factsCenter/'
    out = []
    for pg in range(1, 6):
        url = BASE + (f'pcUpcomingEvents?sportId=sr:sport:3&marketId=1,251,256,258,260,261,275,276'
                      f'&pageSize=100&pageNum={pg}&option=1')
        try:
            d = json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=HDRS), timeout=25)
                           .read().decode('utf-8', 'replace'))['data']
        except Exception:
            break
        tours = d.get('tournaments', [])
        if not tours: break
        for t in tours:
            for e in t.get('events', []):
                e['_tournament'] = t['name']
                out.append(e)
        if pg * 100 >= d.get('totalNum', 0): break
    return out

# ---------------- feature extraction ----------------
def recent_kc(raw):
    tab = blk = None; last = {}; h2h = []
    for s in F.sections(raw):
        if 'KA' in s: tab = s['KA']
        if 'KB' in s: blk = s['KB']; continue
        if tab != 'Overall' or 'KU' not in s or 'KT' not in s: continue
        try: ku, kt = int(s['KU']), int(s['KT'])
        except ValueError: continue
        if blk and blk.startswith('Last matches'):
            team_name = blk.split(": ")[1] if ": " in blk else ""
            kj = s.get('KJ', '').replace('*', '').strip()
            kk = s.get('KK', '').replace('*', '').strip()
            
            if team_name and kj and kk:
                tn_words = set(team_name.replace('.', '').split())
                kj_words = set(kj.replace('.', '').split())
                kk_words = set(kk.replace('.', '').split())
                
                # Check for overlap of words >= 3 chars (e.g. 'Cubs', 'Tigers', 'Lions')
                tn_sig = {w for w in tn_words if len(w) >= 3}
                if tn_sig & {w for w in kj_words if len(w) >= 3}:
                    v = 'home'
                    gf, ga = ku, kt
                elif tn_sig & {w for w in kk_words if len(w) >= 3}:
                    v = 'away'
                    gf, ga = kt, ku
                else:
                    v = s.get('KS', '')
                    gf, ga = (ku, kt) if v == 'home' else (kt, ku)
            else:
                v = s.get('KS', '')
                gf, ga = (ku, kt) if v == 'home' else (kt, ku)
                
            last.setdefault(blk, []).append(dict(ks=v, gf=gf, ga=ga, kc=int(s.get('KC', 0))))
        elif blk == 'Head-to-head matches':
            h2h.append(dict(home=s.get('KJ', '').lstrip('*'), away=s.get('KK', ''), hg=ku, ag=kt))
    names = list(last)
    hr = last[names[0]] if names else []
    ar = last[names[1]] if len(names) > 1 else []
    return hr, ar, h2h

def form_stats(rows, venue_filter=None):
    if venue_filter:
        rows = [r for r in rows if r.get('venue') == venue_filter or r.get('ks') == venue_filter]
    if not rows: return 0, 0
    o = rows[:10]
    return sum(r['gf'] for r in o)/len(o), sum(r['ga'] for r in o)/len(o)

def build_pf(hr, ar, h2h, rich, home_pitcher=None, away_pitcher=None):
    h_gf, h_ga = form_stats(hr, venue_filter='home')
    a_gf, a_ga = form_stats(ar, venue_filter='away')
    
    orig_h_ga, orig_a_ga = h_ga, a_ga
    if home_pitcher and home_pitcher.get('era') is not None:
        h_ga = (home_pitcher['era'] * 0.6) + (h_ga * 0.4)
    if away_pitcher and away_pitcher.get('era') is not None:
        a_ga = (away_pitcher['era'] * 0.6) + (a_ga * 0.4)

    h_exp = max(1.0, min((h_gf + a_ga) / 2, 10.0))
    a_exp = max(1.0, min((a_gf + h_ga) / 2, 10.0))
    
    # First 5 innings expected runs
    h_exp_f5 = a_exp_f5 = 0.0
    if rich and rich.get("deep_games", 0) > 0:
        if rich.get("home_f5_scored") is not None and rich.get("away_f5_conceded") is not None:
            h_exp_f5 = max(0.5, min((rich["home_f5_scored"] + rich["away_f5_conceded"]) / 2, 7.0))
        if rich.get("away_f5_scored") is not None and rich.get("home_f5_conceded") is not None:
            a_exp_f5 = max(0.5, min((rich["away_f5_scored"] + rich["home_f5_conceded"]) / 2, 7.0))
            
    # Fallback to roughly 55% of full game runs if F5 stats fail
    if h_exp_f5 == 0.0: h_exp_f5 = h_exp * 0.55
    if a_exp_f5 == 0.0: a_exp_f5 = a_exp * 0.55
    
    return dict(h_exp=h_exp, a_exp=a_exp, exp=h_exp+a_exp, gap=h_exp-a_exp,
                h_gf=h_gf, h_ga=h_ga, a_gf=a_gf, a_ga=a_ga, orig_h_ga=orig_h_ga, orig_a_ga=orig_a_ga,
                h_exp_f5=h_exp_f5, a_exp_f5=a_exp_f5, home_pitcher=home_pitcher, away_pitcher=away_pitcher)

def stat_block(pf):
    lines = [
        f"exp runs {pf['exp']:.2f} (home {pf['h_exp']:.2f} / away {pf['a_exp']:.2f})  gap {pf['gap']:.2f}",
        f"home runs_scored {pf['h_gf']:.2f}  runs_conceded {pf['h_ga']:.2f} (orig {pf['orig_h_ga']:.2f})",
        f"away runs_scored {pf['a_gf']:.2f}  runs_conceded {pf['a_ga']:.2f} (orig {pf['orig_a_ga']:.2f})",
    ]
    if pf.get('home_pitcher') or pf.get('away_pitcher'):
        hp = pf.get('home_pitcher') or {'pitcher': 'TBD', 'era': 'N/A'}
        ap = pf.get('away_pitcher') or {'pitcher': 'TBD', 'era': 'N/A'}
        lines.append(f"pitchers: {hp.get('pitcher')} ({hp.get('era')} ERA) v {ap.get('pitcher')} ({ap.get('era')} ERA)")
    if pf.get('h_exp_f5'):
        lines.append(f"F5 exp runs {pf['h_exp_f5']+pf['a_exp_f5']:.2f} (home {pf['h_exp_f5']:.2f} / away {pf['a_exp_f5']:.2f})")
    return lines

# ---------------- model + form-driven selection ----------------
def pois(k, l): return mexp(-l) * l**k / factorial(k)

def match_fixture(ev, fixtures):
    th, ta = S.toks(ev['homeTeamName']), S.toks(ev['awayTeamName'])
    if not th or not ta: return None
    et = ev['estimateStartTime'] / 1000
    for f in fixtures:
        if th & S.toks(f['home']) and ta & S.toks(f['away']) and abs(f['ts'] - et) <= 25 * 60:
            return f
    return None

def candidates(ev, pf):
    lh, la = pf['h_exp'], pf['a_exp']
    lh_f5, la_f5 = pf.get('h_exp_f5', 0), pf.get('a_exp_f5', 0)
    gap = abs(lh - la)
    home_fav = lh > la
    home_fav_f5 = lh_f5 > la_f5
    f5_contradicts = (home_fav != home_fav_f5)

    P  = lambda c: sum(pois(h, lh)*pois(a, la) for h in range(15) for a in range(15) if c(h, a))
    P_f5 = lambda c: sum(pois(h, lh_f5)*pois(a, la_f5) for h in range(15) for a in range(15) if c(h, a))
    
    hp_info = pf.get('home_pitcher') or {}
    ap_info = pf.get('away_pitcher') or {}
    hp_era = hp_info.get('era')
    ap_era = ap_info.get('era')
    has_pitchers = (hp_era is not None and ap_era is not None)
    max_era = max(hp_era, ap_era) if has_pitchers else 99.0

    out = []
    def add(label, fam, prob, odds, mid, spec, oid):
        od = S.fnum(odds)
        if od is None or od < MIN_ODDS or prob < MIN_CONF: return
        out.append(dict(label=label, fam=fam, prob=prob, odds=od, mid=mid, spec=spec, oid=oid))
        
    for m in ev.get('markets', []):
        mid = m.get('id'); spec = m.get('specifier', '')
        if mid not in STABLE_MKT: continue
        if m.get('status') not in (0, '0'): continue
        for o in m.get('outcomes', []):
            if o.get('isActive') == 0: continue
            d = o.get('desc', ''); oid = o.get('id'); od = o.get('odds')
            if mid == '1': # 9 innings 1X2
                if gap < 2.0 or f5_contradicts or not has_pitchers: continue
                if d == 'Home':   add('1 Home (9inn)', 'side_home', P(lambda h, a: h > a), od, mid, spec, oid)
                elif d == 'Away': add('2 Away (9inn)', 'side_away', P(lambda h, a: h < a), od, mid, spec, oid)
            elif mid == '251': # Winner incl extra innings (no draw)
                if gap < 2.0 or f5_contradicts or not has_pitchers: continue
                if d == 'Home':   add('Moneyline Home', 'side_home', P(lambda h, a: h >= a), od, mid, spec, oid)
                elif d == 'Away': add('Moneyline Away', 'side_away', P(lambda h, a: h <= a), od, mid, spec, oid)
            elif mid == '258': # Total runs
                mm = re.match(r'(Over|Under)\s+([\d.]+)', d)
                if mm:
                    line = float(mm.group(2))
                    if abs((lh + la) - line) < 1.0: continue
                    if mm.group(1) == 'Over':
                        if hp_era is not None and hp_era < 3.50: continue
                        if ap_era is not None and ap_era < 3.50: continue
                        add(f'Over {line:g}', 'over', P(lambda h, a: h + a > line), od, mid, spec, oid)
                    else:
                        if max_era >= 4.50: continue
                        add(f'Under {line:g}', 'under', P(lambda h, a: h + a < line), od, mid, spec, oid)
            elif mid == '260': # Home total
                mm = re.match(r'(Over|Under)\s+([\d.]+)', d)
                if mm:
                    line = float(mm.group(2))
                    Ph = lambda c: sum(pois(k, lh) for k in range(15) if c(k))
                    if mm.group(1) == 'Over':
                        if ap_era is not None and ap_era < 3.90: continue
                        add(f'Home Over {line:g}', 'home_over', Ph(lambda k: k > line), od, mid, spec, oid)
                    else:
                        if ap_era is not None and ap_era > 4.50: continue
                        add(f'Home Under {line:g}', 'home_under', Ph(lambda k: k < line), od, mid, spec, oid)
            elif mid == '261': # Away total
                mm = re.match(r'(Over|Under)\s+([\d.]+)', d)
                if mm:
                    line = float(mm.group(2))
                    Pa = lambda c: sum(pois(k, la) for k in range(15) if c(k))
                    if mm.group(1) == 'Over':
                        if hp_era is not None and hp_era < 3.90: continue
                        add(f'Away Over {line:g}', 'away_over', Pa(lambda k: k > line), od, mid, spec, oid)
                    else:
                        if hp_era is not None and hp_era > 4.50: continue
                        add(f'Away Under {line:g}', 'away_under', Pa(lambda k: k < line), od, mid, spec, oid)
            elif mid == '256': # Handicap
                if gap < 2.5 or f5_contradicts or not has_pitchers: continue
                mm = re.match(r'(Home|Away)\s*\(([+-]?\d+(?:\.\d+)?)\)', d)
                if mm:
                    hc = float(mm.group(2))
                    if mm.group(1) == 'Home':
                        add(f'AH {hc:+g} Home', 'hc_home', P(lambda h, a: h - a + hc > 0), od, mid, spec, oid)
                    else:
                        add(f'AH {hc:+g} Away', 'hc_away', P(lambda h, a: a - h + hc > 0), od, mid, spec, oid)

    return out

def pick_leg(ev, pf):
    cands = candidates(ev, pf)
    if not cands: return None
    
    # Simple strategy: prioritize highest probability over highest odds.
    # We want safe baseball bets.
    def rankkey(c):
        # We heavily weight probability for baseball.
        return (c['prob'], c['odds'])
        
    return max(cands, key=rankkey)

def main():
    dry = '--dry' in sys.argv
    print("loading MLB probable pitchers...", flush=True)
    mlb_pitchers = F.get_mlb_pitchers()
    print("loading full SportyBet baseball board + flashscore fixtures...", flush=True)
    events = fetch_events_baseball()
    fixtures = []
    for off in (0, 1):
        fixtures += F.get_fixtures(off)
    now = time.time()
    fixtures = [f for f in fixtures if f['ts'] > now + 300]
    
    pairs, seen = [], set()
    for ev in events:
        if ev['estimateStartTime'] / 1000 <= now + 300: continue
        f = match_fixture(ev, fixtures)
        if f and f['id'] not in seen:
            seen.add(f['id']); pairs.append((ev, f))
            
    print(f"{len(events)} events, {len(fixtures)} fixtures, {len(pairs)} matched & bettable", flush=True)

    legs = []
    for i, (ev, f) in enumerate(pairs):
        home_rows, away_rows, h2h, rich = F.fetch_rich_history(f['id'])
        if not home_rows or not away_rows: continue
        
        home_pitcher = away_pitcher = None
        if 'MLB' in f['league']:
            for t_name, p_info in mlb_pitchers.items():
                if t_name in f['home'].lower() or f['home'].lower() in t_name:
                    home_pitcher = p_info
                if t_name in f['away'].lower() or f['away'].lower() in t_name:
                    away_pitcher = p_info
                    
        pf = build_pf(home_rows, away_rows, h2h, rich, home_pitcher, away_pitcher)
        leg = pick_leg(ev, pf)
        if not leg: continue
        
        sb = stat_block(pf)
        bs = dict(eventId=ev['eventId'], productId=3, marketId=leg['mid'], specifier=leg['spec'], outcomeId=leg['oid'])
        legs.append((leg['prob'], f['ts'], f"{f['home']} v {f['away']}", leg['label'], leg['odds'], f['league'], sb, bs))
        
        if (i + 1) % 10 == 0:
            print(f"  ...{i+1}/{len(pairs)} processed, {len(legs)} legs", flush=True)
        time.sleep(0.05)

    legs.sort(key=lambda x: x[1]) # Sort by time
    
    if not legs:
        print("No valid baseball bets found.")
        return
        
    combo = p_all = 1.0; sels = []
    out = [f"{len(legs)} BASEBALL ACCUMULATOR LEGS", "==============================="]
    
    for prob, ts, match, label, odds, league, sb, bs in legs:
        combo *= odds; p_all *= prob
        ko = datetime.fromtimestamp(ts, tz=WAT).strftime('%H:%M')
        out.append(f"\n[{prob*100:3.0f}%] {ko}  {match}   {label} @{odds:.2f}   {league}")
        out += [f"        {line}" for line in sb]
        sels.append(bs)
        
    out.append(f"\n  -> {len(legs)} legs, combined ~{combo:,.0f}x, all-land ~{p_all*100:.1f}%")
    
    if not dry:
        bk = book(sels)
        if bk and bk['code']:
            extra = "" if bk['booked'] == bk['req'] else f" (booked {bk['booked']}/{bk['req']})"
            out.append(f"  >> BOOKING CODE: {bk['code']}  ({bk['verified']} load back)  {bk['url']}{extra}")
            log_booking(bk['code'], bk['url'], "BASEBALL", [(lg[1], lg[2], lg[3], lg[4], lg[6]) for lg in legs])
        else:
            out.append(f"  >> booking failed: {bk['msg'] if bk else 'no selections'}")
            
    txt = "\n".join(out)
    print("\n" + txt)
    open('/Users/apple/Downloads/draw/acca_baseball_out.txt', 'w').write(txt)

if __name__ == '__main__':
    main()
