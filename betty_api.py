#!/usr/bin/env python3
"""Betty web API - the data behind web/index.html.

Everything the site shows comes from three places that already exist:
  bookings.md (the repo copy + the Railway volume copy) -> every code, its legs
      and the stat lines printed at booking time (the "sheet")
  the SportyBet share API (grade_code.grade_struct)     -> leg-by-leg grading
  ui.LIVE / live_ht.BOARD                                -> the live watcher

No new state. Grades are cached: settled codes for good, open codes for 60s.
"""
import os, re, ast, json, time, datetime as dt, collections, threading
import acca as A
import grade_code as G

ROOT = os.path.dirname(os.path.abspath(__file__))
WAT = A.WAT
_lock = threading.Lock()
_grade_cache = {}          # code -> (ts, result)
_book_cache = {'sig': None, 'val': []}

PRODUCTS = ['Live', 'Winners', 'Draws', 'American Football', 'Basketball', 'Ice Hockey', 'Handball', 'Max']


# ---------------------------------------------------------------- bookings

def bookings_paths():
    out = [os.path.join(ROOT, 'bookings.md')]
    vol = os.environ.get('BOOKINGS_PATH')
    if vol and vol not in out:
        out.append(vol)
    return [p for p in out if os.path.exists(p)]


def product_of(label):
    l = label.lower()
    if l.startswith('live '):
        return 'Live'
    if 'american football' in l or 'nfl' in l or 'ncaa' in l:
        return 'American Football'
    if 'basketball' in l:
        return 'Basketball'
    if 'hockey' in l:
        return 'Ice Hockey'
    if 'handball' in l:
        return 'Handball'
    if 'winner' in l:                 # before 'draw': "3+ venue draws -> double chance" is a winners slip
        return 'Winners'
    if 'draw' in l:
        return 'Draws'
    return 'Max'


_HEAD = re.compile(r'^## (\d{4}-\d\d-\d\d \d\d:\d\d) WAT\s+\|\s+(.+?)\s+\|\s+code (\w{6})\s*$', re.M)
_LEG = re.compile(r'^- (\w{3}) (\d\d:\d\d)  (.+?)  -  (.+?) @([\d.]+)\s*$')


def parse_bookings():
    """[{code, when, label, product, url, legs:[{day,ko,match,sel,note,price,stats:[...]}]}]
    newest first, one entry per code (a code logged twice keeps its first record).
    Cached on the files' (size, mtime)."""
    paths = bookings_paths()
    sig = tuple((p, os.path.getsize(p), int(os.path.getmtime(p))) for p in paths)
    if sig == _book_cache['sig']:
        return _book_cache['val']
    seen = {}
    for path in paths:
        try:
            txt = open(path, encoding='utf-8').read()
        except OSError:
            continue
        heads = list(_HEAD.finditer(txt))
        for i, m in enumerate(heads):
            code = m.group(3)
            if code in seen:
                continue
            block = txt[m.end():heads[i + 1].start() if i + 1 < len(heads) else len(txt)]
            legs, cur, url = [], None, None
            for line in block.split('\n'):
                if line.startswith('http'):
                    url = line.strip(); continue
                lm = _LEG.match(line)
                if lm:
                    sel, note = lm.group(4), ''
                    if '  [' in sel:
                        sel, note = sel.split('  [', 1); note = note.rstrip(']')
                    cur = dict(day=lm.group(1), ko=lm.group(2), match=lm.group(3), sel=sel.strip(), note=note,
                               price=float(lm.group(5)), stats=[])
                    legs.append(cur); continue
                if cur is not None and line.startswith('    ') and line.strip():
                    cur['stats'].append(line.strip())
            seen[code] = dict(code=code, when=m.group(1), label=m.group(2), product=product_of(m.group(2)),
                              url=url or f'http://www.sportybet.com/ng/?shareCode={code}', legs=legs)
    val = sorted(seen.values(), key=lambda c: c['when'], reverse=True)
    _book_cache.update(sig=sig, val=val)
    return val


# ---------------------------------------------------------------- grading

