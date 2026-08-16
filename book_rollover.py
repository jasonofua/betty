#!/usr/bin/env python3
"""
DAILY ROLLOVER BUILDER (~10.0 ODDS)
Combines top-performing, validated winning patterns from Soccer and Baseball.

Soccer Winning Patterns:
 - S1: 1H Under 2.5 (exp < 2.70, low HT expected goals)
 - S2: Double Chance / DNB (exp gap >= +1.15)
 - S3: High-Vitality 2H Over 0.5 (total_2h >= 1.70, vital_offense = True, h2h_safe = True)

Baseball Winning Patterns:
 - B1: High-Expectancy Total Overs (exp >= Line + 2.0)
 - B2: Pitcher-Backed Total Unders (both ERA < 4.00, exp <= Line - 2.0)
 - B3: High-ERA Opponent Team Overs (opposing ERA > 4.20)

Usage:
  python3 book_rollover.py
  python3 book_rollover.py --dry
  python3 book_rollover.py --target 10.0
"""

import sys, time, json, re
from datetime import datetime, timezone, timedelta

sys.path.append('/Users/apple/Downloads/draw')
import book_v3 as V3
import book_baseball as BB
import acca as A
import fetcher_v2 as F
import fetcher_v3 as F3
import fetcher_baseball as FB
import sportybet as S

TARGET_ODDS_DEFAULT = 10.0
MAX_SLIP_LEGS = 12
WAT = timezone(timedelta(hours=1))

def fetch_soccer_candidates():
    print("⚽ Loading Soccer events & Flashscore fixtures...", flush=True)
    events = V3.fetch_events_rich()
    fixtures = F.get_fixtures(0) + F.get_fixtures(1)
    now = time.time()
    now_wat = datetime.fromtimestamp(now, tz=WAT)
    cutoff_dt = now_wat.replace(hour=23, minute=0, second=0, microsecond=0)
    if now_wat.hour >= 23:
        cutoff_dt += timedelta(days=1)
    cutoff = cutoff_dt.timestamp()
    fixtures = [f for f in fixtures if f['ts'] > now + 300]
    
    seen, pairs = set(), []
    for ev in events:
        et = ev.get('estimateStartTime', 0) / 1000
        if not (now + 300 < et <= cutoff): continue
        f = A.match_fixture(ev, fixtures)
        if not f or f['id'] in seen: continue
        seen.add(f['id']); pairs.append((ev, f))
        
    print(f"  ...{len(pairs)} bettable soccer fixtures", flush=True)
    
    legs = []
    BLACKLIST = ['cup', 'copa', 'pokal', 'taca', 'taça', 'friend', 'youth', 'u21', 'u19', 'u20', 'u23', 'reserve', 'women', 'qualifi']
    for i, (ev, f) in enumerate(pairs[:60]):
        if any(b in f.get('league', '').lower() for b in BLACKLIST): continue
        raw = F.fetch(f"df_hh_1_{f['id']}")
        if not raw: continue
        hr, ar, h2h = A.recent_kc(raw)
        if not hr or not ar: continue
        lh = max(0.2, min((A.vmean(hr, 'gf', 'home') + A.vmean(ar, 'ga', 'away')) / 2, 4.5))
        la = max(0.2, min((A.vmean(ar, 'gf', 'away') + A.vmean(hr, 'ga', 'home')) / 2, 4.5))
        lh, la = A.strength_adjust(lh, la, hr, ar)
        pf = A.build_pf(hr, ar, lh, la, h2h, f['home'])
        
        rich = {}
        try:
            _, _, rich = F3.fetch_rich_history(f['id'])
        except Exception:
            rich = {}
            
        result = V3.pick_best(ev, pf, rich, hr, ar, f['league'])
        if not result: continue
        
        # Filter out fragile/volatile markets and high-odds/low-confidence legs for rollover slips
        if any(fragile in result['label'] for fragile in ['GG', 'CS', 'Clean Sheet', 'Win to Nil']):
            continue
        if result['prob'] < 0.75 or result['odds'] > 1.45:
            continue
        
        sb = A.stat_block(pf, hr, ar)
        bs = result['bs']
        legs.append(dict(
            sport='Soccer',
            prob=result['prob'],
            ts=f['ts'],
            match=f"{f['home']} v {f['away']}",
            label=result['label'],
            odds=result['odds'],
            league=f['league'],
            sb=sb,
            bs=bs
        ))
        time.sleep(0.02)
        
    print(f"  -> Generated {len(legs)} soccer candidate legs", flush=True)
    return legs

