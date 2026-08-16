from http.server import BaseHTTPRequestHandler
import json, sys, os

root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

import book_rollover as BR
import acca as A

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            soccer_cands = BR.fetch_soccer_candidates()
            baseball_cands = BR.fetch_baseball_candidates()
            all_cands = soccer_cands + baseball_cands
            slip, total_odds = BR.build_rollover_slip(all_cands, target_odds=10.0)
            
            p_all = 1.0
            for leg in slip:
                p_all *= leg['prob']
                
            sels = [leg['bs'] for leg in slip]
            bk = A.book(sels)
            
            data = {
                "status": "success",
                "type": "rollover",
                "title": "Daily ~10.0 Odds Rollover Slip",
                "total_odds": round(total_odds, 2),
                "model_prob": round(p_all * 100, 1),
                "booking_code": bk.get('code') if bk else None,
                "share_url": bk.get('url') if bk else None,
                "legs_count": len(slip),
                "legs": [
                    {
                        "sport": leg['sport'],
                        "match": leg['match'],
                        "label": leg['label'],
                        "odds": leg['odds'],
                        "prob": round(leg['prob'] * 100, 1),
                        "league": leg['league'],
                        "time": leg.get('ts'),
                        "stat_block": leg.get('sb', [])
                    }
                    for leg in slip
                ]
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
