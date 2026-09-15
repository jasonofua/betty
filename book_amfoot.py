#!/usr/bin/env python3
"""Points sports - totals, first-period totals and handicaps, no winners.
One engine for American football, basketball, ice hockey and handball (--sport).

Data: Flashscore sport 5 (form feed df_hh_5: each side's last 7 games at its
venue; df_sur_5: quarter scores per past game -> first-half totals). No team
statistics exist on the feed for this sport, so scores are all we have.
Book: SportyBet sr:sport:16, markets 225 FT O/U, 68 1H O/U, 223 Handicap.

Rule (12 Sep, first look - NOT a measured instrument yet):
  - a line qualifies when the two venue histories agree on >= AGREE of 14
    (home side's 7 home games + away side's 7 away games)
  - games the book prices at <= MISMATCH_PRICE on the winner are skipped
    outright: the histories on both sides are against a different class of
    opponent (FCS visitors at FBS hosts) and say nothing about the meeting
  - one leg per game, the strongest line; prefer the middle of a ladder
  - one slip, capped at acca.MAX_CODE

    python3 book_amfoot.py [--days 1] [--dry] [--agree 11]
"""
import sys, re, json, urllib.request, datetime as dt, collections
import acca as A
import fetcher_v3 as F

BASE = 'https://www.sportybet.com/api/ng/factsCenter/'
# 13 Sep: one engine, four sports. Everything below is the same read - each side's
# last 7 games at its venue, first-period totals from the period-score feed, a
# line qualifies when the two histories agree - keyed by a sport config.
SPORTS = {
    'amfoot':     dict(fs=5,  sb='sr:sport:16', board='1,18,60',     winner='219', ft='225', hcp='223', first='68',
                       periods=4, first_periods=2, blowout=30, reg_only=False, label='American football'),
    'basketball': dict(fs=3,  sb='sr:sport:2',  board='219,225,223', winner='219', ft='225', hcp='223', first='68',
                       periods=4, first_periods=2, blowout=20, reg_only=False, label='Basketball'),
    'hockey':     dict(fs=4,  sb='sr:sport:4',  board='1,18,16',     winner='1',   ft='18',  hcp='16',  first='446',
                       periods=3, first_periods=1, blowout=4,  reg_only=True,  label='Ice hockey'),
    'handball':   dict(fs=7,  sb='sr:sport:6',  board='1,18,16',     winner='1',   ft='18',  hcp='16',  first='68',
                       periods=2, first_periods=1, blowout=10, reg_only=False, label='Handball'),
}
SPORT = SPORTS['amfoot']
AGREE = 11
MIN_PRICE = 1.40          # user wants the 1.6-2.2 band on these sports; 1.14 hockey Overs are not it. --min-price
MISMATCH_PRICE = 1.05
SKIP_MISMATCH = False     # user, 12 Sep: never skip - find the better option instead
SEASON_START = dt.datetime(2026, 8, 20).timestamp()
# 12 Sep evening, after Wagner +45.5 (11/14) lost 80:3 and Wash State +18.5
# (13/14) lost 34:7. Two changes, neither a skip:
#   MISMATCH games (winner <= 1.05): the mixed 14-game history is against
#   a different class of opponent. Score the lines against the games that
#   LOOK like this one instead - the favourite's blowouts (won by 30+) and
#   the underdog's heaviest defeats (lost by 30+). Indiana's blowouts said
#   Howard +62.5 4/4 (won); JMU's and Wagner's said +45.5 1/5 (lost).
#   STALE college rosters (fewer than 2 this-season venue games a side): a
#   handicap is a bet on relative class, which a new roster changes; a total
#   is a bet on tempo, which the coaching carries over. Prefer the total,
#   and hold a handicap to 12/14 instead of 11/14.
BLOWOUT = 30
def keep_map():
    return {SPORT['winner']: 'Winner', SPORT['ft']: 'FT O/U', SPORT['hcp']: 'Handicap', SPORT['first']: '1H O/U'}
