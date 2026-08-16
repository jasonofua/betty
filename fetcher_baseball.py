"""
fetcher_baseball.py — Flashscore data fetcher for Baseball (Sport ID 6)
=======================================================================
Pulls baseball fixtures and H2H run data.
"""

import os, sys, time, hashlib, urllib.request, json
from collections import defaultdict

# ── constants ──────────────────────────────────────────────────────────────────
BASE     = "https://global.flashscore.ninja/2030/x/feed/"
HDRS     = {"x-fsign": "SW9D1eZo", "User-Agent": "Mozilla/5.0"}
SEP_FIELD = "\xac"
SEP_KV    = "\xf7"

CACHE     = "/tmp/fscache_baseball"
os.makedirs(CACHE, exist_ok=True)

MAX_DEEP_GAMES = 7
DEEP_SLEEP = 0.35

# ── low-level network ──────────────────────────────────────────────────────────
def fetch(feed, retries=2, ttl=6 * 3600):
    cf = os.path.join(CACHE, hashlib.md5(feed.encode()).hexdigest())
    if os.path.exists(cf) and time.time() - os.path.getmtime(cf) < ttl:
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

# ── fixture list ───────────────────────────────────────────────────────────────
def get_fixtures(offset=0):
    raw = fetch(f"f_6_{offset}_1_en-ng_1")
    out, cur = [], None
    for f in sections(raw):
        if "ZA" in f:
            cur = f["ZA"]
        elif "AA" in f and cur:
            out.append(dict(
                id=f["AA"], league=cur,
                home=f.get("AE", "?"), away=f.get("AF", "?"),
                ts=int(f.get("AD", 0))
            ))
    return out

# ── H2H history parser ────────────────────────────────────────────────────────
def parse_history(raw):
    blocks    = defaultdict(list)
    past_ids  = defaultdict(list)
    tab, blk  = None, None
    for f in sections(raw):
        if "KA" in f:
            tab = f["KA"]
        if "KB" in f:
            blk = f["KB"]
            continue
        if "KJ" in f and "KK" in f and tab == "Overall" and blk:
            try:
                # Baseball runs are in KU (home) and KT (away)
                hg, ag = int(f.get("KU", "")), int(f.get("KT", ""))
            except ValueError:
                continue
            
            team_name = blk.split(": ")[1] if ": " in blk else ""
            kj = f.get('KJ', '').replace('*', '').strip()
            kk = f.get('KK', '').replace('*', '').strip()
            
            if team_name and kj and kk:
                tn_words = set(team_name.replace('.', '').split())
                kj_words = set(kj.replace('.', '').split())
                kk_words = set(kk.replace('.', '').split())
                
                tn_sig = {w for w in tn_words if len(w) >= 3}
                if tn_sig & {w for w in kj_words if len(w) >= 3}:
                    venue = "home"
                    gf, ga = hg, ag
                elif tn_sig & {w for w in kk_words if len(w) >= 3}:
                    venue = "away"
                    gf, ga = ag, hg
                else:
                    venue = f.get("KS", "")
                    gf, ga = (hg, ag) if venue == "home" else (ag, hg)
            else:
                venue = f.get("KS", "")
                gf, ga = (hg, ag) if venue == "home" else (ag, hg)
                
            res = "W" if gf > ga else "L" if gf < ga else "D"
            match_id = f.get("KP", "")
            row = dict(gf=gf, ga=ga, res=res, venue=venue, match_id=match_id)
            blocks[blk].append(row)
            if match_id:
                past_ids[blk].append(match_id)

    names     = list(blocks)
    home_rows = blocks[names[0]] if len(names) > 0 else []
    away_rows = blocks[names[1]] if len(names) > 1 else []
    h2h_rows  = blocks[names[2]] if len(names) > 2 else []
    return home_rows, away_rows, h2h_rows, dict(past_ids)

def parse_match_summary(match_id):
    raw = fetch(f"df_sui_1_{match_id}", ttl=48 * 3600)
    if not raw:
        return None

    result = {"f5_home": 0, "f5_away": 0}
    for f in sections(raw):
        if "AC" in f:
            inning = f["AC"].lower()
            if any(str(i) in inning for i in range(1, 6)) and "inning" in inning:
                try:
                    result["f5_home"] += int(f.get("IH", 0))
                except ValueError: pass
                
                # Check if away team runs is 'X' (did not bat, which shouldn't happen in F5 but just in case)
                ig = f.get("IG", "0")
                if ig != 'X':
                    try:
                        result["f5_away"] += int(ig)
                    except ValueError: pass

    return result

