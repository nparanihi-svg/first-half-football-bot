"""V5.5 low-volume background history collector. Never called from scanner.

Reads team IDs from existing historical_shadow_v55 records. Stores fixtures and
explicit first-half corner statistics in the shared history_v55 cache.
Requests limited by a SQLite pre-call reservation ledger. Defaults: 2 per run,
6 per UTC day, 100 per UTC month. All limits are configurable DOWN only.
A request reservation counts even if the call fails, by design.
"""
import argparse
import json
import os
import sqlite3
from datetime import datetime, timezone, timedelta

import requests
import history_v55 as hist

STATE_DB = os.getenv('SIGNAL_DB_PATH', '/var/data/first_half_v3.sqlite3')
RUN_MAX = min(10, max(0, int(os.getenv('HISTORY_AUTO_RUN_MAX', '2'))))
DAY_MAX = min(500, max(0, int(os.getenv('HISTORY_AUTO_DAY_MAX', '6'))))
MONTH_MAX = min(10000, max(0, int(os.getenv('HISTORY_AUTO_MONTH_MAX', '100'))))


def queue_teams():
    with sqlite3.connect(STATE_DB, timeout=10) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='historical_shadow_v55'").fetchone():
            return []
        rows = db.execute('''SELECT home_id,away_id FROM historical_shadow_v55
                             ORDER BY captured_at DESC LIMIT 250''').fetchall()
    teams = []
    # V5.6: upcoming teams first, sourced by prematch_v56 without affecting live scanning.
    try:
        with sqlite3.connect(STATE_DB, timeout=10) as db:
            found = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='prematch_v56_teams'").fetchone()
            if found:
                soon = db.execute("SELECT team_id FROM prematch_v56_teams WHERE kickoff_at > ? ORDER BY kickoff_at ASC LIMIT 120",
                    (datetime.now(timezone.utc).isoformat(),)).fetchall()
                for (tid,) in soon:
                    if isinstance(tid, int) and tid > 0 and tid not in teams:
                        teams.append(tid)
    except sqlite3.Error as exc:
        print('V5.6 prematch queue read skipped:', str(exc)[:100], flush=True)
    for home, away in rows:
        for tid in (home, away):
            if isinstance(tid, int) and tid > 0 and tid not in teams:
                teams.append(tid)
    return teams


def reserve_call(db):
    """Reserve before the HTTP request, atomically and durably."""
    now = datetime.now(timezone.utc)
    day, month = now.strftime('%Y-%m-%d'), now.strftime('%Y-%m')
    db.execute('''CREATE TABLE IF NOT EXISTS history_auto_requests_v55
                  (at_utc TEXT NOT NULL, day TEXT NOT NULL, month TEXT NOT NULL)''')
    db.execute('BEGIN IMMEDIATE')
    try:
        day_count = db.execute('SELECT COUNT(*) FROM history_auto_requests_v55 WHERE day=?', (day,)).fetchone()[0]
        month_count = db.execute('SELECT COUNT(*) FROM history_auto_requests_v55 WHERE month=?', (month,)).fetchone()[0]
        if day_count >= DAY_MAX or month_count >= MONTH_MAX:
            db.rollback()
            return False, day_count, month_count
        db.execute('INSERT INTO history_auto_requests_v55 VALUES (?,?,?)', (now.isoformat(), day, month))
        db.commit()
        return True, day_count + 1, month_count + 1
    except Exception:
        db.rollback()
        raise


def fetch(session, db, endpoint, **params):
    allowed, d, m = reserve_call(db)
    if not allowed:
        print(f'V5.5 budget reached: {d}/{DAY_MAX} today, {m}/{MONTH_MAX} this month', flush=True)
        return None
    print(f'V5.5 API reservation: {d}/{DAY_MAX} day, {m}/{MONTH_MAX} month', flush=True)
    return hist.api_get(session, endpoint, **params)


def cache_put(db, key, response):
    db.execute('INSERT OR REPLACE INTO cache VALUES (?,?,?)',
               (key, json.dumps(response), datetime.now(timezone.utc).isoformat()))
    db.commit()


def has_cache(db, key):
    return db.execute('SELECT payload FROM cache WHERE key=?', (key,)).fetchone()


