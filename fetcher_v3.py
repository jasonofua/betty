"""
fetcher_v3.py — Enhanced Flashscore data fetcher
=================================================
Everything from fetcher_v2 PLUS:
  - Half-time / Full-time score split per historical match
  - Goal minute data (first goal minute, last goal minute, goal distribution)
  - First-half vs second-half goal averages per team (venue-split)
  - BTTS (both teams to score) first half flag
  - Red card tracking per past match
  - FULL MATCH STATISTICS per past match, split by half: xG, xGOT, xA, big
    chances, shots (total/on/off/blocked/in-box/out-box), possession, corners,
    cards, fouls, saves, duels, and the rest of the df_st table

Flashscore per-match feeds (probed 29 Jul, all under BASE with the x-fsign header;
feeds that exist but return the literal "0" for football are omitted):
  df_hh_1_{id}   form + head-to-head, with each past match's own id in KP
  df_sui_1_{id}  summary incidents - goals, scorers, minutes, cards, half scores
  df_sur_1_{id}  per-half scoreline only, 64 bytes (BA/BB = 1H h/a, BC/BD = 2H h/a)
  df_st_1_{id}   match statistics table, sectioned Match / 1st Half / 2nd Half
  df_to_1_{id}   squad/player image manifest (no match data, not parsed here)
Statistics coverage is league-dependent: top divisions carry xG and big chances,
smaller ones only possession/shots/corners/fouls. Absent stats read as None.

Deep fetch strategy:
  - Step 1: df_hh_1_{id} → get form list with past match IDs (KP field)
  - Step 2: df_sui_1_{past_id} → crack open each past match for HT score + goal minutes
  - MAX_DEEP_GAMES (default 7) PER TEAM to avoid API hammering
  - Sleeps DEEP_SLEEP between real requests only, not on cache hits

Every deep summary is validated against the scoreline already known from the H2H
feed and discarded when it disagrees, so a stub body or a feed field change can
never quietly feed a wrong half-time split into the model.

Usage:
  python3 fetcher_v3.py [--offset N]

  Or import and call:
    from fetcher_v3 import get_fixtures, fetch_rich_history, rich_feats
"""

import os, sys, re, time, json, hashlib, urllib.request
from collections import defaultdict

# ── constants ──────────────────────────────────────────────────────────────────
BASE     = "https://global.flashscore.ninja/2030/x/feed/"
HDRS     = {"x-fsign": "SW9D1eZo", "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
SEP_FIELD = "\xac"
SEP_KV    = "\xf7"

CACHE     = "/tmp/fscache_v3"
os.makedirs(CACHE, exist_ok=True)

# How many past match IDs to open with deep fetch per team (keep <= 7 for speed)
MAX_DEEP_GAMES = 7
# Look only at the last RECENT_WINDOW games, THEN split by venue - not "the last 7
# games at this venue", which reached back a median of 145 days (max 1027) because
# a team only plays half its fixtures at home. Measured 4 Aug on 38 fixtures: the
# last 7 games regardless of venue span 75 days, the venue-filtered 7 span 145.
# All games are weighted equally by the consistency scanner, so a February match
# was counting as hard as yesterday's. Capping the window fixes that at the source.
RECENT_WINDOW = 10
# A break longer than this means the games before it belong to a previous season.
# Used ONLY to stop the consistency scanner counting across the gap - the fixture
# itself stays bettable. Montana v Nesebar (4 Aug, lost 2-1) built a [4/5+4/5]
# tally entirely out of last April and May, while its one match since the break -
# in which it scored 2, the exact number that beat the leg - was filtered out for
# being an away game.
SEASON_GAP_DAYS = 45


def trim_at_season_gap(rows, gap_days=SEASON_GAP_DAYS):
    """Cut a most-recent-first row list at the first break longer than gap_days."""
    gap = gap_days * 86400
    out = []
    for r in rows:
        kc = r.get("kc")
        if kc and out:
            prev = out[-1].get("kc")
            if prev and prev - kc > gap:
                break
        out.append(r)
    return out
DEEP_SLEEP     = 0.35   # seconds between deep requests
MIN_MINUTE_GAMES = 4    # fewest complete games before a first/early/late rate is reported

# Event types (IK) that put a goal on the scoreboard. "Penalty" is a CONVERTED
# penalty and carries its own IK - the old `"Goal" in event` test dropped it, while
# matching exactly keeps "Penalty Awarded" and "Penalty missed" out of the count.
GOAL_EVENTS = {"Goal", "Penalty", "Own Goal"}

# Competitions dropped from the deep sample - same rule acca.recent_kc applies to the
# form rows, so the deep stats and the form rows are drawn from the SAME match universe.
DROP_COMP = re.compile(r'Friendl|\bCup\b|Copa|Coupe|Pokal|Taca|Beker|Trophy|Super.?cup'
                       r'|Champions Leag|Europa|Conference Leag|UEFA|Libertadores|Sudamericana', re.I)

# Pull the df_st statistics table alongside each deep summary. Doubles the request
# count per past match, so it is a switch.
FETCH_STATS = True

# df_st SD (stat id) -> short key. SD is the stable identifier; SG is the localised
# display name and must not be keyed on. Anything not listed is still parsed into
# the table under its raw SD, this map just gives the useful ones readable names.
STAT_KEYS = {
    "432": "xg",              "499": "xgot",           "503": "xa",
    "501": "xgot_faced",      "511": "goals_prevented",
    "12":  "possession",      "34":  "shots",          "13":  "sot",
    "14":  "shots_off",       "158": "blocked_shots",  "457": "woodwork",
    "459": "big_chances",     "461": "shots_in_box",   "463": "shots_out_box",
    "471": "box_touches",     "465": "headed_goals",
    "16":  "corners",         "17":  "offsides",       "15":  "free_kicks",
    "18":  "throw_ins",       "21":  "fouls",          "23":  "yellow",
    "19":  "saves",           "20":  "goal_kicks",
    "342": "pass_pct",        "467": "final_third_pct","433": "cross_pct",
    "517": "long_pass_pct",   "521": "through_passes",
    "475": "tackle_pct",      "513": "duels_won",      "479": "clearances",
    "434": "interceptions",   "507": "err_to_shot",    "509": "err_to_goal",
}

# The df_st stats averaged into a team profile. Beyond the goals model, corners /
# yellow / fouls / offsides are here because SportyBet prices them directly:
# Corners 900300-900303, Bookings 900304-900307 + 900312, and the Match/Teams
# groups (fouls, shots, shots on target, offsides). Each has a venue-split
# for/against mean per half, which is exactly what those lines need.
PROFILE_STATS = ("xg", "xgot", "sot", "shots", "big_chances", "corners",
                 "possession", "shots_in_box", "box_touches", "saves", "yellow",
                 "fouls", "offsides")


# ── league goal regime (added 3 Aug) ──────────────────────────────────────────
# The engine reads a team's goal averages and never asks what LEAGUE it is in.
# analysis_step5_league_effects found two regimes - steady draw/under leagues vs
# chaotic over leagues - and nothing ever used it. Over 1-2 Aug, FT Under 2.5 ran
# 12/23 = 52% (it is 93% in the 758-leg audit) and SEVEN of the eleven losses were
# Argentine, every one clearing 2.5 comfortably (3:3, 4:2, 5:1, 4:0).
# An Under does not care about a league's average so much as its SPREAD: a league
# can average 2.4 goals and still be safe if it is consistent, and be unbettable at
# the same average if it swings 0-6 week to week.
# This table is built for free from the df_hh feeds the booker already fetches -
# every one carries a team's last ~10 matches with KH (country), KF (competition)
# and the scoreline. It persists in the PROJECT dir, not /tmp, because temp
# cleanup wipes the feed cache.
LEAGUE_STATS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "league_goals.json")
_league_seen = {}      # match_id -> (league_key, total_goals)  - dedupes across feeds
_match_league = {}     # match_id -> league_key, so a df_st table can be attributed
_league_stat_seen = {} # (match_id, stat) -> (league_key, home_val, away_val)

