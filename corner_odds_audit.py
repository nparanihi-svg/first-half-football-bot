"""Bounded corner feed snapshots and read-only diagnostic report."""
import json
import os
import sqlite3
from pathlib import Path


def record(path, fixture_id, minute, rows, diagnostic):
    with sqlite3.connect(path, timeout=2) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS corner_odds_audit (
            id INTEGER PRIMARY KEY, checked_at TEXT NOT NULL,
            fixture_id INTEGER NOT NULL, minute INTEGER NOT NULL,
            diagnostic TEXT NOT NULL, response TEXT NOT NULL)""")
        db.execute("""INSERT INTO corner_odds_audit
            (checked_at,fixture_id,minute,diagnostic,response)
            VALUES (strftime('%Y-%m-%dT%H:%M:%fZ','now'),?,?,?,?)""",
            (fixture_id, minute, json.dumps(diagnostic), json.dumps(rows)))
        db.execute("""DELETE FROM corner_odds_audit WHERE id NOT IN
            (SELECT id FROM corner_odds_audit ORDER BY id DESC LIMIT 500)""")
    print('CORNER ODDS AUDIT', fixture_id, minute, json.dumps(diagnostic), flush=True)


def report(db):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='corner_odds_audit'").fetchone():
        print('No corner feed diagnostics recorded yet.')
        return
    for checked, fid, minute, diagnostic, response in db.execute("""
        SELECT checked_at,fixture_id,minute,diagnostic,response
        FROM corner_odds_audit ORDER BY id DESC LIMIT 20"""):
        print(checked, fid, minute, diagnostic)
        for row in json.loads(response):
            for market in row.get('odds') or []:
                if 'corner' in market.get('name', '').lower() or market.get('id') == 204:
                    print(' ', market.get('id'), market.get('name'),
                          json.dumps(market.get('values') or []))


if __name__ == '__main__':
    path = Path(os.getenv('SIGNAL_DB_PATH', '/var/data/first_half_v3.sqlite3'))
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as db:
        report(db)
