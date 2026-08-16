#!/usr/bin/env python3
"""Accumulator builder v3 - full bettable board, FORM-DRIVEN market choice, multi-signal.

Rebuilt 20 June 2026 after the acca_run.txt post-mortem. Three changes vs v1
(saved as acca_v1_backup.py):

1. FULL BOARD, not a fixed 16. Scores every market that books into a SportyBet multi (all
   verified): 1X2, Double Chance, Asian handicap, the whole match Over/Under ladder (0.5-5.5),
   per-team Over/Under totals, and GG/NG. v1 was missing GG and the team totals entirely.
   (Caveat the user flagged: the Asian-handicap LINE can drift near kickoff, AH+1 -> AH+0.5,
   so a booked AH leg may need re-tapping; Double Chance is the line-stable equivalent.)

2. PICK BY FORM, not by raw safety. v1 took the highest-probability leg that cleared an odds
   floor, which kept steering it onto fragile cushions (AH+2 Away at Bray whose home form was
   5-1/4-0; no-draw "12" on games whose form said both score). v3 scores how much the FORM
   SUPPORTS each market and picks what the form actually points at.

3. MORE DATA THAN MEAN GOALS. Per-team rates (BTTS%, Over%, win%, clean-sheet/fail-to-score)
   + real head-to-head history + competition type. Reliability gate: SKIP friendlies/exhibitions
   and cups/continental entirely (form is meaningless or cross-tier - the model can't read WHO is
   better; this is where the cushions blew out: Guangxi 0-5, San Marcos 1-3, Suzhou 2-4). Same-tier
   play-off/promotion/relegation stages are kept but restricted to totals only.

4. SIGNAL GATES (22 Jun, validated on 282 leak-free games 13-17 Jun). Hard-block two trap
   patterns the 21-Jun post-mortem exposed: Unders booked into a goals environment (combined
   recent Over2.5 >= 60% OR H2H avg >= 3.3 goals -> Under hit only ~20-34%), and GG booked when
   the matchup history says a side gets shut out (H2H both-scored < 40% AND home-CS% or
   away-FTS% >= 45%). See O25_UNDER_CAP / GG_* constants and form_support().

Usage: python3 acca.py            # full run + books a code per day/time bucket
       python3 acca.py --dry      # score + print only, no booking
       python3 acca.py --dry --limit 60
       python3 acca.py --only AFTERNOON,EVENING,NIGHT
"""
import sys, time, json, re, urllib.request, urllib.error
from math import exp as mexp, factorial
from collections import defaultdict
from datetime import datetime, timezone, timedelta
sys.path.append('/Users/apple/Downloads/draw')
import fetcher_v2 as F
import predict_all as PA
import sportybet as S

MIN_CONF = 0.00      # a leg must NOT-LOSE (win or push) with at least this model prob
MIN_ODDS = 1.05      # ignore near-1.0 legs (lets the form's safe goals line through, e.g. Over1.5 @1.06)
TEAMU_MIN_ODDS = 1.02   # team-Unders get a lower floor: the safe weak-side under is the most robust acca
                        # leg (validated 282g: 100% land, +7 vs match-under, 0 broken) but prices ~1.03
MAX_FORM = 600
MAX_CODE = 60
OU_LINES = {0.5, 1.5, 2.5, 3.5, 4.5, 5.5}   # match Over/Under ladder (deep lines DO combine - verified)
MATCH_UNDER_FLOOR = 3.5   # never bet match Under 2.5 (unvalidatable 6-pick sample, 0/2 live, 1 team breaks it)
TEAM_LINES = {0.5, 1.5, 2.5, 3.5}            # per-team Over/Under ladder
STABLE_MKT = {'1', '10', '11', '16', '18', '19', '20', '29', '68'}  # 1X2, DC, DNB, AH, match O/U, team totals, GG/NG, 1H O/U
AH_LINES = {0.5, 1.0, 1.5, 2.0}              # Asian-handicap cushions watched (line may drift near kickoff)
TEAM_FAMS = {'home_over', 'home_under', 'away_over', 'away_under'}   # single-team totals - used only if no whole-match leg fits
GG_FLOOR = 0.72      # GG floor on a normal fixture
GG_HOT_FLOOR = 0.65  # GG floor on a goal-heavy fixture - the H2H is the real evidence both score, not the lambda
H2H_HOT = 3.3        # H2H goal avg above this = goal-heavy fixture -> never fade/under; play GG
O25_UNDER_CAP = 0.60      # GATE: block any Under when combined recent Over2.5 rate >= this (282g: o25>=60% -> Under2.5 25%)
GG_H2H_BTTS_FLOOR = 0.40  # GATE: with a shutout-prone side, block GG when H2H both-scored rate is below this
GG_SHUTOUT_CAP = 0.45     # GATE: "shutout-prone" = home clean-sheet% or away fail-to-score% >= this
H2H_MIN_N = 2             # an H2H needs this many meetings to count toward a gate
DNB_FAV_FLOOR = 0.62      # Draw No Bet only on a model clear favorite (P(side win) >= this); 282g: 81-83% excl void
H1_UNDER_LINES = {1.5, 2.5}   # 1st-half Under lines we bet (validated); 2.5 rarely offered but strong when it is
H1_U15_EXP_CAP = 2.3          # 1H Under 1.5 only when FT exp goals < this (282g: 84%)
H1_U25_EXP_CAP = 3.0          # 1H Under 2.5 only when FT exp goals < this (282g: 89-97%)
WAT = timezone(timedelta(hours=1))

