#!/usr/bin/env python3
"""Dedupe experiments/dataset.jsonl by match id, keeping the LAST row per id.

Found 6 Sep 2026: 53,290 lines but 42,777 unique ids - 10,513 duplicate rows,
725 ids present three or more times, from overlapping deep/depth/repair
harvests. Every trainer reads the file line by line, so a duplicated match
was weighted twice and could sit in BOTH halves of a time split, inflating
every held-out number. The last row wins because later harvests carry the
fuller stat sheet (the half-split repair rewrote rows in place).

Rewrites atomically; prints before/after. Safe to run any time the
accumulator is NOT mid-append.
"""
import json, os, sys, collections, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DS = os.path.join(HERE, 'dataset.jsonl')

last = collections.OrderedDict()
lines = 0
for line in open(DS, encoding='utf-8'):
    lines += 1
    try:
        r = json.loads(line)
    except ValueError:
        continue
    i = r.get('id')
    if not i:
        continue
    if i in last:
        del last[i]          # re-insert so order follows the LAST occurrence
    last[i] = line.rstrip('\n')

rows = sorted(last.values(), key=lambda s: json.loads(s).get('ts') or 0)
fd, tmp = tempfile.mkstemp(dir=HERE, prefix='dataset.', suffix='.tmp')
with os.fdopen(fd, 'w', encoding='utf-8') as out:
    for s in rows:
        out.write(s + '\n')
os.replace(tmp, DS)
print(f'dedupe: {lines} lines -> {len(rows)} unique matches ({lines - len(rows)} removed)')