def graded(code, force=False):
    """Cached share-API grade. Settled codes are kept for good; open ones
    refresh after 60s; a code the API no longer returns (share codes expire
    after about two weeks) is retried every 30 minutes."""
    now = time.time()
    with _lock:
        hit = _grade_cache.get(code)
    if hit and not force:
        ts, res = hit
        legs = res.get('legs') or []
        settled = legs and all(l['state'] in ('won', 'lost', 'void') for l in legs)
        ttl = 1800 if not legs else 60
        if settled or now - ts < ttl:
            return res
    try:
        res = G.grade_struct(code)
    except Exception as e:
        res = dict(error=f"{type(e).__name__}: {str(e)[:80]}", legs=[])
    with _lock:
        _grade_cache[code] = (now, res)
    return res


def warm(days=35):
    """Grade every code of the last N days (newest first) so the pages answer
    from cache. Runs in the background from ui.py; a full cold pass over two
    weeks of codes takes about a minute."""
    since = dt.datetime.now(tz=WAT).date() - dt.timedelta(days=days)
    for c in parse_bookings():
        if _day_of(c['when']) < since:
            break
        if c['legs']:
            try:
                graded(c['code'])
            except Exception:
                pass


def start_warmer(every=180):
    def loop():
        while True:
            try:
                warm()
            except Exception:
                pass
            time.sleep(every)
    threading.Thread(target=loop, daemon=True).start()


def _key(name):
    return re.sub(r'[^a-z]', '', (name or '').lower())


def merge_grades(legs, gl):
    """Attach the graded row to each booked leg: by home-team match first, by order second."""
    used = set()
    for i, leg in enumerate(legs):
        home = _key(leg['match'].split(' v ')[0])[:10]
        hit = None
        for j, x in enumerate(gl):
            if j in used:
                continue
            gh = _key(x['home'])
            if home and (home in gh or gh[:10] in home):
                hit = j; break
        if hit is None and i < len(gl) and i not in used:
            hit = i
        if hit is not None:
            used.add(hit); x = gl[hit]
            leg.update(state=x['state'], score=x['score'], hint=x['hint'], comp=x.get('comp', ''),
                       kots=x.get('ko', 0), ht=x.get('ht'))
        else:
            leg.update(state='pending', score='', hint='not started', comp='', kots=0)
    return legs


# ---------------------------------------------------------------- the sheet

_VENUE = re.compile(r'^(.+?) (HOME|AWAY) ((?:\d+:\d+ ?)+?)\s*(?:(\d+)W(\d+)D(\d+)L gd([+-]?\d+))?$')


def _wdl(f, a):
    return 'W' if f > a else 'L' if f < a else 'D'


def _tuple(s):
    try:
        v = ast.literal_eval(s)
        return v if isinstance(v, (tuple, list)) else None
    except Exception:
        return None


def _pretty_note(note):
    """The engine's bracket note in words: 'draw-prone: fav 1 opp 5 venue, opp overall (5, 5, 0)'."""
    m = re.match(r'draw-prone: fav (\d+) opp (\d+) venue, opp overall \((\d+), (\d+), (\d+)\)', note)
    if m:
        return (f"draw-prone: {m.group(1)} venue draw{'s' if m.group(1) != '1' else ''} for the favourite, {m.group(2)} for the opponent; "
                f"opponent's last 10 overall {m.group(3)}W {m.group(4)}D {m.group(5)}L")
    if note == 'no shot stats':
        return 'no shot stats on the favourite, so the cover only'
    return note


