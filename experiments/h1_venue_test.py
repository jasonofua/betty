"""4 Oct: venue-only, leak-free rows for every settled 1H Under leg 26 Sep - 3 Oct.
Home side: its HOME league games before the leg's kickoff; away side: its AWAY games.
Friendlies dropped, cut at the first 45-day gap, up to 7 per side, halves from df_sui."""
import sys, re, json, datetime as dt, urllib.request, os
sys.path.insert(0, '/Users/apple/Downloads/draw')
import fetcher_v2 as F2, fetcher_v3 as F3
OUT = '/Users/apple/Downloads/draw/experiments/h1_venue_rows.jsonl'
done = set()
if os.path.exists(OUT):
    for ln in open(OUT):
        try: done.add(json.loads(ln)['key'])
        except Exception: pass
fx = {}
for off in range(-9, 1):
    try:
        for f in F2.get_fixtures(off): fx[f['id']] = f
    except Exception as e: print('fx', off, e, flush=True)
def norm(s): return re.sub(r'[^a-z0-9]', '', (s or '').lower())
B = 'https://betty-production-ea69.up.railway.app'
legs, seen = [], set()
for i in range(9):
    day = (dt.date(2026, 9, 26) + dt.timedelta(i)).isoformat()
    try: codes = json.load(urllib.request.urlopen(f'{B}/api/codes?day={day}', timeout=120))['codes']
    except Exception: continue
    for c in codes:
        for l in c['legs']:
            m = re.search(r'1st Half - Over/Under / Under (1|2)\.5', l['sel'] or '')
            if not m or l['state'] not in ('won', 'lost') or not l.get('kots'): continue
            key = f"{norm(l['match'])[:14]}|{m.group(1)}.5|{l['kots']}"
            if key in seen: continue
            seen.add(key); legs.append((key, l, int(m.group(1))))
print('legs', len(legs), 'done', len(done), flush=True)
def series(rows, venue, ko):
    rs = [r for r in rows if r['venue'] == venue and not r.get('friendly') and 0 < r['kc'] < ko - 3600]
    rs = F3.trim_at_season_gap(rs)[:7]
    out = []
    for r in rs:
        s = F3.parse_match_summary(r['match_id'], r['hg'], r['ag'])
        if not s: continue
        h, a = s['ht_home'], s['ht_away']
        out.append((h, a) if venue == 'home' else (a, h))
    return out
with open(OUT, 'a') as fo:
    for key, l, cap in legs:
        if key in done: continue
        h = l['match'].split(' v ')[0]; ko = int(l['kots'])
        f = next((f for f in fx.values() if abs(int(f['ts']) - ko) <= 5400 and
                  (norm(f['home'])[:6] in norm(h) or norm(h)[:6] in norm(f['home']))), None)
        if not f: continue
        try:
            hr, ar, _ = F3.parse_history(F2.fetch(f"df_hh_1_{f['id']}"))
        except Exception as e:
            print('err', key, e, flush=True); continue
        hp, ap = series(hr, 'home', ko), series(ar, 'away', ko)
        fo.write(json.dumps(dict(key=key, line=cap + 0.5, price=float(l['price']), won=l['state'] == 'won',
                                 match=l['match'], hp=hp, ap=ap)) + '\n'); fo.flush()
print('done', flush=True)
