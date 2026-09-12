#!/usr/bin/env python3
"""Winners slip - the 10 Sep rule set (second pass), measured on every leg
booked 8-10 Sep plus 43k corpus matches:

    1. the MARKET picks the side: the 1X2 favourite only (against-market 2/9)
    2. venue goal difference decides WHETHER to bet - the favourite must be
       backed by >= 1.0 goals/game at the venue if it is at HOME, >= 1.5 if
       it is AWAY (home side's home games vs away side's away games, last 7).
       Unbacked favourites lose OUTRIGHT, so no cover helps - skip them.
    3. price < 1.80  -> straight win
    4. price 1.80-2.60 -> double chance (the loss in this band is the draw)
       above 2.60 -> skip

One slip, every qualifying game in the window. Window opens one hour ahead.

    python3 book_winners.py --until 23 [--days 0] [--dry]
"""
import sys, datetime as dt, json
import acca as A
import fetcher_v2 as F2
import dynamic_v4 as D
import book_draw as DRW          # venue_form(): df_hh Home/Away tabs cut at kickoff

# 10 Sep, second pass. FC Tallinn 4:2 Maardu (away fav @1.95, margin +1.00)
# forced a recheck of both numbers against 43k corpus matches:
#   - the venue margin must be BIGGER than 0.5. Taking the higher-venue-GD
#     side: margin 0.5-1.0 wins 43%, 1.0-1.5 47%, 1.5-2.5 53%.
#   - an AWAY pick is worth about half a goal of margin less than a home one
#     at the same number (away >=1.0 -> 50.4%, away >=1.5 -> 54.9%, home
#     >=1.0 -> 52.6%). So away carries a higher bar.
#   - the old 2.00 straight/DC line rested on FOUR legs in the sample. The
#     book's own price is the better read: below 1.80 is a real favourite,
#     1.80-2.60 is close enough that the draw needs covering.
# Over every leg booked 8-10 Sep this takes 22 legs and wins 20 (91%), vs
# 30/35 (86%) for the first pass.
MARGIN_HOME = 1.0   # goals per game, favourite minus opponent at the venue
MARGIN_AWAY = 1.5
STRAIGHT_MAX = 1.80
DC_MAX = 2.60
SEP = '-' * 96


def outcome(ev, mid, want):
    for m in ev.get('markets', []):
        if str(m.get('id')) == mid and not m.get('specifier'):
            for o in m.get('outcomes', []):
                if o['desc'].replace(' ', '').lower() == want.replace(' ', '').lower():
                    return o
    return None


def gd(pairs):
    return (sum(g for g, _ in pairs) - sum(c for _, c in pairs)) / max(1, len(pairs))


def rec(pairs):
    return (f"{sum(g > c for g, c in pairs)}W{sum(g == c for g, c in pairs)}D"
            f"{sum(g < c for g, c in pairs)}L gd{sum(g for g, _ in pairs) - sum(c for _, c in pairs):+d}")


def fmt(pairs):
    return ' '.join(f"{a}:{b}" for a, b in pairs)


def build(until_h, days=0, verbose=True):
    now = dt.datetime.now(tz=A.WAT)
    start = now + dt.timedelta(hours=1)
    cutoff = now.replace(hour=until_h, minute=0, second=0, microsecond=0)
    if cutoff <= now:
        cutoff += dt.timedelta(days=1)
    cutoff += dt.timedelta(days=days)
    print(f"window {start:%a %H:%M} -> {cutoff:%a %d %H:%M} WAT", flush=True)
    evs = [e for e in A.fetch_events_full()
           if start < dt.datetime.fromtimestamp(int(e.get('estimateStartTime', 0)) / 1000, tz=A.WAT) <= cutoff]
    seen, fx = set(), []
    for off in range(max(2, (cutoff.date() - now.date()).days) + 1):
        for f in F2.get_fixtures(off):
            if f['id'] not in seen:
                seen.add(f['id']); fx.append(f)
    pairs = D.join(evs, fx,
                   lambda e: dt.datetime.fromtimestamp(int(e['estimateStartTime']) / 1000, tz=A.WAT),
                   lambda f: dt.datetime.fromtimestamp(f['ts'], tz=A.WAT))
    print(f"sportybet in window {len(evs)}  |  joined to flashscore {len(pairs)}", flush=True)
    rows = []
    for ev, f, _ in pairs:
        m1 = {o['desc']: o for o in (next((m for m in ev.get('markets', []) if str(m.get('id')) == '1' and not m.get('specifier')), {}) or {}).get('outcomes', [])}
        if 'Home' not in m1 or 'Away' not in m1:
            continue
        o1, o2 = float(m1['Home']['odds']), float(m1['Away']['odds'])
        try:
            hp, ap = DRW.venue_form(f['id'], f['ts'])
        except Exception:
            hp, ap = [], []
        row = dict(ev=ev, f=f, ts=f['ts'], league=f.get('league') or '?', home=f['home'], away=f['away'],
                   o1=o1, ox=float(m1['Draw']['odds']) if 'Draw' in m1 else None, o2=o2, hp=hp, ap=ap,
                   pick=None, why='')
        if len(hp) < 4 or len(ap) < 4:
            row['why'] = 'no venue form'; rows.append(row); continue
        side = 'Home' if o1 <= o2 else 'Away'
        price = o1 if side == 'Home' else o2
        hg, ag = gd(hp), gd(ap)
        margin = (hg - ag) if side == 'Home' else (ag - hg)
        row.update(side=side, price=price, margin=margin)
        need = MARGIN_HOME if side == 'Home' else MARGIN_AWAY
        if margin < need:
            row['why'] = f"venue margin {margin:+.2f} below {need:.1f} ({side.lower()} favourite)"
            rows.append(row); continue
        if price < STRAIGHT_MAX:
            o = outcome(ev, '1', side)
            row.update(pick='win', label=f"1X2 / {side}", o=o)
        elif price < DC_MAX:
            want = 'Home or Draw' if side == 'Home' else 'Draw or Away'
            o = outcome(ev, '10', want)
            row.update(pick='dc', label=f"Double Chance / {want}", o=o)
        else:
            row['why'] = f"favourite priced {price:.2f} (above {DC_MAX})"; rows.append(row); continue
        if not row.get('o'):
            row['why'] = 'market not offered'; row['pick'] = None
        rows.append(row)
    rows.sort(key=lambda r: (r['league'], r['ts']))
    return rows