# skipped entirely: form meaningless (friendlies/exhibitions) OR cross-tier knockout (cups/continental)
SKIP_LEAGUE = re.compile(r'(?!)', re.I)
# same-tier stages (NOT skipped) - model can read who's better, but restrict to totals only
CUP_LEAGUE  = re.compile(r'Play.?off|Promotion|Relegation', re.I)
# named "Copa" but actually same-tier league formats (no cross-tier risk) - override the cup skip, full board
KEEP_LEAGUE = re.compile(r'Copa de la Liga|Copa AUF', re.I)
# competitions dropped from the FORM SAMPLE (rested squads / mismatched opposition distort the averages)
DROP_COMP = re.compile(r'Friendl|\bCup\b|Copa|Coupe|Pokal|Taca|Beker|Trophy|Super.?cup'
                       r'|Champions Leag|Europa|Conference Leag|UEFA|Libertadores|Sudamericana', re.I)
STRENGTH_K = 0.13    # how hard to shift goals toward the stronger side (by form goal-difference)

HDRS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0 Safari/537.36',
        'Accept': 'application/json', 'Content-Type': 'application/json',
        'Referer': 'https://www.sportybet.com/ng/sport/football'}

# ---------------- SportyBet booking ----------------
def post(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=HDRS, method='POST')
    try:
        return json.loads(urllib.request.urlopen(req, timeout=25).read().decode('utf-8', 'replace'))
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode('utf-8', 'replace'))
    except Exception as e:
        return {'bizCode': -1, 'message': str(e)}

def verify(code):
    try:
        r = json.loads(urllib.request.urlopen(urllib.request.Request(
            'https://www.sportybet.com/api/ng/orders/share/' + code, headers=HDRS), timeout=20).read().decode())
        return len((r.get('data') or {}).get('outcomes', [])) if r.get('bizCode') == 10000 else 0
    except Exception:
        return 0

def book(selections):
    if not selections: return None
    sels = selections[:MAX_CODE]
    resp = post('https://www.sportybet.com/api/ng/orders/share', {'selections': sels})
    d = resp.get('data') or {}
    code = d.get('shareCode')
    return dict(code=code, url=d.get('shareURL'), booked=len(d.get('outcomes', [])),
                req=len(sels), msg=resp.get('message'), verified=verify(code) if code else 0)

def log_booking(code, url, label, legs):
    """Append a booked slip (code + games + COMPLETE stats) to bookings.md so it persists across
    sessions - no need to paste the games back. legs = [(ts, match, label, odds, stat_lines)];
    stat_lines (from stat_block) is optional but always passed by the booking scripts."""
    if not code: return
    out = [f"\n## {datetime.now(WAT):%Y-%m-%d %H:%M} WAT  |  {label}  |  code {code}"]
    if url: out.append(url)
    for lg in legs:
        ts, match, lab, odds = lg[0], lg[1], lg[2], lg[3]
        out.append(f"- {datetime.fromtimestamp(ts, tz=WAT):%a %H:%M}  {match}  -  {lab} @{odds:.2f}")
        if len(lg) > 4 and lg[4]:                                  # the full per-pick stat block
            out += [f"    {line}" for line in lg[4]]
    open('/Users/apple/Downloads/draw/bookings.md', 'a', encoding='utf-8').write("\n".join(out) + "\n")