def fetch_rich_history(fixture_id):
    raw = fetch(f"df_hh_1_{fixture_id}")
    if not raw:
        return [], [], [], {}

    home_rows, away_rows, h2h_rows, past_ids = parse_history(raw)

    rich = {
        "home_f5_scored": None, "home_f5_conceded": None,
        "away_f5_scored": None, "away_f5_conceded": None,
        "deep_games": 0,
    }

    home_deep_stats = []
    away_deep_stats = []

    fetched = 0
    # Home team games
    for row in home_rows:
        mid = row.get("match_id", "")
        if not mid or row["venue"] != "home": continue
        if fetched >= MAX_DEEP_GAMES: break
        summary = parse_match_summary(mid)
        if summary:
            home_deep_stats.append(summary)
            fetched += 1
        time.sleep(DEEP_SLEEP)

    # Away team games
    for row in away_rows:
        mid = row.get("match_id", "")
        if not mid or row["venue"] != "away": continue
        if fetched >= MAX_DEEP_GAMES * 2: break
        summary = parse_match_summary(mid)
        if summary:
            away_deep_stats.append(summary)
            fetched += 1
        time.sleep(DEEP_SLEEP)

    rich["deep_games"] = len(home_deep_stats) + len(away_deep_stats)

    if home_deep_stats:
        n = len(home_deep_stats)
        rich["home_f5_scored"] = sum(s["f5_home"] for s in home_deep_stats) / n
        rich["home_f5_conceded"] = sum(s["f5_away"] for s in home_deep_stats) / n

    if away_deep_stats:
        n = len(away_deep_stats)
        rich["away_f5_scored"] = sum(s["f5_away"] for s in away_deep_stats) / n
        rich["away_f5_conceded"] = sum(s["f5_home"] for s in away_deep_stats) / n

    return home_rows, away_rows, h2h_rows, rich

def feats(rows):
    if not rows: return None
    f10 = rows[:10]
    form5 = "".join(r["res"] for r in rows[:5])
    return dict(
        form5=form5,
        W=form5.count("W"), L=form5.count("L"),
        gf_pg=sum(r["gf"] for r in f10) / len(f10),
        ga_pg=sum(r["ga"] for r in f10) / len(f10),
    )

def fetch_json(url):
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode('utf-8'))
    except Exception as e:
        print(f"  ! mlb api fetch failed {url}: {e}", file=sys.stderr)
        return None

def get_mlb_pitchers():
    """
    Returns a dict mapping normalized MLB team names to:
      {'pitcher': <fullName>, 'era': <current_season_era>}
    """
    url = "https://statsapi.mlb.com/api/v1/schedule?sportId=1&hydrate=probablePitcher(note)"
    schedule = fetch_json(url)
    if not schedule or not schedule.get('dates'): return {}
    
    pitchers = {}
    for d in schedule['dates']:
        for game in d.get('games', []):
            for side in ['home', 'away']:
                team_data = game.get('teams', {}).get(side, {})
                t_name = team_data.get('team', {}).get('name', '')
                p_data = team_data.get('probablePitcher', {})
                pid = p_data.get('id')
                pname = p_data.get('fullName')
                
                if t_name and pid:
                    # Fetch pitcher ERA
                    p_url = f"https://statsapi.mlb.com/api/v1/people/{pid}?hydrate=stats(group=[pitching],type=[season])"
                    p_info = fetch_json(p_url)
                    era = None
                    if p_info and p_info.get('people'):
                        try:
                            stats = p_info['people'][0].get('stats', [])
                            for stat_group in stats:
                                if stat_group.get('type', {}).get('displayName') == 'season':
                                    era = float(stat_group['splits'][0]['stat']['era'])
                                    break
                        except Exception:
                            pass
                    
                    if era is not None:
                        # Normalize team name for matching (e.g. "Boston Red Sox" -> "boston red sox")
                        pitchers[t_name.lower()] = {'pitcher': pname, 'era': era}
    return pitchers


def main():
    offset = 0
    if "--offset" in sys.argv:
        offset = int(sys.argv[sys.argv.index("--offset") + 1])

    fixtures = get_fixtures(offset)
    print(f"feed: {len(fixtures)} fixtures (offset {offset})")
    
    for i, fx in enumerate(fixtures[:10]):
        print(f"\n[{i+1}] {fx['home']} v {fx['away']}  —  {fx['league']}")
        raw = fetch(f"df_hh_1_{fx['id']}")
        if not raw: continue
        home_rows, away_rows, _, _ = parse_history(raw)
        
        hf = feats(home_rows)
        af = feats(away_rows)
        if hf: print(f"  home form5={hf['form5']}  runs_scored={hf['gf_pg']:.2f}  runs_conceded={hf['ga_pg']:.2f}")
        if af: print(f"  away form5={af['form5']}  runs_scored={af['gf_pg']:.2f}  runs_conceded={af['ga_pg']:.2f}")

if __name__ == "__main__":
    main()
