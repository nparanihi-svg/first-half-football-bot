"""V5.5 cache-only historical shadow logging; never changes live predictions.

Integrate in scanner.py after record(snapshot, result):
    try:
        from shadow_v55 import record_shadow
        record_shadow(fixture, result)
    except Exception as shadow_error:
        print('V5.5 shadow unavailable:', type(shadow_error).__name__, flush=True)

No network requests are made by this module. Existing comparison cache is read only.
"""
import json
import os
import sqlite3
from datetime import datetime, timezone

import history_v55 as history
from compare_v55 import summarise_team

SHADOW_DB = os.getenv('SIGNAL_DB_PATH', '/var/data/first_half_v3.sqlite3')


def record_shadow(fixture, result):
    """Store first observed live estimate and contemporaneously available history.

    Any error is isolated and logged rather than allowed to interrupt the scanner.
    """
    try:
        fixture_id = int(fixture['fixture']['id'])
        home_id = int(fixture['teams']['home']['id'])
        away_id = int(fixture['teams']['away']['id'])
        market = str(result['market'])
        estimate = result.get('probability')
        elapsed = fixture.get('fixture', {}).get('status', {}).get('elapsed')
        captured_at = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(SHADOW_DB, timeout=3) as out:
            out.execute('''CREATE TABLE IF NOT EXISTS historical_shadow_v55 (
                fixture_id INTEGER NOT NULL, market TEXT NOT NULL,
                captured_at TEXT NOT NULL, match_minute INTEGER,
                home_id INTEGER NOT NULL, away_id INTEGER NOT NULL,
                live_estimate REAL, home_history TEXT, away_history TEXT,
                PRIMARY KEY (fixture_id, market))''')
            if out.execute('SELECT 1 FROM historical_shadow_v55 WHERE fixture_id=? AND market=?',
                           (fixture_id, market)).fetchone():
                return
            # Cache-only comparison. No API client is configured or invoked.
            with history.db_open() as cache:
                home = summarise_team(cache, home_id, 20, 20, None)
                away = summarise_team(cache, away_id, 20, 20, None)
            out.execute('''INSERT OR IGNORE INTO historical_shadow_v55
                (fixture_id,market,captured_at,match_minute,home_id,away_id,live_estimate,home_history,away_history)
                VALUES (?,?,?,?,?,?,?,?,?)''',
                (fixture_id, market, captured_at, int(elapsed) if elapsed is not None else None,
                 home_id, away_id, float(estimate) if estimate is not None else None,
                 json.dumps(home), json.dumps(away)))
    except Exception as exc:
        print('V5.5 shadow failed:', type(exc).__name__, str(exc)[:120], flush=True)


if __name__ == '__main__':
    with sqlite3.connect(SHADOW_DB) as db:
        table = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='historical_shadow_v55'").fetchone()
        if not table:
            print('No shadow records yet. Integration must run during live scans.')
        else:
            rows = db.execute('''SELECT fixture_id, market, match_minute, home_id, away_id,
                                live_estimate, home_history, away_history
                                FROM historical_shadow_v55 ORDER BY captured_at DESC LIMIT 5''').fetchall()
            print('Shadow records (latest 5):', len(rows))
            for fid, market, minute, hid, aid, est, hs, aws in rows:
                h, a = json.loads(hs), json.loads(aws)
                print(f'{fid} {market} minute={minute} estimate={est} teams={hid}/{aid} '
                      f'history={h.get("matches_with_scores",0)}/{a.get("matches_with_scores",0)} '
                      f'corners={h.get("corner_samples",0)}/{a.get("corner_samples",0)}')