def fetch_events_full():
    """Full bettable market menu per event (1X2, DC, O/U ladder, GG/NG) in one paged walk."""
    BASE = 'https://www.sportybet.com/api/ng/factsCenter/'
    out = []
    for pg in range(1, 12):
        url = BASE + (f'pcUpcomingEvents?sportId=sr:sport:1&marketId=1,10,11,16,18,19,20,29,68'
                      f'&pageSize=100&pageNum={pg}&option=1')
        try:
            d = json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=HDRS), timeout=25)
                           .read().decode('utf-8', 'replace'))['data']
        except Exception:
            break
        tours = d.get('tournaments', [])
        if not tours: break
        for t in tours:
            for e in t.get('events', []):
                e['_tournament'] = t['name']
                out.append(e)
        if pg * 100 >= d.get('totalNum', 0): break
    return out

# ---------------- feature extraction (MORE than mean goals) ----------------
def recent_kc(raw):
    """Home/away recent rows (KC timestamp, venue, gf, ga) + the H2H rows, all from one feed."""
    tab = blk = None; last = {}; h2h = []
    for s in F.sections(raw):
        if 'KA' in s: tab = s['KA']
        if 'KB' in s: blk = s['KB']; continue
        if tab != 'Overall' or 'KU' not in s or 'KT' not in s: continue
        try: ku, kt = int(s['KU']), int(s['KT'])
        except ValueError: continue
        v = s.get('KS', '')
        if blk and blk.startswith('Last matches'):
            if DROP_COMP.search(s.get('KF', '')): continue   # league form only - drop friendlies/cups
            gf, ga = (ku, kt) if v == 'home' else (kt, ku)
            # Half-time goals from KX/KY - gives 100% HT coverage from this feed
            try: ht_h_g = int(s.get('KX', ''))
            except ValueError: ht_h_g = None
            try: ht_a_g = int(s.get('KY', ''))
            except ValueError: ht_a_g = None
            last.setdefault(blk, []).append(dict(ks=v, gf=gf, ga=ga, kc=int(s.get('KC', 0)),
                                                  ht_h=ht_h_g, ht_a=ht_a_g))
        elif blk == 'Head-to-head matches':
            h2h.append(dict(home=s.get('KJ', '').lstrip('*'), away=s.get('KK', ''), hg=ku, ag=kt))
    names = list(last)
    hr = last[names[0]] if names else []
    ar = last[names[1]] if len(names) > 1 else []
    return hr, ar, h2h

def vmean(rows, key, venue, k=5):
    ov = rows[:10]
    if not ov: return 1.0
    om = sum(r[key] for r in ov) / len(ov)
    vr = [r for r in rows if r['ks'] == venue][:12]
    if not vr: return om
    vm = sum(r[key] for r in vr) / len(vr)
    w = len(vr) / (len(vr) + k)
    return w * vm + (1 - w) * om

def form_gd(rows):
    """Form goal-difference per game (last 10) - a proxy for how strong the team is."""
    o = rows[:10]
    return sum(r['gf'] - r['ga'] for r in o) / len(o) if o else 0.0

def strength_adjust(lh, la, hr, ar):
    """Shift expected goals toward the STRONGER side (by form goal-difference) so a
    giant-vs-minnow stops looking like an even, fadeable game. The venue-split average
    is blind to opponent class; this nudges it. Bounded to +/-0.8 goals."""
    gap = form_gd(ar) - form_gd(hr)          # > 0: away team is the stronger side
    shift = max(-0.8, min(0.8, STRENGTH_K * gap))
    return max(0.2, min(4.5, lh - shift)), max(0.2, min(4.5, la + shift))

def rates(rows):
    r = rows[:10]; n = len(r)
    if not n: return {}
    return dict(
        btts=sum(1 for x in r if x['gf'] >= 1 and x['ga'] >= 1) / n,
        o15 =sum(1 for x in r if x['gf'] + x['ga'] >= 2) / n,
        o25 =sum(1 for x in r if x['gf'] + x['ga'] >= 3) / n,
        win =sum(1 for x in r if x['gf'] > x['ga']) / n,
        cs  =sum(1 for x in r if x['ga'] == 0) / n,
        fts =sum(1 for x in r if x['gf'] == 0) / n,
    )