def sheet_for(leg, product):
    """The design's sheet blocks, built from the stat lines logged at booking time."""
    sh = dict(venue=None, overall=None, h2h=None, shots=None, half=None, h2goals=None,
              af=None, afLine=None, afAgree=None, flags=[])
    lines = leg.get('stats') or []
    sel = leg['sel']; note = _pretty_note(leg.get('note') or '')
    venues = []
    for ln in lines:
        m = _VENUE.match(ln)
        if m:
            scores = [tuple(int(x) for x in s.split(':')) for s in m.group(3).split()]
            venues.append((m.group(1), m.group(2), scores, m.group(7)))
    if product in ('Winners', 'Draws', 'Max') and venues:
        sh['venue'] = []
        for name, side, scores, gd in venues[:2]:
            w = sum(_wdl(f, a) == 'W' for f, a in scores); d = sum(_wdl(f, a) == 'D' for f, a in scores)
            l = len(scores) - w - d
            gf = sum(f for f, a in scores); ga = sum(a for f, a in scores)
            margin = (gf - ga) / len(scores) if scores else 0
            sh['venue'].append(dict(side=name, label=f"last {len(scores)} at home" if side == 'HOME' else f"last {len(scores)} away",
                                    pills=[[f"{f}-{a}", _wdl(f, a)] for f, a in scores],
                                    rec=f"{w}W {d}D {l}L, scored {gf}, conceded {ga}", margin=f"{margin:+.2f} per game"))
    elif venues:                                   # points sports: the venue history and the line
        sh['af'] = []
        for name, side, scores, gd in venues[:2]:
            sh['af'].append(dict(side=f"{name}, last {len(scores)} {'home' if side == 'HOME' else 'away'}",
                                 pills=[[f"{f}-{a}", _wdl(f, a)] for f, a in scores]))
        sh['afLine'] = sel
    for ln in lines:
        if _VENUE.match(ln):
            continue
        m = re.match(r'^1X2 ([\d.]+)/([\d.]+)/([\d.]+)\s+favourite (Home|Away) venue margin ([+-][\d.]+)', ln)
        if m:
            sh['flags'].append(f"Market favourite {m.group(4)} at {m.group(1) if m.group(4) == 'Home' else m.group(3)} "
                               f"(1X2 {m.group(1)} / {m.group(2)} / {m.group(3)}); venue margin {m.group(5)}")
            continue
        m = re.match(r'^overall last10 home (\(.*?\)) away (\(.*?\))\s+h2h (\[.*?\])\s+SoT home (\(.*?\)|None) away (\(.*?\)|None)', ln)
        if m:
            names = [v[0] for v in venues[:2]] or ['Home', 'Away']
            ho, ao = _tuple(m.group(1)), _tuple(m.group(2))
            if ho and ao:
                sh['overall'] = [dict(side=names[0], strip='W' * ho[0] + 'D' * ho[1] + 'L' * ho[2], rec=f"{ho[0]}W {ho[1]}D {ho[2]}L"),
                                 dict(side=names[-1], strip='W' * ao[0] + 'D' * ao[1] + 'L' * ao[2], rec=f"{ao[0]}W {ao[1]}D {ao[2]}L")]
            h2h = _tuple(m.group(3)) or []
            if h2h:
                sh['h2h'] = [[f"{x[0]}-{x[1]}", _wdl(x[0], x[1])] for x in h2h if isinstance(x, (tuple, list)) and len(x) == 2]
            hs, as_ = _tuple(m.group(4)), _tuple(m.group(5))
            shots = []
            if hs: shots += [[f"{names[0]} for", round(hs[0], 1), True], [f"{names[0]} against", round(hs[1], 1), False]]
            if as_: shots += [[f"{names[-1]} for", round(as_[0], 1), True], [f"{names[-1]} against", round(as_[1], 1), False]]
            sh['shots'] = shots or None
            continue
        m = re.match(r'^(.+?): (\d+)/(\d+) of the two venue histories agree', ln)
        if m:
            sh['afAgree'] = f"{m.group(2)} of {m.group(3)} agree"; sh['afLine'] = m.group(1)
            sh['flags'].append(f"{m.group(1)}: {m.group(2)} of the {m.group(3)} venue games agree with the line")
            continue
        m = re.match(r'^1H stats (\{.*\})', ln)
        if m:
            st = {}
            try:
                st = ast.literal_eval(m.group(1))
            except Exception:
                pass
            half = []
            ht = re.search(r'HT (\d+:\d+)', sel)
            if ht: half.append(dict(k='Half time', v=ht.group(1).replace(':', '-')))
            for k, lab in (('shots', 'Shots'), ('sot', 'On target'), ('poss', 'Possession'), ('corners', 'Corners')):
                if st.get(k):
                    v = st[k]; half.append(dict(k=lab, v=f"{v[0]}{'%' if k == 'poss' else ''} v {v[1]}{'%' if k == 'poss' else ''}"))
            sh['half'] = half or [dict(k='1H stats', v='none on the feed')]
            continue
        m = re.match(r'^trailing 2H totals (\[.*\])', ln)
        if m:
            t2 = _tuple(m.group(1)) or []
            hn, an = leg['match'].split(' v ')[0], leg['match'].split(' v ')[-1]
            sh['h2goals'] = [dict(side=f"{hn} 2nd half", nums=list(t2[:7])), dict(side=f"{an} 2nd half", nums=list(t2[7:14]))]
            continue
        m = re.match(r'^model p ([\d.]+)\s+xg ([\d.]+)\s+mismatch ([\d.]+)\s+combined draws (\d+)\s+btts (\d+)%\s+2H goals ([\d.]+)\s+league draw ([\d.]+)(?:\s+gate (\S+))?', ln)
        if m:
            sh['half'] = None
            sh['flags'] += [f"Gate {'quiet game' if m.group(8) == 'quiet' else 'home profile' if m.group(8) else 'passed'}: expected goals {m.group(2)}, mismatch {m.group(3)}, combined draws {m.group(4)}",
                            f"Both score {m.group(5)}% of the time; league draws {float(m.group(7)) * 100:.0f}%; second halves average {m.group(6)} goals",
                            f"Model draw chance {float(m.group(1)) * 100:.0f}%"]
            continue
        if ln.startswith('exp ') or ln.startswith('home@home') or ln.startswith('away@away') or ln.startswith('support '):
            sh['flags'].append(ln)
            continue
        if ln.startswith('DECIDED BY') or ln.startswith('FULL RECORD') or re.match(r'^(home|away) \w', ln):
            continue                               # the raw per-stat record is too wide for a card
        if len(sh['flags']) < 6 and len(ln) < 160:
            sh['flags'].append(ln)
    # the shape of the pick, in words
    if product == 'Winners':
        if 'Double Chance' in sel:
            sh['flags'].insert(0, f"Cover taken at {leg['price']:.2f}" + (f": {note}" if note else ''))
        elif note:
            sh['flags'].insert(0, note)
        else:
            sh['flags'].insert(0, f"Straight win at {leg['price']:.2f}: price under 1.80 and the venue margin clears the bar")
    elif product == 'Live':
        if lines:
            sh['flags'].insert(0, lines[0])
    elif note and product != 'Draws':
        sh['flags'].insert(0, note)
    return sh


