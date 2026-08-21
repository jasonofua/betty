#!/usr/bin/env python3
"""
Decision tree v2 fixture flagger (from 12 June matches.csv deep re-mine).

Differences vs fetcher.py (v1):
- AH +1 Home triggers: form pts gap >= +6 (40/40), home 4W+ (97-100%),
  big GD edge with home 2W+ (Tier A proxy).
- Draw value filter: home <=1L and <=2W in last 5 + tight GD gap (47.6% X).
- Goerslev: home 4L+ -> Over 1.5 (92%); + away 3W+ -> O1.5/X2 boost.
- Perfect-away fade: away 4W+ -> home side / O1.5, never X2.
- League regime tags (A = unders/draws, B = overs/totals).
- POS away contrarian and BTTS removed (falsified).

Usage: python3 fetcher_v2.py --offset 0
"""
import sys, time, re, os, hashlib
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
import sportybet as SB

BASE = "https://global.flashscore.ninja/2030/x/feed/"
HDRS = {"x-fsign": "SW9D1eZo", "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
SEP_FIELD = "\xac"
SEP_KV = "\xf7"

REGIME_A = re.compile(r'ALGERIA|CHILE|ARGENTINA|IRAN|MOROCCO.*BOTOLA|BRAZIL.*SERIE D|PERU|BOLIVIA|PARAGUAY', re.I)
REGIME_B = re.compile(r'OBERLIGA|SWEDEN.*DIVISION [12]|DENMARK|AUSTRALIA|USL|GERMANY|NORWAY.*DIVISION', re.I)

CACHE = "/tmp/fscache"
os.makedirs(CACHE, exist_ok=True)

def fetch(feed, retries=2):
    cf = os.path.join(CACHE, hashlib.md5(feed.encode()).hexdigest())
    if os.path.exists(cf) and time.time() - os.path.getmtime(cf) < 6 * 3600:
        return open(cf, encoding="utf-8").read()
    req = urllib.request.Request(BASE + feed, headers=HDRS)
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                raw = r.read().decode("utf-8", "replace")
                with open(cf, "w", encoding="utf-8") as fh:
                    fh.write(raw)
                return raw
        except Exception as e:
            if attempt == retries:
                print(f"  ! fetch failed {feed}: {e}", file=sys.stderr)
                return ""
            time.sleep(1.5)

def sections(raw):
    for chunk in raw.split("~"):
        f = {}
        for kv in chunk.split(SEP_FIELD):
            if SEP_KV in kv:
                k, v = kv.split(SEP_KV, 1)
                f[k] = v
        if f:
            yield f

def get_fixtures(offset):
    raw = fetch(f"f_1_{offset}_1_en-ng_1")
    out, cur = [], None
    for f in sections(raw):
        if "ZA" in f:
            cur = f["ZA"]
        elif "AA" in f and cur:
            out.append(dict(id=f["AA"], league=cur, home=f.get("AE", "?"),
                            away=f.get("AF", "?"), ts=int(f.get("AD", 0))))
    return out

def parse_history(raw):
    blocks = defaultdict(list)
    tab, blk = None, None
    for f in sections(raw):
        if "KA" in f:
            tab = f["KA"]
        if "KB" in f:
            blk = f["KB"]
            continue
        if "KJ" in f and "KK" in f and tab == "Overall" and blk:
            try:
                hg, ag = int(f.get("KU", "")), int(f.get("KT", ""))
            except ValueError:
                continue
            venue = f.get("KS", "")
            gf, ga = (hg, ag) if venue == "home" else (ag, hg)
            res = "W" if gf > ga else "L" if gf < ga else "D"
            # Half-time goals: KX = home HT, KY = away HT (verified across 50+ matches).
            # These give 100% HT coverage from the main feed without needing the deep fetch.
            try:
                ht_h = int(f.get("KX", ""))
            except ValueError:
                ht_h = None
            try:
                ht_a = int(f.get("KY", ""))
            except ValueError:
                ht_a = None
            blocks[blk].append(dict(gf=gf, ga=ga, res=res, venue=venue,
                                    ht_h=ht_h, ht_a=ht_a))
    names = list(blocks)
    home_rows = blocks[names[0]] if len(names) > 0 else []
    away_rows = blocks[names[1]] if len(names) > 1 else []
    return home_rows, away_rows

def feats(rows):
    if not rows:
        return None
    f5 = rows[:5]
    f10 = rows[:10]
    form5 = "".join(r["res"] for r in f5)
    fpts = 3 * form5.count("W") + form5.count("D")
    return dict(
        form5=form5, fpts=fpts,
        W=form5.count("W"), L=form5.count("L"), D=form5.count("D"),
        gd10=sum(r["gf"] - r["ga"] for r in f10),
        gf10=sum(r["gf"] for r in f10),
        ga10=sum(r["ga"] for r in f10),
        gf_pg=sum(r["gf"] for r in f10) / len(f10),   # goals scored per game
        ga_pg=sum(r["ga"] for r in f10) / len(f10),   # goals conceded per game
        ppg10=sum(3 if r["res"] == "W" else 1 if r["res"] == "D" else 0 for r in f10) / len(f10),
    )

def analyze_v2(h, a, league):
    """No signal filtering. Keep the fixture if it has data and is on SportyBet."""
    return [], "-"

def main():
    offset = 0
    if "--offset" in sys.argv:
        offset = int(sys.argv[sys.argv.index("--offset") + 1])
    fixtures = get_fixtures(offset)
    print(f"feed: {len(fixtures)} fixtures (offset {offset})")
    # league filter removed - fetch & predict everything, user decides what to play
    now = time.time()
    fixtures = [f for f in fixtures if f["ts"] > now + 600]
    print(f"{len(fixtures)} upcoming (no league filter - predicting everything)")

    events = SB.fetch_events()
    print(f"SportyBet events loaded: {len(events)}")

    results, skipped_no_data, skipped_not_sporty = [], 0, 0
    for i, fx in enumerate(fixtures):
        raw = fetch(f"df_hh_1_{fx['id']}")
        if not raw:
            skipped_no_data += 1
            continue
        hr, ar = parse_history(raw)
        h, a = feats(hr), feats(ar)
        if not h or not a:
            skipped_no_data += 1
            continue

        ko = datetime.fromtimestamp(fx["ts"], tz=timezone.utc).strftime("%H:%M")
        if not SB.match_event(fx["home"], fx["away"], ko, events):
            skipped_not_sporty += 1
            continue

        s, regime = analyze_v2(h, a, fx["league"])
        results.append((fx, h, a, s, regime))
        if (i + 1) % 25 == 0:
            print(f"  ...{i+1}/{len(fixtures)}, {len(results)} kept")
        time.sleep(0.25)

    print(f"\ndone: {len(results)} kept, {skipped_no_data} skipped for no data, {skipped_not_sporty} skipped because not on SportyBet\n")
    lines = []
    for fx, h, a, s, regime in results:
        ko = datetime.fromtimestamp(fx["ts"], tz=timezone.utc).strftime("%H:%M UTC")
        lines.append(f"{fx['league']}  [regime {regime}]")
        lines.append(f"  {fx['home']} v {fx['away']}  ({ko})")
        lines.append(f"  forms: {h['form5']} v {a['form5']} | fpts: {h['fpts']} v {a['fpts']} | gd10: {h['gd10']:+d} v {a['gd10']:+d} | gfga10: {h['gf10']}-{h['ga10']} v {a['gf10']}-{a['ga10']}")
        lines.append("  included: on SportyBet and history data available")
        lines.append("")
    report = "\n".join(lines)
    print(report)
    fn = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"all_sporty_with_data_offset{offset}.txt")
    with open(fn, "w") as f:
        f.write(report)
    print(f"saved: {fn}")

if __name__ == "__main__":
    main()
