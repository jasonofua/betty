#!/usr/bin/env python3
"""Local web UI for the dynamic engine.

    python3 ui.py            ->  http://localhost:8017

Drives book_dynamic/dynamic_v4 directly - target odds, until-hour and days go
straight into the same build/pick_for_target/book path the CLI uses. This has
to run locally: a board build takes 15-40 minutes of Flashscore fetching and
Poisson work, which is why the old Vercel page could never use this engine.
"""
import io, json, re, threading, subprocess, sys, contextlib, time, datetime as dt
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import os
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import acca as A
import book_dynamic as BD
import dynamic_v4 as D

# Build stamp: written by deploy.sh before `railway up` and reported in
# /api/status, so a deploy can be VERIFIED rather than assumed. During a
# rolling deploy the old container keeps answering requests, which is how a
# rule that was already committed silently failed to apply on 30 Aug.
try:
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           'BUILD_STAMP')) as _f:
        BUILD = _f.read().strip()
except OSError:
    BUILD = 'unknown'

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


def crawl_job(cap, floor_iso, mode='deep'):
    """Run a crawler as a subprocess, streaming its progress into CRAWL so the
    status endpoint shows real numbers instead of 'starting'."""
    import subprocess
    # 'recent' sweeps the last two days of finished matches with full stat
    # sheets - the daily accumulator, run here because Railway's connection
    # holds up where the local machine's DNS keeps dropping.
    script = ({'depth': 'depth_crawl.py', 'recent': 'accumulate.py'}
              .get(mode, 'deep_crawl.py'))
    args = [sys.executable, os.path.join(ROOT, 'experiments', script)]
    if mode == 'depth':
        args += [str(cap), '6']
    elif mode != 'recent':
        args += [str(cap), floor_iso]
    CRAWL.update(state='running', kept=0, seen=0, note=f'{mode} crawl starting')
    try:
        p = subprocess.Popen(args, cwd=ROOT, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, bufsize=1)
        for line in p.stdout:
            line = line.strip()
            if not line:
                continue
            CRAWL['note'] = line[-200:]
            m = re.search(r'kept (\d+)', line)
            if m:
                CRAWL['kept'] = int(m.group(1))
            m = re.search(r'(\d+)/(\d+)', line)
            if m:
                CRAWL['seen'] = int(m.group(1))
            try:
                with open(os.path.join(ROOT, 'experiments', 'dataset.jsonl')) as f:
                    CRAWL['corpus'] = sum(1 for _ in f)
            except OSError:
                pass
        p.wait()
        CRAWL['state'] = 'done'
    except Exception as e:
        CRAWL.update(state='done', note=f'{type(e).__name__}: {e}')


class _LogIO(io.TextIOBase):
    """stdout shim: every line the engine prints lands in the job log."""
    def write(self, s):
        for part in s.splitlines():
            if part.strip():
                JOB['log'].append(part.rstrip())
        return len(s)


def draw_job(until, days, dry, half=False):
    """Draw mode - the trained model in book_draw, booked as SINGLES.

    A slice that hits ~37% is a singles instrument; nine of them on one slip
    (HVGEU3, 6 Sep) needs all nine. One code per fixture, all reported."""
    JOB.update(state='building', log=[], result=None,
               params=dict(mode='draws', until=until, days=days, dry=dry),
               started=dt.datetime.now(A.WAT).strftime('%H:%M'))
    try:
        import book_draw as DRW
        DRW.HALF = bool(half)
        with contextlib.redirect_stdout(_LogIO()):
            legs = DRW.build(until_h=until, days=days)
            if not legs:
                JOB.update(state='done', result={'error':
                    'no fixture clears the draw gate'})
                return
            combo = 1.0
            for l in legs:
                combo *= l['odds']
            res = {'combo': round(combo, 1), 'pool': len(legs),
                   'est': round(DRW.MEASURED ** len(legs) * 100, 2),
                   'legs': [dict(when=l['ts'].strftime('%a %H:%M'), match=l['match'],
                                 label=l['label'], odds=l['odds'],
                                 prob=round(l['p'] * 100)) for l in legs]}
            if dry:
                res['dry'] = True
            else:
                JOB['state'] = 'booking'
                bk = A.book([l['bs'] for l in legs])          # ONE SLIP (user, 6 Sep)
                if bk and bk.get('code'):
                    res['code'] = bk['code']; res['url'] = bk['url']
                    A.log_booking(bk['code'], bk['url'],
                                  f"{'HT ' if half else ''}draw slip {combo:,.1f}x ({len(legs)} legs) until {until}:00",
                                  [(l['ts'].timestamp(), l['match'], l['label'],
                                    l['odds'], l['stats']) for l in legs])
        JOB.update(state='done', result=res)
    except Exception as e:
        JOB.update(state='done', result={'error': f'{type(e).__name__}: {e}'})


