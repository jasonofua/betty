#!/usr/bin/env python3
"""Grade a SportyBet booking code straight from the share API (legs + scores).
Usage: python3 grade_code.py HFDETF Y5RUNJ"""
import urllib.request, json, re, sys
sys.path.append('/Users/apple/Downloads/draw')
import acca as A
HDRS = A.HDRS

import dynamic_v4 as D


def api_label(m, oc):
    """The market's OWN name from the share API, plus the outcome.

    label_of() below is a hand-written id -> name table; every market added to
    SportyBet since it was written graded as "?". The API has carried the name
    in m['desc'] all along, so there is nothing to maintain."""
    d = (m.get('desc') or '').strip()
    o = (oc.get('desc') or '').strip()
    if not d:
        return f"mid{m.get('id')} / {o}"
    return f"{d} / {o}" if o else d


def grade_by_predicate(m, oc, h, a, h1h, h1a, teams=(None, None)):
    """Settle from the score using the SAME predicate the picker counted with.

    Reusing dynamic_v4 here means the grader cannot disagree with the picker
    about what a market means - if one of them is wrong about "Home or Draw",
    both are, and it shows up immediately instead of silently."""
    if h is None or a is None:
        return '?'
    name = (m.get('desc') or '')
    spec = m.get('specifier', '') or ''
    if not D.spec_ok(spec):
        return '?'
    q, per, side = D.parse_market(name, ('goals', 'h1', 'h2'), teams)
    if q != 'goals':
        return '?'                       # corners/cards: no data in the score
    if per == 'h1':
        if h1h is None:
            return '?'
        f, ag = h1h, h1a
    elif per == 'h2':
        if h1h is None:
            return '?'
        f, ag = h - h1h, a - h1a
    else:
        f, ag = h, a
    if side == 'away':
        f, ag = ag, f
    test = D.parse_outcome(name, oc.get('desc'), spec, ('goals', 'h1', 'h2'), teams)
    if test is None:
        return '?'
    try:
        return 'WIN' if test(f, ag) else 'LOSE'
    except Exception:
        return '?'


