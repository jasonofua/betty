#!/usr/bin/env python3
"""Which of a day's matches drew, and what the deployed draw gate said about
each one. Reads draw_dataset.jsonl (strictly pre-match features), applies the
deployed pocket from draw_model.pkl with the 2.0 SoT evenness floor, and
prints every match of the day with the FIRST gate term it failed.

    python3 experiments/day_gate_audit.py 2026-09-08 [from_hour_WAT]
"""
import json, pickle, sys, datetime as dt, collections
ROOT = '/Users/apple/Downloads/draw/experiments/'
B = pickle.load(open(ROOT + 'draw_model.pkl', 'rb')); P = B['pocket']
day = sys.argv[1]; from_h = int(sys.argv[2]) if len(sys.argv) > 2 else 0

def fails(f):
    if not f['xg'] < P['xg_max']: return f"xg {f['xg']:.2f} >= {P['xg_max']}"
    if not f['cd'] >= P['cd_min']: return f"combined draws {f['cd']} < {P['cd_min']}"
    if not f['mismatch'] <= P['mm_max']: return f"mismatch {f['mismatch']:.2f} > {P['mm_max']}"
    if f.get('sxg') is None or f.get('smis') is None: return "no SoT history"
    if f['sxg'] > P['sxg_max']: return f"exp SoT {f['sxg']:.1f} > {P['sxg_max']}"
    if f['smis'] > min(P['smis_max'], 2.0): return f"SoT gap {f['smis']:.1f} > 2.0"
    if f['blank'] < P.get('blank_min', 0): return f"blanks {f['blank']} < {P['blank_min']}"
    if abs(f['h_blank'] - f['a_blank']) > P.get('bgap_max', 99): return f"blank split {f['h_blank']}-{f['a_blank']}"
    return ""

rows = []
for l in open(ROOT + 'draw_dataset.jsonl'):
    r = json.loads(l); d = dt.datetime.fromtimestamp(r['ts'])   # local clock = WAT
    if d.strftime('%Y-%m-%d') == day and d.hour >= from_h:
        r['d'] = d; r['why'] = fails(r); rows.append(r)
rows.sort(key=lambda r: r['ts'])
draws = [r for r in rows if r['draw']]
passed = [r for r in rows if not r['why']]
print(f"{day} from {from_h:02d}:00  matches {len(rows)}  draws {len(draws)} ({len(draws)/max(1,len(rows)):.0%})"
      f"  gate passed {len(passed)}  of which drew {sum(r['draw'] for r in passed)}")
print(f"\nTHE DRAWS - and the first gate term each one failed")
print(f"{'ko':>5} {'league':30} {'match':44} {'score':>5}  {'xg':>4} {'cd':>2} {'mm':>4} {'sot':>4} {'gap':>3} {'bl':>2} {'split':>5}  reason")
for r in draws:
    print(f"{r['d'].strftime('%H:%M'):>5} {(r['lg'] or '')[:30]:30} {(r['home'] or '?')[:20]+' v '+(r['away'] or '?')[:20]:44} {r['ft_h']}:{r['ft_a']:<3}"
          f"  {r['xg']:>4.1f} {r['cd']:>2} {r['mismatch']:>4.2f} {(r['sxg'] if r.get('sxg') is not None else -1):>4.1f} {(r['smis'] if r.get('smis') is not None else -1):>3.1f} {r['blank']:>2} {r['h_blank']}-{r['a_blank']:<3}  {r['why'] or 'PASSED'}")
print(f"\nGATE PASSERS")
for r in passed:
    print(f"{r['d'].strftime('%H:%M'):>5} {(r['lg'] or '')[:30]:30} {(r['home'] or '?')[:20]+' v '+(r['away'] or '?')[:20]:44} {r['ft_h']}:{r['ft_a']:<3}"
          f"  {r['xg']:>4.1f} {r['cd']:>2} {r['mismatch']:>4.2f} {r['sxg']:>4.1f} {r['smis']:>3.1f} {r['blank']:>2} {r['h_blank']}-{r['a_blank']:<3}  {'DRAW' if r['draw'] else 'lost'}")
c = collections.Counter(r['why'].split(' ')[0] + ('' if r['why'] else 'PASSED') for r in draws)
print(f"\nwhy the draws were rejected (first failing term): {dict(c)}")
c2 = collections.Counter(r['why'].split(' ')[0] for r in rows if r['why'])
print(f"all matches, first failing term: {dict(c2)}")
