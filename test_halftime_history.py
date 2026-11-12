import io
import json
import sqlite3
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import halftime_shadow as shadow
from halftime_history import team_rates

KICKOFF = datetime(2026, 11, 12, tzinfo=timezone.utc)


def fixture(fid, date, home_goals=1):
    return {'fixture': {'id': fid, 'date': date.isoformat(), 'status': {'short': 'FT'}},
            'teams': {'home': {'id': 1}, 'away': {'id': 2}},
            'score': {'halftime': {'home': home_goals, 'away': 0}}}


def test_rates_exclude_future_current_duplicate_and_incomplete_fixtures():
    previous = [fixture(i, KICKOFF - timedelta(days=i)) for i in range(1, 7)]
    noisy = previous + [previous[0], fixture(90, KICKOFF + timedelta(days=1), 99),
                        fixture(91, KICKOFF, 99)]
    rate = team_rates(noisy, 1, 'home', KICKOFF, 91)
    assert rate == team_rates(previous, 1, 'home', KICKOFF, 91)
    assert rate['matches'] == 6
    assert team_rates(previous[:4], 1, 'home', KICKOFF, 91) is None


def test_history_keeps_original_comparison_when_live_stats_arrive(tmp_path, monkeypatch):
    monkeypatch.setattr(shadow, 'DB_PATH', str(tmp_path / 'shadow.db'))
    monkeypatch.setattr(shadow, 'cached_rates', lambda f: (1, .5, {'version': 1}))
    f = fixture(99, KICKOFF)
    f['goals'] = {'home': 0, 'away': 0}
    s = SimpleNamespace(elapsed=25, fixture_id=99, league='Test')
    shadow.record_prediction(f, s, [])
    with sqlite3.connect(shadow.DB_PATH) as db:
        original = json.loads(db.execute('SELECT payload FROM halftime_shadow').fetchone()[0])
    stats = [{'team': {'id': i}, 'statistics': [{'type': 'Shots on Goal', 'value': 2}]}
             for i in (1, 2)]
    s.elapsed = 28
    shadow.record_prediction(f, s, stats)
    with sqlite3.connect(shadow.DB_PATH) as db:
        updated = json.loads(db.execute('SELECT payload FROM halftime_shadow').fetchone()[0])
        assert updated['history'] == original['history']
        assert updated['history_baseline'] == original['baseline']
        assert updated['baseline'] != original['baseline']
        db.execute('UPDATE halftime_shadow SET home_ht=1,away_ht=0')
        output = io.StringIO()
        with redirect_stdout(output):
            shadow.report(db)
        assert 'Team history checkpoint 25′ | Paired settled forecasts: 1' in output.getvalue()


def test_legacy_forecasts_remain_reportable():
    with sqlite3.connect(':memory:') as db:
        db.execute('CREATE TABLE halftime_shadow(checkpoint,payload,home_ht,away_ht)')
        db.execute('INSERT INTO halftime_shadow VALUES(15,?,0,0)',
                   (json.dumps({'baseline': shadow.forecast((0, 0), .5, .5),
                                'team_stats': None}),))
        with redirect_stdout(io.StringIO()) as output:
            shadow.report(db)
        assert 'Saved forecasts: 1 | Settled: 1' in output.getvalue()