def winners_job(until, days, dry):
    """Winners slip - book_winners rule set (10 Sep v2): the market picks the
    side, venue goal difference decides whether to bet (>=1.0 home, >=1.5
    away), below 1.80 straight, 1.80-2.60 double chance, above that skip."""
    JOB.update(state='building', log=[], result=None,
               params=dict(mode='winners', until=until, days=days, dry=dry),
               started=dt.datetime.now(A.WAT).strftime('%H:%M'))
    try:
        import book_winners as BW
        with contextlib.redirect_stdout(_LogIO()):
            rows = BW.build(until_h=until, days=days)
            picks = [r for r in rows if r['pick']]
            if not picks:
                JOB.update(state='done', result={'error': 'no fixture clears the winners rule'})
                return
            picks.sort(key=lambda r: r['ts'])
            combo = 1.0
            for r in picks:
                combo *= float(r['o']['odds'])
            res = {'combo': round(combo, 1), 'pool': len(picks), 'seen': len(rows),
                   'legs': [dict(when=dt.datetime.fromtimestamp(r['ts'], tz=A.WAT).strftime('%a %H:%M'),
                                 match=f"{r['home']} v {r['away']}", label=r['label'],
                                 odds=float(r['o']['odds']), margin=round(r['margin'], 2))
                            for r in picks]}
            if dry:
                res['dry'] = True
            else:
                JOB['state'] = 'booking'
                bk = A.book([dict(eventId=r['ev']['eventId'], productId=3,
                                  marketId=('10' if r['pick'] == 'dc' else '1'),
                                  specifier='', outcomeId=r['o']['id']) for r in picks])
                if bk and bk.get('code'):
                    res['code'] = bk['code']; res['url'] = bk['url']
                    A.log_booking(bk['code'], bk['url'],
                                  f"winners slip {combo:,.0f}x ({len(picks)} legs) until {until}:00 - "
                                  f"fix v2: favourite, margin >=1.0 home / >=1.5 away, <1.80 win / 1.80-2.60 DC",
                                  [(r['ts'], f"{r['ev']['homeTeamName']} v {r['ev']['awayTeamName']}",
                                    r['label'], float(r['o']['odds']),
                                    [f"{r['home']} HOME {BW.fmt(r['hp'])} {BW.rec(r['hp'])}",
                                     f"{r['away']} AWAY {BW.fmt(r['ap'])} {BW.rec(r['ap'])}",
                                     f"1X2 {r['o1']}/{r['ox']}/{r['o2']}  favourite {r['side']} "
                                     f"venue margin {r['margin']:+.2f}"]) for r in picks])
        JOB.update(state='done', result=res)
    except Exception as e:
        JOB.update(state='done', result={'error': f'{type(e).__name__}: {e}'})


LIVE = {'state': 'idle', 'log': [], 'started': None}