def label_of(mid, spec, odesc):
    if mid == '1':  return {'Home': '1 Home', 'Away': '2 Away', 'Draw': 'X Draw'}.get(odesc, '?')
    if mid == '10': return {'Home or Draw': '1X', 'Draw or Away': 'X2', 'Home or Away': '12'}.get(odesc, '?')
    if mid == '11': return {'Home': 'DNB Home', 'Away': 'DNB Away'}.get(odesc, '?')
    if mid == '16':
        m = re.match(r'(Home|Away)\s*\(([+-]?[\d.]+)\)', odesc)
        if m: return f"AH{'+' if float(m.group(2))>=0 else ''}{float(m.group(2)):g} {m.group(1)}"
    if mid == '18':
        m = re.match(r'(Over|Under)\s+([\d.]+)', odesc)
        if m: return f"{m.group(1)}{float(m.group(2)):g}"
    if mid == '68':
        m = re.match(r'(Over|Under)\s+([\d.]+)', odesc)
        if m: return f"1H {m.group(1)}{float(m.group(2)):g}"
    if mid == '90':
        m = re.match(r'(Over|Under)\s+([\d.]+)', odesc)
        if m: return f"2H {m.group(1)}{float(m.group(2)):g}"
    if mid in ('19', '20', '69', '70', '81', '82'):
        # 19/20 = FT Home/Away OU, 69/70 = 1H Home/Away OU, 81/82 = 2H Home/Away OU (mostly guessed, but let's just parse what we can)
        side = 'Home' if int(mid) % 2 != 0 else 'Away'
        m = re.match(r'(Over|Under)\s+([\d.]+)', odesc)
        if m: return f"{side} {m.group(1)[0]}{float(m.group(2)):g}"
    if mid == '29': return {'Yes': 'GG', 'No': 'NG'}.get(odesc, '?')
    if mid == '75': return {'Yes': '1H GG', 'No': '1H NG'}.get(odesc, '?')
    if mid == '95': return {'Yes': '2H GG', 'No': '2H NG'}.get(odesc, '?')
    if mid == '52': return f"Highest Scoring Half: {odesc}"
    if mid == '76': return {'Yes': '1H Home CS (Away 0)', 'No': '1H Home CS No'}.get(odesc, '?')
    if mid == '77': return {'Yes': '1H Away CS (Home 0)', 'No': '1H Away CS No'}.get(odesc, '?')
    if mid == '21': return f"Exact Goals: {odesc}"
    # 1H DNB (mid 64) - distinct from FT DNB mid 11
    if mid == '64': return {'Home': '1H DNB Home', 'Away': '1H DNB Away'}.get(odesc, '?')
    # 2H Double Chance (mid 84) - distinct from FT DC mid 10
    if mid == '84': return {'Home or Draw': '2H 1X', 'Draw or Away': '2H X2', 'Home or Away': '2H 12'}.get(odesc, '?')
    # HT/FT (mid 47) - 9 outcomes: "Home/Home", "Home/Draw", etc.
    if mid == '47':
        if '/' in odesc:
            parts = odesc.split('/')
            return f"HT/FT {parts[0][:1]}/{parts[1][:1]}"      # H/H, H/D, H/A, D/H, D/D, D/A, A/H, A/D, A/A
        return f"HT/FT {odesc}"
    # Result + BTTS combos (mid 35/78/540-545)
    if mid in ('35', '78', '540', '541', '542', '543', '545'):
        return f"Res+BTTS: {odesc}"
    # Result + Total combos (mid 37/544)
    if mid in ('37', '544'):
        return f"Res+Total: {odesc}"
    # DC + BTTS (mid 546)
    if mid == '546':
        return f"DC+BTTS: {odesc}"
    # DC + Total (mid 547)
    if mid == '547':
        return f"DC+Total: {odesc}"
    # FT Odd/Even (mid 26/27/28)
    if mid in ('26', '27', '28'):
        return {'Odd': 'Odd', 'Even': 'Even'}.get(odesc, f'OE {odesc}')
    # Winning Margin (mid 15) - "Home by 1", "Home by 2", "Home by 3+", etc.
    if mid == '15': return f"WM: {odesc}"
    # ── ids below re-checked 30 Jul against the live SportyBet catalog. Several
    # were labelled as the wrong market, which also mis-graded them in the
    # "ended but not yet settled" window where grade() computes from the score.
    # 48/49 is to WIN both halves; 56/57 is to SCORE in both halves - not the same.
    if mid == '48': return {'Yes': 'Home wins both halves', 'No': 'Home not win both halves'}.get(odesc, f'HWBH:{odesc}')
    if mid == '49': return {'Yes': 'Away wins both halves', 'No': 'Away not win both halves'}.get(odesc, f'AWBH:{odesc}')
    if mid == '56': return {'Yes': 'Home both halves', 'No': 'Home not both halves'}.get(odesc, f'HBH:{odesc}')
    if mid == '57': return {'Yes': 'Away both halves', 'No': 'Away not both halves'}.get(odesc, f'ABH:{odesc}')
    # 50/51 are "to win EITHER half", not "to score first"
    if mid == '50': return {'Yes': 'Home wins a half', 'No': 'Home wins no half'}.get(odesc, f'HWEH:{odesc}')
    if mid == '51': return {'Yes': 'Away wins a half', 'No': 'Away wins no half'}.get(odesc, f'AWEH:{odesc}')
    # 31/32 are team CLEAN SHEET, not "score first"
    if mid == '31': return {'Yes': 'Home CS', 'No': 'Home CS No'}.get(odesc, f'HCS:{odesc}')
    if mid == '32': return {'Yes': 'Away CS', 'No': 'Away CS No'}.get(odesc, f'ACS:{odesc}')
    if mid in ('96', '97'):
        side = 'Home' if mid == '96' else 'Away'
        return {'Yes': f'2H {side} CS', 'No': f'2H {side} CS No'}.get(odesc, f'2H{side}CS:{odesc}')
    # 55 is 1st/2nd half GG-NG
    if mid == '55': return {'Yes': 'BTTS both halves', 'No': 'NG one half'}.get(odesc, f'BBH:{odesc}')
    # per-half exact goals
    if mid == '71': return f"1H Exact Goals: {odesc}"
    if mid == '93': return f"2H Exact Goals: {odesc}"
    if mid == '74': return {'Odd': '1H Odd', 'Even': '1H Even'}.get(odesc, f'1H OE {odesc}')
    if mid == '94': return {'Odd': '2H Odd', 'Even': '2H Even'}.get(odesc, f'2H OE {odesc}')
    # 85 is 2H Double Chance (84 is "2nd Half - Xth Goal")
    if mid == '85': return {'Home or Draw': '2H 1X', 'Draw or Away': '2H X2', 'Home or Away': '2H 12'}.get(odesc, f'2HDC:{odesc}')
    # 63 = 1st Half Double Chance. Missing entirely until 3 Aug, so every 1H DC
    # leg - the whole output of the DNB->DC swap - graded as '?' and was invisible
    # in the family breakdown.
    if mid == '63': return {'Home or Draw': '1H 1X', 'Draw or Away': '1H X2', 'Home or Away': '1H 12'}.get(odesc, f'1HDC:{odesc}')
    # Both halves over/under a line
    if mid in ('58', '59'):
        ln = spec.split('=')[1] if '=' in spec else '?'
        way = 'Over' if mid == '58' else 'Under'
        return {'Yes': f'Both halves {way} {ln}', 'No': f'Not both halves {way} {ln}'}.get(odesc, f'BH{way}:{odesc}')
    # Multigoals bands
    if mid == '548': return f"Multigoals {odesc}"
    if mid == '552': return f"1H Multigoals {odesc}"
    if mid == '553': return f"2H Multigoals {odesc}"
    # per-half handicap (decimal form)
    if mid in ('66', '88'):
        half = '1H' if mid == '66' else '2H'
        return f"{half} AH {odesc}"

    # ── Corners / Bookings / Match-stat / Team-stat markets (900xxx namespace).
    # Not computable from the scoreline, so grade() returns '?' for them and the
    # SportyBet settlement flag decides - these labels are for readability only.
    STAT = {
        '900300': 'Home corners', '900301': 'Away corners',
        '900302': '1H Home corners', '900303': '1H Away corners',
        '900304': 'Home bookings', '900305': 'Away bookings',
        '900306': '1H Home bookings', '900307': '1H Away bookings',
        '900342': 'Fouls', '900393': 'Shots on target', '900394': 'Shots',
        '900396': 'Offsides',
        '900544': 'Home fouls', '900545': 'Away fouls',
        '900546': 'Home SoT', '900547': 'Away SoT',
        '900552': 'Home shots', '900553': 'Away shots',
        '900568': 'Home offsides', '900569': 'Away offsides',
    }
    if mid in STAT:
        m = re.match(r'(Over|Under)\s+([\d.]+)', odesc)
        if m: return f"{STAT[mid]} {m.group(1)[0]}{float(m.group(2)):g}"
        return f"{STAT[mid]}: {odesc}"
    if mid == '900312': return f"Bookings AH {odesc}"
    if mid in ('900318', '900320', '900539', '900570'):
        what = {'900318': 'SoT', '900320': 'Shots', '900539': 'Fouls', '900570': 'Offsides'}[mid]
        return f"{what} 1X2: {odesc}"
    if mid == '900313':
        parts = dict(p.split('=') for p in spec.split('|') if '=' in p)
        m = re.match(r'(Over|Under)\s+([\d.]+)', odesc)
        way = f"{m.group(1)}{float(m.group(2)):g}" if m else odesc
        return f"{way} goals 0-{parts.get('minute','?')}min"
    # Match result after X minutes (mid 900069) - "Draw 0-15 Min", "Home 30 Min", etc.
    if mid == '900069':
        minute = spec.split('=')[1] if '=' in spec else '?'
        return {'Home': f'Home {minute}m', 'Draw': f'Draw {minute}m', 'Away': f'Away {minute}m'}.get(odesc, f'Interval {minute}m: {odesc}')
    RUN = {'60010': 'any2run', '60020': 'any3run', '60011': 'home2run',
           '60021': 'home3run', '60012': 'away2run', '60022': 'away3run'}
    if mid in RUN: return f"{odesc} {RUN[mid]}"      # 'No any3run' etc - settled via SportyBet flag
    return '?'

