#!/usr/bin/env python3
"""Winners slip - the 10 Sep rule set (second pass), measured on every leg
booked 8-10 Sep plus 43k corpus matches:

    1. the MARKET picks the side: the 1X2 favourite only (against-market 2/9)
    2. venue goal difference decides WHETHER to bet - the favourite must be
       backed by >= 1.0 goals/game at the venue if it is at HOME, >= 1.5 if
       it is AWAY (home side's home games vs away side's away games, last 7).
       Unbacked favourites lose OUTRIGHT, so no cover helps - skip them.
    3. price < 1.80  -> straight win
    4. price 1.80-2.60 -> double chance (the loss in this band is the draw)
       above 2.60 -> skip

One slip, every qualifying game in the window. Window opens one hour ahead.

    python3 book_winners.py --until 23 [--days 0] [--dry]
"""
import collections, re, sys, datetime as dt, json
import acca as A
import fetcher_v2 as F2
import dynamic_v4 as D
import book_draw as DRW          # venue_form(): df_hh Home/Away tabs cut at kickoff

# 10 Sep, second pass. FC Tallinn 4:2 Maardu (away fav @1.95, margin +1.00)
# forced a recheck of both numbers against 43k corpus matches:
#   - the venue margin must be BIGGER than 0.5. Taking the higher-venue-GD
#     side: margin 0.5-1.0 wins 43%, 1.0-1.5 47%, 1.5-2.5 53%.
#   - an AWAY pick is worth about half a goal of margin less than a home one
#     at the same number (away >=1.0 -> 50.4%, away >=1.5 -> 54.9%, home
#     >=1.0 -> 52.6%). So away carries a higher bar.
#   - the old 2.00 straight/DC line rested on FOUR legs in the sample. The
#     book's own price is the better read: below 1.80 is a real favourite,
#     1.80-2.60 is close enough that the draw needs covering.
# Over every leg booked 8-10 Sep this takes 22 legs and wins 20 (91%), vs
# 30/35 (86%) for the first pass.
MARGIN_HOME = 1.0   # goals per game, favourite minus opponent at the venue
MARGIN_AWAY = 1.5
MIN_PRICE = 1.30         # 17 Sep: 246 settled legs 9-17 Sep by the favourite's price. 1.15-1.30:
                         # 79% won, -5.1% return (29 legs). 1.30-1.60: 81% won, +14.5% (59 legs).
                         # 15 Sep it was 1.15 after DAC at 1.01; the band under 1.30 still loses.
STRAIGHT_MAX = 1.60      # 14 Sep: was 1.80. Two days of settled legs (106): straight wins at
                         # 1.60-1.94 went 9-9 with six of the nine losses draws; covers in the
                         # same band 15-2. Under 1.60 the straight win went 22-9.
DC_MAX = STRAIGHT_MAX    # 17 Sep: NO covers on favourites above 1.60. Same 246 legs: covers on
                         # favourites at 1.60-2.70 won 59-79% at 1.15-1.40, return -11% over 158
                         # legs (1.60-1.80 -11.8%, 1.80-2.00 0.0%, 2.00-2.30 -18.6%, 2.30-2.70
                         # -7.5%; Phnom Penh 2-1 at 90+1 the last of them). Draw-prone favourites
                         # inside 1.30-1.60 keep the cover (7 of 7, +13%).
SEP = '-' * 96


def outcome(ev, mid, want):
    for m in ev.get('markets', []):
        if str(m.get('id')) == mid and not m.get('specifier'):
            for o in m.get('outcomes', []):
                if o['desc'].replace(' ', '').lower() == want.replace(' ', '').lower():
                    return o
    return None


def gd(pairs):
    return (sum(g for g, _ in pairs) - sum(c for _, c in pairs)) / max(1, len(pairs))


def rec(pairs):
    return (f"{sum(g > c for g, c in pairs)}W{sum(g == c for g, c in pairs)}D"
            f"{sum(g < c for g, c in pairs)}L gd{sum(g for g, _ in pairs) - sum(c for _, c in pairs):+d}")


def fmt(pairs):
    return ' '.join(f"{a}:{b}" for a, b in pairs)


