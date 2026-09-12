#!/usr/bin/env python3
"""American football - totals, first-half totals and handicaps, no winners.

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
AGREE = 11
MISMATCH_PRICE = 1.05
KEEP = {'219': 'Winner', '225': 'FT O/U', '223': 'Handicap', '68': '1H O/U'}
norm = lambda s: re.sub(r'[^a-z]', '', (s or '').lower())


def get(url):
    return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=A.HDRS), timeout=25)
                      .read().decode('utf-8', 'replace'))


def sb_board():
    evs = []
    for pg in range(1, 6):
        try:
            d = get(BASE + f'pcUpcomingEvents?sportId=sr:sport:16&marketId=1,18,60&pageSize=100&pageNum={pg}&option=1')
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
        for s in F.sections(F.fetch(f'f_5_{off}_1_en-ng_1', ttl=900)):
            if 'ZA' in s:
                cur = s['ZA']
            elif 'AA' in s and s.get('AG') is None:
                games.append(dict(lg=cur, id=s['AA'], ts=int(s['AD']), h=s['AE'], a=s['AF']))
    return games


def venue_games(mid, kickoff, team, suffix):
    hh = F.fetch(f'df_hh_5_{mid}', ttl=3600); out = []
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
                home_is_team = d.get('KJ', '').lstrip('*') == team
                pf, pa = int(d['KU']), int(d['KT'])
                if not home_is_team:
                    pf, pa = pa, pf
                out.append(dict(id=d.get('KP'), pf=pf, pa=pa, home=home_is_team))
    return out[:7]


def first_half(gid, home_is_team):
    try:
        d = dict(re.findall(r'B([A-H])÷(\d+)', F.fetch(f'df_sur_5_{gid}', ttl=999 * 3600)))
        h1, a1 = int(d['A']) + int(d['C']), int(d['B']) + int(d['D'])
    except Exception:
        return None
    return (h1, a1) if home_is_team else (a1, h1)


def lines(mk, key):
    out = []
    for m in mk.get(key, []):
        try:
            v = float(m['spec'].split('=')[-1])
        except ValueError:
            continue
        out.append((v, m))
    return sorted(out)


def score_game(hg, ag, mk):
    """Every qualifying line for one game -> list of (hits, n, label, odds, sel)."""
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
        need = -v
        hcov = sum(x > need for x in hm) + sum(-x > need for x in am); acov = n - hcov
        for want, hits, lab in (('Home', hcov, f"Home {v:+g}"), ('Away', acov, f"Away {-v:+g}")):
            if hits >= AGREE:
                sel = next((s for s in m['outs'] if s[0].startswith(want)), None)
                if sel:
                    cands.append((hits, n, f"Handicap {lab}", sel[1], dict(mid=m['id'], spec=m['spec'], oid=sel[2])))
    return cands


def best_of(cands):
    """One leg per game: highest agreement, then the line nearest the middle of
    its ladder (the middle line is where the book's own number sits)."""
    if not cands:
        return None
    top = max(c[0] for c in cands)
    tied = [c for c in cands if c[0] == top]
    tied.sort(key=lambda c: abs(c[3] - 1.85))
    return tied[0]


def main():
    days = int(sys.argv[sys.argv.index('--days') + 1]) if '--days' in sys.argv else 0
    dry = '--dry' in sys.argv
    global AGREE
    if '--agree' in sys.argv:
        AGREE = int(sys.argv[sys.argv.index('--agree') + 1])
    now = dt.datetime.now(tz=A.WAT); start = now + dt.timedelta(hours=1)
    sb = [e for e in sb_board() if dt.datetime.fromtimestamp(int(e['estimateStartTime']) / 1000, tz=A.WAT) > start]
    fs = fs_board(days)
    print(f"sportybet american football {len(sb)} events  |  flashscore {len(fs)} fixtures over {days + 1} day(s)", flush=True)
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
        if win and min(o[1] for o in win[0]['outs']) <= MISMATCH_PRICE:
            skipped['class mismatch (winner <= 1.05)'] += 1; print(f"  {t:%a %H:%M}  {name:52} skip: class mismatch"); continue
        hg = venue_games(f['id'], f['ts'], f['h'], '- Home'); ag = venue_games(f['id'], f['ts'], f['a'], '- Away')
        if len(hg) < 5 or len(ag) < 5:
            skipped['no venue form'] += 1; print(f"  {t:%a %H:%M}  {name:52} skip: venue form {len(hg)}/{len(ag)}"); continue
        for g in hg + ag:
            g['fh'] = first_half(g['id'], g['home']) if g['id'] else None
        cands = score_game(hg, ag, mk)
        pick = best_of(cands)
        fmt = lambda gs: ' '.join(f"{g['pf']}:{g['pa']}" for g in gs)
        if not pick:
            skipped['no line at agreement'] += 1
            print(f"  {t:%a %H:%M}  {name:52} no line reaches {AGREE}/14   H {fmt(hg)} | A {fmt(ag)}"); continue
        hits, n, lab, odds, sel = pick
        print(f"  {t:%a %H:%M}  {name:52} PICK {lab} @{odds:.2f}  {hits}/{n}   H {fmt(hg)} | A {fmt(ag)}")
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
        A.log_booking(bk['code'], bk.get('url'), f"american football slip {combo:,.1f}x ({len(legs)} legs) - totals/1H/handicap, venue histories agree >= {AGREE}/14",
                      [(l['ts'], l['match'], l['label'], l['odds'], l['stats']) for l in legs])
        print(f"code {bk['code']}  {bk.get('url')}   ({bk.get('booked')}/{bk.get('req')} legs, verified {bk.get('verified')})")


if __name__ == '__main__':
    main()
