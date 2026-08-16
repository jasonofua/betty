from http.server import BaseHTTPRequestHandler
import json, sys, os

root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

import book_v3 as V3
import acca as A
import fetcher_v2 as F
import fetcher_v3 as F3
import time

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            events = V3.fetch_events_rich()
            fixtures = F.get_fixtures(0) + F.get_fixtures(1)
            now = time.time()
            cutoff = now + 36 * 3600
            fixtures = [f for f in fixtures if f['ts'] > now + 300]
            
            seen, pairs = set(), []
            for ev in events:
                et = ev.get('estimateStartTime', 0) / 1000
                if not (now + 300 < et <= cutoff): continue
                f = A.match_fixture(ev, fixtures)
                if not f or f['id'] in seen: continue
                seen.add(f['id']); pairs.append((ev, f))
                
            legs = []
            BLACKLIST = ['cup', 'copa', 'pokal', 'taca', 'taça', 'friend', 'youth', 'u21', 'u19', 'u20', 'u23', 'reserve', 'women', 'qualifi']
            for i, (ev, f) in enumerate(pairs[:35]):
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
                
                sb = A.stat_block(pf, hr, ar)
                legs.append(dict(
                    sport='Soccer',
                    prob=round(result['prob'] * 100, 1),
                    match=f"{f['home']} v {f['away']}",
                    label=result['label'],
                    odds=result['odds'],
                    league=f['league'],
                    time=f['ts'],
                    stat_block=sb,
                    bs=result['bs']
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
                "type": "soccer",
                "title": "Soccer Model Pro Accumulator",
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