def live_job(until, dry, chat=None, auto=False):
    """Live draw watcher (live_draw.py) - runs in its own thread, separate from
    JOB so a build can still run alongside it. Pushes codes to the chat that
    started it."""
    import live_ht as LD          # 12 Sep: the general half-time watcher (draw + 2H goals)
    if LIVE['state'] == 'running':
        return False
    # 12 Sep: a live code is loadable only while the market is open - minutes,
    # not hours. Without a chat the code sat in the log until someone read it
    # (UV8C8U). Default to the bot's own chat so every code is pushed at once.
    if not chat:
        try:
            import telegram_bot as _TB
            chat = _TB.LAST_CHAT
        except Exception:
            chat = None
    LIVE.update(state='running', log=[], started=dt.datetime.now(A.WAT).strftime('%H:%M'), chat=bool(chat), wanted=True)
    def log(msg):
        for part in str(msg).splitlines():
            if part.strip():
                LIVE['log'].append(part.rstrip())
        LIVE['log'][:] = LIVE['log'][-400:]
    def send(text):
        if chat:
            try:
                import telegram_bot as TB
                TB.send(chat, text)
            except Exception:
                pass
    def worker():
        try:
            LD.LOG = log; LD.SEND = send
            LD.run(until_h=until or None, dry=dry, greet=not auto)
        except Exception as e:
            import traceback
            log(f"live watcher crashed: {type(e).__name__}: {e}")
            print(f"live watcher crashed: {type(e).__name__}: {e}\n{traceback.format_exc()}", flush=True)
            LIVE.setdefault('crashes', []).append(time.time())
        finally:
            LIVE['state'] = 'idle'
    threading.Thread(target=worker, daemon=True).start()
    return True


def script_job(mode, args, label):
    """Run a booking script as a subprocess and stream its output into the job
    log - the same run the operator gets locally (book_winners.py includes the
    yesterday check, --top and the wider sheet; book_amfoot.py the four points
    sports). The share code is read off the script's own 'code XXXXXX' line."""
    JOB.update(state='building', log=[], result=None, params=dict(mode=mode, args=args),
               started=dt.datetime.now(A.WAT).strftime('%H:%M'))
    try:
        p = subprocess.Popen([sys.executable, '-u'] + args, cwd=ROOT, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True)
        code = url = None
        for line in p.stdout:
            line = line.rstrip()
            if line:
                JOB['log'].append(line); JOB['log'][:] = JOB['log'][-600:]
            m = re.search(r'\bcode ([A-Z0-9]{6})\b\s+(http\S+)?', line)
            if m and 'shareCode' in (m.group(2) or ''):
                code, url = m.group(1), m.group(2)
        p.wait(timeout=3600)
        res = dict(mode=mode, label=label)
        if code:
            res.update(code=code, url=url)
        elif '--dry' in args:
            res['dry'] = True
        else:
            res['error'] = 'no code booked - see the log'
        JOB.update(state='done', result=res)
    except Exception as e:
        JOB.update(state='done', result={'error': f'{type(e).__name__}: {e}'})


def live_stop():
    try:
        import live_ht as LD
        LD.STOP['flag'] = True
        LIVE['wanted'] = False            # 13 Sep: the scheduler leaves it off until /live
        return LIVE['state'] == 'running'
    except Exception:
        return False