def _band(desc, total):
    """Settle a Multigoals band description ('1-2', '2-3', '4+', 'No goal')."""
    d = desc.strip()
    if d.lower() in ('no goal', 'no goals'):
        return 'WIN' if total == 0 else 'LOSE'
    if d.endswith('+'):
        try: return 'WIN' if total >= int(d[:-1]) else 'LOSE'
        except ValueError: return '?'
    if '-' in d:
        try:
            lo, hi = (int(x) for x in d.split('-', 1))
            return 'WIN' if lo <= total <= hi else 'LOSE'
        except ValueError: return '?'
    return '?'


def grade(label, h, a, h1h=None, h1a=None):
    tot = h + a; d = h - a
    m = re.match(r'(1H|2H) (Over|Under)([\d.]+)$', label)
    if m:
        if h1h is None: return '?'          # half-time score unavailable
        ln = float(m.group(3))
        if m.group(1) == '1H':
            t = h1h + h1a
        else:
            h2h = h - h1h
            h2a = a - h1a
            t = h2h + h2a

        if t == ln: return 'PUSH'
        win = (t > ln) if m.group(2) == 'Over' else (t < ln)
        return 'WIN' if win else 'LOSE'
    m = re.match(r'Over([\d.]+)$', label)
    if m: ln = float(m.group(1)); return 'WIN' if tot > ln else ('PUSH' if tot == ln else 'LOSE')
    m = re.match(r'Under([\d.]+)$', label)
    if m: ln = float(m.group(1)); return 'WIN' if tot < ln else ('PUSH' if tot == ln else 'LOSE')
    m = re.match(r'AH\+([\d.]+) Home$', label)
    if m: g = d + float(m.group(1)); return 'WIN' if g > 0 else ('PUSH' if g == 0 else 'LOSE')
    m = re.match(r'AH\+([\d.]+) Away$', label)
    if m: g = -d + float(m.group(1)); return 'WIN' if g > 0 else ('PUSH' if g == 0 else 'LOSE')
    m = re.match(r'Home O([\d.]+)$', label)
    if m: return 'WIN' if h > float(m.group(1)) else 'LOSE'
    m = re.match(r'Home U([\d.]+)$', label)
    if m: return 'WIN' if h < float(m.group(1)) else 'LOSE'
    m = re.match(r'Away O([\d.]+)$', label)
    if m: return 'WIN' if a > float(m.group(1)) else 'LOSE'
    m = re.match(r'Away U([\d.]+)$', label)
    if m: return 'WIN' if a < float(m.group(1)) else 'LOSE'
    if label == 'DNB Home': return 'PUSH' if h == a else ('WIN' if h > a else 'LOSE')
    if label == 'DNB Away': return 'PUSH' if h == a else ('WIN' if a > h else 'LOSE')

    if h1h is not None and h1a is not None:
        h2h = h - h1h
        h2a = a - h1a

        if label == '1H GG': return 'WIN' if (h1h >= 1 and h1a >= 1) else 'LOSE'
        if label == '1H NG': return 'WIN' if (h1h == 0 or h1a == 0) else 'LOSE'
        if label == '2H GG': return 'WIN' if (h2h >= 1 and h2a >= 1) else 'LOSE'
        if label == '2H NG': return 'WIN' if (h2h == 0 or h2a == 0) else 'LOSE'

        if label == '1H Home CS (Away 0)': return 'WIN' if h1a == 0 else 'LOSE'
        if label == '1H Away CS (Home 0)': return 'WIN' if h1h == 0 else 'LOSE'

        if label.startswith('Highest Scoring Half'):
            t1 = h1h + h1a
            t2 = h2h + h2a
            if '1st half' in label: return 'WIN' if t1 > t2 else 'LOSE'
            if '2nd half' in label: return 'WIN' if t2 > t1 else 'LOSE'
            if 'Equal' in label: return 'WIN' if t1 == t2 else 'LOSE'

        # 1H DNB (mid 64) - draw refunds, win/lose otherwise
        if label == '1H DNB Home': return 'PUSH' if h1h == h1a else ('WIN' if h1h > h1a else 'LOSE')
        if label == '1H DNB Away': return 'PUSH' if h1h == h1a else ('WIN' if h1h < h1a else 'LOSE')

        # 2H Double Chance (mid 84)
        if label == '2H 1X': return 'WIN' if h2h >= h2a else 'LOSE'
        if label == '2H X2': return 'WIN' if h2h <= h2a else 'LOSE'
        if label == '2H 12': return 'WIN' if h2h != h2a else 'LOSE'

        # HT/FT (mid 47) - need both HT and FT results
        if label.startswith('HT/FT '):
            ht = label[6:7]    # H, D, or A
            ft = label[8:9]
            ht_result = 'H' if h1h > h1a else 'A' if h1h < h1a else 'D'
            ft_result = 'H' if h > a else 'A' if h < a else 'D'
            if ht == ht_result and ft == ft_result:
                return 'WIN'
            elif ht_result == 'D' or ft_result == 'D':
                # partial - some HT/FT markets refund on draw, but SportyBet's typical HT/FT voids
                return 'LOSE'      # safest default; SportyBet's own flag will override if void
            return 'LOSE'

        # Team SCORES in both halves (mid 56/57)
        if label == 'Home both halves': return 'WIN' if (h1h >= 1 and h2h >= 1) else 'LOSE'
        if label == 'Away both halves': return 'WIN' if (h1a >= 1 and h2a >= 1) else 'LOSE'
        if label == 'Home not both halves': return 'WIN' if not (h1h >= 1 and h2h >= 1) else 'LOSE'
        if label == 'Away not both halves': return 'WIN' if not (h1a >= 1 and h2a >= 1) else 'LOSE'

        # Team WINS both halves (mid 48/49) - a strictly rarer event than scoring
        # in both, and previously graded with the scoring rule above.
        if label == 'Home wins both halves': return 'WIN' if (h1h > h1a and h2h > h2a) else 'LOSE'
        if label == 'Away wins both halves': return 'WIN' if (h1a > h1h and h2a > h2h) else 'LOSE'
        if label == 'Home not win both halves': return 'WIN' if not (h1h > h1a and h2h > h2a) else 'LOSE'
        if label == 'Away not win both halves': return 'WIN' if not (h1a > h1h and h2a > h2h) else 'LOSE'
        # Team wins EITHER half (mid 50/51)
        if label == 'Home wins a half': return 'WIN' if (h1h > h1a or h2h > h2a) else 'LOSE'
        if label == 'Away wins a half': return 'WIN' if (h1a > h1h or h2a > h2h) else 'LOSE'
        if label == 'Home wins no half': return 'WIN' if not (h1h > h1a or h2h > h2a) else 'LOSE'
        if label == 'Away wins no half': return 'WIN' if not (h1a > h1h or h2a > h2h) else 'LOSE'
        # 1H Double Chance (mid 63)
        if label == '1H 1X': return 'WIN' if h1h >= h1a else 'LOSE'
        if label == '1H X2': return 'WIN' if h1a >= h1h else 'LOSE'
        if label == '1H 12': return 'WIN' if h1h != h1a else 'LOSE'
        # 2H Double Chance (mid 85)
        if label == '2H 1X': return 'WIN' if h2h >= h2a else 'LOSE'
        if label == '2H X2': return 'WIN' if h2h <= h2a else 'LOSE'
        if label == '2H 12': return 'WIN' if h2h != h2a else 'LOSE'
        # per-half odd/even and exact goals
        if label in ('1H Odd', '1H Even'):
            t = h1h + h1a
            return 'WIN' if (t % 2 == 1) == (label == '1H Odd') else 'LOSE'
        if label in ('2H Odd', '2H Even'):
            t = h2h + h2a
            return 'WIN' if (t % 2 == 1) == (label == '2H Odd') else 'LOSE'
        for pre, t in (('1H Exact Goals: ', h1h + h1a), ('2H Exact Goals: ', h2h + h2a)):
            if label.startswith(pre):
                d = label[len(pre):]
                if d.endswith('+'):
                    try: return 'WIN' if t >= int(d[:-1]) else 'LOSE'
                    except ValueError: return '?'
                try: return 'WIN' if t == int(d) else 'LOSE'
                except ValueError: return '?'
        # per-half multigoals bands: '1-2', '2-3', '4+', 'No goal'
        for pre, t in (('1H Multigoals ', h1h + h1a), ('2H Multigoals ', h2h + h2a)):
            if label.startswith(pre):
                return _band(label[len(pre):], t)

        # BTTS both halves (mid 32/55)
        if label == 'BTTS both halves':
            return 'WIN' if (h1h >= 1 and h1a >= 1 and h2h >= 1 and h2a >= 1) else 'LOSE'
        if label == 'NG one half':
            return 'WIN' if not (h1h >= 1 and h1a >= 1 and h2h >= 1 and h2a >= 1) else 'LOSE'

        # Match Result after X minutes (mid 900069) - only at minute X, the score must be X:0 or 0:X or 0:0
        if label.startswith('Draw ') and 'm' in label and 'Min' not in label and 'm  ' not in label:
            m = re.search(r'Draw (\d+)m', label)
            if m:
                return 'WIN' if h1h == 0 and h1a == 0 and (h1h + h1a) >= 0 else 'LOSE'  # can't easily check minute
        if label.startswith('Home ') and 'm' in label and 'Min' not in label:
            m = re.search(r'Home (\d+)m', label)
            if m: return '?'     # minute-specific, SportyBet flag authoritative
        if label.startswith('Away ') and 'm' in label and 'Min' not in label:
            m = re.search(r'Away (\d+)m', label)
            if m: return '?'

    simple = {'1X': h >= a, 'X2': h <= a, '12': h != a, '1 Home': h > a, '2 Away': h < a,
              'GG': (h >= 1 and a >= 1), 'NG': (h == 0 or a == 0)}
    if label in simple: return 'WIN' if simple[label] else 'LOSE'

    # FT Odd/Even (mid 26/27/28)
    if label == 'Odd': return 'WIN' if tot % 2 == 1 else 'LOSE'
    if label == 'Even': return 'WIN' if tot % 2 == 0 else 'LOSE'

    # Result + BTTS combos (mid 35/78/540)
    if label.startswith('Res+BTTS: '):
        combo = label[10:]      # e.g. "Home & Yes"
        side, _, btts = combo.partition(' & ')
        won = (h > a) if side == 'Home' else (a > h) if side == 'Away' else (h == a)
        bt = (h >= 1 and a >= 1) if btts == 'Yes' else (h == 0 or a == 0)
        return 'WIN' if won and bt else 'LOSE'

    # Result + Total combos (mid 37/544) - "Home & Over 2.5", etc.
    if label.startswith('Res+Total: '):
        combo = label[11:]      # e.g. "Home & Over 2.5"
        side, _, ou = combo.partition(' & ')
        m = re.match(r'(Over|Under)\s+([\d.]+)', ou)
        if m:
            ou_type, ln = m.group(1), float(m.group(2))
            won = (h > a) if side == 'Home' else (a > h) if side == 'Away' else (h == a)
            over_under_hit = (tot > ln) if ou_type == 'Over' else (tot < ln)
            return 'WIN' if won and over_under_hit else 'LOSE'

    # DC + BTTS (mid 546)
    if label.startswith('DC+BTTS: '):
        combo = label[9:]       # e.g. "Home/Draw & Yes"
        dc_part, _, btts = combo.partition(' & ')
        dc_hit = (h >= a) if dc_part == 'Home/Draw' else (a >= h) if dc_part == 'Draw/Away' else (h != a)
        bt = (h >= 1 and a >= 1) if btts == 'Yes' else (h == 0 or a == 0)
        return 'WIN' if dc_hit and bt else 'LOSE'

    # DC + Total (mid 547)
    if label.startswith('DC+Total: '):
        combo = label[10:]      # e.g. "Home/Draw & Over 1.5"
        dc_part, _, ou = combo.partition(' & ')
        m = re.match(r'(Over|Under)\s+([\d.]+)', ou)
        if m:
            dc_hit = (h >= a) if dc_part == 'Home/Draw' else (a >= h) if dc_part == 'Draw/Away' else (h != a)
            ou_type, ln = m.group(1), float(m.group(2))
            over_under_hit = (tot > ln) if ou_type == 'Over' else (tot < ln)
            return 'WIN' if dc_hit and over_under_hit else 'LOSE'

    # Winning Margin (mid 15) - "Home by 1", "Home by 2", "Home by 3+", etc.
    if label.startswith('WM: '):
        desc = label[4:]
        diff = h - a
        if desc.startswith('Home by '):
            num = desc[8:].rstrip('+')
            target = int(num) if num.isdigit() else None
            if desc.endswith('3+'):
                return 'WIN' if diff >= 3 else 'LOSE'
            return 'WIN' if diff == target else 'LOSE'
        if desc.startswith('Away by '):
            num = desc[8:].rstrip('+')
            target = int(num) if num.isdigit() else None
            if desc.endswith('3+'):
                return 'WIN' if (-diff) >= 3 else 'LOSE'
            return 'WIN' if -diff == target else 'LOSE'

    # Exact Goals alt (mid 71) - "0", "1", "2", "3+", "4+"
    if label.startswith('Exact Goals (alt):'):
        tgt = label.split(':')[-1].strip()
        if tgt.endswith('+'):
            n = int(tgt[:-1])
            return 'WIN' if tot >= n else 'LOSE'
        try:
            n = int(tgt)
            return 'WIN' if tot == n else 'LOSE'
        except ValueError:
            pass

    if label.startswith('Exact Goals'):
        m = re.match(r'Exact Goals:\s*(\d+)', label)
        if m:
            tgt = int(m.group(1))
            return 'WIN' if tot == tgt else 'LOSE'
        if '4+' in label: return 'WIN' if tot >= 4 else 'LOSE'

    return '?'

