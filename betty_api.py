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

PRODUCTS = ['Live', 'Winners', 'Draws', 'Half-time draw', 'Points sports', 'Max odds']
SPORT_LABEL = {'american football': 'American football', 'nfl': 'American football', 'ncaa': 'American football',
               'basketball': 'Basketball', 'ice hockey': 'Ice hockey', 'hockey': 'Ice hockey', 'handball': 'Handball'}


# ---------------------------------------------------------------- bookings

def bookings_paths():
    out = [os.path.join(ROOT, 'bookings.md')]
    vol = os.environ.get('BOOKINGS_PATH')
    if vol and vol not in out:
        out.append(vol)
    return [p for p in out if os.path.exists(p)]


def sport_of(label):
    l = label.lower()
    for k, v in SPORT_LABEL.items():
        if k in l:
            return v
    return None


def product_of(label):
    """The design's six products: Winners, Draws, Half-time draw, Live, Points sports, Max odds."""
    l = label.lower()
    if l.startswith('live '):
        return 'Live'
    if sport_of(label):
        return 'Points sports'
    if 'winner' in l:                 # before 'draw': "3+ venue draws -> double chance" is a winners slip
        return 'Winners'
    if 'ht draw' in l or 'half-time draw' in l or 'half time draw' in l:
        return 'Half-time draw'
    if 'draw' in l:
        return 'Draws'
    return 'Max odds'


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
            lab = m.group(2)
            rb = re.match(r'^.*?\b([A-Z0-9]{6}) with\b', lab)         # a hand rebook names the code it replaces
            nest = re.search(r'nested ([\d.]+)x from ([A-Z0-9]{6})', lab)   # pick-your-odds subset of another code
            seen[code] = dict(code=code, when=m.group(1), label=lab, product=product_of(lab), sport=sport_of(lab),
                              url=url or f'http://www.sportybet.com/ng/?shareCode={code}', legs=legs,
                              replaces=rb.group(1) if rb and rb.group(1) != code else None, superseded_by=None,
                              nested_from=nest.group(2) if nest else None)
    for c in seen.values():
        if c['replaces'] and c['replaces'] in seen:
            seen[c['replaces']]['superseded_by'] = c['code']
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
    """Attach the graded row to each booked leg. Match on the home team, then on
    the away team; fall back to position ONLY when both lists are the same
    length. A booked leg the share API no longer carries (NETHQ5: Sandecja v
    Slask II vanished from the code) used to shift every later leg one row
    down and mis-grade the whole slip."""
    used = set()

    def find(key, side):
        if len(key) < 5:
            return None
        for j, x in enumerate(gl):
            if j in used:
                continue
            gk = _key(x[side])
            if key in gk or (len(gk) >= 5 and gk[:10] in key):
                return j
        return None

    hits = {}
    for i, leg in enumerate(legs):
        home, away = (leg['match'].split(' v ') + [''])[:2]
        j = find(_key(home)[:10], 'home')
        if j is None:
            j = find(_key(away)[:10], 'away')
        if j is not None:
            used.add(j); hits[i] = j
    if len(legs) == len(gl):
        for i in range(len(legs)):
            if i not in hits and i not in used:
                hits[i] = i; used.add(i)
    for i, leg in enumerate(legs):
        j = hits.get(i)
        if j is not None:
            x = gl[j]
            leg.update(state=x['state'], score=x['score'], hint=x['hint'], comp=x.get('comp', ''),
                       kots=x.get('ko', 0), ht=x.get('ht'))
        else:
            leg.update(state='void', score='', hint='not on the share code', comp='', kots=0)
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


# ---------------------------------------------------------------- losses

def _score(leg):
    m = re.match(r'^(\d+)-(\d+)', leg.get('score') or '')
    return (int(m.group(1)), int(m.group(2))) if m else (None, None)