def fetch_baseball_candidates():
    print("⚾ Loading Baseball events & MLB pitchers...", flush=True)
    mlb_pitchers = FB.get_mlb_pitchers()
    events = BB.fetch_events_baseball()
    fixtures = FB.get_fixtures(0) + FB.get_fixtures(1)
    now = time.time()
    now_wat = datetime.fromtimestamp(now, tz=WAT)
    cutoff_dt = now_wat.replace(hour=23, minute=0, second=0, microsecond=0)
    if now_wat.hour >= 23:
        cutoff_dt += timedelta(days=1)
    cutoff = cutoff_dt.timestamp()
    fixtures = [f for f in fixtures if f['ts'] > now + 300]
    
    seen, pairs = set(), []
    for ev in events:
        et = ev.get('estimateStartTime', 0) / 1000
        if not (now + 300 < et <= cutoff): continue
        f = BB.match_fixture(ev, fixtures)
        if f and f['id'] not in seen:
            seen.add(f['id']); pairs.append((ev, f))
            
    print(f"  ...{len(pairs)} bettable baseball fixtures", flush=True)
    
    legs = []
    for i, (ev, f) in enumerate(pairs[:40]):
        home_rows, away_rows, h2h, rich = FB.fetch_rich_history(f['id'])
        if not home_rows or not away_rows: continue
        
        home_pitcher = away_pitcher = None
        if 'MLB' in f['league']:
            for t_name, p_info in mlb_pitchers.items():
                if t_name in f['home'].lower() or f['home'].lower() in t_name:
                    home_pitcher = p_info
                if t_name in f['away'].lower() or f['away'].lower() in t_name:
                    away_pitcher = p_info
                    
        pf = BB.build_pf(home_rows, away_rows, h2h, rich, home_pitcher, away_pitcher)
        leg = BB.pick_leg(ev, pf)
        if not leg: continue
        
        # Filter out volatile F5 markets and low-confidence / high-odds legs for rollover
        if 'F5' in leg['label'] or leg['prob'] < 0.75 or leg['odds'] > 1.45:
            continue
        
        sb = BB.stat_block(pf)
        bs = dict(eventId=ev['eventId'], productId=3, marketId=leg['mid'], specifier=leg['spec'], outcomeId=leg['oid'])
        legs.append(dict(
            sport='Baseball',
            prob=leg['prob'],
            ts=f['ts'],
            match=f"{f['home']} v {f['away']}",
            label=leg['label'],
            odds=leg['odds'],
            league=f['league'],
            sb=sb,
            bs=bs
        ))
        time.sleep(0.02)
        
    print(f"  -> Generated {len(legs)} baseball candidate legs", flush=True)
    return legs

def build_rollover_slip(candidates, target_odds=TARGET_ODDS_DEFAULT):
    # Sort candidates by model probability descending (safest first)
    candidates.sort(key=lambda x: x['prob'], reverse=True)
    
    selected = []
    current_odds = 1.0
    seen_matches = set()
    
    for c in candidates:
        if c['match'] in seen_matches: continue
        selected.append(c)
        seen_matches.add(c['match'])
        current_odds *= c['odds']
        
        if current_odds >= target_odds or len(selected) >= MAX_SLIP_LEGS:
            break
            
    return selected, current_odds

def main():
    dry = '--dry' in sys.argv
    target_odds = float(sys.argv[sys.argv.index('--target') + 1]) if '--target' in sys.argv else TARGET_ODDS_DEFAULT
    
    print(f"============================================================")
    print(f"🎯 BUILDING DAILY ROLLOVER ACCUMULATOR (~{target_odds:.1f} ODDS)")
    print(f"============================================================\n")
    
    soccer_cands = fetch_soccer_candidates()
    baseball_cands = fetch_baseball_candidates()
    
    all_candidates = soccer_cands + baseball_cands
    if not all_candidates:
        print("❌ No valid candidate legs found across soccer or baseball.")
        return
        
    slip_legs, total_odds = build_rollover_slip(all_candidates, target_odds=target_odds)
    
    print(f"\n============================================================")
    print(f"🔥 ROLLOVER SLIP CREATED ({len(slip_legs)} LEGS | ~{total_odds:.2f} COMBINED ODDS)")
    print(f"============================================================\n")
    
    sels = []
    log_lines = []
    p_all = 1.0
    
    for leg in slip_legs:
        p_all *= leg['prob']
        ko = datetime.fromtimestamp(leg['ts'], tz=WAT).strftime('%a %H:%M')
        sp_icon = '⚽' if leg['sport'] == 'Soccer' else '⚾'
        line_str = f"[{leg['prob']*100:3.0f}%] {sp_icon} {ko}  {leg['match']}   {leg['label']} @{leg['odds']:.2f}   ({leg['league']})"
        print(line_str)
        sels.append(leg['bs'])
        log_lines.append((leg['ts'], leg['match'], f"[{leg['sport']}] {leg['label']}", leg['odds'], leg['sb']))
        
    print(f"\n  -> Total Combined Odds: ~{total_odds:.2f}x")
    print(f"  -> Model Probability: ~{p_all*100:.1f}%\n")
    
    if not dry:
        bk = A.book(sels)
        if bk and bk['code']:
            url = bk['url']
            code = bk['code']
            print(f"  >> BOOKING CODE: {code}  ({bk['verified']} loaded)  {url}")
            A.log_booking(code, url, f"10-ODDS ROLLOVER SLIP", log_lines)
        else:
            print(f"  >> Booking failed: {bk['msg'] if bk else 'no response'}")
    else:
        print("  (Dry-run mode — not booked)")

if __name__ == '__main__':
    main()