KEEP = keep_map()
ALIAS = {'fiu': 'floridainternational', 'britishcolumbia': 'bc', 'ucf': 'centralflorida', 'smu': 'southernmethodist',
         'lsu': 'lsu', 'byu': 'brighamyoung', 'usc': 'southerncalifornia', 'tcu': 'tcu', 'utsa': 'utsa', 'unlv': 'unlv'}
def norm(s):
    s = re.sub(r'[^a-z]', '', (s or '').lower())
    for k, v in ALIAS.items():
        if s.startswith(k):
            s = v + s[len(k):]
    return s


def get(url):
    return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=A.HDRS), timeout=25)
                      .read().decode('utf-8', 'replace'))


def sb_board():
    evs = []
    for pg in range(1, 6):
        try:
            d = get(BASE + f"pcUpcomingEvents?sportId={SPORT['sb']}&marketId={SPORT['board']}&pageSize=100&pageNum={pg}&option=1")
        except Exception:
            break
        tours = (d.get('data') or {}).get('tournaments') or []
        if not tours:
            break
        for t in tours:
            for e in t.get('events', []):
                e['_tour'] = t.get('name'); evs.append(e)
    return evs


def sb_markets(eid):
    mk = ((get(BASE + f"event?eventId={eid}&productId=3").get('data') or {}).get('markets')) or []
    out = collections.defaultdict(list)
    for m in mk:
        mid = str(m.get('id'))
        if mid in KEEP:
            out[KEEP[mid]].append(dict(id=mid, spec=m.get('specifier') or '',
                                       outs=[(o['desc'], float(o['odds']), o['id']) for o in m.get('outcomes', [])]))
    return out


def fs_board(days):
    games = []
    for off in range(0, days + 1):
        cur = None
        for s in F.sections(F.fetch(f"f_{SPORT['fs']}_{off}_1_en-ng_1", ttl=900)):
            if 'ZA' in s:
                cur = s['ZA']
            elif 'AA' in s and s.get('AG') is None:
                games.append(dict(lg=cur, id=s['AA'], ts=int(s['AD']), h=s['AE'], a=s['AF']))
    return games


def _comp_key(name):
    """'SPAIN: ACB' / 'ACB' / 'Chile: LNB - Clausura' -> 'acb', 'lnb'."""
    n = (name or '').split(':')[-1].split(' - ')[0]
    return re.sub(r'[^a-z0-9]', '', n.lower())


def venue_games(mid, kickoff, team, suffix, comp=None):
    """The side's last 7 games at this venue. 15 Sep: SAME COMPETITION ONLY.
    Breogan (ACB) v Rilski Sportist (Bulgarian league) was a pre-season friendly;
    "12 of 14 venue games agree" compared two histories against different
    classes of opponent and Rilski +15.5 lost by 45. A pre-season, cup or
    friendly game in the history is dropped for the same reason."""
    hh = F.fetch(f"df_hh_{SPORT['fs']}_{mid}", ttl=3600); out = []
    want = _comp_key(comp) if comp else None
    for tab in hh.split('~KA÷')[1:]:
        if not tab.split('¬')[0].endswith(suffix):
            continue
        for blk in tab.split('~KB÷')[1:]:
            if blk.split('¬')[0] != f'Last matches: {team}':
                continue
            for g in re.split(r'~(?=KC÷)', blk):
                d = dict(re.findall(r'([A-Z]{2,3})÷([^¬]*)', g))
                if 'KC' not in d or not d.get('KU') or not d.get('KT') or int(d['KC']) >= kickoff - 3600:
                    continue
                if want and _comp_key(d.get('KI') or d.get('KF')) != want:
                    continue                          # a different competition tells us nothing here
                home_is_team = d.get('KJ', '').lstrip('*') == team
                pf, pa = int(d['KU']), int(d['KT'])
                if not home_is_team:
                    pf, pa = pa, pf
                out.append(dict(id=d.get('KP'), pf=pf, pa=pa, home=home_is_team, ts=int(d['KC'])))
    return out[:7]


