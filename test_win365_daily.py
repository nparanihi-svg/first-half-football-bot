import sqlite3
from datetime import datetime, timezone
from win365_daily import daily_tally, daily_line


def make_db():
    db = sqlite3.connect(':memory:')
    db.executescript('''
    CREATE TABLE result_notices(fixture_id INTEGER,market TEXT,sent_at TEXT);
    CREATE TABLE sent_alerts(fixture_id INTEGER,market TEXT);
    CREATE TABLE observations(fixture_id INTEGER,market TEXT,result TEXT,settled_at TEXT);
    ''')
    return db


def insert(db,fid,market,result,settled,notified=True,observations=1):
    db.execute('INSERT INTO sent_alerts VALUES (?,?)',(fid,market))
    if notified: db.execute('INSERT INTO result_notices VALUES (?, ?, ?)',(fid,market,settled))
    for _ in range(observations):
        db.execute('INSERT INTO observations VALUES (?,?,?,?)',(fid,market,result,settled))


def test_todays_outcomes_and_legacy_record():
    db=make_db(); insert(db,1,'GOALS','WIN','2026-10-27T12:30:00+00:00'); insert(db,2,'CORNERS','LOSS','2026-10-27T13:00:00+00:00')
    insert(db,3,'GOALS','WIN','2026-10-26T01:00:00+00:00')
    now=datetime(2026,10,27,14,tzinfo=timezone.utc)
    assert daily_tally(db,2,'CORNERS','LOSS',now)==(1,1)
    assert daily_line(db,2,'CORNERS','LOSS',now)=='Today (Perth): 1 W / 1 L (50.0% outcomes)'


def test_unnotified_current_included_once():
    db=make_db();insert(db,8,'GOALS','WIN','2026-10-27T13:00:00Z',False,4)
    assert daily_tally(db,8,'GOALS','WIN',datetime(2026,10,27,14,tzinfo=timezone.utc))==(1,0)


def test_perth_midnight_boundary():
    db=make_db(); insert(db,1,'GOALS','WIN','2026-10-27T15:59:59+00:00')
    insert(db,2,'GOALS','LOSS','2026-10-27T16:00:00+00:00')
    assert daily_tally(db,2,'GOALS','LOSS',datetime(2026,10,28,2,tzinfo=timezone.utc))==(0,1)


def test_exclude_incomplete_outcomes():
    db=make_db();insert(db,1,'GOALS','WIN','2026-10-27T13:00:00Z')
    db.execute('INSERT INTO observations VALUES (?,?,?,?)',(1,'GOALS','PENDING',None))
    insert(db,2,'GOALS','LOSS','2026-10-27T13:00:00Z')
    assert daily_tally(db,2,'GOALS','LOSS',datetime(2026,10,27,14,tzinfo=timezone.utc))==(0,1)


def test_zero():
    db=make_db(); assert daily_line(db,9,'GOALS','WIN',datetime(2026,10,27,14,tzinfo=timezone.utc)).startswith('Today (Perth): 0 W')