def build_jobs(db, teams):
    """Complete imminent fixtures as pairs, then broaden global historical coverage.

    Cached-only queue building: no HTTP. First obtain both teams' fixture
    histories, then request missing 1H corner samples for their recent matches.
    Never treat absent corner stats as zero. Keep all leagues eligible.
    """
    now = datetime.now(timezone.utc).isoformat()
    upcoming = []
    try:
        with sqlite3.connect(STATE_DB, timeout=5) as state:
            exists = state.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='prematch_v56_fixtures'").fetchone()
            if exists:
                upcoming = state.execute(
                    "SELECT home_id, away_id, kickoff_at FROM prematch_v56_fixtures "
                    "WHERE kickoff_at > ? ORDER BY kickoff_at ASC, fixture_id ASC",
                    (now,)).fetchall()
    except sqlite3.Error as exc:
        print('V5.6 fixture readiness queue fallback:', str(exc)[:100], flush=True)

    jobs = []
    seen_jobs = set()
    seen_teams = set()

    def add(kind, ident):
        job = (kind, ident)
        if job not in seen_jobs:
            jobs.append(job)
            seen_jobs.add(job)

    def team_corners(tid, max_per_team=5):
        row = has_cache(db, f'team:{tid}:last40')
        if row is None:
            return
        try:
            fixtures = hist.half_goals(json.loads(row[0]), tid, 20)
        except (ValueError, TypeError, KeyError) as exc:
            print('V5.6 cached team history unreadable:', tid, str(exc)[:80], flush=True)
            return
        count = 0
        for fixture in fixtures:
            fid = int(fixture['id'])
            if has_cache(db, f'first_half_stats:{fid}') is None:
                add('corners', fid)
                count += 1
                if count >= max_per_team:
                    break

    # Organise the first upcoming matches by complete two-team readiness.
    # If either team lacks fixtures, queue both missing team lists together.
    # Once both lists exist, focus their corner samples before moving on.
    for home_id, away_id, _kick in upcoming:
        pair = list(dict.fromkeys((home_id, away_id)))
        for tid in pair:
            seen_teams.add(tid)
            if has_cache(db, f'team:{tid}:last40') is None:
                add('fixtures', tid)
        if all(has_cache(db, f'team:{tid}:last40') is not None for tid in pair):
            for tid in pair:
                team_corners(tid, max_per_team=5)

    # Preserve the original worldwide fallback for all other discovered teams.
    # Teams without available fixture-level pairings are still eligible.
    for tid in teams:
        if tid in seen_teams:
            continue
        if has_cache(db, f'team:{tid}:last40') is None:
            add('fixtures', tid)
        else:
            team_corners(tid, max_per_team=5)
    return jobs


def run(dry_run=False):
    teams = queue_teams()
    print(f'V5.5 history auto: {len(teams)} discovered teams', flush=True)
    if not teams:
        return
    with hist.db_open() as db:
        jobs = build_jobs(db, teams)
        print(f'V5.5 queued jobs: {len(jobs)}; per run max={RUN_MAX}', flush=True)
        if dry_run:
            print('Next jobs:', jobs[:5], '| no API calls', flush=True)
            return
        token = os.getenv('API_FOOTBALL_KEY')
        if not token:
            print('V5.5 collector: API_FOOTBALL_KEY missing', flush=True)
            return
        s = requests.Session()
        s.headers.update({'x-apisports-key': token})
        for kind, ident in jobs[:RUN_MAX]:
            endpoint = '/fixtures' if kind == 'fixtures' else '/fixtures/statistics'
            params = {'team': ident, 'last': 40} if kind == 'fixtures' else {'fixture': ident, 'half': 'true'}
            key = f'team:{ident}:last40' if kind == 'fixtures' else f'first_half_stats:{ident}'
            try:
                data = fetch(s, db, endpoint, **params)
                if data is None:
                    break
                cache_put(db, key, data)
                print(f'V5.5 cached {kind} {ident} ({len(data)} records)', flush=True)
            except Exception as error:
                print(f'V5.5 history request failed: {type(error).__name__}: {str(error)[:140]}', flush=True)
                break


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true', help='Inspect queue without API calls')
    args = parser.parse_args()
    run(dry_run=args.dry_run)
