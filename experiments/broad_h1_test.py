"""4 Oct: broad leak-free first-half set - every finished fixture in a league we have
booked (26 Sep - 4 Oct comps), last five days. Each row: the fixture's own half-time
score (df_sui, validated against FT) and both sides' VENUE first halves before kickoff
(home side's home league games, away side's away league games, friendlies out, cut at
the first 45-day gap, up to 7). Markets settle from the half-time score, so no booking
or price is needed to measure a rule's hit rate."""
import sys, re, json, time, datetime as dt, urllib.request, os
sys.path.insert(0, '/Users/apple/Downloads/draw')
import fetcher_v2 as F2, fetcher_v3 as F3
OUT = '/Users/apple/Downloads/draw/experiments/broad_h1_rows.jsonl'
done = set()
if os.path.exists(OUT):
    for ln in open(OUT):
        try: done.add(json.loads(ln)['fid'])
        except Exception: pass
B = 'https://betty-production-ea69.up.railway.app'
comps = set()
for i in range(9):
    day = (dt.date(2026, 9, 26) + dt.timedelta(i)).isoformat()
    try: codes = json.load(urllib.request.urlopen(f'{B}/api/codes?day={day}', timeout=120))['codes']
    except Exception: continue
    for c in codes:
        for l in c['legs']:
            cp = (l.get('comp') or '')
            if '·' in cp: comps.add(cp.lower())
def key(country, comp):
    toks = [t for t in re.findall(r'[a-z0-9]+', comp.lower()) if t not in ('group', 'a', 'b', 'c', 'd', 'the', 'de', 'la', 'division', 'league', 'liga')]
    return country.lower().strip(), toks
booked = [key(*c.split('·', 1)) for c in comps]
def match(lg):
    if ':' not in lg or re.search(r'women|u19|u20|u21|u23|youth|friendly|reserve', lg, re.I): return False
    c, t = key(*lg.split(':', 1))
    return any(c == bc and (set(t) & set(bt)) for bc, bt in booked)
now = time.time()
fx = []
for off in (-4, -3, -2, -1, 0):
    try: fx += [f for f in F2.get_fixtures(off) if f['ts'] < now - 7200 and match(f['league'])]
    except Exception as e: print('fx', off, e, flush=True)
print('fixtures', len(fx), 'done', len(done), flush=True)
def ser(rows, venue, ko):
    rs = [r for r in rows if r['venue'] == venue and not r.get('friendly') and 0 < r['kc'] < ko - 3600]
    rs = F3.trim_at_season_gap(rs)[:7]; out = []
    for r in rs:
        s = F3.parse_match_summary(r['match_id'], r['hg'], r['ag'])
        if s: out.append((s['ht_home'], s['ht_away']) if venue == 'home' else (s['ht_away'], s['ht_home']))
    return out
import threading
from concurrent.futures import ThreadPoolExecutor
lock = threading.Lock(); fo = open(OUT, 'a'); cnt = [0]
def one(f):
    try:
        hr, ar, _ = F3.parse_history(F2.fetch(f"df_hh_1_{f['id']}"))
        me = next((r for r in hr if r['match_id'] == f['id']), None)
        if not me: return
        s = F3.parse_match_summary(f['id'], me['hg'], me['ag'])
        if not s: return
        ko = int(f['ts'])
        row = dict(fid=f['id'], league=f['league'], ko=ko, home=f['home'], away=f['away'],
                   ft=[me['hg'], me['ag']], ht=[s['ht_home'], s['ht_away']],
                   hp=ser(hr, 'home', ko), ap=ser(ar, 'away', ko))
    except Exception:
        return
    finally:
        with lock:
            cnt[0] += 1
            if cnt[0] % 25 == 0: print(f'  {cnt[0]}', flush=True)
    with lock:
        fo.write(json.dumps(row) + '\n'); fo.flush()
todo = [f for f in fx if f['id'] not in done]
with ThreadPoolExecutor(4) as ex:
    list(ex.map(one, todo))
fo.close()
print('done', flush=True)
