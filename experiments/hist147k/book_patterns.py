"""Thu 29 Oct, user: "use the patterns to book today's games". The three patterns that held on 147,563 league games
(2012-21 found, 2021-26 confirmed, most leagues), league games only (the study had no cups):
  A  Over 2.5   draw chance 0.26 or less (no-margin) + venue goals 3.0+ a game     (+2.5 / +2.7 per 100 over the price, 20 of 22 leagues)
                tier 1: draw chance 0.24 or less                                   (+3.1 / +2.7, 17 of 22)
  B  Home win   home side 1.8+ pts a game in the league this season (6+ games) + home chance 0.67+   (+2.8 / +2.9, 29 of 36)
  C  Home or Draw  home chance 0.47-0.57 + away side 1.4+ pts a game this season (6+ games)          (+3.0 / +2.8, 33 of 38)
     C2         away chance under 0.25 + away side scores 1.5+ a game away                        (+2.0 / +2.8, 30 of 38)
One leg per game (the pattern with the bigger measured edge). Code 1 = every game passing; code 2 = tier-1 legs only.

Thu 29 Oct pm, user: "Implement them", after R7JDC2/PYQ1BE lost 7 legs (all from untested parts: 4 cup legs, 5 price-only):
  1. No record, no leg: both sides need 3+ venue games. A game without them is printed as "no record, not booked"
     (11 of the morning's 60 legs had none - El Mansurah and Africain lost).
  2. The price-only legs (D ladder, A', U) only in the 27 countries the 147k study covered. On SportyBet prices
     17 Sep-28 Oct they beat the price both halves there (Home 1.13-1.55 60/81 v 57 ; 124/156 v 111) but not
     elsewhere (67/96 v 69 ; 138/184 v 134). Elsewhere only the record patterns (A, B, C, C2) book.
  3. Cups (domestic and continental), 1,325 domestic cup games at 90 min: home chance 0.50+ -> Home (271/386 v
     price 235, both halves); away chance 0.50+ -> Draw or Away (147/166 v 140), never Over 2.5 (away-favourite
     cup ties had Over 2.5 about 53 in 100 v 65 in league games); neither side 0.50 -> no leg.
  4. African leagues, no Over/Under legs (Over at a long draw 27/76; Under in close games ~60 in 100 where 1.30-1.45
     needs ~70); favourite at fair 1.67 or shorter -> favourite or draw (52/56 v price 50), else only B/C legs.
  The name match (dynamic_v4._score) now takes one word spelled two ways - El Mansurah had a record all along."""
import sys, json, math, re, collections, datetime as dt
sys.path.insert(0, '/Users/apple/Downloads/draw')
import acca as A, book_v3 as B, findings as FD, fetcher_v2 as F2, fetcher_v3 as F3, predict_stats as PS
dry = '--dry' in sys.argv
now = dt.datetime.now(A.WAT); start = now + dt.timedelta(hours=1); end = now.replace(hour=23, minute=59, second=59, microsecond=0)
kot = lambda e: dt.datetime.fromtimestamp(int(e['estimateStartTime']) / 1000, tz=A.WAT)
evs = [e for e in B._fetch_events_for('1,18,10') if start < kot(e) <= end and 'SRL' not in (e.get('homeTeamName') or '')]
cat = lambda e: e['sport']['category']['name']; tour = lambda e: e['sport']['category']['tournament']['name']
print(f"window {start:%H:%M}-{end:%H:%M}: {len(evs)} games")
CUPRE = re.compile(r'cup|copa|cupa|coupe|pokal|puchar|kupa|kupok|pohar|bikar|coppa|ta[cç]a|beker|trophy|shield|champions league|europa|conference|libertadores|sudamericana|play-?off|super ?cup', re.I)
league = [e for e in evs if cat(e).strip().lower() not in PS.CONTINENTAL and not PS.is_cup(tour(e), cat(e)) and not CUPRE.search(tour(e)) and 'international' not in cat(e).lower()
          and not re.search(r'friendl|women|u1[6-9]|u2[0-3]|reserve|youth', tour(e) + cat(e), re.I)]
