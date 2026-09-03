#!/usr/bin/env python3
"""DEPTH crawler: build stat HISTORY per team, not breadth across teams.

The stat models keep losing to league averages because a match only gets
usable stat features when BOTH teams already have 3+ prior games WITH stat
sheets in the corpus. Breadth crawling adds new teams with one game each and
never crosses that threshold. This walks the teams we already have and mines
THEIR past matches specifically, so individual teams accumulate 5-7 sheeted
games and the form features finally exist.

    python3 depth_crawl.py [rounds] [per_team]
"""
import json, re, sys, os
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import fetcher_v3 as F

WANT_STATS = ('corners', 'yellow', 'sot', 'offsides', 'fouls', 'saves', 'shots')

EXP = os.path.dirname(os.path.abspath(__file__))
ROUNDS = int(sys.argv[1]) if len(sys.argv) > 1 else 3
PER_TEAM = int(sys.argv[2]) if len(sys.argv) > 2 else 6

# The leagues our slips actually bet, measured from bookings.md (8,722 booked
# legs). Depth crawling all 13k corpus teams is ~55k harvests - days of work
# through a throttled connection - while these leagues cover most fixtures the
# engine ever sees, at ~400-800 teams. Substring match, so tiers and stages of
# the same competition are included.
TARGET_LEAGUES = (
    'ARGENTINA:', 'ENGLAND:', 'ROMANIA: Superliga', 'USA: MLS', 'USA: USL',
    'CHINA: Super League', 'ICELAND:', 'BRAZIL: Serie', 'BOLIVIA:',
    'RUSSIA: Premier', 'SCOTLAND:', 'PERU: Liga 1', 'CHILE:',
    'CZECH REPUBLIC:', 'ECUADOR: Liga Pro', 'SOUTH KOREA: K League',
    'URUGUAY:', 'PARAGUAY:', 'COLOMBIA:', 'MEXICO:', 'DENMARK:', 'NORWAY:',
    'SWEDEN:', 'POLAND:', 'NETHERLANDS:', 'GERMANY: 2.', 'GERMANY: 3.',
    'SPAIN: LaLiga', 'ITALY: Serie', 'FRANCE: Ligue', 'TURKEY:', 'GREECE:',
)


def in_target(lg):
    return any(t.lower() in (lg or '').lower() for t in TARGET_LEAGUES)
NAMES = {'Corner kicks': 'corners', 'Yellow cards': 'yellow', 'Shots on target': 'sot',
         'Offsides': 'offsides', 'Fouls': 'fouls', 'Goalkeeper saves': 'saves'}


def load():
    rows = [json.loads(l) for l in open(f'{EXP}/dataset.jsonl')]
    return rows, {r['id'] for r in rows}


def sheeted_per_team(rows):
    c = defaultdict(int)
    for r in rows:
        if r.get('st') and r.get('h'):
            c[(r['lg'], r['h'])] += 1
            c[(r['lg'], r['a'])] += 1
    return c