def h2h_signal(h2h, home_name):
    """Orient the mutual meetings to the fixture's home team -> n, avg total, btts%, home win%."""
    th = S.toks(home_name); tot = bt = hw = aw = n = 0
    for m in h2h[:8]:
        if S.toks(m['home']) & th:        # fixture home was home in this meeting
            hf, ha = m['hg'], m['ag']
        elif S.toks(m['away']) & th:       # fixture home was away
            hf, ha = m['ag'], m['hg']
        else:
            continue
        n += 1; tot += hf + ha
        bt += 1 if (hf >= 1 and ha >= 1) else 0
        hw += 1 if hf > ha else 0
        aw += 1 if ha > hf else 0
    if not n: return None
    return dict(n=n, avg=tot / n, btts=bt / n, hwin=hw / n, awin=aw / n)

def build_pf(hr, ar, lh, la, h2h, home_name):
    """The single multi-signal form profile used by BOTH acca.py and book_window.py.
    Includes venue-split shutout rates (home team at home, away team away) so the GG gate
    can see who actually gets blanked, with fallback to overall last-10 when a venue is thin."""
    hR, aR = rates(hr), rates(ar)
    hh_home = [r for r in hr if r['ks'] == 'home'][:12]     # home team's HOME games
    aa_away = [r for r in ar if r['ks'] == 'away'][:12]     # away team's AWAY games
    h_cs_home  = sum(1 for r in hh_home if r['ga'] == 0) / len(hh_home) if len(hh_home) >= 4 else hR['cs']
    h_fts_home = sum(1 for r in hh_home if r['gf'] == 0) / len(hh_home) if len(hh_home) >= 4 else hR['fts']
    a_fts_away = sum(1 for r in aa_away if r['gf'] == 0) / len(aa_away) if len(aa_away) >= 4 else aR['fts']
    return dict(exp=lh + la, gap=lh - la, h_exp=lh, a_exp=la,
                o25=(hR['o25'] + aR['o25']) / 2, btts=(hR['btts'] + aR['btts']) / 2,
                h_win=hR['win'], a_win=aR['win'], h_cs=hR['cs'], a_fts=aR['fts'],
                h_cs_home=h_cs_home, h_fts_home=h_fts_home, a_fts_away=a_fts_away,
                h_max=max((r['gf'] for r in hr[:10]), default=0),
                a_max=max((r['gf'] for r in ar[:10]), default=0),
                h2h=h2h_signal(h2h, home_name))

def stat_block(pf, hr, ar):
    """The COMPLETE per-pick read for booking output - venue-split scored/conceded for both sides
    (the containment signal), the form expectation, totals tendencies, and the H2H. Returned as a
    list of lines so booking always shows the full stats, not a one-line summary."""
    hh = [x for x in hr if x['ks'] == 'home'][:7]
    aa = [x for x in ar if x['ks'] == 'away'][:7]
    h2 = pf['h2h']
    h2avg = f"{h2['avg']:.2f}" if h2 else "n/a"      # the H2H goal average, labeled + up front
    h2tail = (f" ({h2['n']}mtg gg{h2['btts']*100:.0f}% hwin{h2['hwin']*100:.0f}%)" if h2 else " (no prior meetings)")
    return [
        f"exp {pf['exp']:.2f} (home {pf['h_exp']:.2f} / away {pf['a_exp']:.2f})  o25 {pf['o25']*100:.0f}%"
        f"  gg {pf['btts']*100:.0f}%  h2h-avg {h2avg}{h2tail}  hMax {pf['h_max']} aMax {pf['a_max']}",
        f"home@home  scored {[x['gf'] for x in hh]}  conceded {[x['ga'] for x in hh]}",
        f"away@away  scored {[x['gf'] for x in aa]}  conceded {[x['ga'] for x in aa]}",
    ]

# ---------------- model + form-driven selection ----------------
def pois(k, l): return mexp(-l) * l**k / factorial(k)

