"""V5.6 pre-match team discovery. Optional; never runs inside live scanner.

Dry run: python prematch_v56.py --dry-run  (zero HTTP requests)
Manual discovery: python prematch_v56.py --refresh  (uses historical budget)
Auto: ENABLE_PREMATCH_V56=true; worker runs discovery in background.
"""
import argparse
import os
import sqlite3
from datetime import datetime, timedelta, timezone

import requests
import history_v55 as hist

STATE_DB = os.getenv('SIGNAL_DB_PATH', '/var/data/first_half_v3.sqlite3')
HOURS_AHEAD = min(48, max(1, int(os.getenv('PREMATCH_HOURS_AHEAD', '24'))))
REFRESH_HOURS = 3


def init(db):
    db.execute("""CREATE TABLE IF NOT EXISTS prematch_v56_teams
        (team_id INTEGER PRIMARY KEY, kickoff_at TEXT NOT NULL, last_seen TEXT NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS prematch_v56_checks
        (day TEXT PRIMARY KEY, checked_at TEXT NOT NULL)""")
    db.commit()


def discover(dry_run=False):
    now = datetime.now(timezone.utc)
    with sqlite3.connect(STATE_DB, timeout=15) as db:
        if dry_run:
            has = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='prematch_v56_teams'").fetchone()
            rows = db.execute('SELECT team_id,kickoff_at FROM prematch_v56_teams ORDER BY kickoff_at LIMIT 12').fetchall() if has else []
            print('V5.6 dry run: cached upcoming teams:',len(rows),'(showing up to 12); no API calls')
            print(*rows, sep='\n') if rows else None
            print('Discovery window:', HOURS_AHEAD, 'hours; runs in background when enabled')
            return
        init(db)
        if not os.getenv('API_FOOTBALL_KEY'):
            print('V5.6 no API_FOOTBALL_KEY; skipped',flush=True)
            return
        # Use the same durable budget ledger as the V5.5 collector.
        from history_auto_v55 import reserve_call
        session = requests.Session()
        session.headers.update({'x-apisports-key': os.environ['API_FOOTBALL_KEY']})
        dates = sorted({(now+timedelta(hours=x)).strftime('%Y-%m-%d') for x in (0,HOURS_AHEAD)})
        total = 0
        for day in dates:
            prev = db.execute('SELECT checked_at FROM prematch_v56_checks WHERE day=?',(day,)).fetchone()
            if prev and now-datetime.fromisoformat(prev[0]) < timedelta(hours=REFRESH_HOURS):
                continue
            allowed, used, _ = reserve_call(db)
            if not allowed:
                print('V5.6 history request budget exhausted; discovery deferred',flush=True)
                break
            try:
                fixtures = hist.api_get(session, '/fixtures', date=day)
            except Exception as exc:
                print('V5.6 fixture discovery failed:',type(exc).__name__,str(exc)[:100],flush=True)
                continue
            accepted = 0
            for f in fixtures:
                try:
                    kick = datetime.fromisoformat(f['fixture']['date'].replace('Z','+00:00')).astimezone(timezone.utc)
                    if not (now < kick <= now+timedelta(hours=HOURS_AHEAD)):
                        continue
                    for side in ('home','away'):
                        tid=int(f['teams'][side]['id'])
                        if tid <= 0: continue
                        # A team can appear multiple times; keep earliest upcoming kickoff.
                        old=db.execute('SELECT kickoff_at FROM prematch_v56_teams WHERE team_id=?',(tid,)).fetchone()
                        if not old or kick.isoformat() < old[0]:
                            db.execute('INSERT OR REPLACE INTO prematch_v56_teams VALUES (?,?,?)',(tid,kick.isoformat(),now.isoformat()))
                        accepted+=1
                except (KeyError,TypeError,ValueError,OverflowError):
                    continue
            db.execute('INSERT OR REPLACE INTO prematch_v56_checks VALUES (?,?)',(day,now.isoformat()))
            db.commit()
            total+=accepted
            print(f'V5.6 {day}: discovered {accepted} upcoming team slots; history budget use {used}',flush=True)
        db.execute('DELETE FROM prematch_v56_teams WHERE kickoff_at <= ?', (now.isoformat(),))
        db.commit()
        print('V5.6 upcoming team slots saved:',total,flush=True)


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--dry-run',action='store_true')
    parser.add_argument('--refresh',action='store_true')
    args=parser.parse_args()
    if not args.dry_run and not args.refresh:
        parser.error('Use --dry-run (no requests) or --refresh (real requests)')
    discover(dry_run=args.dry_run)
