import json, collections
rows=[json.loads(l) for l in open('/Users/apple/Downloads/draw/experiments/h1_venue_rows.jsonl')]
print('rows',len(rows))
def f(s): 
    w=sum(r['won'] for r in s); return f"{w}/{len(s)}={w/max(1,len(s)):.0%}"
for line in (2.5,1.5):
    rs=[r for r in rows if r['line']==line]; cap=int(line)
    print(f'\n== Under {line}: all {f(rs)}')
    thin=[r for r in rs if len(r['hp'])<5 or len(r['ap'])<5]; ok=[r for r in rs if r not in thin]
    print(f'  fewer than 5 venue games a side: {f(thin)}   5+ each: {f(ok)}')
    for r in ok:
        tot=[x+y for x,y in r['hp']+r['ap']]
        r['rate']=sum(v<=cap for v in tot)/len(tot)
        r['minside']=min(sum(x+y<=cap for x,y in r['hp'])/len(r['hp']), sum(x+y<=cap for x,y in r['ap'])/len(r['ap']))
        r['two']=sum(v>=2 for v in tot)/len(tot)
        r['attack']=sum(x for x,_ in r['hp'])/len(r['hp'])+sum(x for x,_ in r['ap'])/len(r['ap'])
        r['brk']=sum(v>cap for v in tot)
    b=collections.defaultdict(list)
    for r in ok: b['venue breaches %d'%min(r['brk'],3)].append(r)
    for k in sorted(b): print(f'  {k}: {f(b[k])}')
    b=collections.defaultdict(list)
    for r in ok: b['weaker side %.0f%%'%(r['minside']*100//10*10)].append(r)
    for k in sorted(b): print(f'  {k}: {f(b[k])}')
    for lo,hi in ((0,.25),(.25,.4),(.4,1.1)):
        print(f"  2+ goal halves {lo}-{hi}: {f([r for r in ok if lo<=r['two']<hi])}")
    for lo,hi in ((0,.5),(.5,.8),(.8,1.1),(1.1,9)):
        print(f"  attack {lo}-{hi}: {f([r for r in ok if lo<=r['attack']<hi])}")