def scheduler():
    """13 Sep: the codes are generated every day. Every minute: run any job on
    betty_api.SCHEDULE whose time has passed and that has not run today (one at
    a time, through the same JOB lock as the buttons), build the odds ladder
    from each new code, and keep the live watcher running unless /stop asked
    for it to be off. State lives next to bookings.md on the volume, so a
    redeploy never repeats a run."""
    import betty_api as BA
    time.sleep(20)
    while True:
        try:
            if LIVE.get('wanted', True) and LIVE['state'] != 'running':
                # 17 Sep: a watcher that dies is restarted at most every five minutes, and
                # after three crashes in half an hour it stays down until /live - the
                # restart-every-minute loop re-read and re-sent the same half-times
                recent = [t for t in LIVE.get('crashes', []) if time.time() - t < 1800]
                last = LIVE.get('last_auto', 0)
                if len(recent) >= 3:
                    if not LIVE.get('gave_up'):
                        LIVE['gave_up'] = True
                        print('scheduler: live watcher crashed three times in 30 min - not restarting until /live', flush=True)
                        try:
                            import telegram_bot as _TB
                            _TB.send(_TB.LAST_CHAT, 'live watcher crashed three times in 30 min - left off; /live restarts it (the crash is in the server log)')
                        except Exception:
                            pass
                elif time.time() - last >= 300:
                    LIVE['last_auto'] = time.time(); LIVE['gave_up'] = False
                    live_job(0, False, auto=True)
            for hhmm, job, path, body in BA.sched_due():
                with LOCK:
                    if JOB['state'] not in ('idle', 'done'):
                        break
                    JOB['state'] = 'building'
                try:
                    if path == '/api/winners':
                        args = ['book_winners.py', '--until', str(body['until']), '--days', str(body.get('days', 0))]
                        script_job('winners', args, 'winners slip (scheduled)')
                    elif path == '/api/points':
                        args = ['book_amfoot.py', '--sport', body['sport'], '--days', str(body.get('days', 0))]
                        script_job('points', args, f"{body['sport']} slip (scheduled)")
                    elif path == '/api/draws':
                        draw_job(body['until'], body.get('days', 0), False, bool(body.get('half')))
                    elif path == '/api/drawsmkt':
                        script_job('draws', ['book_draw_market.py'], 'draw slip (market band)')
                    elif path == '/api/run':
                        run_job(float(body.get('target', 50)), int(body['until']), int(body.get('days', 0)), False,
                                bool(body.get('rollover')), 'composite', bool(body.get('maxodds')),
                                bool(body.get('goalsonly')), bool(body.get('undersonly')), bool(body.get('strict')))
                    elif path == '/api/snapshot':
                        import importlib
                        JOB.update(state='building', log=[], result=None, params=dict(mode='odds snapshot'), started=dt.datetime.now(A.WAT).strftime('%H:%M'))
                        snap = importlib.import_module('experiments.odds_snapshot') if os.path.exists(os.path.join(ROOT, 'experiments', '__init__.py')) else None
                        if snap is None:
                            sys.path.insert(0, os.path.join(ROOT, 'experiments')); import odds_snapshot as snap
                        r = snap.snapshot(); JOB.update(state='done', result=r)
                    elif path == '/api/calib':
                        sys.path.insert(0, os.path.join(ROOT, 'experiments')); import sporty_calibration as CAL
                        JOB.update(state='building', log=[], result=None, params=dict(mode='sporty calibration'), started=dt.datetime.now(A.WAT).strftime('%H:%M'))
                        with contextlib.redirect_stdout(_LogIO()):
                            CAL.main()
                        JOB.update(state='done', result=dict(note='see log'))
                    elif path == '/api/combined':
                        JOB.update(state='building', log=[], result=None, params=dict(mode='all games', slot=body.get('slot')),
                                   started=dt.datetime.now(A.WAT).strftime('%H:%M'))
                        r = BA.combined_code(body.get('slot', 'am'))
                        JOB['log'].append(json.dumps(r)); JOB.update(state='done', result=r)
                except Exception as e:
                    # 16 Sep: a crashed job (calibration before its first snapshot) left the
                    # flag on 'building' and every later run of the day was marked missed.
                    JOB.update(state='done', result=dict(error=f'{type(e).__name__}: {e}'))
                    print(f'scheduler {job}: {type(e).__name__}: {e}', flush=True)
                res = JOB.get('result') or {}
                code = res.get('code')
                if code:
                    try:
                        BA.build_ladder(code)
                    except Exception as e:
                        res['ladder_error'] = f"{type(e).__name__}: {e}"
                BA.sched_mark(job, code or res.get('error') or 'no code')
                JOB['state'] = 'done'
        except Exception as e:
            print(f'scheduler: {type(e).__name__}: {e}', flush=True)
        time.sleep(60)


