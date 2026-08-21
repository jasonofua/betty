"""Cloud booking bridge: the hosted page books with no machine at home.

POST /api/book  {target, until, days, dry}  -> dispatches the GitHub Actions
                                               workflow that runs the engine
GET  /api/book                              -> latest run state + runs/latest.json

The engine needs 15-40 minutes of compute per board, which no serverless
function allows - Actions is the free computer that will sit through it.
Requires GH_TOKEN (repo scope) and GH_REPO env vars on Vercel.
"""
from http.server import BaseHTTPRequestHandler
import json, os, base64, urllib.request

REPO = os.environ.get('GH_REPO', 'jasonofua/betty')
TOKEN = os.environ.get('GH_TOKEN', '')
API = 'https://api.github.com'


def gh(path, method='GET', body=None):
    req = urllib.request.Request(API + path, method=method,
                                 data=json.dumps(body).encode() if body else None)
    req.add_header('Authorization', 'Bearer ' + TOKEN)
    req.add_header('Accept', 'application/vnd.github+json')
    req.add_header('User-Agent', 'betty-ui')
    with urllib.request.urlopen(req, timeout=15) as r:
        raw = r.read()
        return json.loads(raw) if raw.strip() else {}


class handler(BaseHTTPRequestHandler):
    def _out(self, obj, code=200):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        try:
            runs = gh(f'/repos/{REPO}/actions/workflows/book.yml/runs?per_page=1')
            run = (runs.get('workflow_runs') or [{}])[0]
            latest = None
            try:
                c = gh(f'/repos/{REPO}/contents/runs/latest.json?ref=main')
                latest = json.loads(base64.b64decode(c.get('content', '')))
            except Exception:
                pass
            self._out({'run': {'status': run.get('status'),
                               'conclusion': run.get('conclusion'),
                               'created_at': run.get('created_at'),
                               'html_url': run.get('html_url')},
                       'latest': latest})
        except Exception as e:
            self._out({'error': f'{type(e).__name__}: {e}'}, 500)

    def do_POST(self):
        try:
            n = int(self.headers.get('Content-Length', 0))
            p = json.loads(self.rfile.read(n) or b'{}')
            target = float(p.get('target', 50))
            until = int(p.get('until', 23))
            days = int(p.get('days', 0))
            assert 2 <= target <= 100000 and 0 <= until <= 23 and 0 <= days <= 4
            gh(f'/repos/{REPO}/actions/workflows/book.yml/dispatches', 'POST',
               {'ref': 'main', 'inputs': {'target': str(int(target)),
                                          'until': str(until), 'days': str(days),
                                          'dry': 'true' if p.get('dry') else 'false'}})
            self._out({'ok': True})
        except AssertionError:
            self._out({'error': 'bad parameters'}, 400)
        except Exception as e:
            self._out({'error': f'{type(e).__name__}: {e}'}, 500)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()
