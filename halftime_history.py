"""First-half team rates from cached, completed pre-kickoff fixtures."""
import json
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

PRIOR = .575
PRIOR_WEIGHT = 5


def _date(value):
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return stamp if stamp.tzinfo else None
    except (ValueError, AttributeError):
        return None


def team_rates(fixtures, team_id, venue, kickoff, current_id):
    matches = []
    for item in fixtures:
        meta = item.get('fixture') or {}
        date = _date(meta.get('date'))
        if (meta.get('id') == current_id or date is None
                or date + timedelta(hours=3) >= kickoff
                or (meta.get('status') or {}).get('short') not in ('FT', 'AET', 'PEN')):
            continue
        teams = item.get('teams') or {}
        home = (teams.get('home') or {}).get('id') == team_id
        away = (teams.get('away') or {}).get('id') == team_id
        half = (item.get('score') or {}).get('halftime') or {}
        h, a = half.get('home'), half.get('away')
        if home == away or not all(type(v) is int and v >= 0 for v in (h, a)):
            continue
        matches.append((date, meta.get('id'), h if home else a,
                        a if home else h, 'home' if home else 'away'))
    matches.sort(key=lambda row: row[0], reverse=True)
    unique = {}
    for row in matches:
        unique.setdefault(row[1], row)
    matches = list(unique.values())[:20]
    if len(matches) < 5:
        return None
    # Five prior matches temper small samples. Venue data is shrunk toward
    # the overall team rate so a short home/away history cannot dominate.
    overall = [(sum(r[i] for r in matches) + PRIOR_WEIGHT * PRIOR)
               / (len(matches) + PRIOR_WEIGHT) for i in (2, 3)]
    venue_rows = [r for r in matches if r[4] == venue]
    rates = [(sum(r[i] for r in venue_rows) + PRIOR_WEIGHT * overall[j])
             / (len(venue_rows) + PRIOR_WEIGHT) for j, i in enumerate((2, 3))]
    return {'scored': rates[0], 'conceded': rates[1], 'matches': len(matches),
            'venue_matches': len(venue_rows), 'fixture_ids': [r[1] for r in matches]}


def cached_rates(fixture):
    kickoff = _date((fixture.get('fixture') or {}).get('date'))
    if kickoff is None:
        return None
    path = Path(os.getenv('HISTORY_V55_DB', '/var/data/first_half_history_v55.sqlite3'))
    if not path.is_file():
        return None
    teams = fixture.get('teams') or {}
    rates = []
    try:
        with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=2) as db:
            for side in ('home', 'away'):
                team_id = (teams.get(side) or {}).get('id')
                row = db.execute('SELECT payload FROM cache WHERE key=?',
                                 (f'team:{team_id}:last40',)).fetchone()
                if row is None:
                    return None
                rate = team_rates(json.loads(row[0]), team_id, side, kickoff,
                                  fixture['fixture']['id'])
                if rate is None:
                    return None
                rates.append(rate)
    except (sqlite3.Error, ValueError, TypeError):
        return None
    home, away = rates
    home_mean = (home['scored'] + away['conceded']) / 2
    away_mean = (away['scored'] + home['conceded']) / 2
    return home_mean, away_mean, {'version': 1, 'home': home, 'away': away}