def loss_cause(leg, product):
    """What the sheet said against what happened, for a lost leg. Facts from
    the logged stat lines and the final score - no theory."""
    sh = leg.get('sheet') or {}
    sel = leg['sel']; h, a = _score(leg)
    if h is None:
        return 'Settled as lost by SportyBet; no final score on the share API.'
    home, away = (leg['match'].split(' v ') + [''])[:2]
    bits = []
    if product == 'Winners':
        fav_home = 'Home' in sel and 'Away' not in sel
        fav, opp = (home, away) if fav_home else (away, home)
        venue = sh.get('venue') or []
        vf = next((v for v in venue if v['side'] == fav), None); vo = next((v for v in venue if v['side'] != fav), None)
        if h == a:
            bits.append(f"Finished {h}-{a}: the favourite ({fav}) was held and the straight win died to the draw; the double chance would have landed")
        else:
            fav_goals, opp_goals = (h, a) if fav_home else (a, h)
            bits.append(f"Finished {h}-{a}: the favourite ({fav}) was beaten {opp_goals}-{fav_goals} {'at home' if fav_home else 'away'}")
        if vf: bits.append(f"{fav} at its venue: {vf['rec']}, goal difference {vf['margin']}")
        if vo:
            d = sum(p[1] == 'D' for p in vo['pills']); w = sum(p[1] == 'W' for p in vo['pills'])
            bits.append(f"{opp} at its venue: {vo['rec']}" + (f" - {d} draws in {len(vo['pills'])}" if d >= 3 else '') + (f" - {w} wins in {len(vo['pills'])}" if w >= 3 else ''))
        shots = sh.get('shots') or []
        if len(shots) == 4:
            ff, fa = (shots[0][1], shots[1][1]) if fav_home else (shots[2][1], shots[3][1])
            of_, oa = (shots[2][1], shots[3][1]) if fav_home else (shots[0][1], shots[1][1])
            if ff < of_:
                bits.append(f"shots on target per game: {fav} {ff} v {opp} {of_} - the favourite was the lower-shot side")
        ov = sh.get('overall') or []
        for o in ov:
            if o['side'] == fav and o.get('rec'):
                bits.append(f"{fav} last 10 overall {o['rec']}")
        h2h = sh.get('h2h') or []
        if h2h:
            hv = [x[1] for x in h2h]
            fav_view = hv if fav_home else ['W' if r == 'L' else 'L' if r == 'W' else r for r in hv]
            if fav_view.count('L') >= fav_view.count('W'):
                bits.append(f"head to head from the favourite's side: {fav_view.count('W')}W {fav_view.count('D')}D {fav_view.count('L')}L")
    elif product == 'Live':
        ht = leg.get('ht')
        if ht:
            hh, ha = (int(x) for x in ht.split('-'))
            bits.append(f"Half time {ht}, full time {h}-{a}: the second half produced {(h - hh) + (a - ha)} goal{'s' if (h - hh) + (a - ha) != 1 else ''}")
        else:
            bits.append(f"Full time {h}-{a}")
        if sh.get('flags'):
            bits.append('rule fired on: ' + sh['flags'][0])
        for x in sh.get('half') or []:
            if x['k'] in ('Shots', 'Possession'):
                bits.append(f"first-half {x['k'].lower()} {x['v']}")
    elif product in ('Draws', 'Half-time draw'):
        bits.append(f"Finished {h}-{a}" + (f" (half time {leg['ht']})" if leg.get('ht') else ''))
        bits += [f for f in (sh.get('flags') or [])[:2]]
    elif product == 'Points sports':
        tot, mrg = h + a, h - a
        bits.append(f"Finished {h}-{a}: total {tot}, home margin {mrg:+d}; the line was {sh.get('afLine') or sel}" + (f", {sh['afAgree']}" if sh.get('afAgree') else ''))
    else:
        bits.append(f"Finished {h}-{a}" + (f" (half time {leg['ht']})" if leg.get('ht') else ''))
        bits += [f for f in (sh.get('flags') or [])[:1]]
    return '. '.join(bits) + '.'


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
        if o['code'] == c['code'] or o['product'] != c['product'] or o['nested_from'] or abs((_day_of(o['when']) - d).days) > 1:
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
    out = []
    for c in parse_bookings():
        if _day_of(c['when']) != target or c['nested_from']:
            continue
        d = decorate(c)
        d['nested'] = nested_for(c['code'])
        d['targets'] = [t for t in NEST_TARGETS if d['odds'] and t < d['odds'] * 0.9] if c['product'] != 'Live' else []
        out.append(d)
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
        if c['nested_from'] or c['superseded_by']:
            continue
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
    settled, heat, losses = [], collections.defaultdict(lambda: dict(won=0, lost=0)), []
    for c in parse_bookings():
        d = _day_of(c['when'])
        if d < since:
            break
        if not c['legs'] or c['nested_from']:
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
        if res == 'lost' and len(losses) < 40:
            dc = decorate(c)
            for l in dc['legs']:
                if l['state'] == 'lost' and len(losses) < 40:
                    losses.append(dict(code=c['code'], product=c['product'], date=d.strftime('%-d %b'), match=l['match'],
                                       sel=l['sel'], score=l['score'] or 'lost', cause=loss_cause(l, c['product'])))
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
    return dict(tiles=tiles, per=per, settled=settled, heat=heat, since=str(since), losses=losses)


def legs_list(days=35, limit=1200):
    """The Results page: every graded leg of the last N days as a flat list,
    newest code first, with per-leg stats. Superseded rebooks are skipped so a
    leg is not counted three times."""
    today = dt.datetime.now(tz=WAT).date()
    since = today - dt.timedelta(days=days)
    rows, codes = [], dict(won=0, lost=0, open=0)
    for c in parse_bookings():
        d = _day_of(c['when'])
        if d < since:
            break
        if not c['legs'] or c['superseded_by'] or c['nested_from']:
            continue
        g = graded(c['code'])
        if not (g.get('legs') or []):
            continue
        dc = decorate(c)
        st = [l['state'] for l in dc['legs']]
        codes['lost' if 'lost' in st else 'won' if all(x in ('won', 'void') for x in st) else 'open'] += 1
        for l in dc['legs']:
            sh = l.get('sheet') or {}
            note = loss_cause(l, c['product']) if l['state'] == 'lost' else ((sh.get('flags') or [''])[0])
            rows.append(dict(code=c['code'], product=c['product'], sport=c['sport'], date=d.strftime('%-d %b'), iso=str(d),
                             comp=l.get('comp') or '', match=l['match'], sel=l['sel'], price=l['price'], score=l['score'] or '',
                             state=l['state'], hint=l.get('hint') or '', note=note))
    won = sum(r['state'] == 'won' for r in rows); lost = sum(r['state'] == 'lost' for r in rows)
    return dict(rows=rows[:limit], total=len(rows), since=str(since), legs=dict(won=won, lost=lost, graded=won + lost,
                rate=round(won / (won + lost) * 100, 1) if won + lost else None), codes=codes)


