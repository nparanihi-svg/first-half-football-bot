"""V5.7 cache-only fixture readiness assessment; no live prediction or network changes."""
import argparse
import json
import os
import sqlite3
from datetime import datetime, timezone

from compare_v55 import summarise_team
import history_v55 as history

STATE_DB = os.getenv('SIGNAL_DB_PATH', '/var/data/first_half_v3.sqlite3')


def classify(home, away, minimum=8):
    """A corner data gate, NOT a measure of predictive accuracy."""
    hc = home.get('corner_samples', 0)
    ac = away.get('corner_samples', 0)
    if hc >= minimum and ac >= minimum:
        return 'DATA_READY_FOR_VALIDATION'
    if hc > 0 or ac > 0:
        return 'PARTIAL_CORNER_HISTORY'
    return 'NO_VERIFIED_CORNER_HISTORY'


def assessment(home, away, minimum=8):
    status = classify(home, away, minimum)
    result = {'status': status, 'home_corner_samples': home.get('corner_samples', 0),
              'away_corner_samples': away.get('corner_samples', 0),
              'home_goal_samples': home.get('matches_with_scores', 0),
              'away_goal_samples': away.get('matches_with_scores', 0)}
    if status == 'DATA_READY_FOR_VALIDATION':
        # Context only: descriptive histories are not fitted Poisson parameters.
        result['home_corners_for_avg'] = home['corners_won_avg']
        result['home_corners_against_avg'] = home['corners_conceded_avg']
        result['away_corners_for_avg'] = away['corners_won_avg']
        result['away_corners_against_avg'] = away['corners_conceded_avg']
    return result


def report(minimum=8, limit=30):
    if not os.path.isfile(STATE_DB):
        print('No V5.6 fixture database available yet. Start discovery first.')
        return []
    with sqlite3.connect(STATE_DB, timeout=10) as db:
        table = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='prematch_v56_fixtures'").fetchone()
        if not table:
            print('No upcoming fixture records yet. Enable V5.6 discovery first.')
            return []
        fixtures = db.execute('''SELECT fixture_id,home_id,away_id,league_name,country,kickoff_at
              FROM prematch_v56_fixtures WHERE kickoff_at > ? ORDER BY kickoff_at LIMIT ?''',
              (datetime.now(timezone.utc).isoformat(), limit)).fetchall()
    if not fixtures:
        print('No upcoming fixtures cached. No network calls made.')
        return []
    results = []
    with history.db_open() as cache:
        memo = {}
        for fid, hid, aid, league, country, kickoff in fixtures:
            for tid in (hid, aid):
                if tid not in memo:
                    memo[tid] = summarise_team(cache, tid, 20, 20, None)
            record = {'fixture_id': fid, 'league': league, 'country': country,
                      'kickoff': kickoff, 'home_id': hid, 'away_id': aid}
            record.update(assessment(memo[hid], memo[aid], minimum))
            results.append(record)
    totals = {label: sum(r['status'] == label for r in results) for label in
              ('DATA_READY_FOR_VALIDATION', 'PARTIAL_CORNER_HISTORY', 'NO_VERIFIED_CORNER_HISTORY')}
    print('V5.7 OFFLINE CORNER HISTORY READINESS — NOT A BETTING MODEL')
    print(f'Upcoming fixtures checked: {len(results)}; minimum confirmed historical matches per team: {minimum}')
    print('Readiness:', json.dumps(totals))
    for item in results:
        print(f"{item['fixture_id']} | {item['country']} / {item['league']} | "
              f"teams {item['home_id']}-{item['away_id']} | "
              f"verified corners {item['home_corner_samples']}/{item['away_corner_samples']} | "
              f"{item['status']}")
    print('No API requests, Telegram messages, model probability changes or bets.')
    return results


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Offline first-half historical-data readiness check')
    parser.add_argument('--min-corner-samples', type=int, default=8)
    parser.add_argument('--limit', type=int, default=30)
    args = parser.parse_args()
    if not 1 <= args.min_corner_samples <= 20 or not 1 <= args.limit <= 500:
        parser.error('min-corner-samples must be 1..20 and limit must be 1..500')
    report(args.min_corner_samples, args.limit)
