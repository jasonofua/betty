#!/usr/bin/env python3
"""Repair the corpus's half-split stats, from cache only.

The harvesters read the stat feed by row order, but it repeats every value
twice as [FT, FT, 1H, 1H, 2H, 2H] - so index 1 is a duplicate of the full
match. Every _h1 field in the corpus is therefore a copy of FT. This re-parses
each cached feed using its own SE section markers (the approach
fetcher_v3.parse_match_stats uses and which the live booker has always used).

Cache-only: parse_match_stats has a 48h TTL and would re-fetch 43k feeds over
the network, so this reads the cache directly with an effectively infinite TTL
and skips anything not already stored.
"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import fetcher_v3 as F

EXP = os.path.dirname(os.path.abspath(__file__))
WANT = {'Corner kicks': 'corners', 'Yellow cards': 'yellow',
        'Shots on target': 'sot', 'Offsides': 'offsides',
        'Fouls': 'fouls', 'Goalkeeper saves': 'saves', 'Total shots': 'shots'}


def split_stats(raw):
    """{'match'|'1h'|'2h': {stat: [home, away]}} using the feed's own sections."""
    out, sec = {}, None
    for f in F.sections(raw):
        if 'SE' in f:
            n = f['SE'].lower()
            sec = 'match' if 'match' in n else '1h' if '1st' in n else '2h' if '2nd' in n else None
            if sec:
                out.setdefault(sec, {})
        if sec and 'SG' in f and f['SG'] in WANT:
            try:
                out[sec][WANT[f['SG']]] = [float(f.get('SH', 0)), float(f.get('SI', 0))]
            except (TypeError, ValueError):
                pass
    return out


rows = [json.loads(l) for l in open(f'{EXP}/dataset.jsonl')]
todo = [r for r in rows if r.get('st')]
print(f'{len(rows)} rows, {len(todo)} with stats', flush=True)
fixed = nocache = nosec = 0
for i, r in enumerate(todo):
    if i % 5000 == 0:
        print(f'  {i}/{len(todo)}  repaired {fixed}, uncached {nocache}, no-sections {nosec}', flush=True)
    if not F.is_cached(f"df_st_1_{r['id']}", ttl=10 ** 9):
        nocache += 1
        r['st'] = {k: v for k, v in r['st'].items() if not k.endswith(('_h1', '_h2'))}
        continue
    try:
        raw = F.fetch(f"df_st_1_{r['id']}", ttl=10 ** 9)
        parts = split_stats(raw)
    except Exception:
        parts = {}
    if not parts.get('1h'):
        nosec += 1
        r['st'] = {k: v for k, v in r['st'].items() if not k.endswith(('_h1', '_h2'))}
        continue
    st = {}
    for sec, suf in (('match', ''), ('1h', '_h1'), ('2h', '_h2')):
        for k, v in (parts.get(sec) or {}).items():
            st[k + suf] = v
    r['st'] = st
    fixed += 1
with open(f'{EXP}/dataset.jsonl', 'w') as out:
    for r in rows:
        out.write(json.dumps(r) + '\n')
h1 = sum(1 for r in rows if any(k.endswith('_h1') for k in (r.get('st') or {})))
print(f'DONE: {fixed} repaired, {nocache} uncached, {nosec} without half sections; '
      f'{h1} rows now carry REAL 1H stats', flush=True)