# ---------------------------------------------------------------- pick your odds

NEST_TARGETS = [3, 5, 10, 25, 50, 100, 200]
_NEST = {'loaded': False, 'map': {}}


def _nest_path():
    vol = os.environ.get('BOOKINGS_PATH')
    return os.path.join(os.path.dirname(vol) if vol else ROOT, 'nested.json')


def _nest_load():
    if not _NEST['loaded']:
        try:
            _NEST['map'] = json.load(open(_nest_path()))
        except Exception:
            _NEST['map'] = {}
        _NEST['loaded'] = True
    return _NEST['map']


def _nest_save():
    try:
        json.dump(_NEST['map'], open(_nest_path(), 'w'))
    except OSError:
        pass


def nested_for(code):
    """[{target, code, odds, n}] already booked from this code, freshest first."""
    m = _nest_load()
    out = [v for k, v in m.items() if k.startswith(code + ':')]
    return sorted(out, key=lambda v: v['target'])


def nest_code(code, target):
    """Pick your odds: a slip of the MOST LIKELY legs of an existing code (shortest
    prices first, games not yet kicked off) up to the target multiplier, booked as
    its own share code. One booking per (code, target); repeats return the cached
    code while its first game has not started."""
    code = (code or '').strip().upper()
    try:
        target = float(target)
    except (TypeError, ValueError):
        return dict(error='target must be a number')
    if target < 1.5 or target > 5000:
        return dict(error='target must be between 1.5 and 5000')
    if target == int(target):
        target = int(target)
    key = f"{code}:{target:g}"
    cache = _nest_load()
    hit = cache.get(key)
    now = time.time()
    if hit and hit.get('first_ko', 0) > now + 60:
        return dict(hit, cached=True)
    src = next((c for c in parse_bookings() if c['code'] == code), None)
    g = graded(code, force=True)
    legs = [l for l in (g.get('legs') or []) if l['state'] == 'pending' and l.get('ko', 0) > now + 300
            and l.get('ids', {}).get('active', 1) != 0]
    if not legs:
        return dict(error='no leg of this code is still to kick off')
    legs.sort(key=lambda l: l['price'])
    # the shortest k prices plus ONE more leg from further down the list, the
    # combination that lands nearest the target (a 5x rung should read ~5x, not
    # 6.8x because the tenth short price overshot)
    best, base, pick, combo = None, 1.0, [], 1.0
    for k in range(len(legs)):
        for j in range(k, len(legs)):
            cand = base * legs[j]['price']
            if cand >= target * 0.95 and (best is None or abs(cand - target) < abs(best[0] - target)):
                best = (cand, k, j)
        base *= legs[k]['price']
    if best:
        cand, k, j = best
        pick = legs[:k] + [legs[j]]; combo = cand
    else:
        pick = list(legs); combo = base
    if combo < target * 0.95:
        return dict(error=f"the legs still to play only reach {combo:.2f}x together; pick a lower target", max=round(combo, 2))
    if src and src['product'] == 'Live':
        return dict(error='live codes are single shots at the whistle; nothing to pick from')
    bk = A.book([dict(eventId=l['ids']['eventId'], productId=3, marketId=l['ids']['marketId'],
                      specifier=l['ids']['specifier'], outcomeId=l['ids']['outcomeId']) for l in pick])
    if not bk or not bk.get('code'):
        return dict(error='SportyBet did not return a code' + (f": {bk.get('msg')}" if bk and bk.get('msg') else ''))
    prod = (src['product'] if src else 'code').lower()
    words = {'winners': 'winners', 'draws': 'draw', 'half-time draw': 'HT draw', 'points sports': (src or {}).get('sport') or 'points', 'max odds': 'max odds'}.get(prod, prod)
    A.log_booking(bk['code'], bk.get('url'), f"{words} nested {target:g}x from {code} ({len(pick)} legs, {combo:.2f}x) - pick your odds",
                  [(l['ko'], l['match'], l['sel'], l['price'], [f"from {code}: the {len(pick)} shortest prices still to play"]) for l in pick])
    res = dict(target=target, code=bk['code'], url=bk.get('url'), odds=round(combo, 2), n=len(pick), source=code,
               first_ko=min(l['ko'] for l in pick), first=dt.datetime.fromtimestamp(min(l['ko'] for l in pick), tz=WAT).strftime('%a %H:%M'),
               booked=dt.datetime.now(tz=WAT).strftime('%H:%M'),
               legs=[dict(match=l['match'], sel=l['sel'], price=l['price'], ko=dt.datetime.fromtimestamp(l['ko'], tz=WAT).strftime('%a %H:%M')) for l in pick])
    cache[key] = res; _nest_save()
    return res


