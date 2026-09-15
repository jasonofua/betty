#!/usr/bin/env python3
"""15 Sep: put MARKET ODDS next to results. football-data.co.uk - the main
divisions season by season (E0 ... G1, 2015/16 on) and the 'extra leagues'
files (Argentina ... USA). Downloads land in experiments/odds_raw/ and are
normalised into experiments/odds_history.jsonl: one row per match with the
result, half-time score, match stats where the file has them, and the
Bet365 / Pinnacle / average / max 1X2 prices (opening and closing).

    python3 experiments/backfill_odds.py download   # fetch the CSVs (curl)
    python3 experiments/backfill_odds.py build      # -> odds_history.jsonl
"""
import csv, glob, io, json, os, subprocess, sys, datetime as dt
ROOT = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(ROOT, 'odds_raw')
MAIN = ['E0', 'E1', 'E2', 'E3', 'EC', 'SC0', 'SC1', 'SC2', 'SC3', 'D1', 'D2', 'I1', 'I2', 'SP1', 'SP2', 'F1', 'F2', 'N1', 'B1', 'P1', 'T1', 'G1']
SEASONS = ['1516', '1617', '1718', '1819', '1920', '2021', '2122', '2223', '2324', '2425', '2526']
EXTRA = ['ARG', 'AUT', 'BRA', 'CHN', 'DNK', 'FIN', 'IRL', 'JPN', 'MEX', 'NOR', 'POL', 'ROU', 'RUS', 'SWE', 'SWZ', 'USA']


def download():
    os.makedirs(RAW, exist_ok=True)
    jobs = [(f'https://www.football-data.co.uk/mmz4281/{s}/{d}.csv', f'{s}_{d}.csv') for s in SEASONS for d in MAIN]
    jobs += [(f'https://www.football-data.co.uk/new/{c}.csv', f'extra_{c}.csv') for c in EXTRA]
    for url, name in jobs:
        out = os.path.join(RAW, name)
        if os.path.exists(out) and os.path.getsize(out) > 500:
            continue
        subprocess.run(['curl', '-s', '-L', '--max-time', '60', '-A', 'Mozilla/5.0', '-o', out, url])
        ok = os.path.exists(out) and os.path.getsize(out) > 500
        print(('ok  ' if ok else 'MISS') + ' ' + name, flush=True)
        if not ok and os.path.exists(out):
            os.remove(out)


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def parse_date(d, t=None):
    for fmt in ('%d/%m/%Y', '%d/%m/%y'):
        try:
            day = dt.datetime.strptime(d.strip(), fmt)
            break
        except ValueError:
            day = None
    if not day:
        return None
    if t:
        try:
            hh, mm = t.strip().split(':'); day = day.replace(hour=int(hh), minute=int(mm))
        except ValueError:
            pass
    return day


def build():
    n = 0
    with open(os.path.join(ROOT, 'odds_history.jsonl'), 'w') as out:
        for path in sorted(glob.glob(os.path.join(RAW, '*.csv'))):
            name = os.path.basename(path)
            raw = open(path, 'rb').read().decode('utf-8', 'replace')
            rd = csv.DictReader(io.StringIO(raw))
            for r in rd:
                r = {k.strip(): (v.strip() if isinstance(v, str) else v) for k, v in r.items() if k}
                if name.startswith('extra_'):
                    lg = f"{r.get('Country')}: {r.get('League')}"; season = r.get('Season')
                    home, away = r.get('Home'), r.get('Away'); hg, ag = num(r.get('HG')), num(r.get('AG'))
                    hth = hta = None
                    odds = dict(psh=num(r.get('PH')), psd=num(r.get('PD')), psa=num(r.get('PA')),
                                avgh=num(r.get('AvgH')), avgd=num(r.get('AvgD')), avga=num(r.get('AvgA')),
                                maxh=num(r.get('MaxH')), maxd=num(r.get('MaxD')), maxa=num(r.get('MaxA')))
                    stats = {}
                else:
                    season, div = name[:4], name[5:-4]
                    lg = div; home, away = r.get('HomeTeam'), r.get('AwayTeam'); hg, ag = num(r.get('FTHG')), num(r.get('FTAG'))
                    hth, hta = num(r.get('HTHG')), num(r.get('HTAG'))
                    odds = dict(b365h=num(r.get('B365H')), b365d=num(r.get('B365D')), b365a=num(r.get('B365A')),
                                psh=num(r.get('PSH')), psd=num(r.get('PSD')), psa=num(r.get('PSA')),
                                psch=num(r.get('PSCH')), pscd=num(r.get('PSCD')), psca=num(r.get('PSCA')),
                                avgh=num(r.get('AvgH')), avgd=num(r.get('AvgD')), avga=num(r.get('AvgA')),
                                maxh=num(r.get('MaxH')), maxd=num(r.get('MaxD')), maxa=num(r.get('MaxA')))
                    stats = {k.lower(): num(r.get(k)) for k in ('HS', 'AS', 'HST', 'AST', 'HC', 'AC', 'HF', 'AF', 'HY', 'AY', 'HR', 'AR') if r.get(k) not in (None, '')}
                    for k in ('B365>2.5', 'B365<2.5', 'Avg>2.5', 'Avg<2.5', 'Max>2.5', 'Max<2.5', 'BbAv>2.5', 'BbAv<2.5'):
                        v = num(r.get(k))
                        if v: odds[k.lower().replace('bbav', 'avg')] = v
                    if r.get('Referee'): odds['referee'] = r['Referee']
                if not home or hg is None or ag is None:
                    continue
                day = parse_date(r.get('Date', ''), r.get('Time'))
                if not day:
                    continue
                row = dict(src=name, lg=lg, season=season, ts=int(day.timestamp()), date=day.strftime('%Y-%m-%d'),
                           home=home, away=away, hg=int(hg), ag=int(ag),
                           hth=int(hth) if hth is not None else None, hta=int(hta) if hta is not None else None,
                           st=stats, **{k: v for k, v in odds.items() if v is not None})
                out.write(json.dumps(row) + '\n'); n += 1
    print(f"{n} matches -> odds_history.jsonl")


if __name__ == '__main__':
    {'download': download, 'build': build}[sys.argv[1]]()
