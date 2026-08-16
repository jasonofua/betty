from http.server import BaseHTTPRequestHandler
import json, sys, os

root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

import book_baseball as BB
import fetcher_baseball as FB
import acca as A
import time

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            mlb_pitchers = FB.get_mlb_pitchers()
            events = BB.fetch_events_baseball()
            fixtures = FB.get_fixtures(0) + FB.get_fixtures(1)
            now = time.time()
            fixtures = [f for f in fixtures if f['ts'] > now + 300]
            
            seen, pairs = set(), []
            for ev in events:
                if ev['estimateStartTime'] / 1000 <= now + 300: continue
                f = BB.match_fixture(ev, fixtures)
                if f and f['id'] not in seen:
                    seen.add(f['id']); pairs.append((ev, f))
                    
            legs = []
            for i, (ev, f) in enumerate(pairs[:30]):
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
                
                sb = BB.stat_block(pf)
                bs = dict(eventId=ev['eventId'], productId=3, marketId=leg['mid'], specifier=leg['spec'], outcomeId=leg['oid'])
                legs.append(dict(
                    sport='Baseball',
                    prob=round(leg['prob'] * 100, 1),
                    match=f"{f['home']} v {f['away']}",
                    label=leg['label'],
                    odds=leg['odds'],
                    league=f['league'],
                    time=f['ts'],
                    stat_block=sb,
                    bs=bs
                ))
                time.sleep(0.01)
                
            combo = 1.0
            p_all = 1.0
            for l in legs:
                combo *= l['odds']
                p_all *= (l['prob'] / 100.0)
                
            sels = [l['bs'] for l in legs]
            bk = A.book(sels)
            
            data = {
                "status": "success",
                "type": "baseball",
                "title": "Baseball Model Pro Accumulator",
                "total_odds": round(combo, 2),
                "model_prob": round(p_all * 100, 1),
                "booking_code": bk.get('code') if bk else None,
                "share_url": bk.get('url') if bk else None,
                "legs_count": len(legs),
                "legs": legs
            }
            
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(data).encode('utf-8'))
        except Exception as e:
            self.send_response(500)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({"status": "error", "message": str(e)}).encode('utf-8'))
