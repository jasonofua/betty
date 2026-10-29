"""Wed 28 Oct, user: "Book today's games" after "What is the fix". The 27 Oct 10k slip (ZWFXEY) rebuilt with the fix:
every game now+1h to 23:59 passing a check that held on fresh games (C1-C4, P1-P3, P5-P9, P12; P5/P6 home favourite only;
cross-league games skipped in cups), and NEW: a Double Chance leg is dropped when the side bet against won half or more of
the last 3-6 meetings (held on 6,320 fresh league games: P8 kept 605 of 776 v book 581, dropped 121 of 176 v book 127;
P7 kept 437 of 512 v book 416). Each game gets its likeliest bet; book_allgames.to_target then takes the fewest, likeliest
legs that reach the target.

  python3 book_tenk.py [--target 10000] [--dry]       (writes tenk.json in the current folder)

29 Oct, user: "analyze the losses" -> "whats the fix" -> "Implement them", after MDTB4H (10,031.82x) lost 8 of 36 on 28 Oct.
Tested on the same 6,320 league games:
  1. A leg's chance is the book's no-margin chance plus the edge the check has shown OVER the book (LIFT), no longer the
     check's flat record and the book averaged. Close games (home and away price within 1.05) land at the book's price
     (P8 340 of 492 v book 332, P7 83 of 115 v 78, P12 413 of 577 v 411), so the average put 0.10-0.17 on them that was
     never there: on MDTB4H the legs whose record ran 0.10+ above the book lost 4 of 10 (Tivat, Paide, Al Jabalain,
     Parma), the rest 4 of 26. With the head-to-head drop the checks beat the book by 0.00-0.05 a game (LIFT). Goal-line
     bets (Over 1.5, 2nd half Over 0.5, Under 3.5) get the book's chance alone: no Over/Under price history was saved,
     so their edge over the book cannot be measured. P9 has no half-time data in the test; it takes all away favourites'.
  2. Over 1.5 only when the venue games average 2.5+ goals (O15_MIN_VENUE): P1 451 of 544 v 158 of 203 under it, P3 500
     of 608 v 214 of 285, P2 742 of 907 v 181 of 234, every one held in both halves (17 Sep-6 Oct, 7-26 Oct). Tivat 1.83
     and Al Jabalain 1.38 lost; Spezia 2.19 and Uta Arad 2.43 won. Dropping close games from Over 1.5 FAILED (P1 with
     the favourite 2.20+ 314 of 386, the same as the rest)."""
import sys, json, math, re, datetime as dt
sys.path.insert(0, '/Users/apple/Downloads/draw')
import acca as A, book_v3 as B, book_allgames as GA, collections, findings as FD, fetcher_v2 as F2, fetcher_v3 as F3
dry = '--dry' in sys.argv; TARGET = float(sys.argv[sys.argv.index('--target') + 1]) if '--target' in sys.argv else 10000
now = dt.datetime.now(A.WAT); start = now + dt.timedelta(hours=1); end = now.replace(hour=23, minute=59, second=59, microsecond=0)
kot = lambda e: dt.datetime.fromtimestamp(int(e['estimateStartTime']) / 1000, tz=A.WAT)
evs = [e for e in B._fetch_events_for('1,18,10,90') if start < kot(e) <= end and 'SRL' not in (e.get('homeTeamName') or '')]
comp = lambda e: f"{e['sport']['category']['name']}: {e['sport']['category']['tournament']['name']}"
print(f"window {start:%H:%M}-{end:%H:%M}: {len(evs)} games")
st = FD.prepare(evs)
mean = lambda xs: sum(xs) / len(xs) if xs else 0
BET = {'2H': ('90', 'total=0.5', 'Over 0.5', '2nd Half - Over/Under / Over 0.5'), 'O15': ('18', 'total=1.5', 'Over 1.5', 'Over/Under / Over 1.5'),
       'U35': ('18', 'total=3.5', 'Under 3.5', 'Over/Under / Under 3.5'), 'HoD': ('10', None, 'Home or Draw', 'Double Chance / Home or Draw'),
       'DoA': ('10', None, 'Draw or Away', 'Double Chance / Draw or Away'), '12': ('10', None, 'Home or Away', 'Double Chance / Home or Away')}