LADDER = [3, 5, 10, 25, 50, 100]


def build_ladder(code):
    """Every rung of the ladder from one base code: nest_code at each target the
    slip can reach. Cached rungs (first game not started) are reused."""
    out = {}
    for t in LADDER:
        r = nest_code(code, t)
        if r.get('code'):
            out[t] = r
        elif r.get('max') is not None:
            break                                  # the slip cannot reach this or any higher rung
    return out


def ladder_for(day='today', build=True):
    """The Codes page: per product, today's base slip and its ladder of odds."""
    today = dt.datetime.now(tz=WAT).date()
    target = {'today': today, 'yesterday': today - dt.timedelta(days=1)}.get(day, today)
    products, seen = [], set()
    for c in parse_bookings():
        if _day_of(c['when']) != target or c['nested_from'] or c['superseded_by'] or not c['legs'] or c['product'] == 'Live':
            continue                                # live codes live on the Live tab
        key = (c['product'], c['sport'])
        if key in seen:
            continue                                # newest base code per product only
        seen.add(key)
        d = decorate(c)
        rungs = {n['target']: n for n in nested_for(c['code'])}
        if build and c['product'] != 'Live' and target == today:
            for t in LADDER:
                if t in rungs and rungs[t].get('first_ko', 0) > time.time() + 60:
                    continue
                if not d['odds'] or t >= d['odds'] * 0.9:
                    break
                r = nest_code(c['code'], t)
                if r.get('code'):
                    rungs[t] = r
                else:
                    break
        d['ladder'] = {str(t): rungs[t] for t in sorted(rungs)}
        products.append(d)
    order = {p: i for i, p in enumerate(PRODUCTS)}
    products.sort(key=lambda d: order.get(d['product'], 9))
    return dict(day=str(target), targets=LADDER, products=products)


# ---------------------------------------------------------------- any code

