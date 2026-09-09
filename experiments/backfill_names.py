#!/usr/bin/env python3
"""Backfill h/a team names on corpus rows that accumulate.py wrote without
them (every harvest row from ~14 Aug to 9 Sep 2026). Names come from
matches.jsonl by match id, else from the match's own df_hh feed, whose
'Last matches: <team>' blocks are ordered home then away. Rewrites
dataset.jsonl atomically. Never run while accumulate.py is appending."""
import json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
import fetcher_v3 as F
DS = os.path.join(HERE, 'dataset.jsonl')
names = {}
for l in open(os.path.join(HERE, 'matches.jsonl')):
    r = json.loads(l)
    if r.get('h') and r.get('a'): names[r['id']] = (r['h'], r['a'])
rows = [json.loads(l) for l in open(DS)]
need = [r for r in rows if not r.get('h') or not r.get('a')]
print(f'rows {len(rows)}  nameless {len(need)}', flush=True)
filled = feed = failed = 0
for i, r in enumerate(need):
    if r['id'] in names:
        r['h'], r['a'] = names[r['id']]; filled += 1; continue
    try:
        _, _, past = F.parse_history(F.fetch(f"df_hh_1_{r['id']}", ttl=999 * 3600))
        ks = [k.replace('Last matches: ', '').strip() for k in past.keys()]
        if len(ks) >= 2:
            r['h'], r['a'] = ks[0], ks[1]; feed += 1
        else:
            failed += 1
    except Exception:
        failed += 1
    if i % 200 == 0 and i: print(f'  {i}/{len(need)}  id {filled}  feed {feed}  failed {failed}', flush=True)
tmp = DS + '.tmp'
with open(tmp, 'w') as fh:
    for r in rows: fh.write(json.dumps(r) + '\n')
os.replace(tmp, DS)
print(f'done: from matches.jsonl {filled}  from feed {feed}  still nameless {failed}')
