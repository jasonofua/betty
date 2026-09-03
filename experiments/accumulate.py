#!/usr/bin/env python3
"""Daily corpus growth: append newly finished matches (last 3 days, deduped)
to matches.jsonl and harvest their features+labels into dataset.jsonl.
Run any day; ~2 minutes warm. At ~450 matches/day the corpus reaches
30-40k in two to three months - neural-network scale - and every model
(composite, XGBoost, NN) retrains against the same beat-the-incumbent gate."""
import json, re, sys, os
HERE=os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import fetcher_v3 as F

WANT_STATS = ('corners', 'yellow', 'sot', 'offsides', 'fouls', 'saves', 'shots')

M=os.path.join(HERE,'matches.jsonl'); DS=os.path.join(HERE,'dataset.jsonl')
seen={json.loads(l)['id'] for l in open(M)} if os.path.exists(M) else set()
new=[]
for off in (-1,-2,-3):
    try: raw=F.fetch(f'f_1_{off}_1_en-ng_1', ttl=6*3600)
    except Exception: continue
    cur=None
    for f in F.sections(raw):
        if 'ZA' in f: cur=f['ZA']
        elif 'AA' in f and cur and f.get('AG') is not None and f.get('AH') is not None and f['AA'] not in seen:
            try:
                new.append(dict(id=f['AA'],ts=int(f.get('AD',0)),lg=cur,h=f.get('AE'),a=f.get('AF'),
                                gh=int(f['AG']),ga=int(f['AH'])))
                seen.add(f['AA'])
            except (TypeError,ValueError): pass
with open(M,'a') as out:
    for r in new: out.write(json.dumps(r)+'\n')
print(f'{len(new)} new finished matches appended (corpus now {len(seen)})')
kept=0
with open(DS,'a') as out:
    for i,r in enumerate(new):
        if i%100==0 and i: print(f'  harvest {i}/{len(new)}, kept {kept}', flush=True)
        try:
            hr,ar,_=F.parse_history(F.fetch(f"df_hh_1_{r['id']}", ttl=999*3600))
        except Exception: continue
        cut=r['ts']-3600
        hv=[x for x in hr if x['venue']=='home' and 0<x['kc']<cut][:7]
        av=[x for x in ar if x['venue']=='away' and 0<x['kc']<cut][:7]
        if len(hv)<4 or len(av)<4: continue
        try:
            m=re.findall(r'B[ABCD]÷(\d+)', F.fetch(f"df_sur_1_{r['id']}", ttl=999*3600))
            if len(m)<4: continue
            h1h,h1a,h2h,h2a=(int(x) for x in m[:4])
        except Exception: continue
        if h1h+h2h!=r['gh'] or h1a+h2a!=r['ga']: continue
        # the full stat sheet: corners, cards, SoT, offsides, fouls, saves
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
        out.write(json.dumps(dict(id=r['id'],ts=r['ts'],lg=r['lg'],
            hgf=[x['gf'] for x in hv],hga=[x['ga'] for x in hv],
            agf=[x['gf'] for x in av],aga=[x['ga'] for x in av],
            h1=[h1h,h1a],h2=[h2h,h2a],ft=[r['gh'],r['ga']],st=st))+'\n')
        kept+=1
print(f'{kept} new training rows -> dataset.jsonl')