# ---------------------------------------------------------------- shaping

def _day_of(when):
    return dt.datetime.strptime(when, '%Y-%m-%d %H:%M').date()


def _combo(legs):
    c = 1.0
    for l in legs:
        c *= (l['price'] or 1.0)
    return c


def _window(label, legs):
    m = re.search(r'until (\d{1,2}):00', label)
    if legs:
        timed = [l for l in legs if l.get('kots')]
        if timed:                                  # the share API's kickoff, when graded
            t = dt.datetime.fromtimestamp(min(l['kots'] for l in timed), tz=WAT)
            first = dict(day=t.strftime('%a'), ko=t.strftime('%H:%M'))
        else:
            first = legs[0]                        # booking scripts log legs in kickoff order
        return f"{first['day']} {first['ko']}" + (f" to {int(m.group(1)):02d}:00" if m else '')
    return ''


def _donor_stats(c, leg):
    """Stat lines for a leg logged without them (a hand rebook): the same match
    and product booked within a day of it."""
    d = _day_of(c['when'])
    for o in parse_bookings():
        if o['code'] == c['code'] or o['product'] != c['product'] or abs((_day_of(o['when']) - d).days) > 1:
            continue
        for l in o['legs']:
            if l['match'] == leg['match'] and l['stats']:
                return l['stats'], l['note']
    return [], ''


