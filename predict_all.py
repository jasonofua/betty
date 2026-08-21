#!/usr/bin/env python3
"""Full-time + half-time market model per match.
FT markets from recent full-time scores; HT markets from each team's recent
1st-half scores (df_sui per historical match). Half markets only emitted when a
team has >=4 matches with half-time data."""
import sys, time, urllib.request
from math import exp, factorial
import os as _o; sys.path.append(_o.path.dirname(_o.path.abspath(__file__)))
import fetcher_v2 as F
from datetime import datetime, timezone

BASE = 'https://global.flashscore.ninja/2030/x/feed/'
HDRS = {'x-fsign': 'SW9D1eZo', 'User-Agent': 'Mozilla/5.0'}
SEPF, SEPK = chr(0xac), chr(0xf7)

def pois(k, lam): return exp(-lam) * lam**k / factorial(k)
def grid(lh, la, n=8):
    return {(h, a): pois(h, lh) * pois(a, la) for h in range(n+1) for a in range(n+1)}
def totdist(lam, n=10):
    return [pois(k, lam) for k in range(n+1)]

# ---- recent matches with KP/venue + venue-adjusted gf/ga ----
def recent(raw):
    tab = blk = None; out = {}
    for s in F.sections(raw):
        if 'KA' in s: tab = s['KA']
        if 'KB' in s: blk = s['KB']; continue
        if tab == 'Overall' and blk and blk.startswith('Last matches') and 'KP' in s and 'KU' in s:
            try: ku, kt = int(s['KU']), int(s['KT'])
            except: continue
            v = s.get('KS', '')
            gf, ga = (ku, kt) if v == 'home' else (kt, ku)
            out.setdefault(blk, []).append(dict(kp=s['KP'], ks=v, gf=gf, ga=ga))
    names = list(out)
    return (out[names[0]] if names else []), (out[names[1]] if len(names) > 1 else [])

def ht_split(mid):
    try:
        raw = urllib.request.urlopen(urllib.request.Request(BASE+'df_sui_1_'+mid, headers=HDRS), timeout=10).read().decode('utf-8', 'replace')
    except Exception:
        return None
    for chunk in raw.split('~'):
        f = {}
        for kv in chunk.split(SEPF):
            if SEPK in kv: k, v = kv.split(SEPK, 1); f[k] = v
        if f.get('AC') == '1st Half' and 'IG' in f and 'IH' in f:
            try: return int(f['IG']), int(f['IH'])
            except: return None
    return None

def half_rates(rows):
    f1 = a1 = f2 = a2 = n = 0
    for r in rows[:6]:
        ht = ht_split(r['kp'])
        if ht is None: continue
        ig, ih = ht
        t1f, t1a = (ig, ih) if r['ks'] == 'home' else (ih, ig)
        t2f, t2a = r['gf'] - t1f, r['ga'] - t1a
        if t2f < 0 or t2a < 0: continue
        f1 += t1f; a1 += t1a; f2 += t2f; a2 += t2a; n += 1
    if n < 4: return None
    return dict(f1=f1/n, a1=a1/n, f2=f2/n, a2=a2/n, n=n)

def ft_markets(lh, la):
    g = grid(lh, la); P = lambda c: sum(p for (h, a), p in g.items() if c(h, a))
    m = {'1 Home': P(lambda h,a:h>a), 'X Draw': P(lambda h,a:h==a), '2 Away': P(lambda h,a:h<a),
         '1X': P(lambda h,a:h>=a), 'X2': P(lambda h,a:h<=a), '12': P(lambda h,a:h!=a),
         'AH+1 Home': P(lambda h,a:a-h<=1), 'AH+1 Away': P(lambda h,a:h-a<=1),
         'Over1.5': P(lambda h,a:h+a>=2), 'Over2.5': P(lambda h,a:h+a>=3), 'Under2.5': P(lambda h,a:h+a<=2),
         'BTTS Y': P(lambda h,a:h>=1 and a>=1), 'BTTS N': P(lambda h,a:h==0 or a==0),
         'Home CS': P(lambda h,a:a==0), 'Away CS': P(lambda h,a:h==0),
         'Home or O2.5': P(lambda h,a:h>a or h+a>=3), 'Away or O2.5': P(lambda h,a:h<a or h+a>=3)}
    return m

