"""Persistent first-half research observations and conservative outcome checks.

No bet placement. An outcome is labelled only after a verified half-time score
or first-half corner statistic becomes available.
"""
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(os.getenv('SIGNAL_DB_PATH', '/var/data/first_half_v3.sqlite3'))


def _now():
    return datetime.now(timezone.utc).isoformat()


def _connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB_PATH), timeout=20)
    con.execute('PRAGMA busy_timeout=20000')
    con.execute('''CREATE TABLE IF NOT EXISTS observations (
      fixture_id INTEGER NOT NULL, market TEXT NOT NULL, minute INTEGER NOT NULL,
      recorded_at TEXT NOT NULL, home TEXT, away TEXT, league TEXT,
      probability REAL, first_half_goals INTEGER, first_half_corners INTEGER,
      shots INTEGER, shots_on_target INTEGER, data_note TEXT,
      result TEXT DEFAULT 'PENDING', settled_at TEXT,
      PRIMARY KEY(fixture_id,market,minute))''')
    con.execute('''CREATE TABLE IF NOT EXISTS checks (
      fixture_id INTEGER PRIMARY KEY, last_checked_at TEXT NOT NULL)''')
    con.commit()
    return con


def record(snapshot, result):
    with _connect() as db:
        db.execute('''INSERT OR IGNORE INTO observations
         (fixture_id,market,minute,recorded_at,home,away,league,probability,
          first_half_goals,first_half_corners,shots,shots_on_target,data_note)
          VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
          (snapshot.fixture_id, result['market'], snapshot.elapsed, _now(),
           snapshot.home, snapshot.away, snapshot.league, result['probability'],
           snapshot.half_goals, snapshot.half_corners, snapshot.total_shots,
           snapshot.shots_on_goal,
           'experimental model; not calibrated; no bookmaker odds'))


def _candidates():
    # One fixture per scan; prefer those not checked recently.
    with _connect() as db:
        return db.execute("""SELECT o.fixture_id FROM observations o
          LEFT JOIN checks c ON c.fixture_id=o.fixture_id
          WHERE o.result='PENDING'
            AND (c.last_checked_at IS NULL OR
                 julianday(c.last_checked_at) < julianday('now','-3 minutes'))
          GROUP BY o.fixture_id
          ORDER BY COALESCE(MAX(c.last_checked_at), '1970-01-01'), MIN(o.recorded_at)
          LIMIT 1""").fetchall()


def verify_one(api, live_fixtures):
    """Check one due fixture. Settle goal and corner markets independently."""
    completed = {'HT', '2H', 'ET', 'P', 'FT', 'AET', 'PEN'}
    known = {f['fixture']['id']: f for f in live_fixtures
             if f.get('fixture', {}).get('status', {}).get('short') in completed}
    for (fixture_id,) in _candidates():
        fixture = known.get(fixture_id)
        try:
            if fixture is None:
                response = api.get('/fixtures', id=fixture_id)
                fixture = response[0] if response else None
            if fixture is None:
                print('V3 check:', fixture_id, 'fixture data unavailable', flush=True)
                return
            status = fixture.get('fixture', {}).get('status', {}).get('short')
            if status not in completed:
                print('V3 check:', fixture_id, 'status:', status,
                      'awaiting completed first half', flush=True)
                return
            scores = (fixture.get('score') or {}).get('halftime') or {}
            home, away = scores.get('home'), scores.get('away')
            goals = (home + away if type(home) is int and type(away) is int else None)
            with _connect() as db:
                goal_pending = db.execute("""SELECT 1 FROM observations WHERE
                    fixture_id=? AND market='FH_OVER_0_5_GOALS' AND result='PENDING' LIMIT 1""",
                    (fixture_id,)).fetchone() is not None
                corner_pending = db.execute("""SELECT 1 FROM observations WHERE
                    fixture_id=? AND market='FH_OVER_2_5_CORNERS' AND result='PENDING' LIMIT 1""",
                    (fixture_id,)).fetchone() is not None
                if goal_pending and goals is not None:
                    cur = db.execute("""UPDATE observations SET result=?, settled_at=? WHERE
                        fixture_id=? AND market='FH_OVER_0_5_GOALS' AND result='PENDING'""",
                        ('WIN' if goals >= 1 else 'LOSS', _now(), fixture_id))
                    print('V3 GOALS settled:', fixture_id, 'goals:', goals,
                          'records:', cur.rowcount, flush=True)
            # Do not let unavailable corners prevent a verified goal settlement.
            if corner_pending:
                try:
                    from provider import _stat_map
                    totals, _ = _stat_map(api.first_half_stats(fixture_id))
                    corner_value = totals.get('Corner Kicks')
                    corners = int(corner_value) if corner_value is not None else None
                    if corners is not None:
                        with _connect() as db:
                            cur = db.execute("""UPDATE observations SET result=?, settled_at=? WHERE
                                fixture_id=? AND market='FH_OVER_2_5_CORNERS' AND result='PENDING'""",
                                ('WIN' if corners >= 3 else 'LOSS', _now(), fixture_id))
                        print('V3 CORNERS settled:', fixture_id, 'corners:', corners,
                              'records:', cur.rowcount, flush=True)
                    else:
                        print('V3 check:', fixture_id, 'corners unavailable', flush=True)
                except Exception as error:
                    if ('API_DAILY_RESERVE_REACHED' in str(error) or
                            'API_MINUTE_LIMIT_NEAR' in str(error)):
                        raise
                    print('V3 corners check deferred:', fixture_id,
                          type(error).__name__, str(error)[:120], flush=True)
            if goal_pending and goals is None:
                print('V3 check:', fixture_id, 'halftime goal score unavailable', flush=True)
        finally:
            # Timestamp the attempt even on errors, preventing rapid retries.
            with _connect() as db:
                db.execute('INSERT OR REPLACE INTO checks VALUES (?,?)',
                           (fixture_id, _now()))
        return


def stats():
    with _connect() as db:
        return db.execute('SELECT market,result,COUNT(*) FROM observations '
                          'GROUP BY market,result').fetchall()