def decorate(c, grade=True):
    """One code, legs merged with grades, sheet attached, odds and window computed."""
    legs = [dict(l) for l in c['legs']]
    g = graded(c['code']) if (grade and legs) else {}
    merge_grades(legs, g.get('legs') or [])
    for l in legs:
        if not l['stats']:
            l['stats'], note = _donor_stats(c, l)
            l['note'] = l['note'] or note
        l['sheet'] = sheet_for(l, c['product'])
        l['stats'] = None
    out = dict(c, legs=legs)
    out['odds'] = round(_combo(legs), 2) if legs else None
    out['booked'] = c['when'][11:]
    out['date'] = c['when'][:10]
    out['window'] = _window(c['label'], legs)
    out['error'] = g.get('error')
    return out


def codes_for(day='today'):
    """Codes booked on that day (by booking time), graded."""
    today = dt.datetime.now(tz=WAT).date()
    target = {'today': today, 'yesterday': today - dt.timedelta(days=1), 'tomorrow': today + dt.timedelta(days=1)}.get(day)
    if target is None:
        try:
            target = dt.date.fromisoformat(day)
        except ValueError:
            target = today
    out = [decorate(c) for c in parse_bookings() if _day_of(c['when']) == target]
    return dict(day=str(target), codes=out)


def banner():
    """Open codes + next kickoff across today's and yesterday's codes (cached grades)."""
    now = dt.datetime.now(tz=WAT)
    today = now.date()
    open_codes, next_ko = 0, None
    for c in parse_bookings():
        d = _day_of(c['when'])
        if d < today - dt.timedelta(days=1):
            break
        legs = graded(c['code']).get('legs') or [] if c['legs'] else []
        if legs and any(l['state'] == 'lost' for l in legs):
            continue
        if legs and all(l['state'] in ('won', 'void') for l in legs):
            continue
        if not legs and d < today:
            continue
        open_codes += 1
        for l in legs:
            if l['state'] == 'pending' and l.get('ko'):
                t = dt.datetime.fromtimestamp(l['ko'], tz=WAT)
                if t > now and (next_ko is None or t < next_ko):
                    next_ko = t
    return dict(open=open_codes, next_ko=next_ko.strftime('%H:%M') if next_ko else None)


def record(days=35):
    """Settled codes in the last N days: per-product tallies, the table, the day heat map."""
    today = dt.datetime.now(tz=WAT).date()
    since = today - dt.timedelta(days=days)
    per = collections.defaultdict(lambda: dict(won=0, lost=0, open=0, legs_won=0, legs_lost=0, first=None))
    settled, heat = [], collections.defaultdict(lambda: dict(won=0, lost=0))
    for c in parse_bookings():
        d = _day_of(c['when'])
        if d < since:
            break
        if not c['legs']:
            continue
        g = graded(c['code'])
        gl = g.get('legs') or []
        if not gl:
            if (today - d).days >= 3:              # the share code has expired: known booked, no grade
                heat[str(d)]['expired'] = heat[str(d)].get('expired', 0) + 1
                per[c['product']]['expired'] = per[c['product']].get('expired', 0) + 1
            continue
        st = [l['state'] for l in gl]
        legs_won = sum(s == 'won' for s in st)
        res = 'lost' if any(s == 'lost' for s in st) else 'won' if all(s in ('won', 'void') for s in st) else 'open'
        p = per[c['product']]
        p[res] += 1
        p['legs_won'] += legs_won; p['legs_lost'] += sum(s == 'lost' for s in st)
        p['first'] = d.strftime('%-d %b')
        if res != 'open':
            heat[str(d)][res] += 1
            settled.append(dict(code=c['code'], date=d.strftime('%-d %b'), iso=str(d), product=c['product'],
                                legsWon=f"{legs_won} of {len(gl)}", odds=round(_combo(c['legs']), 2), result=res))
    tiles = []
    for prod in PRODUCTS + [p for p in per if p not in PRODUCTS]:
        if prod not in per:
            continue
        p = per[prod]; n = p['won'] + p['lost']
        if n == 0:
            continue
        lw, ll = p['legs_won'], p['legs_lost']
        tiles.append(dict(product=prod, rate=f"{p['won']}-{p['lost']}",
                          sample=f"{p['won'] / n * 100:.1f}% of {n} codes",
                          since=f"since {p['first']}; legs {lw}-{ll} ({lw / (lw + ll) * 100:.0f}%)" if lw + ll else f"since {p['first']}"))
    return dict(tiles=tiles, per=per, settled=settled, heat=heat, since=str(since))


