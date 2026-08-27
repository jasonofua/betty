#!/usr/bin/env python3
"""Stage B2: per-match harvest. For every backfilled match: df_hh -> both teams'
venue form STRICTLY BEFORE the match (kc leak filter), df_sur -> half labels."""
import json, re, sys
sys.path.insert(0, '/Users/apple/Downloads/draw')
import fetcher_v3 as F

SRC='/private/tmp/claude-501/-Users-apple-Downloads-draw/dfc8926f-fbfd-46e0-943c-cd17aa8625bd/scratchpad/matches.jsonl'
OUT='/private/tmp/claude-501/-Users-apple-Downloads-draw/dfc8926f-fbfd-46e0-943c-cd17aa8625bd/scratchpad/dataset.jsonl'
rows=[json.loads(l) for l in open(SRC)]
done=0; kept=0
with open(OUT,'w') as out:
    for r in rows:
        done+=1
        if done%250==0: print(f'{done}/{len(rows)}  kept {kept}', flush=True)
        try:
            hh = F.fetch(f"df_hh_1_{r['id']}", ttl=999*3600)
            hr, ar, _ = F.parse_history(hh)
        except Exception:
            continue
        cut = r['ts'] - 3600
        hv = [x for x in hr if x['venue']=='home' and 0 < x['kc'] < cut][:7]
        av = [x for x in ar if x['venue']=='away' and 0 < x['kc'] < cut][:7]
        if len(hv)<4 or len(av)<4:
            continue
        try:
            sur = F.fetch(f"df_sur_1_{r['id']}", ttl=999*3600)
            m = re.findall(r'B[ABCD]÷(\d+)', sur)
            if len(m)<4: continue
            h1h,h1a,h2h,h2a = (int(x) for x in m[:4])
        except Exception:
            continue
        if h1h+h2h != r['gh'] or h1a+h2a != r['ga']:
            continue   # halves inconsistent with FT - distrust
        out.write(json.dumps(dict(
            id=r['id'], ts=r['ts'], lg=r['lg'],
            hgf=[x['gf'] for x in hv], hga=[x['ga'] for x in hv],
            agf=[x['gf'] for x in av], aga=[x['ga'] for x in av],
            h1=[h1h,h1a], h2=[h2h,h2a], ft=[r['gh'],r['ga']]))+'\n')
        kept+=1
print(f'DONE: {kept} training matches with leak-free features + half labels', flush=True)
