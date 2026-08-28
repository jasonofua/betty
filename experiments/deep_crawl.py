#!/usr/bin/env python3
"""Deep corpus crawl WITH team names and stat sheets.

The history feed's KJ/KK fields carry both team names (the earlier crawl wrote
'?' and starved every per-team stat feature). This walks the frontier back in
time, and for each match banks: names, leak-free venue form, half scores, and
the full stat sheet. Frontier expands as it goes - each newly harvested match's
history feed names more matches.

    python3 deep_crawl.py [max_matches] [floor_iso_date]
"""
import json, re, sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import fetcher_v3 as F
import datetime as dt

EXP = os.path.dirname(os.path.abspath(__file__))
CAP = int(sys.argv[1]) if len(sys.argv) > 1 else 6000
FLOOR = dt.datetime.fromisoformat(sys.argv[2]).timestamp() if len(sys.argv) > 2 else 0
NAMES = {'Corner kicks': 'corners', 'Yellow cards': 'yellow', 'Shots on target': 'sot',
         'Offsides': 'offsides', 'Fouls': 'fouls', 'Goalkeeper saves': 'saves'}

have = {json.loads(l)['id'] for l in open(f'{EXP}/matches.jsonl')}
seeds = [json.loads(l) for l in open(f'{EXP}/dataset.jsonl')]
seed_ids = [r['id'] for r in seeds]

def frontier(ids):
    """match_id -> (kickoff, home, away, home_goals, away_goals, competition)"""
    out = {}
    for pid in ids:
        try:
            raw = F.fetch(f'df_hh_1_{pid}', ttl=9999 * 3600)
        except Exception:
            continue
        tab = blk = None
        for f in F.sections(raw):
            if 'KA' in f: tab = f['KA']
            if 'KB' in f: blk = f['KB']; continue
            if not blk or not blk.startswith('Last matches') or tab != 'Overall':
                continue
            try:
                mid = f['KP']; kc = int(f['KC'])
                hg = int(f.get('KU', '')); ag = int(f.get('KT', ''))
            except (KeyError, ValueError, TypeError):
                continue
            h = (f.get('KJ') or '').lstrip('*'); a = (f.get('KK') or '').lstrip('*')
            if mid and h and a and kc >= FLOOR and mid not in have and mid not in out:
                out[mid] = (kc, h, a, hg, ag, f.get('KF', 'crawl'))
    return out

cand = frontier(seed_ids)
print(f'frontier: {len(cand)} named candidates', flush=True)
mout = open(f'{EXP}/matches.jsonl', 'a'); dout = open(f'{EXP}/dataset.jsonl', 'a')
kept = 0; processed = []
for i, (mid, (kc, h, a, hg, ag, comp)) in enumerate(sorted(cand.items(), key=lambda kv: -kv[1][0])[:CAP]):
    if i % 400 == 0:
        print(f'{i}/{min(CAP,len(cand))} crawled, kept {kept}', flush=True)
    try:
        hr, ar, _ = F.parse_history(F.fetch(f'df_hh_1_{mid}', ttl=9999 * 3600))
    except Exception:
        continue
    cut = kc - 3600
    hv = [x for x in hr if x['venue'] == 'home' and 0 < x['kc'] < cut][:7]
    av = [x for x in ar if x['venue'] == 'away' and 0 < x['kc'] < cut][:7]
    if len(hv) < 4 or len(av) < 4:
        continue
    try:
        m = re.findall(r'B[ABCD]÷(\d+)', F.fetch(f'df_sur_1_{mid}', ttl=9999 * 3600))
        if len(m) < 4: continue
        h1h, h1a, h2h, h2a = (int(x) for x in m[:4])
    except Exception:
        continue
    if h1h + h2h != hg or h1a + h2a != ag:
        continue
    st = {}
    try:
        sraw = F.fetch(f'df_st_1_{mid}', ttl=9999 * 3600)
        acc = {}
        for row in sraw.split('~'):
            g = re.search(r'SG÷([^¬]+)¬SH÷([\d.]+)¬SI÷([\d.]+)', row)
            if g and g.group(1) in NAMES:
                acc.setdefault(NAMES[g.group(1)], []).append((float(g.group(2)), float(g.group(3))))
        for k, v in acc.items():
            st[k] = [v[0][0], v[0][1]]
            if len(v) >= 3 and v[1][0] <= v[0][0] and v[1][1] <= v[0][1]:
                st[k + '_h1'] = [v[1][0], v[1][1]]
    except Exception:
        pass
    mout.write(json.dumps(dict(id=mid, ts=kc, lg=comp, h=h, a=a, gh=hg, ga=ag)) + '\n')
    dout.write(json.dumps(dict(id=mid, ts=kc, lg=comp, h=h, a=a,
                               hgf=[x['gf'] for x in hv], hga=[x['ga'] for x in hv],
                               agf=[x['gf'] for x in av], aga=[x['ga'] for x in av],
                               h1=[h1h, h1a], h2=[h2h, h2a], ft=[hg, ag], st=st)) + '\n')
    have.add(mid); kept += 1
mout.close(); dout.close()
print(f'DEEP CRAWL DONE: +{kept} named matches with stat sheets', flush=True)
