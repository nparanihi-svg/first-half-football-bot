"""Research-only halftime result and score forecasts, settled from halftime scores."""
import json
import os
import sqlite3
from contextlib import closing
from math import exp
from pathlib import Path

from provider import _number
from halftime_history import cached_rates

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


def record_prediction(fixture, snapshot, stats, api=None):
    checkpoint = next((m for m in CHECKPOINTS if m <= snapshot.elapsed < m + 5), None)
    score = score_pair(fixture.get('goals') or {})
    if getattr(snapshot, 'status', '1H') != '1H':
        return
    from halftime_features import observe, adjusted_rates
    with closing(sqlite3.connect(DB_PATH, timeout=2)) as db, db:
        context = observe(db, fixture, snapshot.elapsed, stats)
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='halftime_shadow'").fetchone()
        existing = (db.execute('SELECT payload,home_ht FROM halftime_shadow WHERE fixture_id=? AND checkpoint=?',
                              (snapshot.fixture_id, checkpoint)).fetchone() if exists else None)
    history_status = 'unavailable'
    # Warm before checkpoint entry so the two-request-per-scan budget can
    # rotate through concurrent games without rewriting saved forecasts.
    if api is not None and snapshot.fixture_id < 0 and 8 <= snapshot.elapsed < 40:
        try:
            api.prepare_halftime_shadow(fixture)
            history_status = 'cache checked'
        except Exception as exc:
            history_status = 'history unavailable: ' + type(exc).__name__
    if checkpoint is None or score is None:
        return
    if existing and (existing[1] is not None or json.loads(existing[0]).get('team_stats') is not None):
        return
    share = target_share(fixture, stats)
    mean = 1.15 * (45 - snapshot.elapsed) / 45
    payload = {'baseline': forecast(score, mean, .5),
               'team_stats': forecast(score, mean, share) if share is not None else None}
    history = cached_rates(fixture)
    payload['history'] = None
    if history is not None:
        home_mean, away_mean, metadata = history
        remaining = (home_mean + away_mean) * (45 - snapshot.elapsed) / 45
        payload['history'] = forecast(score, remaining, home_mean / (home_mean + away_mean))
        payload['history_inputs'] = metadata
    payload['entry_score'] = list(score)
    payload['features'] = context
    payload['history_status'] = 'available' if history is not None else history_status
    enhanced_mean, enhanced_share, used = adjusted_rates(score, snapshot.elapsed, history, context)
    payload['enhanced'] = forecast(score, enhanced_mean, enhanced_share)
    payload['enhanced_version'] = 1
    payload['enhanced_inputs'] = used + (['team_history'] if history else [])
    payload['odds'] = None
    if api is not None and snapshot.fixture_id < 0:
        try:
            payload['odds'] = api.halftime_quotes(fixture)
        except Exception as exc:
            payload['odds_status'] = 'unavailable: ' + type(exc).__name__
    with closing(sqlite3.connect(DB_PATH, timeout=2)) as db, db:
        db.execute('''CREATE TABLE IF NOT EXISTS halftime_shadow (
            fixture_id INTEGER, checkpoint INTEGER, minute INTEGER, league TEXT,
            payload TEXT, home_ht INTEGER, away_ht INTEGER,
            PRIMARY KEY(fixture_id, checkpoint))''')
        # A first scan can arrive before team statistics. Upgrade that checkpoint
        # within its window when stats arrive, preserving settled forecasts.
        existing = db.execute(
            'SELECT payload,home_ht FROM halftime_shadow WHERE fixture_id=? AND checkpoint=?',
            (snapshot.fixture_id, checkpoint)).fetchone()
        if (existing and existing[1] is None and share is not None
                and json.loads(existing[0]).get('team_stats') is None):
            original = json.loads(existing[0])
            payload['history'] = original.get('history')
            if 'history_inputs' in original:
                payload['history_inputs'] = original['history_inputs']
            # A history forecast at the earlier minute must retain its own
            # baseline for a comparison at the same time.
            payload['history_baseline'] = original.get('history_baseline', original['baseline'])
            db.execute(
                'UPDATE halftime_shadow SET minute=?,payload=? WHERE fixture_id=? AND checkpoint=?',
                (snapshot.elapsed, json.dumps(payload), snapshot.fixture_id, checkpoint))
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
        compare_models(paired, ('baseline', 'team_stats'), checkpoint, 'Team stats')
        history_pairs = [(dict(p, history_baseline=p.get('history_baseline', p['baseline'])), h, a)
                         for p, h, a in paired]
        compare_models(history_pairs, ('history_baseline', 'history'), checkpoint, 'Team history')
        compare_models(paired, ('baseline', 'enhanced'), checkpoint, 'Enhanced experimental')
    score_state_report(rows)


def score_state_report(rows):
    from collections import defaultdict
    groups = defaultdict(list)
    for cp,payload,h,a in rows:
        p=json.loads(payload)
        if cp==25 and h is not None and p.get('entry_score') is not None:
            groups[tuple(p['entry_score'])].append((p,h,a))
    for score,items in sorted(groups.items()):
        unchanged=sum(outcome(*score)==outcome(h,a) for p,h,a in items)
        exact=sum(score==(h,a) for p,h,a in items)
        print(f'25-minute entry score {score[0]}-{score[1]} | n={len(items)} | unchanged result {unchanged} | unchanged score {exact}')
        for model in ('baseline','team_stats','enhanced'):
            entries=[(p,h,a) for p,h,a in items if p.get(model)]
            hits=sum(max(p[model]['result'],key=p[model]['result'].get)==outcome(h,a) for p,h,a in entries)
            priced=[]
            for p,h,a in entries:
                pick=max(p[model]['result'],key=p[model]['result'].get)
                price=((p.get('odds') or {}).get('result') or {}).get(pick)
                if price is not None:priced.append(price-1 if pick==outcome(h,a) else -1)
            score_priced=[]
            for p,h,a in entries:
                sh,sa,_=p[model]['scores'][0]
                price=((p.get('odds') or {}).get('scores') or {}).get(f'{sh}-{sa}')
                if price is not None:score_priced.append(price-1 if (sh,sa)==(h,a) else -1)
            rp=f'{sum(priced):+.2f}u' if priced else 'unavailable'
            sp=f'{sum(score_priced):+.2f}u' if score_priced else 'unavailable'
            print(f'  {model}: result {hits}/{len(entries)} | H2H priced {len(priced)} | H2H priced-only profit {rp} | correct-score priced {len(score_priced)} | correct-score priced-only profit {sp}')
    print('Shadow only; missing odds are not imputed. Forecasts from the same game are correlated.')


def compare_models(rows, models, checkpoint, label):
    paired = [(p, h, a) for p, h, a in rows
              if all(p.get(model) is not None for model in models)]
    print(f'{label} checkpoint {checkpoint}′ | Paired settled forecasts: {len(paired)}')
    if not paired:
        return
    for model in models:
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