def run_job(target, until, days, dry, rollover=False, engine='composite',
            maxodds=False, goalsonly=False, undersonly=False, strict=False):
    JOB.update(state='building', log=[], result=None,
               params=dict(target=target, until=until, days=days, dry=dry,
                           rollover=rollover, engine=engine),
               started=dt.datetime.now(A.WAT).strftime('%H:%M'))
    try:
        with contextlib.redirect_stdout(_LogIO()):
            if engine == 'hybrid':
                # book_hybrid swaps D.model_prob for the trained bundle on
                # import; everything downstream (rulebook, gates, selection)
                # is the same code the composite runs.
                try:
                    import book_hybrid  # noqa: F401
                except Exception as e:
                    JOB.update(state='done',
                               result={'error': f'hybrid models unavailable: {e}'})
                    return
            D.set_strict(strict)          # module flag - reset in finally below
            try:
                board = BD.build(until_h=until, days=days)
            finally:
                D.set_strict(False)
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
            if goalsonly:
                pool = BD.goals_only(pool)
                JOB['log'].append(f'goals-only: {len(pool)} legs after dropping stat markets')
            if undersonly:
                pool = BD.unders_only(pool)
                JOB['log'].append(f'unders-only: {len(pool)} legs after dropping every Over')
            if maxodds:
                legs, combo, surv = BD.pick_max_odds(pool)
            elif rollover:
                legs, combo, surv = BD.pick_rollover(pool)
                if not legs:
                    JOB.update(state='done', result={'error':
                        f'no rollover today: even the smallest slip only lands '
                        f'{surv:.0%} - the board does not clear the 30% floor'})
                    return
            else:
                legs, combo, surv = BD.pick_for_target(pool, target,
                                                       floor_sweep=True)
            if not legs:
                JOB.update(state='done', result={'error': 'no bookable legs on this board'})
                return
            warn = None
            if not rollover and not maxodds and combo < target:
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
                                  (f"{engine} strict max-odds {combo:,.1f}x" if strict and maxodds
                                   else f"{engine} unders-only {combo:,.1f}x" if undersonly and maxodds
                                   else f"{engine} max-odds {combo:,.1f}x" if maxodds
                                   else f"{engine} rollover {combo:.2f}x est {surv:.0%}" if rollover
                                   else f"{engine} target {target:g}x until {until}:00"
                                        + (f" +{days}d" if days else "")),
                                  [(l['ts'].timestamp(), l['match'], l['label'],
                                    l['odds'], l['stats']) for l in legs])
                else:
                    res['error'] = f"booking failed: {bk.get('msg') if bk else 'no selections'}"
            JOB.update(state='done', result=res)
    except Exception as e:
        JOB.update(state='done', result={'error': f'{type(e).__name__}: {e}'})


def _page():
    """The operator page (build buttons, rollover, grade box) - public/index.html,
    re-read per request so design changes land on refresh. Served at /ops
    since 13 Sep; the Betty site (web/index.html) took over /."""
    try:
        with open(os.path.join(ROOT, 'public', 'index.html'), encoding='utf-8') as f:
            return f.read()
    except OSError:
        return '<h1>public/index.html missing</h1>'


