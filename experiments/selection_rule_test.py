"""6 Oct: which way of choosing ONE option per game actually wins? Leak-free on the
football-data history (experiments/odds_history.jsonl): each match's venue windows are
the home side's last 7 home games and the away side's last 7 away games, same league and
season, before kickoff. Options with prices in the data: 1X, X2, 12 (from the 1X2 prices)
and Over/Under 2.5. Book chance = no-margin probability from Bet365 (Pinnacle if missing).
Rules: A highest venue rate; B book's most likely; C book's most likely where the venue
record agrees (venue rate >= book chance); D venue rate shrunk toward the book (k games)."""
import json, collections, statistics
rows=[]
for ln in open('/Users/apple/Downloads/draw/experiments/odds_history.jsonl'):
    r=json.loads(ln)
    if r.get('hg') is None or r.get('season','') < '2122': continue
    h,d,a=r.get('b365h') or r.get('psh'), r.get('b365d') or r.get('psd'), r.get('b365a') or r.get('psa')
    o,u=r.get('avg>2.5'), r.get('avg<2.5')
    if not (h and d and a and o and u): continue
    rows.append(r)
rows.sort(key=lambda r:r['ts'])
home=collections.defaultdict(list); away=collections.defaultdict(list)
legs=collections.defaultdict(list)
for r in rows:
    k=(r['lg'],r['season'])
    hp=home[(k,r['home'])][-7:]; ap=away[(k,r['away'])][-7:]
    if len(hp)>=5 and len(ap)>=5:
        h,d,a=r.get('b365h') or r.get('psh'), r.get('b365d') or r.get('psd'), r.get('b365a') or r.get('psa')
        s=1/h+1/d+1/a; ph,pd,pa=(1/h)/s,(1/d)/s,(1/a)/s
        o,u=r['avg>2.5'],r['avg<2.5']; s2=1/o+1/u; po,pu=(1/o)/s2,(1/u)/s2
        hg,ag=r['hg'],r['ag']
        opts={
         '1X':(ph+pd, sum(x>=y for x,y in hp)+sum(x<=y for x,y in ap), hg>=ag),
         'X2':(pd+pa, sum(x<=y for x,y in hp)+sum(x>=y for x,y in ap), hg<=ag),
         '12':(ph+pa, sum(x!=y for x,y in hp)+sum(x!=y for x,y in ap), hg!=ag),
         'O2.5':(po, sum(x+y>2 for x,y in hp)+sum(x+y>2 for x,y in ap), hg+ag>2),
         'U2.5':(pu, sum(x+y<3 for x,y in hp)+sum(x+y<3 for x,y in ap), hg+ag<3)}
        n=len(hp)+len(ap); early=r['season']<'2324'
        def add(rule,key):
            if key is None: return
            p,hits,won=opts[key]; legs[rule].append((won,p,hits/n,early))
        A=max(opts,key=lambda k:(opts[k][1],opts[k][0])); add('A highest venue rate',A if opts[A][1]/n>=0.75 else None)
        Bk=max(opts,key=lambda k:opts[k][0]); add('B book most likely',Bk)
        C=[k for k in opts if opts[k][1]/n>=opts[k][0]]
        add('C book most likely, record agrees',max(C,key=lambda k:opts[k][0]) if C else None)
        for kk in (14,28):
            Dk=max(opts,key=lambda k:(opts[k][1]+kk*opts[k][0])/(n+kk)); add(f'D shrunk (k={kk})',Dk)
        # A where venue >> book (the losing group yesterday)
        add('A, record 10+ pts above book',A if opts[A][1]/n>=0.75 and opts[A][1]/n-opts[A][0]>=0.10 else None)
        add('A, record within 5 pts of book',A if opts[A][1]/n>=0.75 and abs(opts[A][1]/n-opts[A][0])<0.05 else None)
    home[(k,r['home'])].append((r['hg'],r['ag'])); away[(k,r['away'])].append((r['ag'],r['hg']))
print(len(rows),'priced matches since 2021-22')
for rule,L in legs.items():
    w=sum(x[0] for x in L); p=sum(x[1] for x in L)
    e=[x for x in L if x[3]]; l=[x for x in L if not x[3]]
    f=lambda S: f"{sum(x[0] for x in S)/max(1,len(S)):.1%} v book {sum(x[1] for x in S)/max(1,len(S)):.1%}"
    print(f"{rule:36} n={len(L):6}  won {w/len(L):.1%}  book {p/len(L):.1%}  edge {(w-p)/len(L)*100:+.1f} pts | 21-23 {f(e)} | 23-26 {f(l)}")