# ---------------------------------------------------------------- live

_BOT = {}


def bot_name():
    """The Telegram bot's @username (getMe, cached) for the site's links."""
    if 'name' not in _BOT:
        try:
            import telegram_bot as TB
            r = TB._call('getMe')
            _BOT['name'] = ((r or {}).get('result') or {}).get('username')
        except Exception:
            _BOT['name'] = None
    return _BOT['name']


def live_feed(LIVE):
    """What the watcher is doing right now: live codes booked in the last hour,
    the board it is watching, and its tail of log lines."""
    try:
        import live_ht as LH
        board = LH.BOARD
    except Exception:
        board = dict(ts=0, rows=[])
    now = time.time()
    codes = []
    for c in parse_bookings():
        if c['product'] != 'Live':
            continue
        when = dt.datetime.strptime(c['when'], '%Y-%m-%d %H:%M').replace(tzinfo=WAT)
        age = now - when.timestamp()
        if age > 3600:
            break
        d = decorate(c)
        d['age'] = int(age)
        d['closes_in'] = max(0, 900 - int(age))
        codes.append(d)
    return dict(state=LIVE.get('state'), started=LIVE.get('started'), chat=LIVE.get('chat'), bot=bot_name(),
                codes=codes, watching=board.get('rows', []), board_age=int(now - board.get('ts', 0)) if board.get('ts') else None,
                log=(LIVE.get('log') or [])[-30:])


# ---------------------------------------------------------------- rules (static, edited by hand when a rule changes)

RULES = [
    dict(product='Winners', tag='win or draw',
         plain="The market picks the side, the venue record decides whether to bet. A favourite needs a venue goal-difference margin of 1.0 at home or 1.5 away. Under 1.80 it goes as a straight win; 1.80 to 2.60 as a double chance; above that nothing. Draw-proneness on either side, or no shot stats on the favourite, forces the cover at any price. A favourite whose last ten shows wins no better than losses, is out-shot on target, or has a losing head-to-head is dropped.",
         thresholds=[dict(k='Venue margin, home / away', v='+1.0 / +1.5'), dict(k='Straight win, price under', v='1.80'), dict(k='Double chance, price under', v='2.60'),
                     dict(k='Cover forced when', v='fav 5+ or opp 4+ venue draws; combined 4+; opp 4+ of last 10; no shot stats')],
         measured='Corpus: home favourites at margin 1.0+ win 52.6%, win-or-draw 75.9% (19,945 matches). Combined venue draws 4+: straight win falls 54% to 49.5%.'),
    dict(product='Draws', tag='the gate',
         plain="Quiet games. The gate is expected goals under 2.4, combined draws 3+, mismatch 1.0 or less, expected shots on target 8 or fewer with a 2.0 evenness floor, blanks 5+ split evenly. A second branch takes a home side that draws at home, concedes little, in a league that draws 30%+. Price floor at the gate's own rate. One slip.",
         thresholds=[dict(k='Gate draw rate', v='34.6%'), dict(k='Price floor (1 / rate)', v='2.89'), dict(k='Home-profile branch', v='h draws 3+, conceded <= 1.0, league 30%+')],
         measured='Corpus: 871 gate matches at 34.6%, every 2026 month between 30% and 39%. The best any threshold rule reaches on 44,250 matches is 36.7%.'),
    dict(product='Live', tag='half-time whistle',
         plain="At the break the watcher reads the score, the live first-half shots and possession, and both sides' last seven second-half goal totals. Draw on a gate game level at 0-0 or 1-1. 2H Over 0.5 when all fourteen trailing second halves scored and the first half had 7+ shots. 2H Under 1.5 when twelve of fourteen had one goal or fewer, at most one goal on the board, and no side pressing at level. No live stats, no bet. Legs found within two minutes go on one slip.",
         thresholds=[dict(k='Draw: gate game level at HT', v='49.3%, floor 2.10'), dict(k='Over: trailing halves scored', v='14 of 14, floor 1.20'), dict(k='Under: halves with <= 1 goal', v='12 of 14, HT total <= 1, floor 1.55')],
         measured='Corpus: 26,447 rows in time order, 2H Over 0.5 at 14/14 = 83.6%, Under at 0-0 = 71.6%. Live-booked since 12 Sep on the Record page.'),
    dict(product='American Football', tag='line agreement',
         plain="Each side's last seven games at its venue plus the quarter scores. A total, first-half total or handicap line is taken when eleven of the fourteen games agree. Mismatch games, winner at 1.05 or under, are scored on lookalike games only: the favourite's blowouts and the underdog's heaviest defeats. College sides with fewer than two games this season prefer the total over the handicap. No winners.",
         thresholds=[dict(k='Agreement', v='11 of 14'), dict(k='Mismatch games', v='lookalike subset, 80%'), dict(k='Price floor', v='1.40')],
         measured='First weekend (12-13 Sep): totals 5 of 5, first-half totals 2 of 2, handicaps 2 of 4. Unmeasured beyond that; the same engine runs basketball, ice hockey and handball.'),
    dict(product='Max', tag='accumulator engine',
         plain="The original goal-and-stat engine: over/unders, team totals, corners, bookings, shots, offsides, fouls, saves, first and second half markets. Stat Unders need the line above the sample maximum; stat Overs need it below the sample minimum; goal Overs use blank-rate tables; bookings need a 1.5 cushion; a market-knows cap refuses a price the book has already moved. Highest odds inside the 50-leg cap.",
         thresholds=[dict(k='Stat Under cushion', v='line above sample max'), dict(k='Bookings cushion', v='1.5'), dict(k='Market-knows cap', v='refuse if implied < rate - 0.04')],
         measured='Per-leg 84-95% on the families kept; the slips are long by design and die to one or two legs.'),
]