def _site(name='index.html'):
    """The Betty website - web/index.html, the Claude Design page implemented
    over /api/codes, /api/record, /api/rules and /api/livefeed."""
    try:
        with open(os.path.join(ROOT, 'web', name), encoding='utf-8') as f:
            return f.read()
    except OSError:
        return '<h1>web/index.html missing</h1>'



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
        q = parse_qs(u.query)
        if u.path == '/':
            self._send(_site(), 'text/html; charset=utf-8')
        elif u.path == '/ops':
            # 13 Sep: the old operator page is behind the same key as the Console
            if not self._authed({'key': q.get('key', [''])[0]}):
                self._send('operator key required (?key=...)', 'text/plain', 401); return
            self._send(_page(), 'text/html; charset=utf-8')
        elif u.path in ('/api/codes', '/api/record', '/api/rules', '/api/livefeed', '/api/banner', '/api/console', '/api/gradecode', '/api/legs', '/api/ladder'):
            # 13 Sep: the website's data. Everything comes from bookings.md (repo
            # copy + the Railway volume), the share API grader and the watcher.
            import betty_api as BA
            try:
                if u.path == '/api/codes':
                    body = BA.codes_for(q.get('day', ['today'])[0])
                elif u.path == '/api/record':
                    body = BA.record(int(q.get('days', ['35'])[0]))
                    body['corpus'] = BA.CORPUS_TILES
                elif u.path == '/api/rules':
                    body = dict(rules=BA.RULES, changelog=BA.CHANGELOG)
                elif u.path == '/api/banner':
                    body = BA.banner()
                elif u.path == '/api/console':
                    if not self._authed({'key': q.get('key', [''])[0]}):
                        self._send(json.dumps({'error': 'operator key required'}), code=401); return
                    body = BA.console_state(LIVE, JOB, BUILD)
                    body['schedule'] = BA.sched_view()
                elif u.path == '/api/gradecode':
                    body = BA.grade_any(q.get('code', [''])[0])
                elif u.path == '/api/legs':
                    body = BA.legs_list(int(q.get('days', ['35'])[0]))
                elif u.path == '/api/ladder':
                    body = BA.ladder_for(q.get('day', ['today'])[0], build=q.get('build', ['1'])[0] != '0')
                else:
                    body = BA.live_feed(LIVE)
                body['now'] = dt.datetime.now(A.WAT).strftime('%H:%M:%S')
                self._send(json.dumps(body, default=str))
            except Exception as e:
                self._send(json.dumps({'error': f"{type(e).__name__}: {e}"}), code=500)
        elif u.path == '/api/live':
            self._send(json.dumps(LIVE)); return
        elif u.path == '/api/status':
            self._send(json.dumps({'state': JOB['state'], 'log': JOB['log'][-40:],
                                   'result': JOB['result'], 'started': JOB['started'],
                                   'build': BUILD}))
        elif u.path == '/api/slips':
            # Every booking is appended to BOOKINGS_PATH (a Railway volume, so
            # it survives restarts and redeploys). Telegram cannot hand back
            # messages the bot already sent, so this is the durable record.
            import re as _re
            n = 12
            try:
                n = int(parse_qs(u.query).get('n', ['12'])[0])
            except ValueError:
                pass
            path = os.environ.get('BOOKINGS_PATH') or os.path.join(ROOT, 'bookings.md')
            out = []
            try:
                with open(path, encoding='utf-8') as f:
                    txt = f.read()
                for m in _re.finditer(r'^## (\S+ \S+) WAT\s+\|\s+(.+?)\s+\|\s+code (\w+)$',
                                      txt, _re.M):
                    out.append({'when': m.group(1), 'label': m.group(2),
                                'code': m.group(3)})
            except OSError as e:
                self._send(json.dumps({'error': str(e)}), code=500); return
            self._send(json.dumps({'path': path, 'total': len(out),
                                   'slips': out[-n:]}))
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

    def _authed(self, p):
        """13 Sep: optional guard for the operator endpoints. With OPS_KEY set on
        Railway, a POST needs the key (X-Ops-Key header or "key" in the body);
        unset, everything stays open as before."""
        want = os.environ.get('OPS_KEY', '').strip()
        return (not want) or self.headers.get('X-Ops-Key', '') == want or str(p.get('key', '')) == want

    def do_POST(self):
        path = urlparse(self.path).path
        if path in ('/api/yesterday', '/api/points', '/api/winners', '/api/draws', '/api/drawsmkt', '/api/run', '/api/live', '/api/crawl', '/api/combined'):
            n = int(self.headers.get('Content-Length', 0))
            raw = self.rfile.read(n) if n else b''
            try:
                p0 = json.loads(raw or b'{}')
            except Exception:
                self._send(json.dumps({'error': 'bad json'}), code=400); return
            if not self._authed(p0):
                self._send(json.dumps({'error': 'operator key required'}), code=401); return
            self._body = p0
        if path == '/api/nest':
            # pick your odds: a subset of an existing code at a target multiplier.
            # Public, like the codes themselves; one booking per (code, target).
            import betty_api as BA
            n = int(self.headers.get('Content-Length', 0))
            try:
                p3 = json.loads(self.rfile.read(n) or b'{}')
            except Exception:
                self._send(json.dumps({'error': 'bad json'}), code=400); return
            try:
                self._send(json.dumps(BA.nest_code(p3.get('code'), p3.get('target')), default=str))
            except Exception as e:
                self._send(json.dumps({'error': f"{type(e).__name__}: {e}"}), code=500)
            return
        if path == '/api/yesterday':
            import betty_api as BA
            self._send(json.dumps({'ok': BA.run_yesterday(), 'state': BA.YEST['state']})); return
        if path == '/api/drawsmkt':
            with LOCK:
                if JOB['state'] not in ('idle', 'done'):
                    self._send(json.dumps({'error': 'a run is already in progress'}), code=409); return
                JOB['state'] = 'building'
            args = ['book_draw_market.py'] + (['--dry'] if self._body.get('dry') else [])
            if re.fullmatch(r'[A-Z0-9]{6}', str(self._body.get('replaces') or '')):
                args += ['--replaces', self._body['replaces']]
            if str(self._body.get('until') or '').isdigit():
                args += ['--until', str(int(self._body['until']))]
            if self._body.get('only'):
                args += ['--only', re.sub(r'[^A-Za-z0-9 ,.\'-]', '', str(self._body['only']))[:300]]
            if str(self._body.get('days') or '').isdigit():
                args += ['--days', str(min(int(self._body['days']), 7))]
            threading.Thread(target=script_job, args=('draws', args, 'draw slip (market band)'), daemon=True).start()
            self._send(json.dumps({'ok': True})); return
        if path == '/api/combined':
            import betty_api as BA
            try:
                r = BA.combined_code(str(self._body.get('slot', 'am')))
                if r.get('code'):
                    try:
                        BA.build_ladder(r['code'])
                    except Exception:
                        pass
                self._send(json.dumps(r, default=str))
            except Exception as e:
                self._send(json.dumps({'error': f"{type(e).__name__}: {e}"}), code=500)
            return
        if path == '/api/points':
            p2 = self._body
            sport = str(p2.get('sport', 'amfoot'))
            if sport not in ('amfoot', 'basketball', 'hockey', 'handball'):
                self._send(json.dumps({'error': 'sport must be amfoot | basketball | hockey | handball'}), code=400); return
            try:
                days = int(p2.get('days', 0)); assert 0 <= days <= 7
                args = ['book_amfoot.py', '--sport', sport, '--days', str(days)]
                if p2.get('min_price'): args += ['--min-price', str(float(p2['min_price']))]
                if p2.get('agree'): args += ['--agree', str(int(p2['agree']))]
                if p2.get('dry'): args.append('--dry')
            except Exception:
                self._send(json.dumps({'error': 'bad parameters'}), code=400); return
            with LOCK:
                if JOB['state'] not in ('idle', 'done'):
                    self._send(json.dumps({'error': 'a run is already in progress'}), code=409); return
                JOB['state'] = 'building'
            threading.Thread(target=script_job, args=('points', args, f'{sport} slip'), daemon=True).start()
            self._send(json.dumps({'ok': True})); return
        if path == '/api/crawl':
            try:
                p = self._body
                cap = int(p.get('cap', 6000)); floor = str(p.get('floor', '2026-06-01'))
                mode = str(p.get('mode', 'deep'))
            except Exception:
                self._send(json.dumps({'error': 'bad parameters'}), code=400); return
            if CRAWL['state'] == 'running':
                self._send(json.dumps({'error': 'crawl already running'}), code=409); return
            threading.Thread(target=crawl_job, args=(cap, floor, mode),
                             daemon=True).start()
            self._send(json.dumps({'ok': True})); return
        if path == '/api/live':
            try:
                p2 = self._body
                until = int(p2.get('until', 0)); dry = bool(p2.get('dry'))
                assert 0 <= until <= 23
            except Exception:
                self._send(json.dumps({'error': 'bad parameters'}), code=400); return
            if p2.get('stop'):
                self._send(json.dumps({'ok': live_stop()})); return
            ok = live_job(until, dry, chat=p2.get('chat'))
            self._send(json.dumps({'ok': ok, 'error': None if ok else 'live watcher already running'})); return
        if path == '/api/winners':
            try:
                p2 = self._body
                until = int(p2.get('until', 23)); days = int(p2.get('days', 0))
                dry = bool(p2.get('dry')); top = int(p2.get('top', 0) or 0)
                assert 0 <= until <= 23 and 0 <= days <= 4 and 0 <= top <= A.MAX_CODE
            except Exception:
                self._send(json.dumps({'error': 'bad parameters'}), code=400); return
            with LOCK:
                if JOB['state'] not in ('idle', 'done'):
                    self._send(json.dumps({'error': 'a run is already in progress'}), code=409)
                    return
                JOB['state'] = 'building'
            # 13 Sep: the script itself (yesterday check, --top, the wider sheet in
            # bookings.md) instead of the older in-process winners_job.
            args = ['book_winners.py', '--until', str(until), '--days', str(days)]
            if top: args += ['--top', str(top)]
            if dry: args.append('--dry')
            threading.Thread(target=script_job, args=('winners', args, 'winners slip'), daemon=True).start()
            self._send(json.dumps({'ok': True})); return
        if path == '/api/draws':
            try:
                p2 = self._body
                until = int(p2.get('until', 23)); days = int(p2.get('days', 0))
                dry = bool(p2.get('dry')); half = bool(p2.get('half'))
                assert 0 <= until <= 23 and 0 <= days <= 4
            except Exception:
                self._send(json.dumps({'error': 'bad parameters'}), code=400); return
            with LOCK:
                if JOB['state'] not in ('idle', 'done'):
                    self._send(json.dumps({'error': 'a run is already in progress'}), code=409)
                    return
                JOB['state'] = 'building'
            threading.Thread(target=draw_job, args=(until, days, dry, half), daemon=True).start()
            self._send(json.dumps({'ok': True})); return
        if path != '/api/run':
            self._send('not found', 'text/plain', 404); return
        try:
            p = self._body
            target = float(p.get('target', 50))
            until = int(p.get('until', 23))
            days = int(p.get('days', 0))
            dry = bool(p.get('dry'))
            rollover = bool(p.get('rollover'))
            maxodds = bool(p.get('maxodds'))
            goalsonly = bool(p.get('goalsonly'))
            undersonly = bool(p.get('undersonly'))
            strict = bool(p.get('strict'))
            engine = 'hybrid' if str(p.get('engine')) == 'hybrid' else 'composite'
            assert 2 <= target <= 100000 and 0 <= until <= 23 and 0 <= days <= 4
        except Exception:
            self._send(json.dumps({'error': 'bad parameters'}), code=400); return
        with LOCK:
            if JOB['state'] not in ('idle', 'done'):
                self._send(json.dumps({'error': 'a run is already in progress'}), code=409)
                return
            JOB['state'] = 'building'
        threading.Thread(target=run_job,
                         args=(target, until, days, dry, rollover, engine, maxodds,
                               goalsonly, undersonly, strict),
                         daemon=True).start()
        self._send(json.dumps({'ok': True}))


