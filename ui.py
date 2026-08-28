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

import os
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import acca as A
import book_dynamic as BD
import dynamic_v4 as D

PORT = int(os.environ.get('PORT', 8017))
HOST = '0.0.0.0' if os.environ.get('PORT') else '127.0.0.1'

JOB = {'state': 'idle', 'log': [], 'result': None, 'params': None,
       'started': None}
LOCK = threading.Lock()

# Corpus crawling on this machine's connection, in parallel with the local one.
# Two IPs mine the Flashscore history frontier at once and neither is throttled
# as hard as one machine doing both. /api/crawl starts it, /api/crawl_status
# reports, /api/crawl_data streams the harvested rows back for merging.
CRAWL = {'state': 'idle', 'kept': 0, 'seen': 0, 'note': ''}


def crawl_job(cap, floor_iso, skip_ids):
    import subprocess
    CRAWL.update(state='running', kept=0, note='starting')
    try:
        p = subprocess.run([sys.executable,
                            os.path.join(ROOT, 'experiments', 'deep_crawl.py'),
                            str(cap), floor_iso],
                           capture_output=True, text=True, timeout=20000, cwd=ROOT)
        tail = (p.stdout or '')[-400:] + (p.stderr or '')[-200:]
        CRAWL.update(state='done', note=tail.strip()[-300:])
    except Exception as e:
        CRAWL.update(state='done', note=f'{type(e).__name__}: {e}')


class _LogIO(io.TextIOBase):
    """stdout shim: every line the engine prints lands in the job log."""
    def write(self, s):
        for part in s.splitlines():
            if part.strip():
                JOB['log'].append(part.rstrip())
        return len(s)


def run_job(target, until, days, dry, rollover=False):
    JOB.update(state='building', log=[], result=None,
               params=dict(target=target, until=until, days=days, dry=dry,
                           rollover=rollover),
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
            if rollover:
                legs, combo, surv = BD.pick_rollover(pool)
                if not legs:
                    JOB.update(state='done', result={'error':
                        f'no rollover today: even the smallest slip only lands '
                        f'{surv:.0%} - the board does not clear the 30% floor'})
                    return
            else:
                legs, combo, surv = BD.pick_for_target(pool, target)
            if not legs:
                JOB.update(state='done', result={'error': 'no bookable legs on this board'})
                return
            warn = None
            if not rollover and combo < target:
                # Asked-for target not reachable: warn, then book the best the
                # board offers instead of refusing (changed 21 Aug on request).
                warn = (f'{target:g}x not reachable — booked the best available: '
                        f'{combo:,.1f}x from {len(legs)} legs (pool of {len(pool)})')
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
            if warn:
                res['warn'] = warn
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
                                  (f"rollover {combo:.2f}x est {surv:.0%}" if rollover
                                   else f"dynamic_v4 target {target:g}x until {until}:00"
                                        + (f" +{days}d" if days else "")),
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
        with open(os.path.join(ROOT, 'public', 'index.html'), encoding='utf-8') as f:
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
        elif u.path == '/api/crawl_status':
            self._send(json.dumps(CRAWL))
        elif u.path == '/api/crawl_data':
            # stream back the harvested corpus for merging on the other side
            try:
                after = float(parse_qs(u.query).get('after', ['0'])[0])
            except ValueError:
                after = 0.0
            out = []
            try:
                with open(os.path.join(ROOT, 'experiments', 'dataset.jsonl')) as f:
                    for line in f:
                        try:
                            r = json.loads(line)
                        except ValueError:
                            continue
                        if r.get('ts', 0) > after:
                            out.append(line.rstrip())
            except OSError:
                pass
            self._send('\n'.join(out[-20000:]), 'text/plain; charset=utf-8')
        elif u.path == '/api/grade':
            codes = parse_qs(u.query).get('codes', [''])[0].replace(',', ' ').split()
            if not codes:
                self._send('no codes given', 'text/plain'); return
            try:
                p = subprocess.run([sys.executable, 'grade_code.py'] + codes[:8],
                                   capture_output=True, text=True, timeout=600,
                                   cwd=ROOT)
                self._send(p.stdout + p.stderr, 'text/plain; charset=utf-8')
            except subprocess.TimeoutExpired:
                self._send('grade timed out', 'text/plain')
        else:
            self._send('not found', 'text/plain', 404)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == '/api/crawl':
            n = int(self.headers.get('Content-Length', 0))
            try:
                p = json.loads(self.rfile.read(n) or b'{}')
                cap = int(p.get('cap', 6000)); floor = str(p.get('floor', '2026-06-01'))
            except Exception:
                self._send(json.dumps({'error': 'bad parameters'}), code=400); return
            if CRAWL['state'] == 'running':
                self._send(json.dumps({'error': 'crawl already running'}), code=409); return
            threading.Thread(target=crawl_job, args=(cap, floor, None),
                             daemon=True).start()
            self._send(json.dumps({'ok': True})); return
        if path != '/api/run':
            self._send('not found', 'text/plain', 404); return
        n = int(self.headers.get('Content-Length', 0))
        try:
            p = json.loads(self.rfile.read(n) or b'{}')
            target = float(p.get('target', 50))
            until = int(p.get('until', 23))
            days = int(p.get('days', 0))
            dry = bool(p.get('dry'))
            rollover = bool(p.get('rollover'))
            assert 2 <= target <= 100000 and 0 <= until <= 23 and 0 <= days <= 4
        except Exception:
            self._send(json.dumps({'error': 'bad parameters'}), code=400); return
        with LOCK:
            if JOB['state'] not in ('idle', 'done'):
                self._send(json.dumps({'error': 'a run is already in progress'}), code=409)
                return
            JOB['state'] = 'building'
        threading.Thread(target=run_job, args=(target, until, days, dry, rollover),
                         daemon=True).start()
        self._send(json.dumps({'ok': True}))


if __name__ == '__main__':
    print(f"dynamic booker UI  ->  http://localhost:{PORT}")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
