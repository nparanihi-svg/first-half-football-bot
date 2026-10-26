"""Standalone, read-only-to-live-bot historical first-half team research tool.

Usage (Render Shell): python history_v55.py --team-id 33 --corners 5
Requires API_FOOTBALL_KEY. No scanner, alerts or engine changes.
"""
import argparse
import json
import os
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
import requests

BASE = 'https://v3.football.api-sports.io'
CACHE = Path(os.getenv('HISTORY_V55_DB', '/var/data/first_half_history_v55.sqlite3'))
TERMINAL = {'FT', 'AET', 'PEN'}


def api_get(session, endpoint, **params):
    response = session.get(BASE + endpoint, params=params, timeout=25)
    response.raise_for_status()
    payload = response.json()
    if payload.get('errors'):
        raise RuntimeError(f"API-Football errors: {payload['errors']}")
    return payload.get('response', [])


def db_open():
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(CACHE), timeout=15)
    db.execute('CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, payload TEXT NOT NULL, fetched_at TEXT NOT NULL)')
    return db


def cached(db, key, ttl_hours, fetch):
    item = db.execute('SELECT payload,fetched_at FROM cache WHERE key=?', (key,)).fetchone()
    if item:
        date = datetime.fromisoformat(item[1])
        if datetime.now(timezone.utc) - date < timedelta(hours=ttl_hours):
            return json.loads(item[0]), True
    result = fetch()
    db.execute('INSERT OR REPLACE INTO cache VALUES (?,?,?)', (key, json.dumps(result), datetime.now(timezone.utc).isoformat()))
    db.commit()
    return result, False


def half_goals(fixtures, team_id, limit):
    complete = []
    for fixture in fixtures:
        if fixture.get('fixture', {}).get('status', {}).get('short') not in TERMINAL:
            continue
        half = (fixture.get('score') or {}).get('halftime') or {}
        home, away = half.get('home'), half.get('away')
        if not (type(home) is int and type(away) is int):
            continue
        team_home = fixture.get('teams', {}).get('home', {}).get('id') == team_id
        team_away = fixture.get('teams', {}).get('away', {}).get('id') == team_id
        if not (team_home or team_away):
            continue
        complete.append({
            'id': fixture['fixture']['id'],
            'date': fixture.get('fixture', {}).get('date', ''),
            'venue': 'home' if team_home else 'away',
            'goals_for': home if team_home else away,
            'goals_against': away if team_home else home,
            'goals_total': home + away,
        })
        if len(complete) >= limit:
            break
    return complete


def numeric_corner(value):
    if type(value) in (int, float) and value >= 0:
        return int(value)
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def extract_first_half_corners(response, home_id, away_id):
    """Only accept explicitly first-half tagged data, never implicit full-time totals.

    API layouts may differ. Unknown response shapes are marked unavailable.
    """
    corners = {}
    for item in response if isinstance(response, list) else []:
        team_id = (item.get('team') or {}).get('id')
        if team_id not in (home_id, away_id):
            continue
        # API-Football half=true returns explicit statistics_1h alongside
        # full-time statistics. Never substitute full-time corner totals.
        first_half = item.get('statistics_1h')
        stats = {'first_half': first_half} if isinstance(first_half, list) else item.get('statistics')
        # Also support nested first-half data or explicitly labelled team blocks.
        if isinstance(stats, dict):
            stats = next((v for k, v in stats.items() if str(k).lower().replace(' ', '_') in ('first_half', 'firsthalf', '1st_half', '1h', 'half_1')), None)
        else:
            label = str(item.get('half', item.get('period', ''))).lower().replace(' ', '_')
            if label not in ('first_half', 'firsthalf', '1st_half', '1h', '1', 'half_1'):
                stats = None
        if isinstance(stats, list):
            for stat in stats:
                if stat.get('type') == 'Corner Kicks':
                    value = numeric_corner(stat.get('value'))
                    if value is not None:
                        corners[team_id] = value
    if home_id in corners and away_id in corners:
        return corners[home_id], corners[away_id]
    return None


def summarise(rows, field):
    if not rows:
        return 'unavailable'
    return f'{sum(x[field] for x in rows)/len(rows):.2f}'


def run(team_id, limit, corner_sample, refresh=False):
    secret = os.getenv('API_FOOTBALL_KEY')
    if not secret:
        raise SystemExit('API_FOOTBALL_KEY not configured')
    session = requests.Session()
    session.headers.update({'x-apisports-key': secret})
    with db_open() as db:
        fixtures, hit = cached(db, f'team:{team_id}:last40', 0 if refresh else 12,
                               lambda: api_get(session, '/fixtures', team=team_id, last=40))
        rows = half_goals(fixtures, team_id, limit)
        print(f'Last {limit} completed matches - team ID {team_id}')
        print(f'Half-time scores available: {len(rows)}/{limit} | fixtures cache hit: {hit}')
        if rows:
            print(f'1H match over 0.5 goals: {sum(r["goals_total"] >= 1 for r in rows)}/{len(rows)}')
            print(f'1H team goals scored avg: {summarise(rows,"goals_for")} | conceded avg: {summarise(rows,"goals_against")}')
            print(f'1H match total goals avg: {summarise(rows,"goals_total")}')
            for venue in ('home', 'away'):
                subset = [r for r in rows if r['venue'] == venue]
                print(f'  {venue} sample: {len(subset)} | avg scored: {summarise(subset,"goals_for")}')
        if corner_sample:
            corner_rows = []
            checked = rows[:min(corner_sample, len(rows))]
            for row in checked:
                fixture = next(f for f in fixtures if f['fixture']['id'] == row['id'])
                home_id = fixture['teams']['home']['id']
                away_id = fixture['teams']['away']['id']
                response, _ = cached(db, f'first_half_stats:{row["id"]}', 0 if refresh else 168,
                                     lambda fid=row['id']: api_get(session, '/fixtures/statistics', fixture=fid, half='true'))
                pair = extract_first_half_corners(response, home_id, away_id)
                if pair is None:
                    continue
                team_home = home_id == team_id
                won, conceded = pair if team_home else (pair[1], pair[0])
                corner_rows.append({'for': won, 'against': conceded, 'total': won + conceded})
            print(f'FIRST-HALF CORNERS: confirmed {len(corner_rows)} of {len(checked)} requested samples')
            if corner_rows:
                print(f'  avg corners won {summarise(corner_rows,"for")}, conceded {summarise(corner_rows,"against")}, combined {summarise(corner_rows,"total")}')
                for threshold in (2, 3, 4):
                    print(f'  first-half {threshold}+ total corners: {sum(r["total"] >= threshold for r in corner_rows)}/{len(corner_rows)}')
            else:
                print('  Corner history unavailable or response format unrecognised; no zero values imputed.')
    print('Research only. Existing scanner and Telegram alerts unchanged.')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--team-id', type=int, required=True)
    p.add_argument('--last', type=int, default=20, choices=range(1, 21))
    p.add_argument('--corners', type=int, default=0, help='Number of historical fixture stat calls (0-20).')
    p.add_argument('--refresh', action='store_true')
    args = p.parse_args()
    if not 0 <= args.corners <= 20:
        p.error('--corners must be 0..20')
    run(args.team_id, args.last, args.corners, args.refresh)