def grade_any(code):
    """A code we did not book, shaped like one of ours (no sheet)."""
    code = (code or '').strip().upper()
    if not re.match(r'^[A-Z0-9]{5,8}$', code):
        return dict(error='a share code is 6 letters and digits', legs=[])
    ours = next((c for c in parse_bookings() if c['code'] == code), None)
    if ours:
        return decorate(ours)
    g = graded(code, force=True)
    if not g.get('legs'):
        return dict(code=code, error=g.get('error') or 'SportyBet returned no legs for this code (expired or mistyped)', legs=[])
    legs = []
    for x in g['legs']:
        ko = dt.datetime.fromtimestamp(x['ko'], tz=WAT) if x.get('ko') else None
        legs.append(dict(day=ko.strftime('%a') if ko else '', ko=ko.strftime('%H:%M') if ko else '', match=x['match'], sel=x['sel'],
                         note='', price=x['price'], state=x['state'], score=x['score'], hint=x['hint'], comp=x.get('comp', ''),
                         kots=x.get('ko', 0), ht=x.get('ht'), sheet=dict(flags=['Not a Betty code: graded from the share API, no sheet.'])))
    return dict(code=code, when='', label='graded from the share API', product='Graded code', sport=None, url=f'http://www.sportybet.com/ng/?shareCode={code}',
                legs=legs, odds=round(_combo(legs), 2), booked='', date='', window=_window('', legs), error=None, replaces=None, superseded_by=None)


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
    dict(product='Winners', tag='match result',
         plain="The market favourite only, and only when venue goal difference backs it. Short prices go as a straight win, mid prices as a double chance, and any price becomes a double chance when either side is draw-prone or the favourite has no shot stats on the feed. A favourite whose last ten shows more losses than wins, that is out-shot on target, that creates fewer shots on target than the opponent does, or that has a losing head-to-head is dropped. A dead heat in the market is no favourite.",
         thresholds=[dict(k='Venue goal difference, home', v='1.0 a game'), dict(k='Venue goal difference, away', v='1.5 a game'), dict(k='Straight win, price under', v='1.80'),
                     dict(k='Double chance band', v='1.80 to 2.60'), dict(k='Draw-prone: favourite venue draws', v='5+'), dict(k='Draw-prone: opponent venue draws', v='4+'),
                     dict(k='Draw-prone: combined venue draws', v='4+'), dict(k='Dropped when', v='more losses than wins, out-shot on target, out-created on target, losing H2H, dead heat')],
         measured='Corpus: home favourites at margin 1.0+ win 52.6%, win-or-draw 75.9% (19,945 matches). The yesterday check replays the rule over the whole previous board before every booking.'),
    dict(product='Draws', tag='draw gate',
         plain="Two branches. The quiet-game filter reads expected goals, combined draws, mismatch, expected shots on target, shot evenness and blanks. The home-profile branch takes home sides that draw at home and concede little, in leagues that draw often. Price floor at the gate's own rate. One slip a day.",
         thresholds=[dict(k='Quiet game', v='xG < 2.4, draws 3+, mismatch <= 1.0, exp. SoT <= 8, blanks 5+'), dict(k='Home profile', v='home draws 3+, conceded <= 1.0, league draws 30%+'),
                     dict(k='Break-even price floor', v='2.89'), dict(k='Slips per run', v='1')],
         measured='Corpus: 34.6% on 871 matches through the gate, every 2026 month between 30% and 39%. The best any threshold rule reaches on 44,250 matches is 36.7%.'),
    dict(product='Half-time draw', tag='1st Half 1X2 Draw',
         plain="The same gate as Draws, priced into the first-half market instead of the full-time one. Halves finish level far more often than matches do, which is where the edge sits: only about half of the gate games level at the break stay level to the end, so the hour is the bet.",
         thresholds=[dict(k='Gate', v='identical to Draws'), dict(k='Market', v='1st Half 1X2 Draw (market 60)'), dict(k='Fair price inside the gate', v='2.09')],
         measured='Corpus: 47.9% half-time draws inside the gate against 40.0% for all matches; 54.2% where the league draws 34%+.'),
    dict(product='Live', tag='half-time whistle',
         plain="The watcher reads every game on the live board at the break and books three shapes: the Draw on gate games level at 0-0 or 1-1; second-half Over 0.5 when every one of the fourteen trailing second halves scored and the first half had shots in it; second-half Under 1.5 when twelve of fourteen trailing halves stayed at one goal or fewer, the game is 0-0 at the break, and nobody is pressing. No live stats, no bet. Legs that land within two minutes go on one slip.",
         thresholds=[dict(k='Draw taken at', v='0-0 or 1-1, gate games, price 2.10+'), dict(k='2H Over 0.5 needs', v='14 of 14 trailing halves scored, price 1.20+'), dict(k='2H Over 0.5 first-half shots', v='7+'),
                     dict(k='2H Under 1.5 needs', v='12 of 14 halves at <= 1 goal, 0-0 at the break, price 1.55+'), dict(k='Under blocked when a side is at', v='10+ shots and 60% possession'), dict(k='One slip when legs land within', v='2 minutes')],
         measured='Corpus: 2H Over 0.5 at 14/14 = 83.6% (1,073 rows), Under 1.5 at 0-0 = 71.6%, gate draw level at HT = 49.3%. Every half-time read is logged with its stats.'),
    dict(product='Points sports', tag='American football, basketball, ice hockey, handball',
         plain="One engine across four points sports. Totals, first-half or first-period totals and handicaps only, never winners. Each side's last seven venue games and period scores are read, and a line only goes out when the large majority of past margins agree. Mismatch games, winner at 1.05 or under, are scored on lookalike games only. College sides with fewer than two games this season prefer the total.",
         thresholds=[dict(k='Agreement, at least', v='11 of 14'), dict(k='Price floor', v='1.40'), dict(k='Mismatch games (winner at 1.05 or under)', v='scored on lookalikes, 80%'),
                     dict(k='Stale college rosters', v='prefer totals; handicap needs 12 of 14'), dict(k='Markets', v='totals, period totals, handicaps')],
         measured='First weekend (12-13 Sep): totals 5 of 5, first-half totals 2 of 2, handicaps 2 of 4. No corpus yet for the other three sports.'),
    dict(product='Max odds', tag='composite engine',
         plain="The goal-and-stats accumulator: over and unders, team totals, corners, bookings, shots, offsides, fouls, saves, and half markets. Cushion gates and blank-rate tables decide what goes on, family bans stop correlated legs, and the daily rollover follows the biggest slip that lands one time in three.",
         thresholds=[dict(k='Modes', v='strict, unders-only, goals-only, target odds, rollover'), dict(k='Stat Under cushion', v='line above the sample max'), dict(k='Stat Over cushion', v='line below the sample min'),
                     dict(k='Bookings cushion', v='1.5'), dict(k='Rollover trigger', v='biggest slip landing 30%+'), dict(k='SportyBet slip cap', v='50 legs')],
         measured='Per-leg 84-95% on the families kept; the slips are long by design and die to one or two legs.'),
]

CHANGELOG = [
    dict(date='13 Sep', txt='Winners: a favourite that creates fewer shots on target than its opponent is dropped (corpus: 46.3% v 54.7% at home, 36.2% v 56.1% away); a dead heat in the market is no favourite. Live: 2H Under 1.5 only at 0-0 (corpus 74.2% v 63.1% with a goal banked).'),
    dict(date='13 Sep', txt='Website: every code with its sheet, live codes with the countdown, the record, the rules, and the operator console.'),
    dict(date='13 Sep', txt='Live: Draw needs 0-0 or 1-1 at the break; Under 1.5 needs at most one goal banked (corpus 71.6% at 0-0, 58.7% at 2+). First-half shots now check the histories: Over needs 7+ shots, Under refuses a side pressing at level.'),
    dict(date='13 Sep', txt='Winners: draw-proneness on either side forces the cover; combined venue-draw line 6 -> 4; a favourite with no shot stats goes on as cover only.'),
    dict(date='13 Sep', txt='Points sports: one engine for American football, basketball, ice hockey and handball; period totals from the Flashscore period feed; mismatch games scored on lookalike games instead of skipped.'),
    dict(date='12 Sep', txt='Winners: overall form, head-to-head and shots on target added to every pick; 5+ venue draws force a cover; board-wide yesterday check runs before every booking.'),
    dict(date='12 Sep', txt='Live watcher built: draw gate at half-time, 2H Over 0.5 / Under 1.5 from the trailing second halves, Telegram push, /live and /stop, one slip per two-minute batch.'),
    dict(date='12 Sep', txt='Slips capped at 50 legs (SportyBet cap); winners keep the 50 most likely.'),
    dict(date='11 Sep', txt='Draw price floor restored at the gate rate (2.89). Model cut removed on 9 Sep; the gate alone selects.'),
    dict(date='10 Sep', txt='Winners v2: margin 1.0 home / 1.5 away, straight/cover line 1.80. Corpus 43k matches.'),
    dict(date='9 Sep', txt='Half-time draw: the gate priced into the 1st Half 1X2 market (47.9% inside the gate, fair 2.09).'),
]