def grade_code(code):
    r = json.loads(urllib.request.urlopen(urllib.request.Request(
        'https://www.sportybet.com/api/ng/orders/share/' + code, headers=HDRS), timeout=20).read().decode())
    outs = (r.get('data') or {}).get('outcomes', [])
    w = l = p = live = pend = void = 0
    rows = []
    for o in outs:
        m = o['markets'][0]; oc = m['outcomes'][0]
        label = label_of(m.get('id'), m.get('specifier', ''), oc.get('desc', ''))
        shown = api_label(m, oc)          # always meaningful, never '?'
        ms = o.get('matchStatus', ''); ss = o.get('setScore')
        name = f"{o.get('homeTeamName','')[:18]:18} v {o.get('awayTeamName','')[:16]:16}"
        if o.get('status') == 5 or ms == 'Cancelled':
            void += 1; rows.append(('void', f"  void {'-':7} {name} {shown}")); continue
        gs = o.get('gameScore') or []                       # periods: ['1H','2H'] (+ 'ET','PEN' if played)
        def per(i):
            if i < len(gs) and ':' in str(gs[i]):
                try: return tuple(map(int, str(gs[i]).split(':')))
                except ValueError: return None
            return None
        p1, p2 = per(0), per(1)
        h1h, h1a = p1 if p1 else (None, None)
        # ET/pens: SportyBet settles 90-min markets on regulation, but setScore includes ET+penalties.
        disp = ss or '-'; h = a = None
        if ss:
            h, a = map(int, ss.split(':'))
            # Strip extra time and penalties whenever the period list shows them,
            # not only when matchStatus says AET/AP. CS Lotus v Crisul reported
            # plain "Ended" with gameScore ['0:0','0:0','0:0','3:5'] - 0-0 in
            # regulation, won 5-3 on penalties - so setScore was the SHOOTOUT.
            # Grading a 90-minute market off that is simply the wrong match.
            if (ms in ('AET', 'AP') or len(gs) > 2) and p1 and p2:
                h, a = p1[0] + p2[0], p1[1] + p2[1]; disp = f"{h}:{a}r"   # 'r' = 90-min regulation
        # 1) AUTHORITATIVE: SportyBet's own settlement (isWinning/refundFactor, market status 3).
        #    Works for EVERY market incl exotic ones (No 3-in-a-row) that cannot be recomputed from score.
        if 'isWinning' in oc or m.get('status') in (3, '3'):
            rf = oc.get('refundFactor') or 0; iw = oc.get('isWinning')
            res = 'PUSH' if (rf and rf >= 1) else ('WIN' if iw == 1 else ('LOSE' if iw == 0 else '?'))
            w += res == 'WIN'; l += res == 'LOSE'; p += res == 'PUSH'
            tag = {'WIN': 'WIN ', 'LOSE': 'LOSE', 'PUSH': 'void', '?': ' ?  '}[res]
            rows.append((res, f"  {tag} {disp:7} {name} {shown}")); continue
        # 2) not started -> pending
        if not ss:
            pend += 1; rows.append(('pend', f"  .... {'-':7} {name} {shown}")); continue
        # 3) ended but not yet settled (lag) -> score-based for markets we can compute
        if ms in ('Ended', 'AET', 'AP'):
            res = grade(label, h, a, h1h, h1a)
            if res == '?':
                res = grade_by_predicate(m, oc, h, a, h1h, h1a,
                                         (o.get('homeTeamName'), o.get('awayTeamName')))
            w += res == 'WIN'; l += res == 'LOSE'; p += res == 'PUSH'
            tag = {'WIN': 'WIN ', 'LOSE': 'LOSE', 'PUSH': 'void', '?': ' ?  '}[res]
            rows.append((res, f"  {tag} {disp:7} {name} {shown}"))
        # 4) in play -> live covering preview
        else:
            tot = h + a
            failed_live = False
            # A per-half Under must be judged on THAT HALF's goals only. This used to
            # compare the full-match running total against any Under line, so a 2H
            # Under 1.5 was called breached at 2:1 when the second half held one goal
            # (Slavia Sofia v Lokomotiv, 31 Jul).
            m_u = re.search(r'^(1H |2H )?Under([\d.]+)', label)
            if m_u:
                pre, ln = m_u.group(1), float(m_u.group(2))
                if pre == '1H ':
                    cur = (h1h + h1a) if h1h is not None else (tot if ms == '1H' else None)
                elif pre == '2H ':
                    cur = ((h - h1h) + (a - h1a)) if h1h is not None else None
                else:
                    cur = tot
                if cur is not None and cur > ln: failed_live = True
            if label == '1H Home CS (Away 0)' and ((h1a is not None and h1a > 0) or (a > 0 and ms in ('1H', 'HT', '2H'))): failed_live = True
            if label == '1H Away CS (Home 0)' and ((h1h is not None and h1h > 0) or (h > 0 and ms in ('1H', 'HT', '2H'))): failed_live = True

            if failed_live:
                l += 1
                rows.append(('LOSE', f"  LOSE {ss:7} {name} {shown}  (breached in live)"))
            else:
                live += 1
                rows.append(('live', f"  live {ss:7} {name} {shown}  ({ms})"))
    print(f"==== {code}: WON {w} | LOST {l} | VOID {p+void} | live {live} | pending {pend}  (of {len(outs)})")
    if w + l: print(f"     per-leg settled: {w}/{w+l} = {w/(w+l)*100:.0f}%")
    for res, line in rows:
        if res in ('LOSE', 'live') or res == 'WIN':
            print(line)
    print()

if __name__ == '__main__':
    for code in sys.argv[1:] or ['HFDETF', 'Y5RUNJ']:
        grade_code(code)