STUDY = {'Argentina', 'Austria', 'Belgium', 'Brazil', 'China', 'Denmark', 'England', 'Finland', 'France', 'Germany', 'Greece', 'Ireland', 'Italy', 'Japan',
         'Mexico', 'Netherlands', 'Norway', 'Poland', 'Portugal', 'Romania', 'Russia', 'Scotland', 'Spain', 'Sweden', 'Switzerland', 'Turkey', 'USA'}
AFRICA = re.compile(r'^(Egypt|Tunisia|Morocco|Algeria|Libya|Sudan|Nigeria|Ghana|South Africa|Kenya|Tanzania|Uganda|Zambia|Ethiopia|Rwanda|Cameroon|Ivory Coast|Senegal|'
                    r'Botswana|Zimbabwe|Malawi|Angola|Mali|Burkina|Gambia|Mozambique|Benin|Togo|Guinea|Congo|DR Congo|Gabon|Niger|Liberia|Sierra Leone|Burundi|Namibia)', re.I)
print(f"league games: {len(league)} (the rest are cups and continental games - own rule K/K2 - or friendlies, women and youth)")
st = FD.prepare(league)
mean = lambda xs: sum(xs) / len(xs) if xs else None
def price(e, mid, spec, want):
    for m in e.get('markets') or []:
        if str(m.get('id')) == mid and (m.get('specifier') or None) == spec and str(m.get('status', '0')) == '0':
            for x in m.get('outcomes') or []:
                if x.get('desc') == want and x.get('isActive', 1):
                    return float(x['odds']), dict(eventId=e['eventId'], productId=3, marketId=mid, specifier=spec, outcomeId=str(x['id']))
    return None, None
def season(raw, ko):
    """Each side's league games this season: (pts, played, ppg, league name). League = most common non-cup competition
    in its last 10; this season = walking back until a 35-day gap (an off-season or a long break)."""
    tab = blk = None; rows = collections.OrderedDict()
    for f in F3.sections(raw):
        if 'KA' in f: tab = f['KA']
        if 'KB' in f: blk = f['KB']; continue
        if tab != 'Overall' or not blk or not blk.startswith('Last matches') or 'KJ' not in f: continue
        try: hg, ag, kc = int(f.get('KU', '')), int(f.get('KT', '')), int(f.get('KC', 0))
        except ValueError: continue
        if not 0 < kc < ko - 3600: continue
        gf, ga = (hg, ag) if f.get('KS') == 'home' else (ag, hg)
        rows.setdefault(blk, []).append((kc, (f.get('KF') or f.get('KI') or '').strip(), f.get('KH', ''), 3 if gf > ga else 1 if gf == ga else 0))
    out = []
    for key in list(rows)[:2]:
        g = sorted(rows[key], key=lambda r: -r[0])
        lg = collections.Counter(c for _, c, cty, _ in g[:10] if c and not PS.is_cup(c, cty)).most_common(1)
        if not lg: out.append(None); continue
        comp = lg[0][0]; pts = played = 0; prev = None
        for kc, c, _, p in g:
            if c != comp: continue
            if prev is not None and (prev - kc) / 86400 >= 35: break
            pts += p; played += 1; prev = kc
        out.append((pts, played, pts / played if played else 0, comp))
    return out if len(out) == 2 else [None, None]

# every version that held in BOTH periods on the 147k league games; edge = extra wins per 100 bets over the price (average of the two periods)
LADDER = [('Home', 1.13, 1.55, 2.5, 'home win at fair price 1.13-1.55 (short home favourites land more than priced)'),
          ('Home or Draw', 1.00, 1.08, 1.85, 'Home or Draw at fair price 1.00-1.08'), ('Home or Draw', 1.25, 1.43, 1.1, 'Home or Draw at fair price 1.25-1.43'),
          ('Over 2.5', 1.43, 1.55, 2.35, 'Over 2.5 at fair price 1.43-1.55'), ('Over 2.5', 1.55, 1.70, 1.25, 'Over 2.5 at fair price 1.55-1.70'),
          ('Under 2.5', 1.43, 1.55, 2.75, 'Under 2.5 at fair price 1.43-1.55'),
          ('Home or Away', 1.13, 1.33, 1.0, 'Home or Away at fair price 1.13-1.33')]
