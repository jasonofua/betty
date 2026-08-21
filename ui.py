#!/usr/bin/env python3
"""Local web UI for the dynamic engine.

    python3 ui.py            ->  http://localhost:8017

Drives book_dynamic/dynamic_v4 directly - target odds, until-hour and days go
straight into the same build/pick_for_target/book path the CLI uses. This has
to run locally: a board build takes 15-40 minutes of Flashscore fetching and
Poisson work, which is why the old Vercel page could never use this engine.
"""
import io, json, threading, subprocess, sys, contextlib, datetime as dt
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, '/Users/apple/Downloads/draw')
import acca as A
import book_dynamic as BD
import dynamic_v4 as D

PORT = 8017

JOB = {'state': 'idle', 'log': [], 'result': None, 'params': None,
       'started': None}
LOCK = threading.Lock()


class _LogIO(io.TextIOBase):
    """stdout shim: every line the engine prints lands in the job log."""
    def write(self, s):
        for part in s.splitlines():
            if part.strip():
                JOB['log'].append(part.rstrip())
        return len(s)


def run_job(target, until, days, dry):
    JOB.update(state='building', log=[], result=None,
               params=dict(target=target, until=until, days=days, dry=dry),
               started=dt.datetime.now(A.WAT).strftime('%H:%M'))
    try:
        with contextlib.redirect_stdout(_LogIO()):
            board = BD.build(until_h=until, days=days)
            if not board:
                JOB.update(state='done', result={'error': 'no supported options on this board'})
                return
            JOB['state'] = 'selecting'
            pool, seen = [], set()
            for rank in range(D.TOP_N):
                for l in BD.slip(board, rank):
                    k = (l['bs']['eventId'], l['bs']['marketId'], l['bs']['specifier'])
                    if k in seen:
                        continue
                    seen.add(k)
                    pool.append(l)
            legs, combo, surv = BD.pick_for_target(pool, target)
            if not legs or combo < target:
                JOB.update(state='done', result={
                    'error': f'cannot reach {target:g}x - best is {combo:,.1f}x '
                             f'from {len(legs)} legs (pool of {len(pool)})'})
                return
            legs.sort(key=lambda l: l['ts'])
            res = {
                'combo': round(combo, 1), 'est': round(surv * 100, 2),
                'pool': len(pool),
                'legs': [dict(when=l['ts'].strftime('%a %H:%M'),
                              match=l['match'], label=l['label'],
                              odds=l['odds'],
                              prob=round(BD.true_prob(l['odds']) * 100))
                         for l in legs],
            }
            if dry:
                res['dry'] = True
            else:
                JOB['state'] = 'booking'
                bk = A.book([l['bs'] for l in legs])
                if bk and bk.get('code'):
                    res['code'] = bk['code']
                    res['url'] = bk['url']
                    got = bk.get('verified') or bk['booked']
                    if got != bk['req']:
                        res['short'] = f"booked {got}/{bk['req']}"
                    A.log_booking(bk['code'], bk['url'],
                                  f"dynamic_v4 target {target:g}x until {until}:00"
                                  + (f" +{days}d" if days else ""),
                                  [(l['ts'].timestamp(), l['match'], l['label'],
                                    l['odds'], l['stats']) for l in legs])
                else:
                    res['error'] = f"booking failed: {bk.get('msg') if bk else 'no selections'}"
            JOB.update(state='done', result=res)
    except Exception as e:
        JOB.update(state='done', result={'error': f'{type(e).__name__}: {e}'})


def _page():
    """The front-end lives in ui_page.html and is re-read per request, so
    design changes land on refresh without restarting the server."""
    try:
        with open('/Users/apple/Downloads/draw/public/index.html', encoding='utf-8') as f:
            return f.read()
    except OSError:
        return '<h1>public/index.html missing</h1>'



class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _send(self, body, ctype='application/json', code=200):
        data = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        # The Vercel-hosted copy of the page books through this server, so it
        # must be allowed to call across origins.
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        # Chrome's Private Network Access: a public https page may only call
        # localhost when the local server explicitly opts in.
        self.send_header('Access-Control-Allow-Private-Network', 'true')
        self.end_headers()

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == '/':
            self._send(_page(), 'text/html; charset=utf-8')
        elif u.path == '/api/status':
            self._send(json.dumps({'state': JOB['state'], 'log': JOB['log'][-40:],
                                   'result': JOB['result'], 'started': JOB['started']}))
        elif u.path == '/api/grade':
            codes = parse_qs(u.query).get('codes', [''])[0].replace(',', ' ').split()
            if not codes:
                self._send('no codes given', 'text/plain'); return
            try:
                p = subprocess.run([sys.executable, 'grade_code.py'] + codes[:8],
                                   capture_output=True, text=True, timeout=600,
                                   cwd='/Users/apple/Downloads/draw')
                self._send(p.stdout + p.stderr, 'text/plain; charset=utf-8')
            except subprocess.TimeoutExpired:
                self._send('grade timed out', 'text/plain')
        else:
            self._send('not found', 'text/plain', 404)

    def do_POST(self):
        if urlparse(self.path).path != '/api/run':
            self._send('not found', 'text/plain', 404); return
        n = int(self.headers.get('Content-Length', 0))
        try:
            p = json.loads(self.rfile.read(n) or b'{}')
            target = float(p.get('target', 50))
            until = int(p.get('until', 23))
            days = int(p.get('days', 0))
            dry = bool(p.get('dry'))
            assert 2 <= target <= 100000 and 0 <= until <= 23 and 0 <= days <= 4
        except Exception:
            self._send(json.dumps({'error': 'bad parameters'}), code=400); return
        with LOCK:
            if JOB['state'] not in ('idle', 'done'):
                self._send(json.dumps({'error': 'a run is already in progress'}), code=409)
                return
            JOB['state'] = 'building'
        threading.Thread(target=run_job, args=(target, until, days, dry),
                         daemon=True).start()
        self._send(json.dumps({'ok': True}))


if __name__ == '__main__':
    print(f"dynamic booker UI  ->  http://localhost:{PORT}")
    ThreadingHTTPServer(('127.0.0.1', PORT), Handler).serve_forever()
