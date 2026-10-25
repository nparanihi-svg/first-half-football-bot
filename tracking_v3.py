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
    # Keep API usage bounded: process only one due fixture per scan.
    with _connect() as db:
        return db.execute('''SELECT o.fixture_id FROM observations o
          LEFT JOIN checks c ON c.fixture_id=o.fixture_id
          WHERE o.result='PENDING' AND
           (c.last_checked_at IS NULL OR julianday(c.last_checked_at) < julianday('now','-10 minutes'))
          GROUP BY o.fixture_id ORDER BY MIN(o.recorded_at) LIMIT 1''').fetchall()


def verify_one(api, live_fixtures):
    """Attempt settlement for one match per scan; never guess unavailable data."""
    ready = {f['fixture']['id']:f for f in live_fixtures
             if f.get('fixture', {}).get('status', {}).get('short') in
             ('HT','2H','ET','P','FT','AET','PEN')}
    for (fixture_id,) in _candidates():
        fixture = ready.get(fixture_id)
        with _connect() as db:
            db.execute('INSERT OR REPLACE INTO checks VALUES (?,?)',
                       (fixture_id, _now()))
        if fixture is None:
            # Extra fixture request used only for an eligible, previously saved match.
            response = api.get('/fixtures', id=fixture_id)
            if not response:
                return
            fixture = response[0]
        status = fixture.get('fixture', {}).get('status', {}).get('short')
        if status not in ('HT','2H','ET','P','FT','AET','PEN'):
            return
        scores = (fixture.get('score') or {}).get('halftime') or {}
        h, a = scores.get('home'), scores.get('away')
        half_goals = h + a if isinstance(h,int) and isinstance(a,int) else None
        with _connect() as db:
            need_corners = db.execute('''SELECT 1 FROM observations WHERE
                 fixture_id=? AND market='FH_OVER_2_5_CORNERS' AND result='PENDING' LIMIT 1''',
                 (fixture_id,)).fetchone() is not None
        corners = None
        if need_corners:
            # The half=true parameter is the provider's original first-half stats route.
            from provider import _stat_map
            totals, _ = _stat_map(api.first_half_stats(fixture_id))
            if 'Corner Kicks' in totals:
                corners = int(totals['Corner Kicks'])
        with _connect() as db:
            if half_goals is not None:
                db.execute('''UPDATE observations SET result=?, settled_at=? WHERE
                   fixture_id=? AND market='FH_OVER_0_5_GOALS' AND result='PENDING' ''',
                   ('WIN' if half_goals >= 1 else 'LOSS',_now(),fixture_id))
            if corners is not None:
                db.execute('''UPDATE observations SET result=?, settled_at=? WHERE
                   fixture_id=? AND market='FH_OVER_2_5_CORNERS' AND result='PENDING' ''',
                   ('WIN' if corners >= 3 else 'LOSS',_now(),fixture_id))
        print('V3 outcome check:',fixture_id,'goals:',half_goals,
              'corners:',corners,'(None means unverified)',flush=True)
        return


def stats():
    with _connect() as db:
        return db.execute('SELECT market,result,COUNT(*) FROM observations '
                          'GROUP BY market,result').fetchall()
