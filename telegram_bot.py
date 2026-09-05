#!/usr/bin/env python3
"""Telegram front end for the booking engine.

Runs as a daemon thread inside ui.py, so it shares the same job machinery the
web page uses - there is one engine, two ways to reach it. Long-polls
getUpdates (no webhook, no inbound port needed) and pushes results back when a
build finishes, which is the point: start a board, close the app, get the code
when it lands.

Environment:
    TELEGRAM_TOKEN   from @BotFather
    TELEGRAM_CHAT    optional - restrict the bot to one chat id

Commands:
    /rollover              today's rollover ticket (biggest slip landing 30%+)
    /book <target> [until] target payout, e.g. /book 100 23
    /max [until]           highest odds inside the 50-leg cap
    /goals [until]         same, goal markets only
    /grade <codes...>      grade share codes
    /status                what the engine is doing now
    /sweep                 bank yesterday's results into the corpus
    /help
"""
import json, os, threading, time, urllib.parse, urllib.request

API = 'https://api.telegram.org/bot'
HELP = (
    "Two engines. Plain commands run the COMPOSITE (the shipped Poisson\n"
    "engine); the ai- prefix runs the HYBRID (28 nets on goals, 12 XGBoost\n"
    "models on stats). Same rulebook and selection either way.\n\n"
    "/rollover [until]      daily ticket, biggest slip landing 30%+\n"
    "/airollover [until]    same, hybrid engine\n"
    "/book TARGET [until] [days]    e.g. /book 100 23\n"
    "/aibook TARGET [until] [days]\n"
    "/max [until]           highest odds inside the 50-leg cap\n"
    "/aimax [until]\n"
    "/goals [until]         highest odds, goal markets only\n"
    "/aigoals [until]\n"
    "/under [until]         highest odds, Under selections only\n"
    "/aiunder [until]\n\n"
    "/grade CODE ...        grade share codes\n"
    "/sweep                 bank yesterday's results\n"
    "/slips [n]             recent booked codes\n"
    "/status                what the engine is doing\n"
    "/whoami                your chat id"
)
TOKEN = os.environ.get('TELEGRAM_TOKEN', '').strip()
ONLY_CHAT = os.environ.get('TELEGRAM_CHAT', '').strip()


def _call(method, **params):
    if not TOKEN:
        return None
    url = f'{API}{TOKEN}/{method}'
    data = urllib.parse.urlencode(
        {k: (json.dumps(v) if isinstance(v, (dict, list)) else v)
         for k, v in params.items() if v is not None}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=70) as r:
            return json.load(r)
    except Exception:
        return None


def send(chat, text):
    """Send, splitting at Telegram's 4096-char cap.

    Falls back to plain text when HTML parsing fails - the help text contains
    <target>, which Telegram reads as a malformed tag and rejects, silently
    swallowing the whole reply. Any dynamic content (team names, market labels)
    can do the same, so never let formatting cost a message."""
    for i in range(0, len(text), 3500):
        chunk = text[i:i + 3500]
        r = _call('sendMessage', chat_id=chat, text=chunk,
                  parse_mode='HTML', disable_web_page_preview='true')
        if not (r and r.get('ok')):
            plain = chunk.replace('<b>', '').replace('</b>', '')
            _call('sendMessage', chat_id=chat, text=plain,
                  disable_web_page_preview='true')


def fmt_result(res, header=''):
    if not res:
        return 'no result'
    if res.get('error'):
        return f"{header}\n<b>error:</b> {res['error']}"
    out = [header] if header else []
    if res.get('warn'):
        out.append(f"! {res['warn']}")
    out.append(f"<b>{res.get('combo', 0):,.1f}x</b> from {len(res.get('legs', []))} legs"
               f"   est {res.get('est', 0)}%   pool {res.get('pool', 0)}")
    if res.get('code'):
        out.append(f"<b>{res['code']}</b>  {res.get('url', '')}")
    if res.get('short'):
        out.append(f"! {res['short']}")
    for l in res.get('legs', []):
        out.append(f"{l['when']}  {l['match'][:26]}  {l['label'][:34]}  @{l['odds']}")
    return '\n'.join(out)


def start(job, lock, run_job, grade_fn, crawl_fn):
    """Launch the polling loop. Called from ui.py with its own primitives."""
    if not TOKEN:
        print('telegram: no TELEGRAM_TOKEN, bot disabled')
        return
    threading.Thread(target=_loop, args=(job, lock, run_job, grade_fn, crawl_fn),
                     daemon=True).start()
    print('telegram: bot polling started')


def _dispatch_run(chat, job, lock, run_job, label, **kw):
    with lock:
        if job['state'] not in ('idle', 'done'):
            send(chat, 'a build is already running - /status to watch it')
            return
        job['state'] = 'building'
    send(chat, f'building {label} ... I will message you when it lands')

    def work():
        try:
            run_job(**kw)
        except Exception as e:
            send(chat, f'build failed: {type(e).__name__}: {e}')
            return
        send(chat, fmt_result(job.get('result'), f'<b>{label}</b>'))
    threading.Thread(target=work, daemon=True).start()