def periods(gid):
    """[(home, away), ...] per period from the period-score feed, regulation only."""
    try:
        d = dict(re.findall(r'B([A-Z])÷(\d+)', F.fetch(f"df_sur_{SPORT['fs']}_{gid}", ttl=999 * 3600)))
    except Exception:
        return None
    out = []
    for i in range(SPORT['periods']):
        hk, ak = chr(ord('A') + 2 * i), chr(ord('B') + 2 * i)
        if hk not in d or ak not in d:
            break
        out.append((int(d[hk]), int(d[ak])))
    return out if len(out) >= SPORT['first_periods'] else None


def first_half(gid, home_is_team):
    ps = periods(gid)
    if not ps:
        return None
    h1 = sum(p[0] for p in ps[:SPORT['first_periods']]); a1 = sum(p[1] for p in ps[:SPORT['first_periods']])
    return (h1, a1) if home_is_team else (a1, h1)


def regulation(gid, home_is_team):
    """Hockey: the book's plain totals and Asian handicaps are regulation time, and
    the form feed's final score includes overtime and shootout goals."""
    ps = periods(gid)
    if not ps or len(ps) < SPORT['periods']:
        return None
    h, a = sum(p[0] for p in ps), sum(p[1] for p in ps)
    return (h, a) if home_is_team else (a, h)


def lines(mk, key):
    out = []
    for m in mk.get(key, []):
        try:
            v = float(m['spec'].split('=')[-1])
        except ValueError:
            continue
        out.append((v, m))
    return sorted(out)


def score_game(hg, ag, mk, hcp_agree=None):
    """Every qualifying line for one game -> list of (hits, n, label, odds, sel)."""
    hcp_agree = hcp_agree or AGREE
    ht = [g['pf'] + g['pa'] for g in hg]; at = [g['pf'] + g['pa'] for g in ag]
    h1 = [g['fh'][0] + g['fh'][1] for g in hg if g['fh']]; a1 = [g['fh'][0] + g['fh'][1] for g in ag if g['fh']]
    hm = [g['pf'] - g['pa'] for g in hg]; am = [g['pf'] - g['pa'] for g in ag]
    cands = []
    for key, H, Aw in (('FT O/U', ht, at), ('1H O/U', h1, a1)):
        for v, m in lines(mk, key):
            n = len(H) + len(Aw)
            if n < 10:
                continue
            o = sum(x > v for x in H) + sum(x > v for x in Aw); u = n - o
            for want, hits in (('Over', o), ('Under', u)):
                if hits >= AGREE:
                    sel = next((s for s in m['outs'] if s[0].startswith(want)), None)
                    if sel:
                        cands.append((hits, n, f"{key} {want} {v}", sel[1], dict(mid=m['id'], spec=m['spec'], oid=sel[2])))
    for v, m in lines(mk, 'Handicap'):      # v is the HOME line; negative = home gives points
        n = len(hm) + len(am)
        if n < 10:
            continue
        need = -v           # same convention on every sport: spec is the home line, 'Home (+1.0)' for hcp=1
        hcov = sum(x > need for x in hm) + sum(-x > need for x in am); acov = n - hcov
        for want, hits, lab in (('Home', hcov, f"Home {v:+g}"), ('Away', acov, f"Away {-v:+g}")):
            if hits >= hcp_agree:
                sel = next((s for s in m['outs'] if s[0].startswith(want)), None)
                if sel:
                    cands.append((hits, n, f"Handicap {lab}", sel[1], dict(mid=m['id'], spec=m['spec'], oid=sel[2])))
    return cands