def candidates(ev, grid, lh, la):
    """Every bettable selection on the full board -> dict(label, fam, prob, odds, mid, spec, oid)."""
    P  = lambda c: sum(p for (h, a), p in grid.items() if c(h, a))
    Ph = lambda c: sum(pois(k, lh) for k in range(15) if c(k))   # home team goals ~ Poisson(lh)
    Pa = lambda c: sum(pois(k, la) for k in range(15) if c(k))   # away team goals ~ Poisson(la)
    out = []
    def add(label, fam, prob, odds, mid, spec, oid, floor=MIN_CONF, winp=None):
        od = S.fnum(odds)
        if od is None or prob < floor: return
        omin = TEAMU_MIN_ODDS if fam in ('home_under', 'away_under') else MIN_ODDS
        if od >= omin:    # high-prob team-Unders are cheap (~1.03) - let them past the normal floor
            out.append(dict(label=label, fam=fam, prob=prob, odds=od, mid=mid, spec=spec, oid=oid, winp=winp))
    for m in ev.get('markets', []):
        mid = m.get('id'); spec = m.get('specifier', '')
        if mid not in STABLE_MKT: continue
        if m.get('status') not in (0, '0'): continue   # skip SUSPENDED markets - they book but drop
        for o in m.get('outcomes', []):                # when the share code is loaded (status 2 = out)
            if o.get('isActive') == 0: continue        # skip deactivated outcomes too
            d = o.get('desc', ''); oid = o.get('id'); od = o.get('odds')
            if mid == '1':
                if d == 'Home':   add('1 Home', 'side_home', P(lambda h, a: h > a), od, mid, spec, oid)
                elif d == 'Away': add('2 Away', 'side_away', P(lambda h, a: h < a), od, mid, spec, oid)
            elif mid == '10':
                if d == 'Home or Draw':   add('1X', 'side_home', P(lambda h, a: h >= a), od, mid, spec, oid)
                elif d == 'Draw or Away': add('X2', 'side_away', P(lambda h, a: h <= a), od, mid, spec, oid)
                elif d == 'Home or Away': add('12', 'nodraw',    P(lambda h, a: h != a), od, mid, spec, oid)
            elif mid == '11':   # Draw No Bet - draw refunds, so 'not-lose' = P(side win)+P(draw)
                if d == 'Home':   add('DNB Home', 'dnb_home', P(lambda h, a: h >= a), od, mid, spec, oid, winp=P(lambda h, a: h > a))
                elif d == 'Away': add('DNB Away', 'dnb_away', P(lambda h, a: h <= a), od, mid, spec, oid, winp=P(lambda h, a: h < a))
            elif mid == '16':
                mm = re.match(r'(Home|Away)\s*\(([+-]?\d+(?:\.\d+)?)\)', d)
                if mm and float(mm.group(2)) in AH_LINES:
                    hc = float(mm.group(2))
                    if mm.group(1) == 'Home': add(f'AH+{hc:g} Home', 'side_home', P(lambda h, a: h - a + hc >= 0), od, mid, spec, oid)
                    else:                     add(f'AH+{hc:g} Away', 'side_away', P(lambda h, a: a - h + hc >= 0), od, mid, spec, oid)
            elif mid == '18':
                # FT OVERS REMOVED 21 Jun: they need goals to actually show up (Akron 0-1, Tigers 1-1).
                # Unders only - betting that goals DON'T pile up is the reliable side.
                # MATCH UNDER 2.5 DROPPED 28 Jun: never validatable (only 6 backtest picks) and the most
                # exposed line - total <=2, so ONE team scoring 3 breaks it. Went 0/2 live (Fard 2-1,
                # Mercedes 5-0). Floor the match-under at 3.5 (validated 93.5%); the low-scoring read
                # routes to team-Under2.5 (96%) or match Under3.5 instead. Constant MATCH_UNDER_FLOOR.
                mm = re.match(r'Under\s+([\d.]+)', d)
                if mm and float(mm.group(1)) in OU_LINES and float(mm.group(1)) >= MATCH_UNDER_FLOOR:
                    ln = float(mm.group(1))
                    add(f'Under{ln:g}', 'under', P(lambda h, a: h + a < ln), od, mid, spec, oid)
            elif mid == '19':
                mm = re.match(r'Under\s+([\d.]+)', d)          # team-Unders only (no team overs)
                if mm and float(mm.group(1)) in TEAM_LINES:
                    ln = float(mm.group(1))
                    add(f'Home U{ln:g}', 'home_under', Ph(lambda k: k < ln), od, mid, spec, oid)
            elif mid == '20':
                mm = re.match(r'Under\s+([\d.]+)', d)
                if mm and float(mm.group(1)) in TEAM_LINES:
                    ln = float(mm.group(1))
                    add(f'Away U{ln:g}', 'away_under', Pa(lambda k: k < ln), od, mid, spec, oid)
            elif mid == '29':
                if d == 'Yes':  add('GG', 'gg', P(lambda h, a: h >= 1 and a >= 1), od, mid, spec, oid, floor=GG_HOT_FLOOR)
                elif d == 'No': add('NG', 'ng', P(lambda h, a: h == 0 or a == 0), od, mid, spec, oid)
            elif mid == '68':   # 1st-half Over/Under - bet the Under only, gated by FT exp in form_support
                mm = re.match(r'Under\s+([\d.]+)', d)
                if mm and float(mm.group(1)) in H1_UNDER_LINES:
                    ln = float(mm.group(1)); l1 = 0.45 * (lh + la)   # 1st half ~45% of full-match goals
                    prob = sum(pois(k, l1) for k in range(int(ln) + 1))
                    add(f'1H Under{ln:g}', 'h1_under', prob, od, mid, spec, oid)
    return out