CORPUS_TILES = [
    dict(product='Draws', rate='34.6%', sample='871 gate matches', since='draw gate, corpus measured'),
    dict(product='Half-time draw', rate='47.9%', sample='871 gate matches', since='same gate, 1st half 1X2 draw; 40.0% for all matches'),
    dict(product='Winners', rate='75.9%', sample='19,945 matches', since='win-or-draw, home favourite at venue margin 1.0+'),
    dict(product='Live', rate='83.6%', sample='1,073 rows', since='2H Over 0.5 when 14 of 14 trailing halves scored'),
    dict(product='Live', rate='71.6%', sample='0-0 at the break', since='2H Under 1.5 when 12 of 14 halves had one goal or fewer'),
    dict(product='Points sports', rate='unmeasured', sample='one weekend', since='11 of 14 agreement rule'),
]


# ---------------------------------------------------------------- console

YEST = {'state': 'idle', 'ts': 0, 'tiles': [], 'sample': [], 'lines': [], 'error': None}


def run_yesterday():
    """book_winners.yesterday_rows() in the background: the rule replayed over
    yesterday's whole board. Cached for 30 minutes."""
    if YEST['state'] == 'running':
        return False
    if YEST['ts'] and time.time() - YEST['ts'] < 1800 and YEST['tiles']:
        return True
    YEST.update(state='running', error=None)

    def work():
        try:
            import book_winners as BW
            rows = BW.yesterday_rows()
            summ = BW.yesterday_summary(rows)
            tiles = [dict(k='Games the rule backed', v=str(len(rows)))]
            for lab, n, w, wd in summ:
                tiles.append(dict(k=f"{lab.title()} won", v=f"{w * 100:.1f}%"))
                tiles.append(dict(k=f"{lab.title()} win-or-draw", v=f"{wd * 100:.1f}%"))
            sample = [dict(match=r['match'], league=r.get('league') or '', sel=('Home' if r['side'] == 'H' else 'Away') + ' backed by venue GD',
                           margin=f"{r['margin']:+.2f}", res='won' if r['won'] else ('draw' if r['draw'] else 'lost'), score=r['score'])
                      for r in rows]
            YEST.update(state='done', ts=time.time(), tiles=tiles, sample=sample,
                        lines=[f"{lab} n {n} won {w:.1%} win-or-draw {wd:.1%}" for lab, n, w, wd in summ],
                        error=None if len(rows) >= 30 else f"only {len(rows)} qualifying games with cached form")
        except Exception as e:
            YEST.update(state='done', error=f"{type(e).__name__}: {e}")
    threading.Thread(target=work, daemon=True).start()
    return True


COMMANDS = [
    dict(c='/book [target] [until]', d='Composite slip to a target price', ai=True),
    dict(c='/rollover', d='Follow the biggest slip that lands one time in three', ai=False),
    dict(c='/max [until]', d='Highest odds the gates allow', ai=True),
    dict(c='/goals [until]', d='Goals-only mode', ai=True),
    dict(c='/under [until]', d='Unders-only mode', ai=True),
    dict(c='/strict [until]', d="Highest odds, the opponent's record must confirm every team leg", ai=True),
    dict(c='/draw [until]', d='Draw gate, one slip', ai=False),
    dict(c='/hdraw [until]', d='Same gate, half-time draw (market 60)', ai=False),
    dict(c='/winners [until]', d="Market favourite, venue-backed, one slip", ai=False),
    dict(c='/live', d='Start the half-time watcher; codes come to this chat until /stop', ai=False),
    dict(c='/stop', d='Stop the watcher', ai=False),
    dict(c='/livelog', d='What the watcher has seen', ai=False),
    dict(c='/grade CODE ...', d='Grade share codes leg by leg', ai=False),
    dict(c='/sweep', d="Bank yesterday's results into the corpus", ai=False),
    dict(c='/slips [n]', d='Recent booked codes', ai=False),
    dict(c='/status', d='What the engine is doing', ai=False),
    dict(c='/whoami', d='Your chat id', ai=False),
]

