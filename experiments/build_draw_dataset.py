#!/usr/bin/env python3
"""Build the draw-model training set from the match corpus.

Every feature is strictly PRE-MATCH. A corpus row's own `st` block and its h1/h2
scores are that match's RESULT, so they are never features for that row - they
are folded into each team's trailing history AFTER the row is written, keyed on
team name, so a match can only ever see its own past.

Corpus stat coverage (why some fields are here and `shots` is not):
    corners 76%   sot 76%   yellow 71%   offsides 45%   fouls 45%   saves 37%
    shots 2% - and those 990 rows are miscoded (matches with 4+5 total shots),
    so shots is excluded outright rather than imputed.

    python3 experiments/build_draw_dataset.py  ->  experiments/draw_dataset.jsonl
"""
import json, collections, os

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, 'dataset.jsonl')
OUT = os.path.join(ROOT, 'draw_dataset.jsonl')
STATS = ('sot', 'corners', 'yellow', 'offsides', 'fouls', 'saves')
WIN = 10                      # trailing window for stat/half history


def mean(xs):
    """None when there is nothing to average - never a silent 0.0.

    The first cut of this file used a 0.0 default, which turned `shots` into a
    constant that looked like a 92% covered feature."""
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def main():
    rows = []
    for line in open(SRC, encoding='utf-8'):
        r = json.loads(line)
        if not r.get('ft') or len(r['ft']) != 2 or None in r['ft']:
            continue
        if not all(r.get(k) and len(r[k]) >= 5 for k in ('hgf', 'hga', 'agf', 'aga')):
            continue
        rows.append(r)
    rows.sort(key=lambda r: r.get('ts') or 0)
    print(f"corpus rows usable: {len(rows)}")

    hist = collections.defaultdict(list)          # team -> list of past-match dicts
    lg_seen = collections.defaultdict(lambda: [0, 0])

    def trail(team, pre):
        past = hist[team][-WIN:]
        out = {}
        if not past:
            return {f'{pre}_{k}': None for k in
                    list(STATS) + ['htdraw', 'htgoals', 'shgoals', 'btts', 'cs2']} | {f'{pre}_n': 0}
        for k in STATS:
            out[f'{pre}_{k}'] = mean([p.get(k) for p in past])
        out[f'{pre}_htdraw'] = mean([1.0 if p['ht_f'] == p['ht_a'] else 0.0 for p in past])
        out[f'{pre}_htgoals'] = mean([p['ht_f'] + p['ht_a'] for p in past])
        out[f'{pre}_shgoals'] = mean([p['sh_f'] + p['sh_a'] for p in past])
        out[f'{pre}_btts'] = mean([1.0 if p['gf'] > 0 and p['ga'] > 0 else 0.0 for p in past])
        out[f'{pre}_cs2'] = mean([1.0 if p['ga'] == 0 else 0.0 for p in past])
        out[f'{pre}_n'] = len(past)
        return out

    out = 0
    with open(OUT, 'w', encoding='utf-8') as fh:
        for r in rows:
            h, a, lg = r.get('h'), r.get('a'), r.get('lg') or '?'
            ts = r.get('ts') or 0
            hgf, hga = r['hgf'][:7], r['hga'][:7]
            agf, aga = r['agf'][:7], r['aga'][:7]
            h_att, h_def = mean(hgf) or 0, mean(hga) or 0
            a_att, a_def = mean(agf) or 0, mean(aga) or 0

            seen_n, seen_d = lg_seen[lg]
            ft = r['ft']
            rec = dict(
                ts=ts, lg=lg, home=h, away=a, ft_h=ft[0], ft_a=ft[1],
                draw=int(ft[0] == ft[1]), nil=int(ft[0] == 0 and ft[1] == 0),
                h_att=h_att, h_def=h_def, a_att=a_att, a_def=a_def,
                xg=(h_att + a_def) / 2 + (a_att + h_def) / 2,
                mismatch=abs((h_att - h_def) - (a_att - a_def)),
                h_gd=h_att - h_def, a_gd=a_att - a_def,
                h_draws=sum(1 for x, y in zip(hgf, hga) if x == y),
                a_draws=sum(1 for x, y in zip(agf, aga) if x == y),
                h_low=sum(1 for x, y in zip(hgf, hga) if x + y <= 2),
                a_low=sum(1 for x, y in zip(agf, aga) if x + y <= 2),
                h_blank=sum(1 for x in hgf if x == 0),
                a_blank=sum(1 for x in agf if x == 0),
                h_cs=sum(1 for x in hga if x == 0),
                a_cs=sum(1 for x in aga if x == 0),
                lg_draw=(seen_d / seen_n) if seen_n >= 30 else None,
            )
            rec['cd'] = rec['h_draws'] + rec['a_draws']
            rec['low'] = rec['h_low'] + rec['a_low']
            rec['blank'] = rec['h_blank'] + rec['a_blank']
            rec['cs'] = rec['h_cs'] + rec['a_cs']
            rec.update(trail(h, 'h'))
            rec.update(trail(a, 'a'))
            # paired terms: draws want two SIMILAR, quiet sides
            for k in ('sot', 'corners', 'yellow', 'offsides', 'fouls', 'saves',
                      'htdraw', 'htgoals', 'shgoals', 'btts'):
                x, y = rec.get(f'h_{k}'), rec.get(f'a_{k}')
                rec[f'sum_{k}'] = (x + y) if x is not None and y is not None else None
                rec[f'gap_{k}'] = abs(x - y) if x is not None and y is not None else None
            fh.write(json.dumps(rec) + '\n')
            out += 1

            s = r.get('st') or {}
            h1, h2 = r.get('h1') or [None, None], r.get('h2') or [None, None]
            def val(k, i):
                v = s.get(k)
                return v[i] if v and len(v) == 2 and v[i] is not None else None
            for team, i in ((h, 0), (a, 1)):
                d = {k: val(k, i) for k in STATS}
                d.update(gf=ft[i], ga=ft[1 - i],
                         ht_f=h1[i], ht_a=h1[1 - i],
                         sh_f=h2[i], sh_a=h2[1 - i])
                if d['ht_f'] is None:
                    d['ht_f'] = d['ht_a'] = d['sh_f'] = d['sh_a'] = 0
                hist[team].append(d)
            lg_seen[lg][0] += 1
            lg_seen[lg][1] += int(ft[0] == ft[1])

    print(f"wrote {out} rows -> {OUT}")
    rs = [json.loads(l) for l in open(OUT, encoding='utf-8')]
    print(f"\n{'feature':<16}{'coverage':>10}")
    for k in ('xg', 'lg_draw', 'h_sot', 'h_corners', 'h_yellow', 'h_offsides',
              'h_fouls', 'h_saves', 'h_htdraw', 'h_btts', 'sum_offsides'):
        c = sum(1 for r in rs if r.get(k) is not None)
        print(f"{k:<16}{c/len(rs):>10.1%}")


if __name__ == '__main__':
    main()