MKT = {'Home': ('1', None, 'Home', '1X2 / Home'), 'Home or Draw': ('10', None, 'Home or Draw', 'Double Chance / Home or Draw'),
       'Home or Away': ('10', None, 'Home or Away', 'Double Chance / Home or Away'), 'Over 2.5': ('18', 'total=2.5', 'Over 2.5', 'Over/Under / Over 2.5'),
       'Under 2.5': ('18', 'total=2.5', 'Under 2.5', 'Over/Under / Under 2.5'), 'Draw or Away': ('10', None, 'Draw or Away', 'Double Chance / Draw or Away')}
cupset = {id(e) for e in evs if e not in league}
NOTMEN = re.compile(r'friendl|women|u1[6-9]|u2[0-3]|youth|reserve', re.I)
evs2 = [e for e in evs if not NOTMEN.search(tour(e) + ' ' + cat(e)) and not re.search(r' W$|WFC$', e['homeTeamName'] + '|' + e['awayTeamName'])
         and not re.search(r' W$|WFC$', e['homeTeamName']) and not re.search(r' W$|WFC$', e['awayTeamName'])]
print(f'after women/youth/friendlies out: {len(evs2)} games')
cand = []; why_not = collections.Counter(); norec = []
for e in evs2:
    cup = id(e) in cupset
    o1, _ = price(e, '1', None, 'Home'); ox, _ = price(e, '1', None, 'Draw'); o2, _ = price(e, '1', None, 'Away')
    if not (o1 and ox and o2): why_not['no 1X2 price'] += 1; continue
    s_ = 1 / o1 + 1 / ox + 1 / o2; pH, pD, pA = 1 / o1 / s_, 1 / ox / s_, 1 / o2 / s_
    oo, _ = price(e, '18', 'total=2.5', 'Over 2.5'); ou, _ = price(e, '18', 'total=2.5', 'Under 2.5')
    pO = (1 / oo) / (1 / oo + 1 / ou) if oo and ou else None
    fair = {'Home': 1 / pH, 'Home or Draw': 1 / (pH + pD), 'Home or Away': 1 / (pH + pA), 'Over 2.5': 1 / pO if pO else None, 'Under 2.5': 1 / (1 - pO) if pO else None}
    ko = int(e['estimateStartTime']) / 1000
    opts = []
    def add(key, mk, edge, why):
        od, ids = price(e, *MKT[mk][:3])
        if od: opts.append((key, mk, od, ids, edge, why))
    country = cat(e).strip(); africa = bool(AFRICA.search(country)) and not cup
    rec = st.get(str(e['eventId'])) if not cup else (FD.prepare([e]) or {}).get(str(e['eventId']))
    if not cup and not africa and country in STUDY:
        for mk, lo, hi, edge, txt in LADDER:
            if fair[mk] and lo <= fair[mk] < hi: add('D', mk, edge, f"D (price ladder): {txt}; fair {fair[mk]:.2f}")
        if pD <= 0.26: add("A'", 'Over 2.5', 1.95, f"A': draw chance {pD:.2f} (0.26 or less) -> Over 2.5; 147k: 8,880/15,366 v book 8,607 ; 8,791/14,942 v 8,475, 20 of 22 leagues")
        if pD >= 0.30: add('U', 'Under 2.5', 2.0, f"U: draw chance {pD:.2f} (0.30+, a close game) -> Under 2.5; 147k: Over landed 3,083/8,177 v book 3,312 ; 2,627/6,584 v 2,708, 19 of 20 leagues")
    if cup:
        if pH >= 0.50: add('K', 'Home', 9.2, f"K: cup game, home chance {pH:.2f} (0.50+) -> Home; 1,325 domestic cup games 17 Sep-28 Oct: 271/386 v price 235, both halves")
        elif pA >= 0.50: add('K2', 'Draw or Away', 4.0, f"K2: cup game, away chance {pA:.2f} (0.50+) -> Draw or Away, never Over 2.5; cup games: 147/166 v price 140")
    if africa and max(pH, pA) >= 0.60:
        add('F', 'Home or Draw' if pH >= pA else 'Draw or Away', 3.75, f"F: African league, favourite chance {max(pH, pA):.2f} (fair 1.67 or shorter) -> favourite or draw; 309 African games: 52/56 v price 50, both halves")
    hs = as_ = None; vg = hsc = hco = asc = aco = None; VH = VA = []
    if rec:
        VH = [(q['h'], q['a']) for q in rec['games'] if q['side'] == 'home']; VA = [(q['a'], q['h']) for q in rec['games'] if q['side'] == 'away']
        raw = F2.fetch(f"df_hh_1_{rec['fid']}")
        hs, as_ = season(raw, ko) if raw else (None, None)
    if len(VH) < 3 or len(VA) < 3:
        why_not['no record (under 3 venue games a side)'] += 1
        norec.append(f"{kot(e):%H:%M} {e['homeTeamName']} v {e['awayTeamName']} ({country}: {tour(e)}) - " + ('no Flashscore match' if not rec else f"{len(VH)} home / {len(VA)} away venue games"))
        continue
    vg = (mean([a + b for a, b in VH]) + mean([a + b for a, b in VA])) / 2
    hsc, hco, asc, aco = mean([a for a, b in VH]), mean([b for a, b in VH]), mean([a for a, b in VA]), mean([b for a, b in VA])
    avp = mean([3 if a > b else 1 if a == b else 0 for a, b in VA])
    if pD <= 0.26 and vg >= 3.0 and not cup and not africa: add('A', 'Over 2.5', 2.9 if pD <= 0.24 else 2.6, f"A: draw chance {pD:.2f} + venue goals {vg:.2f} a game (3.0+) -> Over 2.5; 147k: 3,636/5,901 v book 3,491 ; 3,970/6,368 v 3,796")
    if cup: pass
    elif pA < 0.25 and asc >= 1.5: add('C2', 'Home or Draw', 2.4, f"C2: away chance {pA:.2f} (under 0.25) + away side scores {asc:.2f} a game away (1.5+) -> Home or Draw; 147k: 4,781/5,680 v book 4,666 ; 3,931/4,647 v 3,799")
    elif pA < 0.25 and avp >= 1.33: add("C2'", 'Home or Draw', 1.65, f"C2': away chance {pA:.2f} + away side {avp:.2f} pts a game away (1.33+) -> Home or Draw; 147k: 7,185/8,579 v book 7,036 ; 5,590/6,711 v 5,480")
    hppg = hs[2] if hs and hs[1] >= 6 and not cup else None; appg = as_[2] if as_ and as_[1] >= 6 and not cup else None
    if hppg is not None and hppg >= 1.8 and pH >= 0.67: add('B', 'Home', 2.85, f"B: home side {hppg:.2f} pts a game this season (1.8+) + home chance {pH:.2f} (0.67+) -> Home; 147k: 2,958/3,753 v book 2,854 ; 2,160/2,745 v 2,079")
    elif hppg is not None and hppg >= 1.8 and pH >= 0.57: add("B'", 'Home', 2.0, f"B': home side {hppg:.2f} pts a game (1.8+) + home chance {pH:.2f} (0.57+) -> Home; 147k: 4,652/6,522 v book 4,566 ; 3,578/4,956 v 3,446")
    if appg is not None and appg >= 1.4 and 0.47 <= pH < 0.57: add('C', 'Home or Draw', 2.9, f"C: home chance {pH:.2f} (0.47-0.57) + away side {appg:.2f} pts a game (1.4+) -> Home or Draw; 147k: 3,291/4,063 v book 3,168 ; 2,454/3,051 v 2,367")
    elif appg is not None and appg >= 1.4 and pH >= 0.57: add("C'", 'Home or Draw', 2.1, f"C': home chance {pH:.2f} (0.47+) + away side {appg:.2f} pts a game (1.4+) -> Home or Draw; 147k: 5,613/6,716 v book 5,458 ; 4,069/4,931 v 3,977")
    if not opts: why_not['no pattern or ladder price'] += 1; continue
    k, mk, od, ids, edge, why = max(opts, key=lambda o: (o[4], o[2]))
    stats = [why + (f" | also passes: {', '.join(sorted(set(o[0] for o in opts if o[0] != k)))}" if len(opts) > 1 else ''),
             f"{cat(e)}: {tour(e)}{' (CUP)' if cup else ''} | 1X2 {o1}/{ox}/{o2} (no-margin {pH:.2f}/{pD:.2f}/{pA:.2f})" + (f" | Over/Under 2.5 {oo}/{ou}" if oo else ''),
             f"league this season: home {hs[0] if hs else '-'} pts from {hs[1] if hs else '-'} ({'-' if hppg is None else round(hppg, 2)} a game), away {as_[0] if as_ else '-'} pts from {as_[1] if as_ else '-'} ({'-' if appg is None else round(appg, 2)} a game)",
             f"venue: home at home {len(VH)} games scores {hsc:.2f} concedes {hco:.2f}; away away {len(VA)} games scores {asc:.2f} concedes {aco:.2f}; goals a game {vg:.2f}"]
    cand.append(dict(ts=ko, t=kot(e).strftime('%H:%M'), match=f"{e['homeTeamName']} v {e['awayTeamName']}", pat=k, sel=MKT[mk][3], odds=od, ids=ids, edge=edge, cup=cup, stats=stats))