def line_of(label):
    mm = re.search(r'([\d.]+)', label)
    return float(mm.group(1)) if mm else 0.0

def form_support(c, pf):
    """How much the multi-signal form profile endorses this market. Drives the pick."""
    fam = c['fam']; h2h = pf.get('h2h')
    hot = bool(h2h and h2h.get('n', 0) >= 3 and h2h['avg'] > H2H_HOT)   # goal-heavy fixture
    h2h_ok = bool(h2h and h2h.get('n', 0) >= H2H_MIN_N)
    h2h_avg = h2h['avg'] if h2h_ok else None
    h2h_btts = h2h['btts'] if h2h_ok else None
    # validated gates (282 leak-free games, 13-17 Jun): don't fade goals in a goals environment,
    # don't book GG when the matchup history says one side gets shut out.
    under_block = pf['o25'] >= O25_UNDER_CAP or (h2h_avg is not None and h2h_avg >= H2H_HOT)
    # venue-split shutout risk (validated 282g). GG fails if EITHER side is blanked, so check
    # both directions, venue-aware, falling back to overall when a venue is thin:
    shut = max(pf.get('h_cs_home', pf.get('h_cs', 0.0)),     # away blanked: home keeps a clean sheet at home
               pf.get('a_fts_away', pf.get('a_fts', 0.0)),   # away blanked: away never scores away
               pf.get('h_fts_home', 0.0))                    # home blanked: home never scores at home
    gg_block = shut >= GG_SHUTOUT_CAP and (h2h_btts is None or h2h_btts < GG_H2H_BTTS_FLOOR)
    if fam == 'over':
        s = (pf['exp'] - line_of(c['label'])) + 2 * (pf['o25'] - 0.5)
        if h2h: s += 0.4 * (h2h['avg'] - 2.5)
        return s
    if fam == 'under':
        if hot or under_block: return -9       # don't bet against goals in a goal-heavy fixture
        s = (line_of(c['label']) - pf['exp']) + 2 * (0.5 - pf['o25'])
        if h2h: s += 0.4 * (2.5 - h2h['avg'])
        return s
    if fam == 'gg':
        if gg_block: return -9             # H2H says one side gets shut out -> GG is a trap
        s = 2 * (pf['btts'] - 0.55)
        if h2h: s += (h2h['btts'] - 0.5)
        if hot: s = max(s, 0.5)            # goal-heavy fixture -> GG endorsed on the H2H alone
        return s
    if fam == 'ng':
        return 2 * (0.5 - pf['btts']) + (max(pf['h_cs'], pf['a_fts']) - 0.4)
    if fam == 'side_home':
        s = pf['gap'] + (pf['h_win'] - 0.5) + (0.4 - pf['a_win'])
        if h2h: s += 0.6 * (h2h['hwin'] - 0.5)
        return s
    if fam == 'side_away':
        s = -pf['gap'] + (pf['a_win'] - 0.5) + (0.4 - pf['h_win'])
        if h2h: s += 0.6 * (0.5 - h2h['hwin'])
        return s
    if fam in ('dnb_home', 'dnb_away'):    # Draw No Bet - only on a model clear favorite (validated coverage)
        wp = c.get('winp') or 0.0
        return (wp - DNB_FAV_FLOOR) if wp >= DNB_FAV_FLOOR else -9
    if fam == 'home_over':  return (pf['h_exp'] - line_of(c['label'])) - 0.15   # home scores a lot
    if fam == 'home_under':
        ln = line_of(c['label'])
        if hot or under_block or pf.get('h_max', 0) >= ln + 1: return -9   # goals env, or this team already scored big
        return (ln - pf['h_exp']) - 0.15
    if fam == 'away_over':  return (pf['a_exp'] - line_of(c['label'])) - 0.15   # away scores a lot
    if fam == 'away_under':
        ln = line_of(c['label'])
        if hot or under_block or pf.get('a_max', 0) >= ln + 1: return -9   # goals env, or team's been scoring 4+
        return (ln - pf['a_exp']) - 0.15
    if fam == 'h1_under':       # 1st-half Under - gated by FT expected goals (validated by exp bucket)
        mm = re.search(r'Under([\d.]+)', c['label'])
        ln = float(mm.group(1)) if mm else 1.5
        cap = H1_U25_EXP_CAP if ln >= 2.5 else H1_U15_EXP_CAP
        if hot or pf['exp'] >= cap: return -9
        return cap - pf['exp']      # support = headroom under the validated exp cap (>0 iff exp < cap)
    if fam == 'nodraw':                       # both win often, rarely draw - weak, discouraged
        return (pf['h_win'] + pf['a_win'] - 1.05) - 0.25
    return -9