def wider_sheet(f):
    """12 Sep: the three columns the 12 Sep losses had and the rule never read.
    overall  - last 10 games at any venue: (W, D, L)
    h2h      - last 6 meetings from the favourite's side: (W, D, L)
    sot      - the favourite's shots on target per game, for and against
    Each returns None when the feed has nothing."""
    import re as _re
    import fetcher_v3 as F3
    out = dict(h_all=None, a_all=None, h2h=[], h_sot=None, a_sot=None)
    try:
        raw = F3.fetch(f"df_hh_1_{f['id']}", ttl=3600)
        hr, ar, _ = F3.parse_history(raw)
        cut = f['ts'] - 3600
        for key, rows in (('h_all', hr), ('a_all', ar)):
            g = [x for x in rows if 0 < x['kc'] < cut][:10]
            if len(g) >= 5:
                out[key] = (sum(x['gf'] > x['ga'] for x in g), sum(x['gf'] == x['ga'] for x in g), sum(x['gf'] < x['ga'] for x in g))
        seen = set()
        for tab in raw.split('~KA÷'):
            for blk in tab.split('~KB÷')[1:]:
                if not blk.startswith('Head-to-head'):
                    continue
                for g in _re.split(r'~(?=KC÷)', blk):
                    d = dict(_re.findall(r'([A-Z]{2,3})÷([^¬]*)', g))
                    if 'KC' not in d or not d.get('KU') or int(d['KC']) >= cut or d['KC'] in seen:
                        continue
                    seen.add(d['KC'])
                    hn = d.get('KJ', '').lstrip('*'); hs, as_ = int(d['KU']), int(d['KT'])
                    home_is_fhome = hn == f['home']
                    out['h2h'].append((int(d['KC']), hs if home_is_fhome else as_, as_ if home_is_fhome else hs))   # from f['home']'s view
        out['h2h'] = sorted(out['h2h'], reverse=True)[:6]
    except Exception:
        pass
    try:
        rich = DRW._rich(f['id'])
        for side, key in (('home', 'h_sot'), ('away', 'a_sot')):
            s = ((rich or {}).get(f'{side}_stats') or {}).get('sot') or {}
            fo, ag = s.get('series_for') or [], s.get('series_against') or []
            if len(fo) >= 3 and len(ag) >= 3:
                out[key] = (sum(fo) / len(fo), sum(ag) / len(ag))
    except Exception:
        pass
    return out


def warnings_for(row, w):
    """Flags on the FAVOURITE from the wider sheet. Each one was present on a
    12 Sep loss: Juventus U20 (overall 2W of 10), Hubei (out-shot 2.1 v 3.7,
    lost both h2h), Daejeon (lost the last two h2h)."""
    fav_home = row['side'] == 'Home'
    flags = []
    allf = w['h_all'] if fav_home else w['a_all']
    # 12 Sep evening: trips at LEVEL too. Hubei (3W4D3L) and Olympiacos (4W2D4L)
    # both passed the strict "<" and both lost; a favourite that has not won
    # more than it has lost in its last ten is not a favourite the sheet backs.
    if allf and allf[0] <= allf[2]:
        flags.append(f"overall form {allf[0]}W{allf[1]}D{allf[2]}L")
    sot = w['h_sot'] if fav_home else w['a_sot']
    if sot and sot[1] > sot[0]:
        flags.append(f"out-shot on target {sot[0]:.1f} for / {sot[1]:.1f} against")
    # 13 Sep evening: the favourite must CREATE more than the opponent does.
    # Corpus (experiments/fix_audit_13sep.py, prior 7 games' shots on target,
    # any venue): a venue-backed favourite whose SoT-for is below the
    # opponent's wins 46.3% at home (54.7% otherwise) and 36.2% away (56.1%);
    # win-or-draw 71.7% / 65.3% against 78.9% / 75.6%. Suwon 3.9 v 4.1,
    # River Plate 5.6 v 6.6 and Al Saqer 4.2 v 4.8 all lost on 13 Sep; on
    # R785M7 the check removes 3 lost legs and 2 won.
    opp_sot = w['a_sot'] if fav_home else w['h_sot']
    if sot and opp_sot and sot[0] < opp_sot[0]:
        flags.append(f"out-created: SoT for {sot[0]:.1f} v opponent's {opp_sot[0]:.1f}")
    # 14 Sep: the OPPONENT'S form. Corpus (experiments/fix_audit_14sep.py, venue-GD
    # favourites, prior results only): opponent with 7+ wins in its last 10 ->
    # favourite wins 37.5% (67.7% win-or-draw, n 248); opponent's overall wins
    # >= favourite's -> 45.7% / 71.7% (n 3,210) against 54.2% / 78.1%; opponent
    # 3+ wins in its last 4 at the venue -> 43.3% / 67.4% (n 383). Concepcion
    # (8W1D1L) held Colo-Colo, Cali (3 of its last 4 away) won at Once Caldas.
    oppall = w['a_all'] if fav_home else w['h_all']
    if oppall and allf and (oppall[0] >= 7 or oppall[0] >= allf[0]):
        flags.append(f"opponent's form {oppall[0]}W{oppall[1]}D{oppall[2]}L v favourite's {allf[0]}W{allf[1]}D{allf[2]}L")
    opp_pairs = row['ap'] if fav_home else row['hp']
    if len(opp_pairs) >= 4 and sum(g > c for g, c in opp_pairs[:4]) >= 3:
        flags.append("opponent has won 3 of its last 4 at this venue")
    if w['h2h']:
        fav_w = sum((a > b) if fav_home else (b > a) for _, a, b in w['h2h'])
        fav_l = sum((a < b) if fav_home else (b < a) for _, a, b in w['h2h'])
        last2 = w['h2h'][:2]
        lost_last2 = len(last2) == 2 and all((a < b) if fav_home else (b < a) for _, a, b in last2)
        if fav_l > fav_w or lost_last2:
            flags.append(f"h2h {fav_w}W{len(w['h2h']) - fav_w - fav_l}D{fav_l}L" + (" lost last two" if lost_last2 else ""))
    return flags