ENDPOINTS = [
    dict(p='POST /api/run', q='target, until, days, engine, maxodds, goalsonly, undersonly, strict, rollover, dry'),
    dict(p='POST /api/winners', q='until, days, top, dry (runs book_winners.py with the yesterday check)'),
    dict(p='POST /api/draws', q='until, days, half, dry'),
    dict(p='POST /api/points', q='sport (amfoot | basketball | hockey | handball), days, min_price, agree, dry'),
    dict(p='POST /api/live', q='until, dry, chat | stop'),
    dict(p='POST /api/yesterday', q='replay the winners rule over yesterday\'s board'),
    dict(p='GET /api/status', q='job state, log, result, build stamp'),
    dict(p='GET /api/codes?day=', q='today | yesterday | tomorrow | YYYY-MM-DD, graded, with sheets'),
    dict(p='GET /api/gradecode?code=', q='any SportyBet share code, leg by leg'),
    dict(p='GET /api/record', q='per-product tallies, settled table, day heat, losses'),
    dict(p='GET /api/livefeed', q='live codes, the board being watched, log tail'),
    dict(p='GET /api/console', q='watcher, corpus, models, commands, coverage, booking log'),
    dict(p='GET /api/grade?codes=', q='the text grader'),
    dict(p='POST /api/crawl', q='cap, floor, mode'),
]

COVERAGE = [
    dict(sport='Football', form=1, stats=1, prices=1, note='46k matches, venue split, half splits, shots/corners/cards'),
    dict(sport='American football', form=1, stats=0, prices=1, note='quarter scores, no post-match stats on the feed'),
    dict(sport='Basketball', form=1, stats=0, prices=1, note='period scores only'),
    dict(sport='Ice hockey', form=1, stats=0, prices=1, note='period scores; regulation totals'),
    dict(sport='Handball', form=1, stats=0, prices=1, note='halves only'),
    dict(sport='Tennis', form=1, stats=0, prices=1, note='no engine'),
    dict(sport='Rugby', form=0, stats=0, prices=1, note='no form on the feed'),
]

_CORPUS = {'sig': None, 'rows': 0}


def corpus_rows():
    path = os.path.join(ROOT, 'experiments', 'dataset.jsonl')
    try:
        sig = (os.path.getsize(path), int(os.path.getmtime(path)))
    except OSError:
        return 0, None
    if sig != _CORPUS['sig']:
        n = 0
        with open(path, 'rb') as fh:
            for _ in fh:
                n += 1
        _CORPUS.update(sig=sig, rows=n)
    return _CORPUS['rows'], dt.datetime.fromtimestamp(sig[1], tz=WAT)


def _mtime(rel):
    try:
        return dt.datetime.fromtimestamp(os.path.getmtime(os.path.join(ROOT, rel)), tz=WAT)
    except OSError:
        return None


def _log_tail(rel, pat):
    try:
        lines = [l.strip() for l in open(os.path.join(ROOT, rel), encoding='utf-8', errors='replace') if l.strip()]
    except OSError:
        return ''
    for l in reversed(lines):
        if re.search(pat, l):
            return l.replace('saved hybrid_bundle.pkl: ', '').replace('saved nn_all_bundle.pkl: ', '')[:120]
    return ''


def models():
    out = []
    for name, rel, target, log, pat in (
            ('hybrid bundle', 'experiments/hybrid_bundle.pkl', 'goal markets (NN) + stat markets (XGBoost); the live composite engine', 'experiments/retrain_hybrid.log', r'^saved hybrid_bundle'),
            ('nn_all', 'experiments/nn_all_bundle.pkl', 'nets on every option family', 'experiments/retrain_nn_all.log', r'beats its baseline|^saved nn_all'),
            ('draw model', 'experiments/draw_model.pkl', 'draw probability; the gate rate sets the price floor', 'experiments/retrain_draw.log', r'%')):
        t = _mtime(rel)
        out.append(dict(name=name, target=target, trained=t.strftime('%-d %b %H:%M') if t else 'missing', metric=_log_tail(log, pat) or '-'))
    try:
        import book_draw as DRW
        out[-1]['metric'] = f"gate {DRW.RATE:.1%} on the pocket; held-out {DRW._B.get('test_precision', 0):.1%} (n {DRW._B.get('test_n')})"
    except Exception:
        pass
    return out


def livelog_rows(LIVE, n=40):
    """Every half-time state the watcher read: from experiments/live_ht_log.jsonl
    when the container still has it, else parsed from the watcher's log lines."""
    rows = []
    path = os.path.join(ROOT, 'experiments', 'live_ht_log.jsonl')
    try:
        lines = open(path, encoding='utf-8').read().splitlines()[-n:]
        for ln in lines:
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            st = r.get('stats') or {}
            parts = []
            if st.get('shots'): parts.append(f"shots {st['shots'][0]} v {st['shots'][1]}")
            if st.get('sot'): parts.append(f"on target {st['sot'][0]} v {st['sot'][1]}")
            if st.get('poss'): parts.append(f"possession {st['poss'][0]}%")
            t2 = r.get('trailing_2h') or []
            if t2: parts.append(f"trailing 2H {sum(x >= 1 for x in t2)}/{len(t2)} scored, {sum(x <= 1 for x in t2)}/{len(t2)} at <=1")
            legs = r.get('legs') or []
            act = ', '.join(f"{l[1]} @{l[2]:.2f}" for l in legs) if legs else ('no bet' + (' (gate game)' if r.get('gate') else ''))
            rows.append(dict(t=dt.datetime.fromtimestamp(r['ts'], tz=WAT).strftime('%H:%M'), match=r.get('match', ''), league=r.get('league') or '',
                             ht=(r.get('ht') or '').replace(':', '-'), stats=' \u00b7 '.join(parts) or 'no live stats', act=act))
    except OSError:
        pass
    if not rows:
        for ln in (LIVE.get('log') or [])[-n:]:
            m = re.match(r'^HT (\d+:\d+) (.+?): (.*)$', ln)
            if m:
                rows.append(dict(t='', match=m.group(2), league='', ht=m.group(1).replace(':', '-'), stats=m.group(3)[:140], act=''))
            m = re.match(r'^queued HT (\d+:\d+) (.+?): (.*)$', ln)
            if m:
                rows.append(dict(t='', match=m.group(2), league='', ht=m.group(1).replace(':', '-'), stats='', act='queued ' + m.group(3)))
    rows.reverse()
    return rows


