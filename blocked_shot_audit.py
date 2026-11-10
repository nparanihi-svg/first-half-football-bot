"""Compare corners qualification with and without the blocked-shot requirement."""
import os
import sqlite3
from collections import Counter

CORNERS = 'FH_OVER_2_5_CORNERS'
DB_PATH = os.getenv('SIGNAL_DB_PATH', '/var/data/first_half_v3.sqlite3')


def record_candidate(path, snapshot, strict):
    minute = snapshot.elapsed
    with sqlite3.connect(path, timeout=2) as db:
        db.execute('''CREATE TABLE IF NOT EXISTS blocked_shot_audit (
            fixture_id INTEGER PRIMARY KEY, relaxed_minute INTEGER NOT NULL,
            blocked_at_relaxed REAL, strict_minute INTEGER)''')
        db.execute('''INSERT INTO blocked_shot_audit VALUES (?, ?, ?, ?)
            ON CONFLICT(fixture_id) DO UPDATE SET strict_minute=excluded.strict_minute
            WHERE blocked_shot_audit.strict_minute IS NULL AND excluded.strict_minute IS NOT NULL''',
                   (snapshot.fixture_id, minute, snapshot.blocked_shots, minute if strict else None))


def report(db):
    rows = db.execute('''SELECT a.fixture_id, a.relaxed_minute, a.blocked_at_relaxed,
             a.strict_minute, CASE WHEN MIN(o.result)=MAX(o.result)
             AND MIN(o.result) IN ('WIN','LOSS') THEN MIN(o.result) ELSE 'UNRESOLVED' END
        FROM blocked_shot_audit a
        LEFT JOIN observations o ON o.fixture_id=a.fixture_id AND o.market=?
        GROUP BY a.fixture_id ORDER BY a.fixture_id''', (CORNERS,))
    counts = Counter()
    print('Fixture | Without filter minute | Blocked shots then | With filter minute | Outcome')
    for fixture, relaxed, blocked, strict, outcome in rows:
        print(fixture, relaxed, blocked, strict, outcome, sep=' | ')
        if strict is None:
            group = 'No strict qualifier recorded'
        elif strict > relaxed:
            group = 'Strict qualified at a later minute'
        else:
            group = 'Both qualified in the same minute'
        counts[group, outcome] += 1
    for (group, outcome), count in sorted(counts.items()):
        print(f'{group} | {outcome} | {count}')
    print('Unresolved fixtures can still qualify or settle.')


if __name__ == '__main__':
    with sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True) as db:
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='blocked_shot_audit'").fetchone():
            report(db)
        else:
            print('No blocked-shot audit candidates recorded yet.')