def build(until_h, days=0, verbose=True):
    now = dt.datetime.now(tz=A.WAT)
    start = now + dt.timedelta(hours=1)
    cutoff = now.replace(hour=until_h, minute=0, second=0, microsecond=0)
    if cutoff <= now:
        cutoff += dt.timedelta(days=1)
    cutoff += dt.timedelta(days=days)
    print(f"window {start:%a %H:%M} -> {cutoff:%a %d %H:%M} WAT", flush=True)
    evs = [e for e in A.fetch_events_full()
           if start < dt.datetime.fromtimestamp(int(e.get('estimateStartTime', 0)) / 1000, tz=A.WAT) <= cutoff]
    seen, fx = set(), []
    for off in range(max(2, (cutoff.date() - now.date()).days) + 1):
        for f in F2.get_fixtures(off):
            if f['id'] not in seen:
                seen.add(f['id']); fx.append(f)
    pairs = D.join(evs, fx,
                   lambda e: dt.datetime.fromtimestamp(int(e['estimateStartTime']) / 1000, tz=A.WAT),
                   lambda f: dt.datetime.fromtimestamp(f['ts'], tz=A.WAT))
    print(f"sportybet in window {len(evs)}  |  joined to flashscore {len(pairs)}", flush=True)
    rows = []
    for ev, f, _ in pairs:
        m1 = {o['desc']: o for o in (next((m for m in ev.get('markets', []) if str(m.get('id')) == '1' and not m.get('specifier')), {}) or {}).get('outcomes', [])}
        if 'Home' not in m1 or 'Away' not in m1:
            continue
        o1, o2 = float(m1['Home']['odds']), float(m1['Away']['odds'])
        try:
            hp, ap = DRW.venue_form(f['id'], f['ts'])
        except Exception:
            hp, ap = [], []
        row = dict(ev=ev, f=f, ts=f['ts'], league=f.get('league') or '?', home=f['home'], away=f['away'],
                   o1=o1, ox=float(m1['Draw']['odds']) if 'Draw' in m1 else None, o2=o2, hp=hp, ap=ap,
                   pick=None, why='')
        if len(hp) < 4 or len(ap) < 4:
            row['why'] = 'no venue form'; rows.append(row); continue
        if o1 == o2:
            # 13 Sep: River Plate URU v Fenix at 2.65 / 2.95 / 2.65 - a dead heat has
            # no favourite; the tie used to break to Home and the cover lost 0-1.
            row['why'] = f"no favourite: home and away both {o1}"; rows.append(row); continue
        side = 'Home' if o1 <= o2 else 'Away'
        price = o1 if side == 'Home' else o2
        if price < MIN_PRICE:
            row['why'] = f"favourite priced {price:.2f} - below {MIN_PRICE:.2f}, no value"; rows.append(row); continue
        # 15 Sep: a tie between two tiers (Velke Ludince v DAC in the Slovak Cup,
        # York City v Newcastle U21 in the EFL Trophy) - the two venue histories
        # come from different competitions and say nothing about each other.
        # Only DOMESTIC cups: an international tie (Copa Sudamericana, Champions
        # League, a U23 qualifier) is two top-flight sides with domestic histories,
        # and the user's rule is that professional sides with stats are playable.
        lg = f.get('league') or ''
        domestic_cup = (re.search(r'cup|pohar|trophy|coppa|copa|pokal|ta[cç]a|cupa|beker|puchar|kubok|shield', lg, re.I)
                        and not re.match(r'\s*(EUROPE|SOUTH AMERICA|ASIA|AFRICA|WORLD|NORTH)', lg, re.I))
        if domestic_cup:
            try:
                hc, ac = DRW.venue_comps(f['id'], f['ts'])
                top = lambda xs: collections.Counter(x for x in xs if x).most_common(1)[0][0] if any(xs) else None
                hm, am = top(hc), top(ac)
                if hm and am and hm != am:
                    row['why'] = f"cup tie between tiers ({hm} v {am})"; rows.append(row); continue
            except Exception:
                pass
        hg, ag = gd(hp), gd(ap)
        margin = (hg - ag) if side == 'Home' else (ag - hg)
        row.update(side=side, price=price, margin=margin)
        need = MARGIN_HOME if side == 'Home' else MARGIN_AWAY
        if margin < need:
            row['why'] = f"venue margin {margin:+.2f} below {need:.1f} ({side.lower()} favourite)"
            rows.append(row); continue
        fav_pairs = hp if side == 'Home' else ap
        opp_pairs = ap if side == 'Home' else hp
        fav_draws = sum(g == c for g, c in fav_pairs)
        opp_draws = sum(g == c for g, c in opp_pairs)
        w = wider_sheet(f); row['wide'] = w
        opp_all = w['a_all'] if side == 'Home' else w['h_all']
        # 12 Sep evening: a favourite that has drawn 5+ of its last 7 at the venue
        # goes on as a double chance whatever the price (corpus: draws 30.7% vs 22%).
        # 13 Sep: draw-proneness on EITHER side. Corpus, 19,945 qualifying
        # favourites: fav + opponent venue draws 6+ -> straight win 46.1% (55.5%
        # at 0-1), opponent 4+ venue draws -> 48.4%; win-or-draw stays 75-76%.
        # Forward Madison (last three home games 1:1) v Sarasota (3 away draws)
        # went straight at 1.66 and drew. Al Nassr at Al Khaleej (0W5D5L overall,
        # unmeasured on the corpus - it has no overall records) drew at 1.29.
        # 13 Sep afternoon: combined line 6 -> 4 (corpus: straight win 54.2% at 2
        # combined draws, 49.5% at 4; Norrkoping 0:0 had 4). No shot stats on the
        # favourite (Aarhus, Suzano lost with two checks blind) -> cover only.
        fav_sot = w['h_sot'] if side == 'Home' else w['a_sot']
        # 21 Sep: the draw signals add up. Jicaral (1.31) had 2 venue draws, Rosario 1,
        # the head-to-head 2 draws in 5 and Jicaral 3 draws in its last 10 - four
        # signals each under its own threshold, straight win, 1-1. Six of the six
        # straight losses at 1.30-1.60 this week were draws; covers in the band 12-2.
        h2h_d = sum(1 for _, x, y in (w.get('h2h') or []) if x == y)
        fav_all = w['h_all'] if side == 'Home' else w['a_all']
        signals = fav_draws + opp_draws + h2h_d + (1 if fav_all and fav_all[1] >= 3 else 0)
        drawy = (fav_draws >= 5 or opp_draws >= 4 or fav_draws + opp_draws >= 4
                 or (opp_all and opp_all[1] >= 4) or fav_sot is None or signals >= 5)
        row['drawy'] = drawy
        if price < STRAIGHT_MAX and not drawy:
            o = outcome(ev, '1', side)
            row.update(pick='win', label=f"1X2 / {side}", o=o)
        elif price >= DC_MAX:
            row['why'] = f"favourite priced {price:.2f} - covers above {DC_MAX:.2f} returned -11% on 158 legs"; rows.append(row); continue
        elif drawy:
            want = 'Home or Draw' if side == 'Home' else 'Draw or Away'
            o = outcome(ev, '10', want)
            tag = ('  [no shot stats]' if fav_sot is None else f"  [draw-prone: fav {fav_draws} opp {opp_draws} venue, opp overall {opp_all}]") if drawy else ''
            row.update(pick='dc', label=f"Double Chance / {want}" + tag, o=o)
        else:
            row['why'] = f"favourite priced {price:.2f} (above {DC_MAX})"; rows.append(row); continue
        if not row.get('o'):
            row['why'] = 'market not offered'; row['pick'] = None
        if row['pick']:
            row['flags'] = warnings_for(row, w)
            if row['flags']:
                row['why'] = 'flagged: ' + '; '.join(row['flags']); row['pick'] = None
        rows.append(row)
    rows.sort(key=lambda r: (r['league'], r['ts']))
    return rows


