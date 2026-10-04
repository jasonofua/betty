"""4 Oct: leak-free first-half rows for every settled 1H Under leg 26 Sep - 3 Oct.
Each side's venue series is cut at the leg's own kickoff (rows with kc >= kickoff dropped),
so later results never leak into an older leg."""
import sys, re, json, datetime as dt, urllib.request, os
sys.path.insert(0, '/Users/apple/Downloads/draw')
import acca as A, fetcher_v2 as F2, fetcher_v3 as F3
OUT = '/Users/apple/Downloads/draw/experiments/h1_leakfree_rows.jsonl'
done = set()
if os.path.exists(OUT):
    for ln in open(OUT):
        try: done.add(json.loads(ln)['key'])
        except Exception: pass
fx = {}
for off in range(-9, 0):
    try:
        for f in F2.get_fixtures(off): fx[f['id']] = f
    except Exception as e: print('fx', off, e, flush=True)
def norm(s): return re.sub(r'[^a-z0-9]', '', (s or '').lower())
B = 'https://betty-production-ea69.up.railway.app'
legs, seen = [], set()
for i in range(8):
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
print('legs', len(legs), 'done', len(done), 'fixtures', len(fx), flush=True)
def cut(rows, side, ser_f, ser_a, ko):
    rs = [r for r in rows if r.get('ks') == side]
    drop = sum(1 for r in rs if (r.get('kc') or 0) >= ko - 3600)
    f, a = (ser_f or [])[drop:], (ser_a or [])[drop:]
    return list(zip(f, a))
with open(OUT, 'a') as fo:
    for key, l, cap in legs:
        if key in done: continue
        h, aw = l['match'].split(' v ')[0], l['match'].split(' v ')[-1]
        ko = int(l['kots'])
        f = next((f for f in fx.values() if abs(int(f['ts']) - ko) <= 5400 and
                  (norm(f['home'])[:6] in norm(h) or norm(h)[:6] in norm(f['home']))), None)
        if not f: continue
        try:
            raw = F2.fetch(f"df_hh_1_{f['id']}")
            hr, ar, _ = A.recent_kc(raw)
            _, _, rich = F3.fetch_rich_history(f['id'], raw=raw)
        except Exception as e:
            print('err', key, e, flush=True); continue
        hp = cut(hr, 'home', rich.get('home_ht_gf_series'), rich.get('home_ht_ga_series'), ko)
        ap = cut(ar, 'away', rich.get('away_ht_gf_series'), rich.get('away_ht_ga_series'), ko)
        row = dict(key=key, line=cap + 0.5, price=float(l['price']), won=l['state'] == 'won',
                   match=l['match'], comp=l.get('comp'), ht=l.get('ht'), hp=hp, ap=ap)
        fo.write(json.dumps(row) + '\n'); fo.flush()
print('done', flush=True)