def best_of(cands, prefer_totals=False):
    """One leg per game: highest agreement, then the line nearest the middle of
    its ladder (the middle line is where the book's own number sits)."""
    cands = [c for c in cands if c[3] >= MIN_PRICE]
    if not cands:
        return None
    if prefer_totals and any('O/U' in c[2] for c in cands):
        cands = [c for c in cands if 'O/U' in c[2]]
    top = max(c[0] for c in cands)
    tied = [c for c in cands if c[0] == top]
    tied.sort(key=lambda c: abs(c[3] - 1.85))
    return tied[0]


def main():
    global SPORT, KEEP
    if '--sport' in sys.argv:
        SPORT = SPORTS[sys.argv[sys.argv.index('--sport') + 1]]; KEEP = keep_map()
    days = int(sys.argv[sys.argv.index('--days') + 1]) if '--days' in sys.argv else 0
    dry = '--dry' in sys.argv
    global AGREE, SKIP_MISMATCH
    if '--skip-mismatch' in sys.argv:
        SKIP_MISMATCH = True
    if '--agree' in sys.argv:
        AGREE = int(sys.argv[sys.argv.index('--agree') + 1])
    global MIN_PRICE
    if '--min-price' in sys.argv:
        MIN_PRICE = float(sys.argv[sys.argv.index('--min-price') + 1])
    now = dt.datetime.now(tz=A.WAT); start = now + dt.timedelta(hours=1)
    horizon = (now + dt.timedelta(days=days)).replace(hour=23, minute=59, second=59)
    sb = [e for e in sb_board() if start < dt.datetime.fromtimestamp(int(e['estimateStartTime']) / 1000, tz=A.WAT) <= horizon]
    fs = fs_board(days)
    print(f"sportybet {SPORT['label'].lower()} {len(sb)} events  |  flashscore {len(fs)} fixtures over {days + 1} day(s)", flush=True)
    legs, skipped = [], collections.Counter()
    for e in sorted(sb, key=lambda e: e['estimateStartTime']):
        ets = int(e['estimateStartTime']) // 1000
        f = next((f for f in fs if abs(f['ts'] - ets) <= 3600 * 3
                  and (norm(f['h'])[:8] in norm(e['homeTeamName']) or norm(e['homeTeamName'])[:8] in norm(f['h']))
                  and (norm(f['a'])[:6] in norm(e['awayTeamName']) or norm(e['awayTeamName'])[:6] in norm(f['a']))), None)
        t = dt.datetime.fromtimestamp(ets, tz=A.WAT)
        name = f"{e['homeTeamName']} v {e['awayTeamName']}"
        if not f:
            skipped['no flashscore fixture'] += 1; print(f"  {t:%a %H:%M}  {name:52} skip: no flashscore fixture"); continue
        mk = sb_markets(e['eventId'])
        win = mk.get('Winner')
        mismatch = bool(win and min(o[1] for o in win[0]['outs']) <= MISMATCH_PRICE)
        if mismatch and SKIP_MISMATCH:
            skipped['class mismatch (winner <= 1.05)'] += 1; print(f"  {t:%a %H:%M}  {name:52} skip: class mismatch"); continue
        if re.search(r'friendl|pre-season|preseason', f.get('lg') or '', re.I):
            skipped['friendly / pre-season'] += 1; print(f"  {t:%a %H:%M}  {name:52} skip: {f.get('lg')}"); continue
        hg = venue_games(f['id'], f['ts'], f['h'], '- Home', f.get('lg')); ag = venue_games(f['id'], f['ts'], f['a'], '- Away', f.get('lg'))
        if len(hg) < 5 or len(ag) < 5:
            skipped['no venue form in this competition'] += 1; print(f"  {t:%a %H:%M}  {name:52} skip: venue form in {f.get('lg')} {len(hg)}/{len(ag)}"); continue
        for g in hg + ag:
            g['fh'] = first_half(g['id'], g['home']) if g['id'] else None
            if SPORT['reg_only'] and g['id']:
                r = regulation(g['id'], g['home'])
                if r:
                    g['pf'], g['pa'] = r
        note = ''
        if mismatch:
            # the games that look like this one: favourite's blowouts, underdog's heaviest defeats
            fav_home = min(o[1] for o in win[0]['outs']) == win[0]['outs'][0][1]
            fav, dog = (hg, ag) if fav_home else (ag, hg)
            fav_sub = [g for g in fav if g['pf'] - g['pa'] >= BLOWOUT]
            dog_sub = [g for g in dog if g['pa'] - g['pf'] >= BLOWOUT]
            sub_h, sub_a = (fav_sub, dog_sub) if fav_home else (dog_sub, fav_sub)
            if len(sub_h) + len(sub_a) >= 4:
                old_agree = AGREE
                globals()['AGREE'] = max(3, int(round(0.8 * (len(sub_h) + len(sub_a)))))
                cands = score_game(sub_h, sub_a, mk); globals()['AGREE'] = old_agree
                note = f"  [MISMATCH: scored on {len(sub_h)}+{len(sub_a)} lookalike games]"
            else:
                cands = []; note = "  [MISMATCH: fewer than 4 lookalike games - no line]"
            pick = best_of(cands)
        else:
            fresh_h = sum(g['ts'] >= SEASON_START for g in hg); fresh_a = sum(g['ts'] >= SEASON_START for g in ag)
            stale = 'NCAA' in (e.get('_tour') or '') and min(fresh_h, fresh_a) < 2
            cands = score_game(hg, ag, mk, hcp_agree=12 if stale else None)
            pick = best_of(cands, prefer_totals=stale)
            if stale:
                note = f"  [stale roster {fresh_h}/{fresh_a} this season: totals first, handicap needs 12/14]"
        fmt = lambda gs: ' '.join(f"{g['pf']}:{g['pa']}" for g in gs)
        if not pick:
            skipped['no line at agreement'] += 1
            print(f"  {t:%a %H:%M}  {name:52} no line{note}   H {fmt(hg)} | A {fmt(ag)}"); continue
        hits, n, lab, odds, sel = pick
        print(f"  {t:%a %H:%M}  {name:52} PICK {lab} @{odds:.2f}  {hits}/{n}{note}   H {fmt(hg)} | A {fmt(ag)}")
        legs.append(dict(ts=ets, match=name, label=lab, odds=odds, hits=hits, n=n, ev=e, sel=sel,
                         stats=[f"{f['h']} HOME {fmt(hg)}", f"{f['a']} AWAY {fmt(ag)}",
                                f"{lab}: {hits}/{n} of the two venue histories agree"]))
    print(f"\nskipped: {dict(skipped)}")
    if not legs:
        print(">> nothing qualifies"); return
    legs = legs[:A.MAX_CODE]
    combo = 1.0
    for l in legs:
        combo *= l['odds']
    print(f"\nSLIP - {len(legs)} legs  ~{combo:,.1f}x")
    for l in legs:
        print(f"  {dt.datetime.fromtimestamp(l['ts'], tz=A.WAT):%a %H:%M}  {l['match'][:44]:44} {l['label']:26} @{l['odds']:.2f}  {l['hits']}/{l['n']}")
    if dry:
        print("\n(dry run - nothing booked)"); return
    sels = [dict(eventId=l['ev']['eventId'], productId=3, marketId=l['sel']['mid'], specifier=l['sel']['spec'], outcomeId=l['sel']['oid']) for l in legs]
    bk = A.book(sels)
    print('\nbooked', bk)
    if bk and bk.get('code'):
        A.log_booking(bk['code'], bk.get('url'), f"{SPORT['label'].lower()} slip {combo:,.1f}x ({len(legs)} legs) - totals/1H/handicap, venue histories agree >= {AGREE}/14",
                      [(l['ts'], l['match'], l['label'], l['odds'], l['stats']) for l in legs])
        print(f"code {bk['code']}  {bk.get('url')}   ({bk.get('booked')}/{bk.get('req')} legs, verified {bk.get('verified')})")


if __name__ == '__main__':
    main()