def yesterday_rows():
    """The rule replayed across yesterday's WHOLE board (cached venue form +
    the results feed): one row per qualifying game with the side the rule
    backed and what happened. Used by yesterday_check() and the website's
    Console."""
    import fetcher_v3 as F3
    res = {}
    for sct in F3.sections(F3.fetch('f_1_-1_1_en-ng_1', ttl=1800)):
        if 'AA' in sct and sct.get('AG') is not None and sct.get('AH') is not None:
            res[(sct.get('AE'), sct.get('AF'))] = (int(sct['AG']), int(sct['AH']))
    rows = []
    for f in F2.get_fixtures(-1):
        key = (f['home'], f['away'])
        if key not in res or not F3.is_cached(f"df_hh_1_{f['id']}", ttl=999 * 3600):
            continue                      # cached form only - this must stay fast
        try:
            hp, ap = DRW.venue_form(f['id'], f['ts'])
        except Exception:
            continue
        if len(hp) < 4 or len(ap) < 4:
            continue
        hg, ag = gd(hp), gd(ap)
        if hg == ag:
            continue
        side = 'H' if hg > ag else 'A'
        m = abs(hg - ag)
        if not ((side == 'H' and m >= MARGIN_HOME) or (side == 'A' and m >= MARGIN_AWAY)):
            continue
        gh, ga = res[key]
        won = (gh > ga) if side == 'H' else (ga > gh)
        rows.append(dict(side=side, won=won, draw=gh == ga, match=f"{f['home']} v {f['away']}",
                         league=f.get('league'), score=f"{gh}-{ga}", margin=round(m, 2)))
    return rows


