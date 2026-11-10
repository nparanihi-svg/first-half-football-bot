"""Research-only halftime result and score forecasts, settled from halftime scores."""
import json
import os
import sqlite3
from contextlib import closing
from math import exp
from pathlib import Path

from provider import _number

DB_PATH = os.getenv('SIGNAL_DB_PATH', '/var/data/first_half_v3.sqlite3')
CHECKPOINTS = (15, 25, 35)
COMPLETED = {'HT', '2H', 'ET', 'P', 'FT', 'AET', 'PEN'}
OUTCOMES = ('Home', 'Draw', 'Away')


def outcome(home, away):
    return 'Home' if home > away else 'Away' if home < away else 'Draw'


def score_pair(values):
    home, away = values.get('home'), values.get('away')
    return (home, away) if all(type(v) is int and v >= 0 for v in (home, away)) else None


def poisson(mean):
    masses = [exp(-mean)]
    while sum(masses) < 1 - 1e-12:
        masses.append(masses[-1] * mean / len(masses))
    return masses


def forecast(score, remaining_mean, home_share):
    outcomes = dict.fromkeys(OUTCOMES, 0.0)
    scores = []
    for h, hp in enumerate(poisson(remaining_mean * home_share), start=score[0]):
        for a, ap in enumerate(poisson(remaining_mean * (1 - home_share)), start=score[1]):
            probability = hp * ap
            outcomes[outcome(h, a)] += probability
            scores.append((h, a, probability))
    return {'result': outcomes, 'scores': sorted(scores, key=lambda row: -row[2])[:3]}


def target_share(fixture, stats):
    teams = fixture.get('teams') or {}
    target = {}
    for entry in stats or []:
        team_id = (entry.get('team') or {}).get('id')
        for stat in entry.get('statistics') or []:
            if stat.get('type') == 'Shots on Goal':
                value = _number(stat.get('value'))
                if value is not None and 0 <= value < float('inf'):
                    target[team_id] = value
    home, away = [(teams.get(side) or {}).get('id') for side in ('home', 'away')]
    if home is None or away is None or home not in target or away not in target:
        return None
    # Equal prior of two shots on target per side, with a bounded live tilt.
    share = (target[home] + 2) / (target[home] + target[away] + 4)
    return min(.70, max(.30, share))


def record_prediction(fixture, snapshot, stats):
    checkpoint = next((m for m in CHECKPOINTS if m <= snapshot.elapsed < m + 5), None)
    score = score_pair(fixture.get('goals') or {})
    if checkpoint is None or score is None:
        return
    share = target_share(fixture, stats)
    mean = 1.15 * (45 - snapshot.elapsed) / 45
    payload = {'baseline': forecast(score, mean, .5),
               'team_stats': forecast(score, mean, share) if share is not None else None}
    with closing(sqlite3.connect(DB_PATH, timeout=2)) as db, db:
        db.execute('''CREATE TABLE IF NOT EXISTS halftime_shadow (
            fixture_id INTEGER, checkpoint INTEGER, minute INTEGER, league TEXT,
            payload TEXT, home_ht INTEGER, away_ht INTEGER,
            PRIMARY KEY(fixture_id, checkpoint))''')
        db.execute('''INSERT OR IGNORE INTO halftime_shadow VALUES (?, ?, ?, ?, ?, NULL, NULL)''',
                   (snapshot.fixture_id, checkpoint, snapshot.elapsed, snapshot.league, json.dumps(payload)))


def settle_predictions(fixtures):
    completed = []
    for fixture in fixtures:
        meta = fixture.get('fixture') or {}
        if (meta.get('status') or {}).get('short') in COMPLETED:
            score = score_pair((fixture.get('score') or {}).get('halftime') or {})
            if score is not None:
                completed.append((*score, meta['id']))
    if not completed:
        return
    with closing(sqlite3.connect(DB_PATH, timeout=2)) as db, db:
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='halftime_shadow'").fetchone():
            db.executemany('''UPDATE halftime_shadow SET home_ht=?, away_ht=?
                              WHERE fixture_id=? AND home_ht IS NULL''', completed)


def report(db):
    rows = db.execute('SELECT checkpoint,payload,home_ht,away_ht FROM halftime_shadow').fetchall()
    print(f'Saved forecasts: {len(rows)} | Settled: {sum(h is not None for _, _, h, _ in rows)}')
    for checkpoint in CHECKPOINTS:
        paired = [(json.loads(payload), h, a) for m, payload, h, a in rows if m == checkpoint and h is not None]
        paired = [(p, h, a) for p, h, a in paired if p['team_stats'] is not None]
        print(f'Checkpoint {checkpoint}′ | Paired settled forecasts: {len(paired)}')
        if not paired:
            continue
        for model in ('baseline', 'team_stats'):
            result_hits = score_hits = top_three = brier = 0
            for payload, home, away in paired:
                scores = payload[model]['scores']
                probs = payload[model]['result']
                actual = outcome(home, away)
                result_hits += max(probs, key=probs.get) == actual
                score_hits += scores[0][:2] == [home, away]
                top_three += any(row[:2] == [home, away] for row in scores)
                brier += sum((p - (side == actual)) ** 2 for side, p in probs.items())
            print(f'  {model}: result {result_hits}/{len(paired)} | score top 1 {score_hits}/{len(paired)}'
                  f' | top 3 {top_three}/{len(paired)} | result Brier {brier / len(paired):.3f}')


if __name__ == '__main__':
    with closing(sqlite3.connect(Path(DB_PATH).resolve().as_uri() + '?mode=ro', uri=True)) as db:
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='halftime_shadow'").fetchone():
            report(db)
        else:
            print('No halftime shadow forecasts recorded yet.')