# Stats worth a per-league norm. Corners first: the 1-2 Aug audit put our corner
# selection 13 points BELOW blind (54 below on the tight 1H lines), the same
# mean-reversion trap the goal shrinkage fixes - we hunt teams whose corner counts
# are temporarily depressed and they revert. Goals get their norms free from df_hh;
# these have to come from the df_st tables the deep fetch already pulls.
LEAGUE_STAT_KEYS = ("corners", "sot", "shots", "yellow", "fouls", "offsides")


def league_key(country, comp):
    """Country-qualified so 'Premier League' in England and Russia stay distinct."""
    return f"{(country or '?').strip()}: {(comp or '?').strip()}"


def record_league_goals(raw):
    """Harvest every past match in a df_hh feed into the league table. Keyed on the
    match id so the same game seen from both teams' feeds counts once."""
    for f in sections(raw):
        mid = f.get("KP")
        if not mid or mid in _league_seen:
            continue
        if "KU" not in f or "KT" not in f or "KF" not in f:
            continue
        try:
            g = int(f["KU"]) + int(f["KT"])
        except ValueError:
            continue
        lk = league_key(f.get("KH"), f.get("KF"))
        _league_seen[mid] = (lk, g)
        _match_league[mid] = lk


def save_league_stats():
    """Fold the harvest into per-league n / mean / sd and merge with what is on disk."""
    from collections import defaultdict
    acc = defaultdict(list)
    for lk, g in _league_seen.values():
        acc[lk].append(g)
    old = load_league_stats()
    for lk, goals in acc.items():
        n = len(goals)
        mean = sum(goals) / n
        var = sum((x - mean) ** 2 for x in goals) / n if n > 1 else 0.0
        prev = old.get(lk)
        if prev and prev.get("n"):
            # running merge so the table improves every time the booker runs
            tn = prev["n"] + n
            tmean = (prev["mean"] * prev["n"] + mean * n) / tn
            tvar = (prev.get("var", 0.0) * prev["n"] + var * n) / tn
            old[lk] = {"n": tn, "mean": round(tmean, 3), "var": round(tvar, 3),
                       "sd": round(tvar ** 0.5, 3)}
        else:
            old[lk] = {"n": n, "mean": round(mean, 3), "var": round(var, 3),
                       "sd": round(var ** 0.5, 3)}
    # per-league stat norms (per TEAM, so a 10-corner match counts as 5 a side)
    sacc = defaultdict(lambda: defaultdict(list))
    for lk, hv, av in _league_stat_seen.values():
        pass
    for (mid, k), (lk, hv, av) in _league_stat_seen.items():
        sacc[lk][k] += [hv, av]
    for lk, stats in sacc.items():
        ent = old.setdefault(lk, {"n": 0, "mean": 0.0, "var": 0.0, "sd": 0.0})
        st_out = ent.setdefault("stats", {})
        for k, vals in stats.items():
            n = len(vals)
            mean = sum(vals) / n
            prev = st_out.get(k)
            if prev and prev.get("n"):
                tn = prev["n"] + n
                st_out[k] = {"n": tn,
                             "mean": round((prev["mean"] * prev["n"] + mean * n) / tn, 3)}
            else:
                st_out[k] = {"n": n, "mean": round(mean, 3)}
    with open(LEAGUE_STATS_PATH, "w", encoding="utf-8") as fh:
        json.dump(old, fh, indent=1, sort_keys=True)
    return old