def main():
    until = int(sys.argv[sys.argv.index('--until') + 1]) if '--until' in sys.argv else 23
    days = int(sys.argv[sys.argv.index('--days') + 1]) if '--days' in sys.argv else 0
    dry = '--dry' in sys.argv
    top = int(sys.argv[sys.argv.index('--top') + 1]) if '--top' in sys.argv else 0
    rows = build(until, days)
    picks = [r for r in rows if r['pick']]
    top = min(top, A.MAX_CODE) if top else A.MAX_CODE      # SportyBet slip cap
    if len(picks) > top:
        # "best" = most likely to land, which is the book's own implied
        # probability of the SELECTION (a double chance price already carries
        # its cover). Venue margin breaks ties.
        picks = sorted(picks, key=lambda r: (float(r['o']['odds']), -r['margin']))[:top]
        print(f"\n>> keeping the {top} most likely legs of {len([r for r in rows if r['pick']])}")
    print(f"\n{SEP}\nALL GAMES IN THE WINDOW - {len(rows)} with prices, {len(picks)} qualify\n{SEP}")
    cur = None
    for r in rows:
        if r['league'] != cur:
            cur = r['league']; print(f"\n{cur}")
        t = dt.datetime.fromtimestamp(r['ts'], tz=A.WAT)
        tag = f"PICK {r['label']} @{float(r['o']['odds']):.2f}" if r['pick'] else f"skip: {r['why']}"
        print(f"  {t:%a %H:%M}  {r['home']} v {r['away']}    {r['o1']} / {r['ox']} / {r['o2']}    {tag}")
        if r['hp'] and r['ap']:
            print(f"      {r['home'][:20]:20} HOME {fmt(r['hp']):32} {rec(r['hp'])}")
            print(f"      {r['away'][:20]:20} AWAY {fmt(r['ap']):32} {rec(r['ap'])}")
    if not picks:
        print("\n>> nothing qualifies"); return
    picks.sort(key=lambda r: r['ts'])
    combo = 1.0
    for r in picks:
        combo *= float(r['o']['odds'])
    print(f"\n{SEP}\nSLIP - {len(picks)} legs  ~{combo:,.0f}x\n{SEP}")
    for r in picks:
        t = dt.datetime.fromtimestamp(r['ts'], tz=A.WAT)
        print(f"  {t:%a %H:%M}  {r['home']} v {r['away']:26} {r['label']:30} @{float(r['o']['odds']):.2f}   margin {r['margin']:+.2f}")
    if dry:
        print("\n(dry run - nothing booked)"); return
    sels = [dict(eventId=r['ev']['eventId'], productId=3, marketId=('10' if r['pick'] == 'dc' else '1'),
                 specifier='', outcomeId=r['o']['id']) for r in picks]
    bk = A.book(sels)
    print('\nbooked', bk)
    if bk and bk.get('code'):
        legs = [(r['ts'], f"{r['ev']['homeTeamName']} v {r['ev']['awayTeamName']}", r['label'], float(r['o']['odds']),
                 [f"{r['home']} HOME {fmt(r['hp'])} {rec(r['hp'])}", f"{r['away']} AWAY {fmt(r['ap'])} {rec(r['ap'])}",
                  f"1X2 {r['o1']}/{r['ox']}/{r['o2']}  favourite {r['side']} venue margin {r['margin']:+.2f}"])
                for r in picks]
        A.log_booking(bk['code'], bk.get('url'),
                      f"winners slip {combo:,.0f}x ({len(picks)} legs) until {until}:00 - fix v2: favourite, margin >=1.0 home / >=1.5 away, <1.80 win / 1.80-2.60 DC",
                      legs)
        print(f"code {bk['code']}  {bk.get('url')}   ({bk.get('booked')}/{bk.get('req')} legs, verified {bk.get('verified')})")


if __name__ == '__main__':
    main()