def yesterday_summary(rows):
    """[(label, n, won%, win-or-draw%)] for all / home / away."""
    out = []
    for lab, rs in (('all', rows), ('home', [r for r in rows if r['side'] == 'H']), ('away', [r for r in rows if r['side'] == 'A'])):
        if rs:
            w = sum(r['won'] for r in rs); d = sum(r['draw'] for r in rs)
            out.append((lab, len(rs), w / len(rs), (w + d) / len(rs)))
    return out


def yesterday_check():
    """12 Sep: run BEFORE booking, every time. Applies the rule to every game
    played yesterday and prints how the venue-backed side did across the
    WHOLE board, next to the corpus norm. This is the "was it the rule or the
    night" test, and on 12 Sep it was run after booking instead of before. It
    reports; it does not block - one bad night is noise (Fri 11 Sep: 55.8%
    across 208 games, our 26 legs 35% outright losses = the bad end of normal)."""
    try:
        rows = yesterday_rows()
        if len(rows) < 30:
            print(f"yesterday check: only {len(rows)} qualifying games with cached form - skipped"); return
        for lab, n, w, wd in yesterday_summary(rows):
            print(f"yesterday check {lab:5} n {n:>4}  won {w:.1%}  win-or-draw {wd:.1%}"
                  f"   (corpus: home 52.6% / 75.9%, away 54.9% / 73.8%)")
    except Exception as e:
        print(f"yesterday check failed: {type(e).__name__}: {e}")


