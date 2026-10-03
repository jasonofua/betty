"""3 Oct, user's idea: the true first-half scoring number of a fixture is the home
side's first-half goals SCORED per home game plus the away side's first-half goals
SCORED per away game. Tests whether that splits settled first-half Under legs."""
import sys, re, json, datetime as dt, urllib.request, os
sys.path.insert(0, '/Users/apple/Downloads/draw')
import fetcher_v2 as F2, dynamic_v4 as D
OUT = '/Users/apple/Downloads/draw/experiments/attack_sum_rows.jsonl'
done = set()
if os.path.exists(OUT):
    for ln in open(OUT):
        try: done.add(json.loads(ln)['key'])
        except Exception: pass
fx = {}
for off in range(-8, 1):
    for f in F2.get_fixtures(off): fx[f['id']] = f
def norm(s): return re.sub(r'[^a-z0-9]', '', (s or '').lower())
B = 'https://betty-production-ea69.up.railway.app'
legs, seen = [], set()
for i in range(9):
    day = (dt.date(2026, 9, 26) + dt.timedelta(i)).isoformat()
    try: codes = json.load(urllib.request.urlopen(f'{B}/api/codes?day={day}', timeout=30))['codes']
    except Exception: continue
    for c in codes:
        for l in c['legs']:
            m = re.search(r'1st Half.*Under (1|2)\.5', l['sel'] or '')
            if not m or l['state'] not in ('won', 'lost'): continue
            key = f"{l['match'][:24]}|{m.group(1)}.5|{day}"
            if key in seen: continue
            seen.add(key); legs.append((key, day, l, float(m.group(1) + '.5')))
print('legs', len(legs), 'already done', len(done), flush=True)
with open(OUT, 'a') as fo:
    for key, day, l, line in legs:
        if key in done: continue
        h = l['match'].split(' v ')[0]
        f = next((f for f in fx.values() if norm(f['home'])[:7] == norm(h)[:7]
                  and abs(dt.datetime.fromtimestamp(f['ts']).date().toordinal() - dt.date.fromisoformat(day).toordinal()) <= 1), None)
        if not f: continue
        hr, ar = D.records_for(f['id'])
        if not hr or not ar: continue
        hp = hr.pairs('h1')[1:]; ap = ar.pairs('h1')[1:]        # first entry is this match itself - drop it
        if len(hp) < 4 or len(ap) < 4: continue
        hf = sum(x for x, _ in hp) / len(hp); af = sum(x for x, _ in ap) / len(ap)
        ha = sum(y for _, y in hp) / len(hp); aa = sum(y for _, y in ap) / len(ap)
        row = dict(key=key, line=line, price=float(l['price']), won=l['state'] == 'won',
                   attack=round(hf + af, 3), model=round((hf + aa) / 2 + (af + ha) / 2, 3), match=l['match'])
        fo.write(json.dumps(row) + '\n'); fo.flush()
print('done', flush=True)