def _loop(job, lock, run_job, grade_fn, crawl_fn):
    offset = None
    while True:
        r = _call('getUpdates', offset=offset, timeout=60)
        if not r or not r.get('ok'):
            time.sleep(5)
            continue
        for upd in r.get('result', []):
            offset = upd['update_id'] + 1
            msg = upd.get('message') or upd.get('channel_post') or {}
            text = (msg.get('text') or '').strip()
            chat = str((msg.get('chat') or {}).get('id', ''))
            if not text or not chat:
                continue
            if ONLY_CHAT and chat != ONLY_CHAT:
                continue
            parts = text.split()
            cmd = parts[0].lower().split('@')[0]
            args = parts[1:]
            print(f'telegram: {chat} -> {text[:60]}', flush=True)
            try:
                _handle(cmd, args, chat, job, lock, run_job, grade_fn, crawl_fn)
            except Exception as e:
                print(f'telegram: handler error {type(e).__name__}: {e}', flush=True)
                send(chat, f'{type(e).__name__}: {e}')


def _handle(cmd, args, chat, job, lock, run_job, grade_fn, crawl_fn):
    def num(i, default):
        try:
            return type(default)(args[i])
        except (IndexError, ValueError):
            return default

    if cmd in ('/start', '/help'):
        send(chat, HELP)

    elif cmd == '/slips':
        try:
            import os as _os, re as _re
            path = _os.environ.get('BOOKINGS_PATH') or 'bookings.md'
            txt = open(path, encoding='utf-8').read()
            rows = _re.findall(r'^## (\S+ \S+) WAT\s+\|\s+(.+?)\s+\|\s+code (\w+)$', txt, _re.M)
            k = 10
            try:
                k = int(args[0])
            except (IndexError, ValueError):
                pass
            if not rows:
                send(chat, 'no slips recorded yet')
            else:
                send(chat, 'recent slips:\n' + '\n'.join(
                    f'{w}  {c}  {l[:40]}' for w, l, c in rows[-k:]))
        except Exception as e:
            send(chat, f'could not read slips: {e}')

    elif cmd == '/whoami':
        send(chat, f'chat id: {chat}')

    elif cmd == '/status':
        s = job.get('state', 'idle')
        log = '\n'.join(job.get('log', [])[-6:]) or '(nothing yet)'
        send(chat, f'<b>{s}</b>\n{log}')
        if s == 'done' and job.get('result'):
            send(chat, fmt_result(job['result'], '<b>last result</b>'))

    elif cmd in ('/rollover', '/airollover'):
        eng = 'hybrid' if cmd.startswith('/ai') else 'composite'
        _dispatch_run(chat, job, lock, run_job, f'{eng} rollover',
                      target=6, until=num(0, 23), days=0, dry=False,
                      engine=eng, rollover=True)

    elif cmd in ('/book', '/aibook'):
        eng = 'hybrid' if cmd.startswith('/ai') else 'composite'
        t = num(0, 100.0)
        _dispatch_run(chat, job, lock, run_job, f'{eng} target {t:g}x',
                      target=t, until=num(1, 23), days=num(2, 0), dry=False,
                      engine=eng)

    elif cmd in ('/max', '/aimax'):
        eng = 'hybrid' if cmd.startswith('/ai') else 'composite'
        _dispatch_run(chat, job, lock, run_job, f'{eng} max odds',
                      target=6, until=num(0, 23), days=0, dry=False,
                      engine=eng, maxodds=True)

    elif cmd in ('/goals', '/aigoals'):
        eng = 'hybrid' if cmd.startswith('/ai') else 'composite'
        _dispatch_run(chat, job, lock, run_job, f'{eng} goals only',
                      target=6, until=num(0, 23), days=0, dry=False,
                      engine=eng, maxodds=True, goalsonly=True)

    elif cmd in ('/under', '/aiunder', '/unders', '/aiunders'):
        eng = 'hybrid' if cmd.startswith('/ai') else 'composite'
        _dispatch_run(chat, job, lock, run_job, f'{eng} unders only',
                      target=6, until=num(0, 23), days=0, dry=False,
                      engine=eng, maxodds=True, undersonly=True)

    elif cmd == '/grade':
        if not args:
            send(chat, 'usage: /grade CODE [CODE ...]')
            return
        send(chat, f'grading {len(args)} code(s) ...')
        threading.Thread(target=lambda: send(chat, grade_fn(args[:8]) or 'no output'),
                         daemon=True).start()

    elif cmd == '/sweep':
        send(chat, 'sweeping yesterday\'s results into the corpus ...')
        threading.Thread(target=lambda: send(chat, crawl_fn() or 'sweep done'),
                         daemon=True).start()

    else:
        send(chat, 'unknown command - /help')
