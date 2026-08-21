#!/usr/bin/env python3
"""Cloud booking runner - executed by GitHub Actions (.github/workflows/book.yml).

    python3 actions_book.py <target> <until> <days> <dry>

Same pipeline as ui.py/book_dynamic: build the board, pool every rank's picks,
pick_for_target with its guards, book unless dry. The full engine log and the
result land in runs/latest.json, which the workflow commits back to the repo so
the hosted page can render it - Actions is the only free compute that will sit
through a 15-40 minute board build.
"""
import io, json, sys, contextlib, datetime as dt

sys.path.insert(0, '.')
import acca as A
import book_dynamic as BD
import dynamic_v4 as D

target = float(sys.argv[1]) if len(sys.argv) > 1 else 50.0
until = int(sys.argv[2]) if len(sys.argv) > 2 else 23
days = int(sys.argv[3]) if len(sys.argv) > 3 else 0
dry = (sys.argv[4].lower() in ('1', 'true', 'yes')) if len(sys.argv) > 4 else False

log = []


class _Tee(io.TextIOBase):
    def write(self, s):
        for part in s.splitlines():
            if part.strip():
                log.append(part.rstrip())
                print(part.rstrip(), file=sys.stderr, flush=True)  # live in the Actions console
        return len(s)


out = {'params': {'target': target, 'until': until, 'days': days, 'dry': dry},
       'started': dt.datetime.now(A.WAT).strftime('%Y-%m-%d %H:%M WAT'),
       'result': None, 'log': None}

try:
    with contextlib.redirect_stdout(_Tee()):
        board = BD.build(until_h=until, days=days)
        if not board:
            out['result'] = {'error': 'no supported options on this board'}
        else:
            pool, seen = [], set()
            for rank in range(D.TOP_N):
                for l in BD.slip(board, rank):
                    k = (l['bs']['eventId'], l['bs']['marketId'], l['bs']['specifier'])
                    if k in seen:
                        continue
                    seen.add(k)
                    pool.append(l)
            legs, combo, surv = BD.pick_for_target(pool, target)
            if not legs:
                out['result'] = {'error': 'no bookable legs on this board'}
            else:
                warn = None
                if combo < target:
                    warn = (f'{target:g}x not reachable — booked the best '
                            f'available: {combo:,.1f}x from {len(legs)} legs')
                legs.sort(key=lambda l: l['ts'])
                res = {'combo': round(combo, 1), 'est': round(surv * 100, 2),
                       'pool': len(pool),
                       'legs': [dict(when=l['ts'].strftime('%a %H:%M'),
                                     match=l['match'], label=l['label'],
                                     odds=l['odds'],
                                     prob=round(BD.true_prob(l['odds']) * 100))
                                for l in legs]}
                if warn:
                    res['warn'] = warn
                if dry:
                    res['dry'] = True
                else:
                    bk = A.book([l['bs'] for l in legs])
                    if bk and bk.get('code'):
                        res['code'], res['url'] = bk['code'], bk['url']
                        got = bk.get('verified') or bk['booked']
                        if got != bk['req']:
                            res['short'] = f"booked {got}/{bk['req']}"
                        A.log_booking(bk['code'], bk['url'],
                                      f"cloud target {target:g}x until {until}:00"
                                      + (f" +{days}d" if days else ""),
                                      [(l['ts'].timestamp(), l['match'], l['label'],
                                        l['odds'], l['stats']) for l in legs])
                    else:
                        res['error'] = f"booking failed: {bk.get('msg') if bk else 'no selections'}"
                out['result'] = res
except Exception as e:
    out['result'] = {'error': f'{type(e).__name__}: {e}'}

out['finished'] = dt.datetime.now(A.WAT).strftime('%Y-%m-%d %H:%M WAT')
out['log'] = log[-120:]

import os
os.makedirs('runs', exist_ok=True)
with open('runs/latest.json', 'w') as f:
    json.dump(out, f, indent=1)
print('written runs/latest.json', file=sys.stderr)