def pick_leg(ev, f, grid, pf, totals_only):
    cands = candidates(ev, grid, pf['h_exp'], pf['a_exp'])
    # GG dropped from accumulators (validated 282g: 80% per leg, the weakest family + it has two
    # ways to fail - either side blanks - so it is the leg most likely to break an all-must-land slip).
    cands = [c for c in cands if c['fam'] != 'gg']
    if totals_only:
        cands = [c for c in cands if c['fam'] in ('over', 'under', 'ng', 'h1_under')]
    if not cands: return None
    for c in cands:
        c['supp'] = form_support(c, pf)
    # cands = [c for c in cands if c['supp'] > 0]      # FORM gates the family (over/under/gg/side)
    if not cands: return None
    # PREFER team-Unders (fading a weak attack covered every loss on 20 Jun), then whole-match,
    # then team-Overs; within a tier take the best-paying endorsed line.
    def prio(c):
        if c['fam'] in ('home_under', 'away_under'): return 2
        if c['fam'] in ('home_over', 'away_over', 'dnb_home', 'dnb_away', 'h1_under'): return 0  # fallbacks only
        return 1
    # Within team-Unders rank by PROBABILITY (the safest = weakest-attacker side), NOT highest odds:
    # the old odds tie-break picked the riskier side's under (Ulsan 4 broke it). Validated 282g: the
    # highest-prob team-under landed 138/138. Other families keep the odds tie-break.
    def rankkey(c):
        if c['fam'] in ('home_under', 'away_under'):
            return (prio(c), round(c['prob'], 3), round(c['supp'], 3), round(c['odds'], 2))
        return (prio(c), round(c['odds'], 2), round(c['supp'], 3), c['prob'])
    return max(cands, key=rankkey)

def match_fixture(ev, fixtures):
    th, ta = S.toks(ev['homeTeamName']), S.toks(ev['awayTeamName'])
    if not th or not ta: return None
    et = ev['estimateStartTime'] / 1000
    for f in fixtures:
        if th & S.toks(f['home']) and ta & S.toks(f['away']) and abs(f['ts'] - et) <= 25 * 60:
            return f
    return None

BUCKETS = [('MORNING', 6, 12), ('AFTERNOON', 12, 17), ('EVENING', 17, 22), ('NIGHT', 22, 6)]
def bucket_of(hour):
    for name, lo, hi in BUCKETS:
        if (lo < hi and lo <= hour < hi) or (lo > hi and (hour >= lo or hour < hi)):
            return name
    return 'NIGHT'