CHK = [  # (key, text, record on new games, test, bets)
 ('C1', 'favourite 1.50 or shorter + venue 1st halves 1.5+', '189 of 213', lambda r: r['fav'] <= 1.5 and r['exp1H'] is not None and r['exp1H'] >= 1.5, ['2H']),
 ('C2', 'favourite 1.50 or shorter + 2 in 3 venue games Over 2.5', '166 of 186', lambda r: r['fav'] <= 1.5 and r['vo25'] > 2 / 3, ['2H']),
 ('C3', 'venue 1st halves under 0.9 + last 5 under 2.2 goals a game', '166 of 209', lambda r: r['exp1H'] is not None and r['exp1H'] < 0.9 and r['g5'] < 2.2, ['U35']),
 ('C4', 'home favourite 1.50 or shorter', '371 of 410', lambda r: r['fav'] <= 1.5 and r['hfav'], ['HoD']),
 ('P1', 'away favourite + away form 7 pts or less', 'O1.5 578 of 721, 2H 568 of 721', lambda r: r['afav'] and r['pa'] <= 7, ['O15', '2H']),
 ('P2', 'home 2+ pts a game at home + favourite 1.90 or shorter', 'O1.5 867 of 1,078, 2H 900 of 1,078', lambda r: r['vph'] >= 2 and r['fav'] <= 1.9, ['O15', '2H']),
 ('P3', 'away favourite + under 1.5 pts a game away', 'O1.5 694 of 881, 2H 711 of 881', lambda r: r['afav'] and r['vpa'] < 1.5, ['O15', '2H']),
 ('P5', 'home 5+ form pts ahead + favourite 1.90 or shorter', '562 of 663', lambda r: r['ph'] - r['pa'] >= 5 and r['fav'] <= 1.9 and r['hfav'], ['HoD']),
 ('P6', 'home form 11+ + favourite 1.90 or shorter', '429 of 509', lambda r: r['ph'] >= 11 and r['fav'] <= 1.9 and r['hfav'], ['HoD']),
 ('P7', 'home 5+ form pts ahead + home favourite', '437 of 512 with the head-to-head check', lambda r: r['ph'] - r['pa'] >= 5 and r['hfav'], ['HoD']),
 ('P8', 'away favourite + away form 8+', '605 of 776 with the head-to-head check', lambda r: r['afav'] and r['pa'] >= 8, ['DoA']),
 ('P9', 'away favourite + venue 1st halves under 1.2', '618 of 833', lambda r: r['afav'] and r['exp1H'] is not None and r['exp1H'] < 1.2, ['DoA']),
 ('P12', 'both scored in half or fewer venue games + home favourite', '1,499 of 2,023', lambda r: r['vgg'] <= 0.5 and r['hfav'], ['12']),
]
# 29 Oct: edge over the book a game (landed minus book no-margin chance), (close games, clear games), head-to-head drop
# applied; close = home and away price within CLOSE. C4/P5/P6 never have close games. Anything not listed: 0.
LIFT = {('C4', 'HoD'): (0.031, 0.031), ('P5', 'HoD'): (0.045, 0.045), ('P6', 'HoD'): (0.048, 0.048),   # 546/603 v 527.1, 487/551 v 462.3, 369/416 v 349.0
        ('P7', 'HoD'): (0.022, 0.040),                     # close 50/71 v 48.4, clear 552/638 v 526.2
        ('P8', 'DoA'): (0.041, 0.025),                     # close 292/408 v 275.4, clear 511/628 v 495.3
        ('P9', 'DoA'): (0.028, 0.011),                     # all away favourites: close 547/781 v 525.2, clear 707/890 v 696.9
        ('P12', '12'): (0.003, 0.001)}                     # close 413/577 v 411.3, clear 1,124/1,491 v 1,122.8
CLOSE = 1.05
O15_MIN_VENUE = 2.5                                        # 29 Oct: Over 1.5 only when the venue games average this many goals
OTHER = {'2H': 'Under 0.5', 'O15': 'Under 1.5', 'U35': 'Over 3.5'}
CUPRE = re.compile(r'cup|copa|cupa|coupe|pokal|puchar|kupa|kupok|pohar|bikar|coppa|ta[cç]a|beker|trophy|shield|champions league|europa|conference|libertadores|sudamericana|friendl', re.I)
def bookchance(e, b, r):
    if b in ('HoD', 'DoA', '12'):
        if not r['ox']: return None
        s = 1 / r['o1'] + 1 / r['ox'] + 1 / r['o2']
        return {'HoD': (1 / r['o1'] + 1 / r['ox']), 'DoA': (1 / r['ox'] + 1 / r['o2']), '12': (1 / r['o1'] + 1 / r['o2'])}[b] / s
    mid, spec, want, _ = BET[b]
    for m in e.get('markets') or []:
        if str(m.get('id')) == mid and (m.get('specifier') or None) == spec and str(m.get('status', '0')) == '0':
            o = {x.get('desc'): float(x['odds']) for x in m.get('outcomes') or [] if x.get('isActive', 1)}
            if want in o and OTHER[b] in o:
                return (1 / o[want]) / (1 / o[want] + 1 / o[OTHER[b]])
    return None