def main():
    until = int(sys.argv[sys.argv.index('--until') + 1]) if '--until' in sys.argv else 23
    yesterday_check()
    days = int(sys.argv[sys.argv.index('--days') + 1]) if '--days' in sys.argv else 0
    dry = '--dry' in sys.argv
    top = int(sys.argv[sys.argv.index('--top') + 1]) if '--top' in sys.argv else 0
    rows = build(until, days)
    picks = [r for r in rows if r['pick']]
    top = min(top, A.MAX_CODE) if top else A.MAX_CODE      # SportyBet slip cap
    if len(picks) > top:
        # "best" = most likely to land, which is the book's own implied
        # probability of the SELECTION (a double chance price already carries
        # its cover). Venue margin breaks ties.
        picks = sorted(picks, key=lambda r: (float(r['o']['odds']), -r['margin']))[:top]
        print(f"\n>> keeping the {top} most likely legs of {len([r for r in rows if r['pick']])}")
    print(f"\n{SEP}\nALL GAMES IN THE WINDOW - {len(rows)} with prices, {len(picks)} qualify\n{SEP}")
    cur = None
    for r in rows:
        if r['league'] != cur:
            cur = r['league']; print(f"\n{cur}")
        t = dt.datetime.fromtimestamp(r['ts'], tz=A.WAT)
        tag = f"PICK {r['label']} @{float(r['o']['odds']):.2f}" if r['pick'] else f"skip: {r['why']}"
        print(f"  {t:%a %H:%M}  {r['home']} v {r['away']}    {r['o1']} / {r['ox']} / {r['o2']}    {tag}")
        if r['hp'] and r['ap']:
            print(f"      {r['home'][:20]:20} HOME {fmt(r['hp']):32} {rec(r['hp'])}")
            print(f"      {r['away'][:20]:20} AWAY {fmt(r['ap']):32} {rec(r['ap'])}")
        w = r.get('wide')
        if w:
            h2 = '  '.join(f"{a}:{b}" for _, a, b in w['h2h']) or 'none'
            print(f"      overall last10: home {w['h_all']}  away {w['a_all']}   h2h (home view): {h2}   "
                  f"SoT for/against: home {tuple(round(x, 1) for x in w['h_sot']) if w['h_sot'] else '-'}  away {tuple(round(x, 1) for x in w['a_sot']) if w['a_sot'] else '-'}")
    if not picks:
        print("\n>> nothing qualifies"); return
    picks.sort(key=lambda r: r['ts'])
    combo = 1.0
    for r in picks:
        combo *= float(r['o']['odds'])
    print(f"\n{SEP}\nSLIP - {len(picks)} legs  ~{combo:,.0f}x\n{SEP}")
    for r in picks:
        t = dt.datetime.fromtimestamp(r['ts'], tz=A.WAT)
        print(f"  {t:%a %H:%M}  {r['home']} v {r['away']:26} {r['label']:30} @{float(r['o']['odds']):.2f}   margin {r['margin']:+.2f}")
    if dry:
        print("\n(dry run - nothing booked)"); return
    sels = [dict(eventId=r['ev']['eventId'], productId=3, marketId=('10' if r['pick'] == 'dc' else '1'),
                 specifier='', outcomeId=r['o']['id']) for r in picks]
    bk = A.book(sels)
    print('\nbooked', bk)
    if bk and bk.get('code'):
        legs = [(r['ts'], f"{r['ev']['homeTeamName']} v {r['ev']['awayTeamName']}", r['label'], float(r['o']['odds']),
                 [f"{r['home']} HOME {fmt(r['hp'])} {rec(r['hp'])}", f"{r['away']} AWAY {fmt(r['ap'])} {rec(r['ap'])}",
                  f"1X2 {r['o1']}/{r['ox']}/{r['o2']}  favourite {r['side']} venue margin {r['margin']:+.2f}",
                  f"overall last10 home {r.get('wide', {}).get('h_all')} away {r.get('wide', {}).get('a_all')}  h2h {[(a, b) for _, a, b in r.get('wide', {}).get('h2h', [])]}  "
                  f"SoT home {r.get('wide', {}).get('h_sot')} away {r.get('wide', {}).get('a_sot')}"])
                for r in picks]
        A.log_booking(bk['code'], bk.get('url'),
                      f"winners slip {combo:,.0f}x ({len(picks)} legs) until {until}:00 - fix v3: favourite at 1.30-1.60, margin >=1.0 home / >=1.5 away, straight win, cover only when draw-prone",
                      legs)
        print(f"code {bk['code']}  {bk.get('url')}   ({bk.get('booked')}/{bk.get('req')} legs, verified {bk.get('verified')})")


if __name__ == '__main__':
    main()
