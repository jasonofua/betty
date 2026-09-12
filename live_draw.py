#!/usr/bin/env python3
"""LIVE draw watcher - the draw gate at half-time.

Measured on the corpus (12 Sep 2026, 1,189 gate games with half-time scores):
    gate game, any HT score        FT draw 33.4%
    gate game, LEVEL at HT (572)   FT draw 49.3%   fair 2.03
    gate game, 0-0 at HT (453)     FT draw 49.9%   fair 2.00   (FT 0-0 36.9%)
The live 1X2 Draw on a quiet game that is 0-0 at the break is priced well
above 2.00. That gap is the whole edge, and half-time is the one window
where a share code can be loaded and staked before the price moves.

Half-time stats (1H shots on target, corners) add NOTHING to the 2H goal
rate on the corpus - the HT score is the whole live state. A big favourite
level at HT gets a 2H goal 79.2% of the time, the same as any other game.

Loop: every POLL seconds pull SportyBet's live board; for every gate game
that reaches half-time level, read the live Draw price; if it clears
MIN_PRICE, book a single share code and push it to Telegram. One code per
game, never re-booked.

    python3 live_draw.py [--until 23] [--poll 45] [--min-price 2.10] [--dry]
Runs on Railway via ui.live_job / Telegram /live.
"""
import sys, json, time, urllib.request, datetime as dt, collections
import acca as A
import book_draw as DRW

BASE = 'https://www.sportybet.com/api/ng/factsCenter/'
POLL = 45
MIN_PRICE = 2.10          # fair at HT level is 2.00-2.03; a nickel over that is the edge
FAIR_HT = 2.03
SEND = None               # set by the caller: fn(text) -> None
LOG = print


def get(url):
    return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=A.HDRS), timeout=25)
                      .read().decode('utf-8', 'replace'))


def live_board():
    d = get(BASE + 'liveOrPrematchEvents?sportId=sr:sport:1&productId=1')
    data = d.get('data') or []
    return [e for t in data for e in (t.get('events', []) if isinstance(t, dict) else [])]


def live_draw_price(eid):
    mk = ((get(BASE + f"event?eventId={eid}&productId=1").get('data') or {}).get('markets')) or []
    m = next((m for m in mk if str(m.get('id')) == '1' and not m.get('specifier')), None)
    if not m or m.get('status') not in (0, None):
        return None, None
    o = next((o for o in m.get('outcomes', []) if o['desc'] == 'Draw' and o.get('isActive', 1)), None)
    return (float(o['odds']), o['id']) if o else (None, None)


def gate_events(until_h, days=0):
    """Today's gate passers as SportyBet events - the pre-match PRICE FLOOR is
    switched off here: a game the book priced under fair before kickoff is
    still a gate game, and the live price at HT is what we bet."""
    old = DRW.FLOOR
    DRW.FLOOR = False
    try:
        legs = DRW.build(until_h=until_h, days=days, verbose=False)
    finally:
        DRW.FLOOR = old
    out = {}
    for l in legs:
        out[l['bs']['eventId']] = dict(match=l['match'], ko=l['ts'], stats=l['stats'])
    return out


def level(e):
    s = (e.get('setScore') or '').split(':')
    return len(s) == 2 and s[0] == s[1]


def at_half_time(e):
    ms = (e.get('matchStatus') or '').upper()
    if ms in ('HT', 'HALF TIME', 'HALFTIME'):
        return True
    if ms == 'H1':
        try:
            mm = int((e.get('playedSeconds') or '0:0').split(':')[0])
            return mm >= 45
        except ValueError:
            return False
    return False


def run(until_h=23, days=0, dry=False, poll=POLL, min_price=MIN_PRICE):
    gate = gate_events(until_h, days)
    LOG(f"live draw watcher: {len(gate)} gate games today; polling every {poll}s until {until_h}:00; min price {min_price}")
    for eid, g in sorted(gate.items(), key=lambda kv: kv[1]['ko']):
        LOG(f"   {g['ko']:%a %H:%M}  {g['match']}")
    if SEND:
        SEND(f"live draw watcher on: {len(gate)} gate games, booking the Draw at half-time when level and priced >= {min_price}\n"
             + "\n".join(f"{g['ko']:%H:%M} {g['match']}" for eid, g in sorted(gate.items(), key=lambda kv: kv[1]['ko'])))
    done, seen_live = set(), set()
    end = dt.datetime.now(tz=A.WAT).replace(hour=until_h, minute=59, second=0, microsecond=0)
    while dt.datetime.now(tz=A.WAT) < end and len(done) < len(gate):
        try:
            board = live_board()
        except Exception as ex:
            LOG(f"board error {type(ex).__name__}: {ex}"); time.sleep(poll); continue
        for e in board:
            eid = e.get('eventId')
            if eid not in gate or eid in done:
                continue
            g = gate[eid]
            if eid not in seen_live:
                seen_live.add(eid); LOG(f"live: {g['match']} {e.get('matchStatus')} {e.get('playedSeconds')} {e.get('setScore')}")
            if not at_half_time(e):
                continue
            if not level(e):
                done.add(eid); LOG(f"HT not level: {g['match']} {e.get('setScore')} - dropped"); continue
            try:
                price, oid = live_draw_price(eid)
            except Exception as ex:
                LOG(f"price error {g['match']}: {ex}"); continue
            if not price:
                LOG(f"HT level but Draw suspended: {g['match']} - retry next poll"); continue
            if price < min_price:
                done.add(eid); LOG(f"HT {e.get('setScore')} {g['match']}: Draw {price:.2f} under {min_price} - no bet"); continue
            done.add(eid)
            if dry:
                LOG(f"DRY  HT {e.get('setScore')} {g['match']}: Draw @{price:.2f} (fair {FAIR_HT}) - would book"); continue
            bk = A.book([dict(eventId=eid, productId=1, marketId='1', specifier='', outcomeId=oid)])
            code = (bk or {}).get('code')
            msg = (f"LIVE DRAW  {g['match']}  HT {e.get('setScore')}\n1X2 / Draw @{price:.2f}  (HT-level gate rate 49.3%, fair {FAIR_HT})\n"
                   f"code {code}  {(bk or {}).get('url')}")
            LOG(msg)
            if code:
                A.log_booking(code, bk.get('url'), f"LIVE draw at HT {e.get('setScore')} @{price:.2f}",
                              [(g['ko'].timestamp(), g['match'], f"1X2 / Draw (live, HT {e.get('setScore')})", price, g['stats'])])
                if SEND:
                    SEND(msg)
        time.sleep(poll)
    LOG(f"live draw watcher finished: {len(done)} of {len(gate)} gate games resolved")


if __name__ == '__main__':
    a = sys.argv
    run(until_h=int(a[a.index('--until') + 1]) if '--until' in a else 23,
        dry='--dry' in a,
        poll=int(a[a.index('--poll') + 1]) if '--poll' in a else POLL,
        min_price=float(a[a.index('--min-price') + 1]) if '--min-price' in a else MIN_PRICE)