def harvest(mid, kc, h, a, comp, out_m, out_d):
    """Full harvest of one match; returns True if banked."""
    try:
        hr, ar, _ = F.parse_history(F.fetch(f'df_hh_1_{mid}', ttl=9999 * 3600))
    except Exception:
        return False
    cut = kc - 3600
    hv = [x for x in hr if x['venue'] == 'home' and 0 < x['kc'] < cut][:7]
    av = [x for x in ar if x['venue'] == 'away' and 0 < x['kc'] < cut][:7]
    if len(hv) < 4 or len(av) < 4:
        return False
    try:
        m = re.findall(r'B[ABCD]÷(\d+)', F.fetch(f'df_sur_1_{mid}', ttl=9999 * 3600))
        if len(m) < 4:
            return False
        h1h, h1a, h2h, h2a = (int(x) for x in m[:4])
    except Exception:
        return False
    st = {}
    # Use the section-aware parser: the raw feed repeats every value
    # twice and orders rows [FT, FT, 1H, 1H, 2H, 2H], so taking row
    # index 1 as the first half silently stored a duplicate of the
    # full match - which is what every _h1 stat in the corpus was.
    try:
        _ps = F.parse_match_stats(mid if 'mid' in dir() else r['id'])
        if _ps:
            for _k, _v in (_ps.get('match') or {}).items():
                if _k in WANT_STATS: st[_k] = [_v[0], _v[1]]
            for _k, _v in (_ps.get('1h') or {}).items():
                if _k in WANT_STATS: st[_k + '_h1'] = [_v[0], _v[1]]
            for _k, _v in (_ps.get('2h') or {}).items():
                if _k in WANT_STATS: st[_k + '_h2'] = [_v[0], _v[1]]
    except Exception:
        pass
    if not st:
        return False          # depth crawl only wants matches WITH stats
    gh, ga = h1h + h2h, h1a + h2a
    out_m.write(json.dumps(dict(id=mid, ts=kc, lg=comp, h=h, a=a, gh=gh, ga=ga)) + '\n')
    out_d.write(json.dumps(dict(id=mid, ts=kc, lg=comp, h=h, a=a,
                                hgf=[x['gf'] for x in hv], hga=[x['ga'] for x in hv],
                                agf=[x['gf'] for x in av], aga=[x['ga'] for x in av],
                                h1=[h1h, h1a], h2=[h2h, h2a], ft=[gh, ga], st=st)) + '\n')
    return True


for rnd in range(ROUNDS):
    rows, have = load()
    depth = sheeted_per_team(rows)
    # teams that need more sheeted games, poorest first - those are the ones
    # blocking their matches from having usable stat features
    targets = sorted({(r['lg'], r['h']) for r in rows if r.get('h') and in_target(r['lg'])} |
                     {(r['lg'], r['a']) for r in rows if r.get('a') and in_target(r['lg'])},
                     key=lambda t: depth.get(t, 0))
    thin = [t for t in targets if depth.get(t, 0) < 5]
    print(f'round {rnd+1}: {len(rows)} matches, {len(thin)} TARGET-LEAGUE teams '
          f'under 5 sheeted games', flush=True)
    # find each thin team's own past matches through any match they appear in
    seeds = {}
    for r in rows:
        for side in ('h', 'a'):
            key = (r['lg'], r.get(side))
            if key in set(thin) and key not in seeds:
                seeds[key] = r['id']
    kept = 0
    out_m = open(f'{EXP}/matches.jsonl', 'a'); out_d = open(f'{EXP}/dataset.jsonl', 'a')
    for i, (team, seed) in enumerate(seeds.items()):
        if i % 100 == 0:
            print(f'  {i}/{len(seeds)} teams, kept {kept}', flush=True)
        try:
            raw = F.fetch(f'df_hh_1_{seed}', ttl=9999 * 3600)
        except Exception:
            continue
        tab = blk = None
        found = 0
        for f in F.sections(raw):
            if 'KA' in f: tab = f['KA']
            if 'KB' in f: blk = f['KB']; continue
            if not blk or not blk.startswith('Last matches') or tab != 'Overall':
                continue
            try:
                mid = f['KP']; kc = int(f['KC'])
            except (KeyError, ValueError, TypeError):
                continue
            hn = (f.get('KJ') or '').lstrip('*'); an = (f.get('KK') or '').lstrip('*')
            if not (mid and hn and an) or mid in have:
                continue
            if team[1] not in (hn, an):
                continue                      # only THIS team's own matches
            if harvest(mid, kc, hn, an, f.get('KF', team[0]), out_m, out_d):
                have.add(mid); kept += 1; found += 1
            if found >= PER_TEAM:
                break
    out_m.close(); out_d.close()
    print(f'round {rnd+1} done: +{kept} sheeted matches', flush=True)

rows, _ = load()
depth = sheeted_per_team(rows)
ready = sum(1 for v in depth.values() if v >= 3)
print(f'FINAL: {len(rows)} matches, {sum(1 for r in rows if r.get("st"))} sheeted, '
      f'{ready} teams with 3+ sheeted games (feature-ready)', flush=True)
