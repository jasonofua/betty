#!/usr/bin/env python3
"""ONE MARKET PER SLIP.

  python3 book_bymarket.py [--until HH] [--days N] [--dry] [--min N] [--cap X]

User's call, 25 Sep: instead of one slip mixing Over 1.5 with a handicap and a
double chance, every slip carries a single option repeated across as many games
as the board supports - all the Over 1.5s on one code, all the first-half Unders
on another, all the second-half Over 0.5s on a third. Football only.

Each match still contributes only its own supported options (the same evaluator
the other products use), so a game appears on a slip only where its own record
backs that market. One leg per event per slip.
"""
import sys, re, collections, datetime as dt
import os as _o; sys.path.insert(0, _o.path.dirname(_o.path.abspath(__file__)))
import acca as A
import book_dynamic as BD
import dynamic_v4 as D

MIN_LEGS = 2             # a market needs at least this many games to be a slip


def market_key(label):
    """The option itself, without the outcome's own line dropping out - 'Over 1.5'
    and 'Over 2.5' are different slips, 'Over/Under / Over 1.5' and
    '1st Half - Over/Under / Over 1.5' are different slips."""
    lab = re.sub(r'\s*\[[^\]]*\]\s*$', '', label).strip()
    lab = re.sub(r'\s+', ' ', lab)
    return lab


def collect(board):
    """Every supported option on the board, grouped by the option itself."""
    groups = collections.defaultdict(list)
    for rank in range(D.TOP_N):
        for l in BD.slip(board, rank):
            groups[market_key(l['label'])].append(l)
    out = {}
    for k, legs in groups.items():
        seen, keep = set(), []
        for l in sorted(legs, key=lambda x: x['ts']):
            if l['bs']['eventId'] in seen:
                continue
            seen.add(l['bs']['eventId']); keep.append(l)
        if len(keep) >= MIN_LEGS:
            out[k] = keep[:BD.MAX_LEGS]
    return out


def main():
    dry = '--dry' in sys.argv
    until = int(sys.argv[sys.argv.index('--until') + 1]) if '--until' in sys.argv else 8
    days = int(sys.argv[sys.argv.index('--days') + 1]) if '--days' in sys.argv else 0
    cap = float(sys.argv[sys.argv.index('--cap') + 1]) if '--cap' in sys.argv else 99.0
    global MIN_LEGS
    if '--min' in sys.argv:
        MIN_LEGS = int(sys.argv[sys.argv.index('--min') + 1])

    board = BD.build(until, None, days=days, cap=cap)
    if not board:
        print(">> no supported options on this board")
        return
    groups = collect(board)
    if not groups:
        print(">> no market has enough games on this board")
        return
    print(f"\n{len(groups)} markets with {MIN_LEGS}+ games\n")
    for key in sorted(groups, key=lambda k: -len(groups[k])):
        legs = groups[key]
        combo = 1.0
        for l in legs:
            combo *= l['odds']
        print(f"=== {key}  -  {len(legs)} games, {combo:,.2f}x")
        for l in legs:
            print(f"   {l['ts']:%a %H:%M}  {l['match'][:40]:40} @{l['odds']:<5} {l['league'][:26]}")
        if dry:
            print()
            continue
        bk = A.book([l['bs'] for l in legs])
        if bk and bk.get('code'):
            print(f"   >> CODE {bk['code']}   {bk['url']}")
            A.log_booking(bk['code'], bk['url'],
                          f"one market: {key} - {combo:,.2f}x ({len(legs)} games)",
                          [(l['ts'].timestamp(), l['match'], l['label'], l['odds'], l['stats']) for l in legs])
        else:
            print(f"   >> booking failed: {bk.get('msg') if bk else 'no selections'}")
        print()


if __name__ == '__main__':
    main()
