"""V5.5 side-by-side first-half historical research; no scanner/alert changes.

Examples:
  python compare_v55.py --home-id 40 --away-id 42
  python compare_v55.py --fixture-id 1234567
  python compare_v55.py --home-id 40 --away-id 42 --fetch --budget 10

Without --fetch, reads historical cache only and makes no historical API requests.
With --fixture-id, uses up to one API lookup if fixture not cached.
The budget limits *new* API requests made by this command; never refreshes existing caches.
"""
import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import requests
import history_v55 as h


def summarise_team(db, team_id, sample, corner_limit, get_history):
    key = f'team:{team_id}:last40'
    record = db.execute('SELECT payload FROM cache WHERE key=?', (key,)).fetchone()
    if record:
        fixtures = json.loads(record[0])
    elif get_history:
        fixtures = get_history('/fixtures', team=team_id, last=40)
        db.execute('INSERT OR REPLACE INTO cache VALUES (?,?,?)', (key, json.dumps(fixtures), datetime.now(timezone.utc).isoformat()))
        db.commit()
    else:
        return {'team_id': team_id, 'status': 'history_not_cached'}
    rows = h.half_goals(fixtures, team_id, sample)
    result = {'team_id': team_id, 'matches_with_scores': len(rows), 'requested': sample}
    if rows:
        result['first_half_goal_rate'] = sum(r['goals_total'] >= 1 for r in rows) / len(rows)
        result['goals_scored_avg'] = sum(r['goals_for'] for r in rows) / len(rows)
        result['goals_conceded_avg'] = sum(r['goals_against'] for r in rows) / len(rows)
        result['goals_total_avg'] = sum(r['goals_total'] for r in rows) / len(rows)
    corner_rows = []
    for row in rows[:corner_limit]:
        key = f'first_half_stats:{row["id"]}'
        record = db.execute('SELECT payload FROM cache WHERE key=?', (key,)).fetchone()
        if record:
            data = json.loads(record[0])
        elif get_history:
            data = get_history('/fixtures/statistics', fixture=row['id'], half='true')
            db.execute('INSERT OR REPLACE INTO cache VALUES (?,?,?)', (key, json.dumps(data), datetime.now(timezone.utc).isoformat()))
            db.commit()
        else:
            continue
        fixture = next((f for f in fixtures if f['fixture']['id'] == row['id']), None)
        if not fixture:
            continue
        home_id, away_id = fixture['teams']['home']['id'], fixture['teams']['away']['id']
        pair = h.extract_first_half_corners(data, home_id, away_id)
        if pair is None:
            continue
        own, other = pair if home_id == team_id else (pair[1], pair[0])
        corner_rows.append((own, other))
    result['corner_samples'] = len(corner_rows)
    result['corners_requested'] = min(corner_limit, len(rows))
    if corner_rows:
        result['corners_won_avg'] = sum(r[0] for r in corner_rows) / len(corner_rows)
        result['corners_conceded_avg'] = sum(r[1] for r in corner_rows) / len(corner_rows)
        result['corners_total_avg'] = sum(sum(r) for r in corner_rows) / len(corner_rows)
        result['over_2_5_corners_rate'] = sum(sum(r) >= 3 for r in corner_rows) / len(corner_rows)
    return result


def show(name, s):
    print(f'\n{name} (team ID {s["team_id"]}):')
    if 'status' in s:
        print('  Missing historical fixture cache; use --fetch with a budget to collect data.')
        return
    print(f'  1H score coverage: {s["matches_with_scores"]}/{s["requested"]}')
    if 'first_half_goal_rate' in s:
        print(f'  1H over 0.5 goals: {s["first_half_goal_rate"]:.1%} | scored: {s["goals_scored_avg"]:.2f} | conceded: {s["goals_conceded_avg"]:.2f}')
    print(f'  1H corners coverage: {s["corner_samples"]}/{s["corners_requested"]} (cached/available)')
    if 'over_2_5_corners_rate' in s:
        print(f'  1H over 2.5 corners: {s["over_2_5_corners_rate"]:.1%} | won: {s["corners_won_avg"]:.2f} | conceded: {s["corners_conceded_avg"]:.2f}')


def main():
    p = argparse.ArgumentParser(description='Two-team historical comparison; research only')
    p.add_argument('--fixture-id', type=int)
    p.add_argument('--home-id', type=int)
    p.add_argument('--away-id', type=int)
    p.add_argument('--last', type=int, default=20, choices=range(1, 21))
    p.add_argument('--corners', type=int, default=20, choices=range(0, 21))
    p.add_argument('--fetch', action='store_true', help='Allow new historical API requests within --budget')
    p.add_argument('--budget', type=int, default=0, help='Max fresh API requests in this invocation (0 default)')
    a = p.parse_args()
    if (a.home_id is None) != (a.away_id is None):
        p.error('Provide both --home-id and --away-id')
    if a.fixture_id is None and a.home_id is None:
        p.error('Provide --fixture-id or both team IDs')
    if a.budget < 0 or a.budget > 42:
        p.error('--budget must be between 0 and 42')
    if a.fetch and a.budget == 0:
        p.error('--fetch requires an explicit --budget greater than zero')
    used = 0
    def get_history(endpoint, **params):
        nonlocal used
        if used >= a.budget:
            raise RuntimeError(f'API request budget reached ({used}/{a.budget}); cached data remains available')
        token = os.environ.get('API_FOOTBALL_KEY')
        if not token:
            raise RuntimeError('API_FOOTBALL_KEY missing')
        session = requests.Session()
        session.headers.update({'x-apisports-key': token})
        data = h.api_get(session, endpoint, **params)
        used += 1
        return data
    with h.db_open() as db:
        home_id, away_id = a.home_id, a.away_id
        if a.fixture_id is not None:
            key = f'comparison_fixture:{a.fixture_id}'
            row = db.execute('SELECT payload FROM cache WHERE key=?', (key,)).fetchone()
            if row:
                matches = json.loads(row[0])
            elif a.fetch:
                matches = get_history('/fixtures', id=a.fixture_id)
                db.execute('INSERT OR REPLACE INTO cache VALUES (?,?,?)', (key, json.dumps(matches), datetime.now(timezone.utc).isoformat()))
                db.commit()
            else:
                p.error('Fixture not cached. Use --fetch --budget N or specify the two team IDs.')
            if not matches:
                raise SystemExit('Fixture not found')
            home_id = matches[0]['teams']['home']['id']
            away_id = matches[0]['teams']['away']['id']
        # Historical fixtures are shared via existing V5.5 cache.
        get_optional = get_history if a.fetch else None
        print('V5.5 TWO-TEAM HISTORICAL COMPARISON (SHADOW RESEARCH)')
        print(f'Matchup: home ID {home_id} vs away ID {away_id}; prior matches per team: {a.last}')
        for name, team in [('HOME', home_id), ('AWAY', away_id)]:
            show(name, summarise_team(db, team, a.last, a.corners, get_optional))
    print(f'\nNew API requests used: {used}/{a.budget}; live scanner, Telegram and probabilities unchanged.')
    print('Historical frequencies are NOT calibrated predictions for this particular fixture.')

if __name__ == '__main__':
    main()