def console_state(LIVE, JOB, BUILD):
    try:
        import live_ht as LH
        booked = len(set(LH.BOOKED.values())); read = len(LH.READ)
    except Exception:
        booked = read = 0
    n, crawled = corpus_rows()
    retrained = _mtime('experiments/draw_model.pkl')
    return dict(
        watcher=dict(state=LIVE.get('state'), started=LIVE.get('started'), chat=LIVE.get('chat'), bot=bot_name(),
                     halftimes_read=read, codes_booked=booked),
        job=dict(state=JOB.get('state'), started=JOB.get('started'), params=JOB.get('params'), result=JOB.get('result'), log=(JOB.get('log') or [])[-60:]),
        corpus=[dict(k='Matches in corpus', v=f"{n:,}"), dict(k='Last crawl', v=crawled.strftime('%-d %b %H:%M') if crawled else '-'),
                dict(k='Last retrain', v=retrained.strftime('%-d %b %H:%M') if retrained else '-'), dict(k='Build stamp', v=BUILD)],
        models=models(), livelog=livelog_rows(LIVE), commands=COMMANDS, endpoints=ENDPOINTS, coverage=COVERAGE,
        yesterday=dict(YEST),
        bookinglog=[dict(code=c['code'], product=c['product'], sport=c['sport'], nlegs=len(c['legs']), odds=round(_combo(c['legs']), 2) if c['legs'] else None,
                         booked=c['when'], url=c['url'], label=c['label']) for c in parse_bookings()[:15]])


# ---------------------------------------------------------------- daily schedule

SCHEDULE = [
    # (time WAT, job id, POST endpoint, body) - the codes are generated every day
    ('09:05', 'winners-am', '/api/winners', dict(until=23, days=0)),
    ('09:20', 'draws', '/api/draws', dict(until=23, days=0)),
    ('09:35', 'points-amfoot', '/api/points', dict(sport='amfoot', days=0)),
    ('09:40', 'points-basketball', '/api/points', dict(sport='basketball', days=0)),
    ('09:45', 'points-hockey', '/api/points', dict(sport='hockey', days=0)),
    ('09:50', 'points-handball', '/api/points', dict(sport='handball', days=0)),
    ('16:35', 'winners-pm', '/api/winners', dict(until=6, days=0)),
]


def _sched_path():
    vol = os.environ.get('BOOKINGS_PATH')
    return os.path.join(os.path.dirname(vol) if vol else ROOT, 'schedule_state.json')


def sched_state():
    try:
        return json.load(open(_sched_path()))
    except Exception:
        return {}


def sched_mark(job, note):
    st = sched_state()
    st[job] = dict(date=str(dt.datetime.now(tz=WAT).date()), at=dt.datetime.now(tz=WAT).strftime('%H:%M'), note=note)
    try:
        json.dump(st, open(_sched_path(), 'w'))
    except OSError:
        pass


GRACE_MIN = 90


def sched_due(now=None):
    """Jobs whose time has passed today (within the grace window) and that have
    not run today. A job older than the grace window is marked missed rather
    than run late - a container that comes up at 19:30 must not book the
    morning's slips."""
    now = now or dt.datetime.now(tz=WAT)
    st = sched_state(); due = []
    for hhmm, job, path, body in SCHEDULE:
        h, m = (int(x) for x in hhmm.split(':'))
        if st.get(job, {}).get('date') == str(now.date()):
            continue
        late = (now.hour * 60 + now.minute) - (h * 60 + m)
        if late < 0:
            continue
        if late > GRACE_MIN:
            sched_mark(job, f'missed (server was not up within {GRACE_MIN} min)')
            continue
        due.append((hhmm, job, path, body))
    return due


def sched_view():
    st = sched_state(); now = dt.datetime.now(tz=WAT)
    out = []
    for hhmm, job, path, body in SCHEDULE:
        last = st.get(job) or {}
        out.append(dict(time=hhmm, job=job, what=f"{path} {json.dumps(body)}", last=(f"{last.get('date')} {last.get('at')} - {last.get('note')}" if last else 'never'),
                        today=last.get('date') == str(now.date())))
    return out