def leagues(raw):
    lg = []
    for b in raw.split('~KB÷')[1:3]:
        c = collections.Counter()
        for x in b.split('~KC÷')[1:11]:
            f = dict(y.split('÷', 1) for y in x.split('¬') if '÷' in y)
            c[f"{f.get('KH')}: {f.get('KF')}"] += 1
        lg.append(c.most_common(1)[0][0] if c else None)
    return lg
def h2h(raw, ko):
    """Last 6 meetings before kick-off as (today's home goals, today's away goals)."""
    tab, blk, home, out = None, None, None, []
    for f in F3.sections(raw):
        if 'KA' in f: tab = f['KA']
        if 'KB' in f:
            blk = f['KB']
            if tab == 'Overall' and home is None and blk.startswith('Last matches'): home = blk.split(':', 1)[1].strip()
            continue
        if tab != 'Overall' or not blk or not blk.startswith('Head-to-head') or 'KJ' not in f: continue
        try: hg, ag, kc = int(f.get('KU')), int(f.get('KT')), int(f.get('KC', 0))
        except Exception: continue
        if not (0 < kc < ko - 3600): continue
        out.append((hg, ag) if f['KJ'].lstrip('*').strip() == (home or '').lstrip('*').strip() else (ag, hg))
    return out[:6]
def leg(e, b):
    mid, spec, want, sel = BET[b]
    for m in e.get('markets') or []:
        if str(m.get('id')) == mid and (m.get('specifier') or None) == spec and str(m.get('status', '0')) == '0':
            for x in m.get('outcomes') or []:
                if x.get('desc') == want and x.get('isActive', 1):
                    return float(x['odds']), dict(eventId=e['eventId'], productId=3, marketId=mid, specifier=spec, outcomeId=str(x['id']))
    return None, None