def load_league_stats():
    try:
        with open(LEAGUE_STATS_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def league_profile(league_label):
    """Look a fixture's 'COUNTRY: Competition' label up in the table. The booker's
    label and the feed's KH/KF wording differ slightly, so match on the country plus
    the longest word overlap in the competition name."""
    tab = load_league_stats()
    if not tab:
        return None
    if league_label in tab:
        return tab[league_label]
    country, _, comp = league_label.partition(":")
    country = country.strip().lower(); comp = comp.strip().lower()
    best, bs = None, 0
    cw = {w for w in re.split(r"\W+", comp) if len(w) > 3}
    for k, v in tab.items():
        kc, _, kcomp = k.partition(":")
        if kc.strip().lower() != country:
            continue
        kw = {w for w in re.split(r"\W+", kcomp.strip().lower()) if len(w) > 3}
        sc = len(cw & kw)
        if sc > bs or (best is None and not cw):
            best, bs = v, sc
    return best


# ── low-level network ──────────────────────────────────────────────────────────
def is_cached(feed, ttl=6 * 3600):
    """True when `feed` is already on disk and still fresh - lets callers skip the
    politeness sleep on a cache hit (a full deep pass is otherwise ~5s of pure sleep
    per fixture even when nothing goes over the wire)."""
    cf = os.path.join(CACHE, hashlib.md5(feed.encode()).hexdigest())
    return os.path.exists(cf) and time.time() - os.path.getmtime(cf) < ttl


def fetch(feed, retries=2, ttl=6 * 3600):
    """Fetch a Flashscore feed, caching to disk for `ttl` seconds."""
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
    """Yield each tilde-separated chunk as a field dict (last value wins)."""
    for chunk in raw.split("~"):
        f = {}
        for kv in chunk.split(SEP_FIELD):
            if SEP_KV in kv:
                k, v = kv.split(SEP_KV, 1)
                f[k] = v
        if f:
            yield f


def incident_sections(raw):
    """
    sections() for the incident feed, where ONE chunk can pack SEVERAL event blocks,
    each with its own IK (event type) and IF (player). Two shapes seen in the wild:

        goal + assist       IK=Goal            then IK=Assistance
        penalty             IK=Penalty Awarded then IK=Penalty

    so neither the first nor the last block is reliably the one that matters. The
    last-wins rule in sections() turned every assisted goal into an 'Assistance'
    event with the assister's name in IF, and the goal then vanished from the goal
    list - verified on lbC7eGro (real 0-3, all three goals lost) and across a Brazil
    Serie A sample where the goal list was complete on 0 of 7 matches.

    Each chunk yields one dict: chunk-level fields (IA team, IB minute) take their
    FIRST value, every block is exposed in order as _events = [(IK, IF), ...], and
    IK/IF are set to the SCORING block when the chunk has one, else the first block.
    The assisting player, when present, is attached as _assist.
    """
    for chunk in raw.split("~"):
        f, events, name = {}, [], None
        for kv in chunk.split(SEP_FIELD):
            if SEP_KV not in kv:
                continue
            k, v = kv.split(SEP_KV, 1)
            if k == "IF":
                name = v
                f.setdefault("IF", v)
            elif k == "IK":
                events.append((v, name or ""))
                name = None
            else:
                f.setdefault(k, v)
        if events:
            f["_events"] = events
            scoring = next(((kd, p) for kd, p in events if kd in GOAL_EVENTS), None)
            f["IK"], f["IF"] = scoring if scoring else events[0]
            assist = next((p for kd, p in events if kd == "Assistance"), "")
            if assist:
                f["_assist"] = assist
        if f:
            yield f


# ── fixture list ───────────────────────────────────────────────────────────────
def get_fixtures(offset=0):
    """Return upcoming fixtures from the Flashscore schedule for `offset` days ahead."""
    raw = fetch(f"f_1_{offset}_1_en-ng_1")
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


# ── H2H history parser (v2-compatible, PLUS extracts past match IDs) ──────────
def parse_history(raw):
    """
    Parse df_hh_1_{id} feed.
    Returns:
      home_rows — list of dicts (gf, ga, res, venue, hg, ag, match_id) for home team
      away_rows — same for away team
      past_ids  — {team_name: [match_id, ...]}  for optional deep fetch

    Only "Last matches" blocks are kept (the head-to-head block is a different
    universe), and friendlies/cups are dropped so this sample matches the one
    acca.recent_kc builds the lambdas from.
    hg/ag are the match's true home/away goals - used to validate deep summaries.
    """
    record_league_goals(raw)          # free league-regime harvest, see above
    blocks    = defaultdict(list)
    past_ids  = defaultdict(list)   # collect match IDs per team
    tab, blk  = None, None
    for f in sections(raw):
        if "KA" in f:
            tab = f["KA"]
        if "KB" in f:
            blk = f["KB"]
            continue
        if not blk or not blk.startswith("Last matches"):
            continue
        if "KJ" in f and "KK" in f and tab == "Overall":
            if DROP_COMP.search(f.get("KF", "")):
                continue
            try:
                hg, ag = int(f.get("KU", "")), int(f.get("KT", ""))
            except ValueError:
                continue
            venue = f.get("KS", "")
            gf, ga = (hg, ag) if venue == "home" else (ag, hg)
            res = "W" if gf > ga else "L" if gf < ga else "D"
            match_id = f.get("KP", "")
            try: kc = int(f.get("KC", 0))
            except ValueError: kc = 0
            # kc lets the consistency scanner tell a season break from a normal
            # week; the parser did not capture it before 4 Aug.
            row = dict(gf=gf, ga=ga, res=res, venue=venue, match_id=match_id,
                       hg=hg, ag=ag, kc=kc)
            blocks[blk].append(row)
            if match_id:
                past_ids[blk].append(match_id)

    names     = list(blocks)
    home_rows = blocks[names[0]] if len(names) > 0 else []
    away_rows = blocks[names[1]] if len(names) > 1 else []
    return home_rows, away_rows, dict(past_ids)


# ── deep match summary parser ──────────────────────────────────────────────────
def parse_match_summary(match_id, expect_home=None, expect_away=None):
    """
    Fetch df_sui_1_{match_id} and return:
      {
        "ht_home": int,   # home goals at half-time
        "ht_away": int,   # away goals at half-time
        "ft_home": int,   # full-time home (ht + 2nd half)
        "ft_away": int,
        "goals": [{"minute": int|None, "team": "home"|"away", "player": str}, ...]
        "red_home": int,
        "red_away": int,
        "halves_ok": bool,     # both half sections were present and parsed
        "goals_ok":  bool,     # the goal events account for every goal in the FT score
      }
    Returns None if the fetch failed, the feed was a stub, or the parsed FT score
    disagrees with the known scoreline (expect_home/expect_away).

    FIELD MAPPING (verified 29 Jul against three matches whose real scorelines were
    read off the H2H feed - e.g. EiZXalET "KVZ 0-4 Al-Hilal Omdurman"):
        IG = HOME goals, IH = AWAY goals.
    This is the opposite of what this parser originally assumed, which mirrored
    every half-time and second-half average it produced.
        IA = "1" home team, IA = "2" away team  (confirmed correct).
    """
    raw = fetch(f"df_sui_1_{match_id}", ttl=48 * 3600)   # past matches -> long TTL
    if not raw:
        return None

    # Keys: AC=section name, IG=home goals, IH=away goals,
    # IB=minute, IK=event type, IF=player, IA=team index
    result = {"ht_home": 0, "ht_away": 0, "ft_home": 0, "ft_away": 0,
              "goals": [], "red_home": 0, "red_away": 0,
              "halves_ok": False, "goals_ok": False}
    current_half = None
    seen_1h = seen_2h = False

    def halves(f):
        """(home, away) goals out of a half section. IG=home, IH=away."""
        return int(f["IG"]), int(f["IH"])

    for f in incident_sections(raw):
        # AC and IG/IH can appear in the SAME chunk - handle together, no continue
        if "AC" in f:
            half_name = f["AC"].lower()
            if "1st" in half_name:
                current_half = 1
                # IG/IH in this chunk = 1st half totals
                if "IH" in f and "IG" in f:
                    try:
                        result["ht_home"], result["ht_away"] = halves(f)
                        seen_1h = True
                    except ValueError:
                        pass
            elif "2nd" in half_name:
                current_half = 2
                # IG/IH in this chunk = 2nd half goals only -> ft = ht + 2H
                if "IH" in f and "IG" in f:
                    try:
                        h2, a2 = halves(f)
                        result["ft_home"] = result["ht_home"] + h2
                        result["ft_away"] = result["ht_away"] + a2
                        seen_2h = True
                    except ValueError:
                        pass
            # Do NOT continue - chunk may also carry an IK event below

        # Standalone IG/IH section headers (no AC, no IK) - fallback
        elif "IH" in f and "IG" in f and "IK" not in f:
            try:
                h_g, a_g = halves(f)
                if current_half == 1:
                    result["ht_home"], result["ht_away"] = h_g, a_g
                    seen_1h = True
                elif current_half == 2:
                    result["ft_home"] = result["ht_home"] + h_g
                    result["ft_away"] = result["ht_away"] + a_g
                    seen_2h = True
            except ValueError:
                pass
            continue

        # Individual events
        if "IK" in f:
            event   = f.get("IK", "")
            player  = f.get("IF", "")
            minute_raw = f.get("IB", "")
            # minute may be "45+2'" or "90'" - strip to int. An unparseable minute
            # stays None rather than 0, which would otherwise read as an early goal.
            try:
                minute = int(re.sub(r"[^\d]", "", minute_raw.split("+")[0]))
            except (ValueError, IndexError):
                minute = None
            # team: IA=1 home, IA=2 away (verified against known scorelines)
            ia = f.get("IA", "")
            if ia == "1":
                team = "home"
            elif ia == "2":
                team = "away"
            else:
                # Fallback to IOX if IA missing (rare)
                iox = f.get("IOX", "")
                team = "home" if iox == "1" else "away"

            if event in GOAL_EVENTS:
                result["goals"].append({"minute": minute, "team": team,
                                        "player": player, "half": current_half,
                                        "assist": f.get("_assist", ""),
                                        "kind": event})
            # Check every block in the chunk - a red card can share a chunk with the
            # foul or the second yellow that produced it.
            elif any("Red" in kd for kd, _ in f.get("_events", [(event, "")])):
                if team == "home":
                    result["red_home"] += 1
                else:
                    result["red_away"] += 1

    result["halves_ok"] = seen_1h and seen_2h
    if not result["halves_ok"]:
        # df_sui came back without half sections (seen on quiet games where the body
        # is a 20-byte stub). df_sur carries the half scoreline on its own - one extra
        # request, only on the matches that need it, and it saves the sample.
        rescue = parse_half_scores(match_id)
        if rescue:
            (result["ht_home"], result["ht_away"],
             result["ft_home"], result["ft_away"]) = rescue
            result["halves_ok"] = True
    # A stub body (seen on 0-0 games: 20 bytes, no sections) parses as 0-0 and is
    # indistinguishable from a real 0-0. Validate against the scoreline we already
    # know from the H2H feed and throw the sample away when it does not agree -
    # this is also what catches any future field-mapping drift.
    if expect_home is not None and expect_away is not None:
        if not result["halves_ok"]:
            return None
        if (result["ft_home"], result["ft_away"]) != (expect_home, expect_away):
            return None
    # HT can never exceed FT; a negative second half would poison the Poisson lambdas.
    if result["ft_home"] < result["ht_home"] or result["ft_away"] < result["ht_away"]:
        return None

    scored = len(result["goals"])
    result["goals_ok"] = (scored == result["ft_home"] + result["ft_away"])
    return result


# ── per-half scoreline (cheapest feed: 64 bytes) ──────────────────────────────
def parse_half_scores(match_id):
    """
    Fetch df_sur_1_{match_id} -> (ht_home, ht_away, ft_home, ft_away) or None.
      BA/BB = 1st half home/away, BC/BD = 2nd half home/away.
    Verified on EiZXalET (KVZ 0-4 Al-Hilal): BA0 BB2 / BC0 BD2 -> HT 0-2, FT 0-4.
    Used to rescue a match whose df_sui body came back as a stub.
    """
    raw = fetch(f"df_sur_1_{match_id}", ttl=48 * 3600)
    if not raw:
        return None
    h1 = a1 = h2 = a2 = None
    for f in sections(raw):
        if "BA" in f and "BB" in f:
            try: h1, a1 = int(f["BA"]), int(f["BB"])
            except ValueError: return None
        if "BC" in f and "BD" in f:
            try: h2, a2 = int(f["BC"]), int(f["BD"])
            except ValueError: return None
    if None in (h1, a1, h2, a2):
        return None
    return h1, a1, h1 + h2, a1 + a2


# ── full match statistics table ───────────────────────────────────────────────
def stat_num(v):
    """'69%' -> 69.0, '89% (593/669)' -> 89.0, '1.79' -> 1.79, '' -> None."""
    m = re.match(r"\s*(-?\d+(?:\.\d+)?)", v or "")
    return float(m.group(1)) if m else None


def record_league_stats(match_id, table):
    """Attribute a df_st table to its league so per-league stat norms accumulate.
    Free - these tables are already being fetched for the team profiles."""
    lk = _match_league.get(match_id)
    if not lk or not table:
        return
    m = table.get("match") or {}
    for k in LEAGUE_STAT_KEYS:
        pair = m.get(k)
        if pair and pair[0] is not None and pair[1] is not None:
            _league_stat_seen[(match_id, k)] = (lk, pair[0], pair[1])


def parse_match_stats(match_id):
    """
    Fetch df_st_1_{match_id} -> the statistics table, split by half:
      {"match": {key: (home, away)}, "1h": {...}, "2h": {...}}
    Returns None when the feed carries no stats (common outside top divisions -
    the body is the single character "0").

    Field mapping: SE = section, SF = display group, SD = stat id, SG = stat name,
    SH = HOME value, SI = AWAY value.
    The SH=home / SI=away orientation was verified empirically over 45 finished
    matches with a decided result: the side with more shots on target is the winner
    34 times under SH=home versus 11 under the reverse.
    """
    raw = fetch(f"df_st_1_{match_id}", ttl=48 * 3600)
    if not raw or len(raw) < 20:
        return None

    out, sec = {}, None
    for f in sections(raw):
        if "SE" in f:
            name = f["SE"].lower()
            sec = "match" if "match" in name else "1h" if "1st" in name else "2h" if "2nd" in name else None
            if sec:
                out.setdefault(sec, {})
        if sec and "SD" in f and ("SH" in f or "SI" in f):
            key = STAT_KEYS.get(f["SD"], f["SD"])
            out[sec][key] = (stat_num(f.get("SH")), stat_num(f.get("SI")))
    if not out:
        return None

    # MISSING DATA MASQUERADING AS ZERO (guard added 31 Jul).
    # Leagues with thin coverage return a stats table of all zeros rather than no
    # table at all - Drogheda v Shamrock Rovers came back shots (0,0), possession
    # (0,0), corners (0,0), xG (0,0). No match has zero shots and zero possession,
    # so that is absence of data, not a goalless grind. Averaged in as real zeros
    # it drags a team's profile DOWN, which makes every Under look safer than it
    # is - the direction that loses money. One such game in a 7-game profile cuts
    # the corner mean by 14%, two by 29%. Measured at 1.4% of games overall, but
    # it clusters in exactly the low-coverage leagues this engine likes to bet.
    m = out.get("match") or {}
    sh, poss = m.get("shots"), m.get("possession")
    if sh and poss and not any(v for v in list(sh) + list(poss) if v):
        return None
    return out


# ── venue-split history with deep HT/goal-minute stats ────────────────────────
def fetch_rich_history(fixture_id, verbose=False, raw=None, with_stats=FETCH_STATS):
    """
    Full enriched history for a fixture.
    Pass `raw` when the caller has already pulled df_hh_1_{fixture_id} (book_v3 does)
    to avoid fetching the same feed twice through two separate caches.
    with_stats also pulls the df_st statistics table for each deep game (xG, shots,
    big chances, possession, ... split by half) - one extra request per past match.
    Returns:
      home_rows, away_rows  — same as parse_history (v2 compatible)
      rich                  — dict with all extra stats below

    rich keys:
      home_ht_avg     — avg goals scored by home team in first half (home games)
      home_2h_avg     — avg goals scored by home team in second half (home games)
      away_ht_avg     — avg goals by away team in first half (away games)
      away_2h_avg     — avg goals by away team in second half (away games)
      home_ht_con_avg — avg goals conceded by home team in first half (home games)
      away_ht_con_avg — avg goals conceded by away team in first half (away games)
      home_btts_ht    — fraction of home games where both scored in first half
      away_btts_ht    — fraction of away games where both scored in first half
      home_first_goal — fraction of home games where home team scored first
      away_first_goal — fraction of away games where away team scored first
      home_early_goal — fraction of home games with goal before 30 min
      away_early_goal — fraction of away games with goal before 30 min
      home_late_goal  — fraction of home games with goal after 75 min
      away_late_goal  — fraction of away games with goal after 75 min
      home_red_avg    — avg red cards against home team in home games
      away_red_avg    — avg red cards against away team in away games
      deep_games      — number of past matches with deep HT/minute data
      deep_rejected   — samples thrown away because the parse did not match the score
      home_stats      — {stat: {for, against, 1h_for, 1h_against, 2h_for, 2h_against, n}}
                        averaged over the home team's HOME games (df_st table)
      away_stats      — same over the away team's AWAY games
      stat_games      — number of past matches that carried a statistics table
      home_xg_for / home_xg_against / home_xg_1h_for / home_xg_2h_for (and the same
      for sot, shots, big_chances, corners, and for the away side) — flat shortcuts
    """
    if raw is None:
        raw = fetch(f"df_hh_1_{fixture_id}")
    if not raw:
        return [], [], {}

    home_rows, away_rows, past_ids = parse_history(raw)

    rich = {
        "home_ht_avg": None, "home_2h_avg": None,
        "away_ht_avg": None, "away_2h_avg": None,
        "home_ht_con_avg": None, "away_ht_con_avg": None,
        "home_btts_ht": None, "away_btts_ht": None,
        "home_first_goal": None, "away_first_goal": None,
        "home_early_goal": None, "away_early_goal": None,
        "home_late_goal": None, "away_late_goal": None,
        "home_red_avg": None, "away_red_avg": None,
        "deep_games": 0, "deep_rejected": 0,
    }

    home_deep_stats = []   # parse_match_summary dicts for the home team's HOME games
    away_deep_stats = []   # parse_match_summary dicts for the away team's AWAY games
    home_stat_rows  = []   # parse_match_stats tables for the same games
    away_stat_rows  = []
    rejected = 0

    def collect(rows, venue, budget, sink, stat_sink, venue_sink=None):
        """Open up to `budget` of this team's games, validating each summary
        against the scoreline already known from the H2H feed.

        venue=None collects EVERY game in the window and records each game's own
        venue alongside it. Venue-splitting here was starving the sample: after
        the 2026 World Cup break most teams have three games played, only one of
        them at any given venue, so a venue-split series can never reach a
        workable minimum. The split is now applied downstream, where it can fall
        back to the full sample when it would leave too little."""
        nonlocal rejected
        for row in trim_at_season_gap(rows)[:RECENT_WINDOW]:
            mid = row.get("match_id", "")
            if not mid or (venue is not None and row["venue"] != venue):
                continue
            if len(sink) >= budget:
                break
            cached = is_cached(f"df_sui_1_{mid}", ttl=48 * 3600)
            summary = parse_match_summary(mid, row.get("hg"), row.get("ag"))
            if summary:
                sink.append(summary)
                if venue_sink is not None:
                    venue_sink.append(row["venue"])
                if with_stats:
                    st = parse_match_stats(mid)
                    if st:
                        stat_sink.append((st, row["venue"]))
                        record_league_stats(mid, st)
            else:
                rejected += 1
            if not cached:          # only pay the politeness sleep on a real request
                time.sleep(DEEP_SLEEP)

    # Each team gets its own budget - a home team with few home games used to leave
    # the away team free to burn the whole allowance.
    home_venues, away_venues = [], []
    collect(home_rows, None, MAX_DEEP_GAMES, home_deep_stats, home_stat_rows, home_venues)
    collect(away_rows, None, MAX_DEEP_GAMES, away_deep_stats, away_stat_rows, away_venues)
    rich["home_game_venues"] = home_venues
    rich["away_game_venues"] = away_venues

    rich["deep_games"]    = len(home_deep_stats) + len(away_deep_stats)
    rich["deep_rejected"] = rejected

    # ── average the df_st table into a venue-split profile per side ───────────
    def stat_profile(tables, side):
        """tables: [(stat_table, venue_of_that_game), ...]. Which column belongs to
        this team is decided PER GAME now - it is column SH when the team played
        at home in that game and SI when away - because the sample is no longer
        filtered to a single venue before it gets here."""
        prof = {}
        for key in PROFILE_STATS:
            for part, sec in (("", "match"), ("1h_", "1h"), ("2h_", "2h")):
                vals_f, vals_a = [], []
                for t, gv in tables:
                    mine, theirs = (0, 1) if gv == "home" else (1, 0)
                    pair = (t.get(sec) or {}).get(key)
                    if not pair:
                        continue
                    if pair[mine] is not None: vals_f.append(pair[mine])
                    if pair[theirs] is not None: vals_a.append(pair[theirs])
                slot = prof.setdefault(key, {})
                slot[part + "for"]     = sum(vals_f) / len(vals_f) if vals_f else None
                slot[part + "against"] = sum(vals_a) / len(vals_a) if vals_a else None
                # Keep the RAW per-game series, not just the mean. A mean cannot tell
                # 4,5,4,5,5 apart from 0,1,12,0,11 - both average 4.8 - but only the
                # first is bettable. The consistency scanner in book_v3 needs the
                # games themselves to count how many actually cleared a line.
                slot[part + "series_for"]     = vals_f
                slot[part + "series_against"] = vals_a
                if not part:
                    slot["n"] = len(vals_f)
        return prof

    rich["home_stats"] = stat_profile(home_stat_rows, "home")
    rich["away_stats"] = stat_profile(away_stat_rows, "away")
    rich["stat_games"] = len(home_stat_rows) + len(away_stat_rows)
    # Flat convenience keys for the stats a goals model actually uses.
    for side in ("home", "away"):
        prof = rich[f"{side}_stats"]
        for key in ("xg", "sot", "shots", "big_chances", "corners"):
            slot = prof.get(key, {})
            rich[f"{side}_{key}_for"]     = slot.get("for")
            rich[f"{side}_{key}_against"] = slot.get("against")
            rich[f"{side}_{key}_1h_for"]  = slot.get("1h_for")
            rich[f"{side}_{key}_2h_for"]  = slot.get("2h_for")

    def minute_stats(stats, side):
        """(first, early, late) fractions for `side`. Computed only over matches whose
        goal events account for the whole scoreline - a dropped event would otherwise
        read as "this team never scored"."""
        usable = [s for s in stats if s["goals_ok"]]
        if len(usable) < MIN_MINUTE_GAMES:
            # Under a handful of games these read 0% or 100% and book_v3 gates on
            # them (safe_1x, safe_1h_fg, early_goal_ok). Better to report nothing.
            return None, None, None
        n = len(usable)
        first = early = late = 0
        for s in usable:
            timed = [g for g in s["goals"] if g.get("minute") is not None]
            ordered = sorted(timed, key=lambda g: g["minute"])
            if ordered and ordered[0]["team"] == side:
                first += 1
            if any(g["team"] == side and g["minute"] < 30 for g in timed):
                early += 1
            if any(g["team"] == side and g["minute"] > 75 for g in timed):
                late += 1
        return first / n, early / n, late / n

    def own(summ, gv, part):
        """This team's own figure for a past game, and the opponent's.

        The summary keys are the PAST MATCH's home/away, so which one is "us"
        depends on where we played that game. Reading ht_home unconditionally
        was correct only while the sample was venue-filtered; it is not now."""
        h, a = summ[part + "_home"], summ[part + "_away"]
        return (h, a) if gv == "home" else (a, h)

    def runs(summary, gv):
        """(longest run of OUR consecutive goals, longest run of THEIRS).

        "Any/Home/Away Team To Score N or More Goals in a Row" needs the ORDER
        goals went in, not the counts - Lyon 3-0 Sparta was H20 -> H66 -> H72, a
        run of three, while a 3-0 built as H-A-H-H is a run of two. The order is
        already in every summary we fetch (`goals` carries team and minute), so
        no extra request is needed. Extra-time goals are dropped: SportyBet
        settles these on regulation, and Apollon v Brann ran to the 105th."""
        seq = [g["team"] for g in (summary.get("goals") or [])
               if g.get("minute") is None or g["minute"] <= 90]
        best, cur, run = {"home": 0, "away": 0}, None, 0
        for t in seq:
            run = run + 1 if t == cur else 1
            cur = t
            if run > best.get(cur, 0):
                best[cur] = run
        h, a = best["home"], best["away"]
        return (h, a) if gv == "home" else (a, h)

    # ── compute home-team deep stats ──────────────────────────────────────────
    if home_deep_stats:
        n = len(home_deep_stats)
        rich["home_ht_avg"]    = sum(s["ht_home"] for s in home_deep_stats) / n
        rich["home_2h_avg"]    = sum(s["ft_home"] - s["ht_home"] for s in home_deep_stats) / n
        rich["home_ht_con_avg"]= sum(s["ht_away"] for s in home_deep_stats) / n
        # Raw per-game half goals, for the consistency scanner. Averages cannot
        # distinguish a side that is quiet EVERY first half from one that has a
        # 0,0,0,0,3 record, and only the first is bettable at 1H Under 0.5.
        _pairs = list(zip(home_deep_stats, home_venues))
        rich["home_ht_gf_series"] = [own(x, v, "ht")[0] for x, v in _pairs]
        rich["home_ht_ga_series"] = [own(x, v, "ht")[1] for x, v in _pairs]
        rich["home_2h_gf_series"] = [own(x, v, "ft")[0] - own(x, v, "ht")[0] for x, v in _pairs]
        rich["home_2h_ga_series"] = [own(x, v, "ft")[1] - own(x, v, "ht")[1] for x, v in _pairs]
        # Full-time goals from the SAME list, so 1H / 2H / FT are index-aligned.
        # Taking FT from the form rows instead let the two lists slide apart
        # whenever a summary failed to parse (~10%), producing games where
        # 1H + 2H did not equal FT and half markets counted the wrong match.
        rich["home_ft_gf_series"] = [own(x, v, "ft")[0] for x, v in _pairs]
        rich["home_ft_ga_series"] = [own(x, v, "ft")[1] for x, v in _pairs]
        rich["home_run_gf_series"] = [runs(x, v)[0] for x, v in _pairs]
        rich["home_run_ga_series"] = [runs(x, v)[1] for x, v in _pairs]

        btts_ht = sum(1 for s in home_deep_stats if s["ht_home"] > 0 and s["ht_away"] > 0)
        rich["home_btts_ht"]   = btts_ht / n

        (rich["home_first_goal"], rich["home_early_goal"],
         rich["home_late_goal"]) = minute_stats(home_deep_stats, "home")
        rich["home_red_avg"]    = sum(s["red_home"] for s in home_deep_stats) / n

    # ── compute away-team deep stats ──────────────────────────────────────────
    if away_deep_stats:
        n = len(away_deep_stats)
        rich["away_ht_avg"]    = sum(s["ht_away"] for s in away_deep_stats) / n
        rich["away_2h_avg"]    = sum(s["ft_away"] - s["ht_away"] for s in away_deep_stats) / n
        rich["away_ht_con_avg"]= sum(s["ht_home"] for s in away_deep_stats) / n
        _pairs = list(zip(away_deep_stats, away_venues))
        rich["away_ht_gf_series"] = [own(x, v, "ht")[0] for x, v in _pairs]
        rich["away_ht_ga_series"] = [own(x, v, "ht")[1] for x, v in _pairs]
        rich["away_2h_gf_series"] = [own(x, v, "ft")[0] - own(x, v, "ht")[0] for x, v in _pairs]
        rich["away_2h_ga_series"] = [own(x, v, "ft")[1] - own(x, v, "ht")[1] for x, v in _pairs]
        rich["away_ft_gf_series"] = [own(x, v, "ft")[0] for x, v in _pairs]
        rich["away_ft_ga_series"] = [own(x, v, "ft")[1] for x, v in _pairs]
        rich["away_run_gf_series"] = [runs(x, v)[0] for x, v in _pairs]
        rich["away_run_ga_series"] = [runs(x, v)[1] for x, v in _pairs]

        btts_ht = sum(1 for s in away_deep_stats if s["ht_home"] > 0 and s["ht_away"] > 0)
        rich["away_btts_ht"]   = btts_ht / n

        (rich["away_first_goal"], rich["away_early_goal"],
         rich["away_late_goal"]) = minute_stats(away_deep_stats, "away")
        rich["away_red_avg"]    = sum(s["red_away"] for s in away_deep_stats) / n

    if verbose:
        print(f"  deep stats fetched: {rich['deep_games']} match summaries"
              f" ({rich['deep_rejected']} rejected)")

    return home_rows, away_rows, rich


# ── feature vector (v2-compatible + new rich fields) ──────────────────────────
def feats(rows):
    """Basic feature vector from summary rows. Same output as fetcher_v2.feats()."""
    if not rows:
        return None
    f5  = rows[:5]
    f10 = rows[:10]
    form5 = "".join(r["res"] for r in f5)
    fpts  = 3 * form5.count("W") + form5.count("D")
    return dict(
        form5=form5, fpts=fpts,
        W=form5.count("W"), L=form5.count("L"), D=form5.count("D"),
        gd10=sum(r["gf"] - r["ga"] for r in f10),
        gf10=sum(r["gf"] for r in f10),
        ga10=sum(r["ga"] for r in f10),
        gf_pg=sum(r["gf"] for r in f10) / len(f10),
        ga_pg=sum(r["ga"] for r in f10) / len(f10),
        ppg10=sum(3 if r["res"] == "W" else 1 if r["res"] == "D" else 0 for r in f10) / len(f10),
    )


def rich_feats(rows, venue_filter):
    """
    Venue-split features.
    venue_filter: "home" or "away"
    Returns same shape as feats() but restricted to venue games.
    """
    vrows = [r for r in rows if r.get("venue") == venue_filter]
    if not vrows:
        return feats(rows)   # fall back to overall
    return feats(vrows)


# ── pretty print rich stats ────────────────────────────────────────────────────
def format_rich(rich):
    """Return a list of display lines for the rich stats block."""
    lines = []
    def pct(v):
        return f"{v*100:.0f}%" if v is not None else "n/a"
    def avg(v):
        return f"{v:.2f}" if v is not None else "n/a"

    ht_line = (f"  HT scored: home {avg(rich['home_ht_avg'])} / away {avg(rich['away_ht_avg'])}"
               f"  |  2H scored: home {avg(rich['home_2h_avg'])} / away {avg(rich['away_2h_avg'])}")
    lines.append(ht_line)

    btts_line = (f"  BTTS-HT: home {pct(rich['home_btts_ht'])} / away {pct(rich['away_btts_ht'])}"
                 f"  |  First goal: home {pct(rich['home_first_goal'])} / away {pct(rich['away_first_goal'])}")
    lines.append(btts_line)

    timing_line = (f"  Early(<30): home {pct(rich['home_early_goal'])} / away {pct(rich['away_early_goal'])}"
                   f"  |  Late(>75): home {pct(rich['home_late_goal'])} / away {pct(rich['away_late_goal'])}")
    lines.append(timing_line)

    red_line = (f"  Reds: home {avg(rich['home_red_avg'])} / away {avg(rich['away_red_avg'])}"
                f"  |  deep_games={rich['deep_games']} rejected={rich.get('deep_rejected', 0)}")
    lines.append(red_line)

    if rich.get("stat_games"):
        lines.append(f"  xG for/against: home {avg(rich.get('home_xg_for'))}/{avg(rich.get('home_xg_against'))}"
                     f"  away {avg(rich.get('away_xg_for'))}/{avg(rich.get('away_xg_against'))}"
                     f"  |  xG by half (for): home {avg(rich.get('home_xg_1h_for'))}+{avg(rich.get('home_xg_2h_for'))}"
                     f"  away {avg(rich.get('away_xg_1h_for'))}+{avg(rich.get('away_xg_2h_for'))}")
        lines.append(f"  Shots on target: home {avg(rich.get('home_sot_for'))}/{avg(rich.get('home_sot_against'))}"
                     f"  away {avg(rich.get('away_sot_for'))}/{avg(rich.get('away_sot_against'))}"
                     f"  |  Big chances: home {avg(rich.get('home_big_chances_for'))}"
                     f"  away {avg(rich.get('away_big_chances_for'))}"
                     f"  |  stat_games={rich['stat_games']}")

    return lines


# ── CLI entrypoint ─────────────────────────────────────────────────────────────
def main():
    offset = 0
    if "--offset" in sys.argv:
        offset = int(sys.argv[sys.argv.index("--offset") + 1])

    deep = "--deep" in sys.argv  # pass --deep to enable HT/goal-minute fetch

    fixtures = get_fixtures(offset)
    print(f"feed: {len(fixtures)} fixtures (offset {offset})")
    now = time.time()
    fixtures = [f for f in fixtures if f["ts"] > now + 600]
    print(f"{len(fixtures)} upcoming")

    for i, fx in enumerate(fixtures[:20]):  # limit to 20 for CLI demo
        print(f"\n[{i+1}] {fx['home']} v {fx['away']}  —  {fx['league']}")
        if deep:
            home_rows, away_rows, rich = fetch_rich_history(fx["id"], verbose=True)
        else:
            raw = fetch(f"df_hh_1_{fx['id']}")
            if not raw: continue
            home_rows, away_rows, _ = parse_history(raw)
            rich = {}

        hf = feats(home_rows)
        af = feats(away_rows)
        if hf:
            print(f"  home form5={hf['form5']}  gf_pg={hf['gf_pg']:.2f}  ga_pg={hf['ga_pg']:.2f}")
        if af:
            print(f"  away form5={af['form5']}  gf_pg={af['gf_pg']:.2f}  ga_pg={af['ga_pg']:.2f}")
        if rich and rich.get("deep_games", 0) > 0:
            for line in format_rich(rich):
                print(line)
        time.sleep(0.1)


if __name__ == "__main__":
    main()