CHANGELOG = [
    dict(date='13 Sep', txt='Live: Draw needs 0-0 or 1-1 at the break; Under 1.5 needs at most one goal banked (corpus 71.6% at 0-0, 58.7% at 2+). First-half shots now check the histories: Over needs 7+ shots, Under refuses a side pressing at level.'),
    dict(date='13 Sep', txt='Winners: draw-proneness on either side forces the cover; combined venue-draw line 6 -> 4; a favourite with no shot stats goes on as cover only.'),
    dict(date='13 Sep', txt='Points sports: one engine for American football, basketball, ice hockey and handball; period totals from the Flashscore period feed; mismatch games scored on lookalike games instead of skipped.'),
    dict(date='12 Sep', txt='Winners: overall form, head-to-head and shots on target added to every pick; 5+ venue draws force a cover; board-wide yesterday check runs before every booking.'),
    dict(date='12 Sep', txt='Live watcher built: draw gate at half-time, 2H Over 0.5 / Under 1.5 from the trailing second halves, Telegram push, /live and /stop, one slip per two-minute batch.'),
    dict(date='12 Sep', txt='Slips capped at 50 legs (SportyBet cap); winners keep the 50 most likely.'),
    dict(date='11 Sep', txt='Draw price floor restored at the gate rate (2.89). Model cut removed on 9 Sep; the gate alone selects.'),
    dict(date='10 Sep', txt='Winners v2: margin 1.0 home / 1.5 away, straight/cover line 1.80. Corpus 43k matches.'),
]

CORPUS_TILES = [
    dict(product='Draws', rate='34.6%', sample='871 gate matches', since='draw gate, corpus measured'),
    dict(product='Winners', rate='75.9%', sample='19,945 matches', since='win-or-draw, home margin 1.0+'),
    dict(product='Live', rate='83.6%', sample='1,073 rows', since='2H Over 0.5 when 14 of 14 trailing halves scored'),
    dict(product='Live', rate='71.6%', sample='0-0 at the break', since='2H Under 1.5 when 12 of 14 halves had one goal or fewer'),
    dict(product='American Football', rate='unmeasured', sample='one weekend', since='11 of 14 agreement rule'),
]