print('left out:', dict(why_not))
print(f"\nno record, not booked ({len(norec)}):"); [print('   ' + x) for x in norec]
cand.sort(key=lambda c: c['ts'])
json.dump(cand, open('pat2.json', 'w'))
lg_ = [c for c in cand if not c['cup']]
print(f"\n{len(cand)} games get a leg ({len(lg_)} league, {len(cand) - len(lg_)} cup): " + ', '.join(f"{k} {v}" for k, v in collections.Counter(c['pat'] for c in cand).most_common()))
for c in cand:
    print(f"{c['t']} {'CUP ' if c['cup'] else ''}{c['match'][:44]:44} {c['sel'][-28:]:28} @{c['odds']:<5} {c['pat']:3} edge {c['edge']}")
def top50(xs): return sorted(sorted(xs, key=lambda c: -c['edge'])[:50], key=lambda c: c['ts'])
cp_ = [c for c in cand if c['cup']]
codes = [('league games - every league game with a tested pattern leg', top50(lg_)), ('cup games - home favourite Home, away favourite Draw or Away', top50(cp_))]
A.FINDINGS_EXEMPT = True
for lab, legs in codes:
    combo = math.prod(c['odds'] for c in legs)
    print(f"\n=== {lab}: {len(legs)} games, {combo:,.2f}x")
    if dry: continue
    bk = A.book([c['ids'] for c in legs])
    if bk and bk.get('code'):
        print(f">> code {bk['code']}  {bk['url']}  ({bk['booked']} of {bk['req']} legs booked, verified {bk.get('verified')})")
        A.log_booking(bk['code'], bk['url'], f"{now:%a %d %b} 147k-game patterns v2 (record needed, cups/Africa rules) ({lab}) - {combo:,.2f}x ({len(legs)} games)",
                      [(c['ts'], c['match'], c['sel'], c['odds'], c['stats']) for c in legs])
    else:
        print('>> booking failed', bk)