def _tg_grade(codes):
    """Grade codes for the bot, returning the text rather than printing it."""
    try:
        p = subprocess.run([sys.executable, 'grade_code.py'] + list(codes),
                           capture_output=True, text=True, timeout=900, cwd=ROOT)
        out = (p.stdout or '') + (p.stderr or '')
        keep = [l for l in out.splitlines()
                if l.startswith('====') or 'per-leg' in l or ' LOSE ' in l]
        return '\n'.join(keep or out.splitlines()[-30:])
    except Exception as e:
        return f'grade failed: {type(e).__name__}: {e}'


def _tg_sweep():
    """Bank the last two days of finished matches, for the bot."""
    try:
        p = subprocess.run([sys.executable,
                            os.path.join(ROOT, 'experiments', 'accumulate.py')],
                           capture_output=True, text=True, timeout=3600, cwd=ROOT)
        return ((p.stdout or '') + (p.stderr or '')).strip()[-1200:]
    except Exception as e:
        return f'sweep failed: {type(e).__name__}: {e}'


try:
    import telegram_bot
    telegram_bot.start(JOB, LOCK, run_job, _tg_grade, _tg_sweep)
except Exception as _e:
    print(f'telegram: not started ({_e})')


if __name__ == '__main__':
    print(f"dynamic booker UI  ->  http://localhost:{PORT}")
    try:
        import betty_api as _BA
        _BA.start_warmer()           # 13 Sep: the website's grade cache, refreshed every 3 min
        threading.Thread(target=scheduler, daemon=True).start()   # daily codes + watcher keep-alive
    except Exception as _e:
        print(f'betty_api: warmer/scheduler not started ({_e})')
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