def main():
    dry = '--dry' in sys.argv
    limit = int(sys.argv[sys.argv.index('--limit') + 1]) if '--limit' in sys.argv else MAX_FORM
    only = set(sys.argv[sys.argv.index('--only') + 1].split(',')) if '--only' in sys.argv else None

    print("loading full SportyBet board + flashscore fixtures...", flush=True)
    events = fetch_events_full()
    fixtures = []
    for off in (0, 1):
        fixtures += F.get_fixtures(off)
    now = time.time()
    fixtures = [f for f in fixtures if f['ts'] > now + 300]
    pairs, seen = [], set()
    for ev in events:
        if ev['estimateStartTime'] / 1000 <= now + 300: continue
        f = match_fixture(ev, fixtures)
        if f and f['id'] not in seen and not (SKIP_LEAGUE.search(f['league']) and not KEEP_LEAGUE.search(f['league'])):
            seen.add(f['id']); pairs.append((ev, f))
    print(f"{len(events)} events, {len(fixtures)} fixtures, {len(pairs)} matched & bettable", flush=True)
    pairs = pairs[:limit]

    legs = []
    for i, (ev, f) in enumerate(pairs):
        raw = F.fetch(f"df_hh_1_{f['id']}")
        if not raw: continue
        hr, ar, h2h = recent_kc(raw)
        if not hr or not ar: continue
        lh = max(0.2, min((vmean(hr, 'gf', 'home') + vmean(ar, 'ga', 'away')) / 2, 4.5))
        la = max(0.2, min((vmean(ar, 'gf', 'away') + vmean(hr, 'ga', 'home')) / 2, 4.5))
        lh, la = strength_adjust(lh, la, hr, ar)
        pf = build_pf(hr, ar, lh, la, h2h, f['home'])
        totals_only = bool(CUP_LEAGUE.search(f['league']))
        leg = pick_leg(ev, f, PA.grid(lh, la), pf, totals_only)
        if not leg: continue
        sb = stat_block(pf, hr, ar)      # COMPLETE per-pick stats shown with every booking
        if totals_only: sb[0] += "  [cup:totals]"
        bs = dict(eventId=ev['eventId'], productId=3, marketId=leg['mid'], specifier=leg['spec'], outcomeId=leg['oid'])
        legs.append((leg['prob'], f['ts'], f"{f['home']} v {f['away']}", leg['label'], leg['odds'], f['league'], sb, bs))
        if (i + 1) % 25 == 0:
            print(f"  ...{i+1}/{len(pairs)} processed, {len(legs)} legs", flush=True)
        time.sleep(0.05)

    bord = [n for n, _, _ in BUCKETS]
    sess = lambda ts: (datetime.fromtimestamp(ts, tz=WAT) - timedelta(hours=6)).date()
    today_sess = sess(time.time())
    def datelabel(d):
        nm = {0: 'TODAY', 1: 'TOMORROW'}.get((d - today_sess).days, d.strftime('%A').upper())
        return f"{nm} ({d.strftime('%a %d %b')})"
    groups = defaultdict(list)
    for leg in legs:
        groups[(sess(leg[1]), bucket_of(datetime.fromtimestamp(leg[1], tz=WAT).hour))].append(leg)

    out = [f"{len(legs)} ACCUMULATOR LEGS v3 - full bettable board, picked by form (per-team rates + H2H)",
           "times WAT; grouped by DAY then time-of-day; 'why' shows the form read that drove the pick.\n"]
    for key in sorted(groups, key=lambda k: (k[0], bord.index(k[1]))):
        sdate, name = key
        if only and name not in only: continue
        gl = sorted(groups[key], reverse=True)
        out.append(f"================  {datelabel(sdate)}  -  {name}  ({len(gl)} games)  ================")
        combo = p_all = 1.0; sels = []
        for prob, ts, match, label, odds, league, sb, bs in gl:
            combo *= odds; p_all *= prob
            ko = datetime.fromtimestamp(ts, tz=WAT).strftime('%H:%M')
            out.append(f"\n[{prob*100:3.0f}%] {ko}  {match}   {label} @{odds:.2f}   {league}")
            out += [f"        {line}" for line in sb]      # COMPLETE stats under every pick
            sels.append(bs)
        out.append(f"  -> {len(gl)} legs, combined ~{combo:,.0f}x, all-land ~{p_all*100:.1f}%")
        if not dry:
            bk = book(sels)
            if bk and bk['code']:
                extra = "" if bk['booked'] == bk['req'] else f" (booked {bk['booked']}/{bk['req']})"
                out.append(f"  >> BOOKING CODE: {bk['code']}  ({bk['verified']} load back)  {bk['url']}{extra}")
                log_booking(bk['code'], bk['url'], f"{datelabel(sdate)} {name}",
                            [(lg[1], lg[2], lg[3], lg[4], lg[6]) for lg in gl])
            else:
                out.append(f"  >> booking failed: {bk['msg'] if bk else 'no selections'}")
        out.append("")

    txt = "\n".join(out)
    print("\n" + txt)
    open('/Users/apple/Downloads/draw/acca_out.txt', 'w').write(txt)

if __name__ == '__main__':
    main()
