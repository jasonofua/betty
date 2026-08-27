#!/usr/bin/env python3
"""Stage B1: backfill finished matches from Flashscore day feeds.
One fetch per day; each finished match gives FT (AG/AH) and HT (BC/BD) scores.
First validates BC/BD really is the half-time score against known df_sur data."""
import json, re, sys
sys.path.insert(0, '/Users/apple/Downloads/draw')
import fetcher_v3 as F

OUT = '/private/tmp/claude-501/-Users-apple-Downloads-draw/dfc8926f-fbfd-46e0-943c-cd17aa8625bd/scratchpad/matches.jsonl'

# --- validate BC/BD on a few matches with known half scores -----------------
checked = ok = 0
raw = F.fetch('f_1_-2_1_en-ng_1', ttl=48*3600)
for f in list(F.sections(raw)):
    if checked >= 4: break
    if 'AA' in f and f.get('AG') is not None and f.get('BC') is not None:
        try:
            sur = F.fetch(f"df_sur_1_{f['AA']}", ttl=96*3600)
            m = re.findall(r'B[AB]÷(\d+)', sur)
            if len(m) >= 2:
                checked += 1
                if int(m[0]) == int(f['BC']) and int(m[1]) == int(f['BD']):
                    ok += 1
        except Exception:
            pass
print(f'BC/BD half-time validation: {ok}/{checked} agree with df_sur', flush=True)
if checked and ok < checked:
    print('!! BC/BD is NOT reliably the HT score - halves excluded from dataset', flush=True)
USE_HT = (checked > 0 and ok == checked)

n = 0
with open(OUT, 'w') as out:
    for off in range(-1, -181, -1):
        try:
            raw = F.fetch(f'f_1_{off}_1_en-ng_1', ttl=999*3600)
        except Exception as e:
            print(f'day {off}: fetch failed {e}', flush=True)
            continue
        cur = None; day = 0
        for f in F.sections(raw):
            if 'ZA' in f:
                cur = f['ZA']
            elif 'AA' in f and cur and f.get('AG') is not None and f.get('AH') is not None:
                try:
                    row = dict(id=f['AA'], ts=int(f.get('AD', 0)), lg=cur,
                               h=f.get('AE'), a=f.get('AF'),
                               gh=int(f['AG']), ga=int(f['AH']))
                    if USE_HT and f.get('BC') is not None and f.get('BD') is not None:
                        row['h1h'] = int(f['BC']); row['h1a'] = int(f['BD'])
                    out.write(json.dumps(row) + '\n'); day += 1
                except (TypeError, ValueError):
                    pass
        n += day
        if off % 20 == 0:
            print(f'day {off}: total {n} matches', flush=True)
print(f'DONE: {n} finished matches -> matches.jsonl', flush=True)