cand = []; norec = 0; skipped = collections.Counter()
for e in evs:
    rec = st.get(str(e['eventId']))
    one = {}
    for m in e.get('markets') or []:
        if str(m.get('id')) == '1': one = {x.get('desc'): float(x['odds']) for x in m.get('outcomes') or [] if x.get('isActive', 1)}
    if not rec or not one.get('Home') or not one.get('Away'): norec += 1; continue
    ko = int(e['estimateStartTime']) / 1000
    VH = [(q['h'], q['a'], q['hh'], q['ah']) for q in rec['games'] if q['side'] == 'home']
    VA = [(q['a'], q['h'], q['ah'], q['hh']) for q in rec['games'] if q['side'] == 'away']
    raw = F2.fetch(f"df_hh_1_{rec['fid']}")
    try: hr, ar, _ = F3.parse_history(raw)
    except Exception: norec += 1; continue
    hr = [q for q in hr if not q['friendly'] and 0 < q['kc'] < ko - 3600]; ar = [q for q in ar if not q['friendly'] and 0 < q['kc'] < ko - 3600]
    if len(VH) < 3 or len(VA) < 3 or len(hr) < 5 or len(ar) < 5: norec += 1; continue
    VH1 = [x for x in VH if x[2] is not None]; VA1 = [x for x in VA if x[2] is not None]
    r = dict(t=kot(e).strftime('%H:%M'), ts=ko, match=f"{e['homeTeamName']} v {e['awayTeamName']}", comp=comp(e), o1=one['Home'], ox=one.get('Draw'), o2=one['Away'])
    r['expFT'] = (mean([x[0] + x[1] for x in VH]) + mean([x[0] + x[1] for x in VA])) / 2
    r['exp1H'] = (mean([x[2] + x[3] for x in VH1]) + mean([x[2] + x[3] for x in VA1])) / 2 if VH1 and VA1 else None
    r['vo25'] = sum(x[0] + x[1] >= 3 for x in VH + VA) / len(VH + VA)
    r['vgg'] = sum(x[0] > 0 and x[1] > 0 for x in VH + VA) / len(VH + VA)
    r['g5'] = (mean([q['gf'] + q['ga'] for q in hr[:5]]) + mean([q['gf'] + q['ga'] for q in ar[:5]])) / 2
    pts = lambda f: sum(3 if q['res'] == 'W' else 1 if q['res'] == 'D' else 0 for q in f[:5])
    r['ph'], r['pa'] = pts(hr), pts(ar); r['fh'] = ''.join(q['res'] for q in hr[:5]); r['fa'] = ''.join(q['res'] for q in ar[:5])
    r['vph'] = sum(3 if x[0] > x[1] else 1 if x[0] == x[1] else 0 for x in VH) / len(VH)
    r['vpa'] = sum(3 if x[0] > x[1] else 1 if x[0] == x[1] else 0 for x in VA) / len(VA)
    r['fav'] = min(r['o1'], r['o2']); r['hfav'] = r['o1'] < r['o2']; r['afav'] = r['o2'] < r['o1']
    if CUPRE.search(r['comp']) or r['comp'].startswith('International'):
        lg = leagues(raw)
        if len(lg) == 2 and lg[0] != lg[1]: skipped['cross-league cup game'] += 1; continue
    mt = h2h(raw, ko); hw = sum(a > b for a, b in mt); aw = sum(a < b for a, b in mt)
    r['h2h'] = f"last {len(mt)} meetings: home won {hw}, away won {aw}, drawn {len(mt) - hw - aw}"
    opts = {}
    for k, txt, recd, f, bets in CHK:
        if f(r):
            for b in bets:
                if b == 'HoD' and len(mt) >= 3 and aw * 2 >= len(mt): skipped['Home or Draw dropped: away side won half+ of the meetings'] += 1; continue
                if b == 'DoA' and len(mt) >= 3 and hw * 2 >= len(mt): skipped['Draw or Away dropped: home side won half+ of the meetings'] += 1; continue
                if b == 'O15' and r['expFT'] < O15_MIN_VENUE: skipped[f'Over 1.5 dropped: venue games under {O15_MIN_VENUE} goals a game'] += 1; continue
                lift = LIFT.get((k, b), (0, 0))[0 if abs(r['o1'] - r['o2']) <= CLOSE else 1]
                opts.setdefault(b, []).append((f"{k} {txt} (new games {recd})", lift))
    best = None
    for b, why in opts.items():
        od, ids = leg(e, b); bc = bookchance(e, b, r)
        if not od or not bc: continue
        lift = max(w[1] for w in why); ch = min(bc + lift, 0.99)
        sc = math.log(od) / -math.log(ch)
        if not best or sc > best[0]: best = (sc, b, od, ids, [w[0] for w in why], ch, bc, lift)
    if not best: continue
    _, b, od, ids, why, ch, bc, lift = best
    stats = [f"checks: {'; '.join(why)} | chance {ch:.2f} = book's no-margin {bc:.2f} + check's edge over the book {lift:.3f}" + (f" | also passes: {', '.join(k for k in opts if k != b)}" if len(opts) > 1 else ''),
             f"{r['comp']} | 1X2 {r['o1']}/{r['ox']}/{r['o2']} | {r['h2h']}",
             f"form last 5: home {r['fh']} ({r['ph']} pts, {mean([q['gf'] + q['ga'] for q in hr[:5]]):.1f} goals a game)  away {r['fa']} ({r['pa']} pts, {mean([q['gf'] + q['ga'] for q in ar[:5]]):.1f} goals a game)",
             f"venue: home {len(VH)} games {r['vph']:.2f} pts a game, away {len(VA)} games {r['vpa']:.2f} pts a game; goals a game {r['expFT']:.2f}, 1st half {r['exp1H'] if r['exp1H'] is None else round(r['exp1H'], 2)}; Over 2.5 in {r['vo25']:.2f}, both scored in {r['vgg']:.2f}"]
    cand.append(dict(ts=ko, t=r['t'], match=r['match'], bet=b, sel=BET[b][3], odds=od, ids=ids, stats=stats, comp=r['comp'], chance=ch))
print(f"with records: {len(evs) - norec}, pass a check with a price: {len(cand)}"); print("left out:", dict(skipped))
pick = sorted(GA.to_target(cand, TARGET, 50) or sorted(cand, key=lambda c: -c['odds'])[:50], key=lambda c: c['ts'])
combo = math.prod(c['odds'] for c in pick)
print(f"picked {len(pick)}, {combo:,.2f}x")
for c in pick:
    print(f"  {c['t']} | {c['match'][:42]:42} | {c['sel'][:32]:32} @{c['odds']:<5} | {c['stats'][0][:110]} | {c['comp'][:30]}")
json.dump(pick, open('tenk.json', 'w'))
print(f'chance every leg lands (estimate) about 1 in {1 / math.prod(c["chance"] for c in pick):,.0f}')
if not dry:
    bk = A.book([c['ids'] for c in pick])
    if bk and bk.get('code'):
        print(f">> code {bk['code']}  {bk['url']}  ({bk['booked']} of {bk['req']} legs booked, verified {bk.get('verified')})")
        A.log_booking(bk['code'], bk['url'], f"{now:%a %d %b} {TARGET:,.0f}x best picks - checks that held on fresh games, likeliest bet per game (book chance + the check's measured edge), Double Chance dropped when the side bet against won half+ of the last meetings, Over 1.5 only on 2.5+ venue goals - {combo:,.2f}x ({len(pick)} games)",
                      [(c['ts'], c['match'], c['sel'], c['odds'], c['stats']) for c in pick])
    else:
        print('>> booking failed', bk)