def half_markets(hr, ar):
    lh1=(hr['f1']+ar['a1'])/2; la1=(ar['f1']+hr['a1'])/2
    lh2=(hr['f2']+ar['a2'])/2; la2=(ar['f2']+hr['a2'])/2
    g1=grid(lh1,la1); g2=grid(lh2,la2)
    P1=lambda c: sum(p for (h,a),p in g1.items() if c(h,a))
    P2=lambda c: sum(p for (h,a),p in g2.items() if c(h,a))
    m={}
    m['1H 1 Home']=P1(lambda h,a:h>a); m['1H X']=P1(lambda h,a:h==a); m['1H 2 Away']=P1(lambda h,a:h<a)
    m['1H Over0.5']=P1(lambda h,a:h+a>=1); m['1H Over1.5']=P1(lambda h,a:h+a>=2); m['1H BTTS']=P1(lambda h,a:h>=1 and a>=1)
    m['2H 1 Home']=P2(lambda h,a:h>a); m['2H X']=P2(lambda h,a:h==a); m['2H 2 Away']=P2(lambda h,a:h<a)
    m['2H Over0.5']=P2(lambda h,a:h+a>=1); m['2H Over1.5']=P2(lambda h,a:h+a>=2)
    m['2H 1X']=P2(lambda h,a:h>=a); m['2H X2']=P2(lambda h,a:h<=a)
    # win either half (independent halves)
    m['Home wins a half']=1-(1-P1(lambda h,a:h>a))*(1-P2(lambda h,a:h>a))
    m['Away wins a half']=1-(1-P1(lambda h,a:h<a))*(1-P2(lambda h,a:h<a))
    # both halves under 1.5
    t1=totdist(lh1+la1); t2=totdist(lh2+la2)
    u1=t1[0]+t1[1]; u2=t2[0]+t2[1]
    m['Both halves U1.5']=u1*u2
    # highest scoring half
    p1g=sum(t1[k]*sum(t2[j] for j in range(k)) for k in range(len(t1)))   # 1H>2H
    p2g=sum(t2[k]*sum(t1[j] for j in range(k)) for k in range(len(t2)))   # 2H>1H
    m['1H highest']=p1g; m['2H highest']=p2g; m['Halves equal']=max(0,1-p1g-p2g)
    # HT/FT (independent halves)
    htft={}
    for (h1,a1),pa in g1.items():
        for (h2,a2),pb in g2.items():
            ht='H' if h1>a1 else 'A' if h1<a1 else 'D'
            ft='H' if h1+h2>a1+a2 else 'A' if h1+h2<a1+a2 else 'D'
            htft[ht+'/'+ft]=htft.get(ht+'/'+ft,0)+pa*pb
    for k,v in htft.items(): m['HT/FT '+k]=v
    return m

def main():
    offset = int(sys.argv[sys.argv.index('--offset')+1]) if '--offset' in sys.argv else 1
    fx = F.get_fixtures(offset)
    now = time.time(); fx = [f for f in fx if f['ts'] > now + 600]
    print(f'{len(fx)} fixtures; fetching FT+HT history...', flush=True)
    blocks = []
    for i, f in enumerate(fx):
        raw = F.fetch(f"df_hh_1_{f['id']}")
        if not raw: continue
        hrows, arows = recent(raw)
        if not hrows or not arows: continue
        ha = lambda rs, k: sum(r[k] for r in rs[:10]) / len(rs[:10])
        lh = max(0.2, min((ha(hrows,'gf')+ha(arows,'ga'))/2, 4.5))
        la = max(0.2, min((ha(arows,'gf')+ha(hrows,'ga'))/2, 4.5))
        ft = ft_markets(lh, la)
        hr, ar = half_rates(hrows), half_rates(arows)
        ko = datetime.fromtimestamp(f['ts'], tz=timezone.utc).strftime('%H:%M')
        L = [f"{ko}  {f['home']} v {f['away']}  | {f['league']}"]
        L.append("    FT: " + " | ".join(f"{k} {v*100:.0f}%" for k,v in sorted(ft.items(), key=lambda kv:-kv[1])))
        if hr and ar:
            hm = half_markets(hr, ar)
            # show meaningful half markets (drop <12% clutter)
            top = sorted(((k,v) for k,v in hm.items() if v>=0.12), key=lambda kv:-kv[1])
            L.append("    HT: " + " | ".join(f"{k} {v*100:.0f}%" for k,v in top))
        else:
            L.append("    HT: (no half-time data for this league)")
        blocks.append((ko, "\n".join(L)))
        if (i+1) % 20 == 0: print(f'  ...{i+1}/{len(fx)}', flush=True)
        time.sleep(0.15)
    blocks.sort(key=lambda b: b[0])
    txt = "\n\n".join(b[1] for b in blocks)
    open('/Users/apple/Downloads/draw/predict_all.md', 'w').write(txt)
    nht = sum(1 for _, b in blocks if '(no half-time' not in b)
    print(f'\n{len(blocks)} games priced, {nht} with half-time markets -> predict_all.md', flush=True)

if __name__ == '__main__':
    main()
