"""Persist entry prices and prospectively compare a tighter goal filter."""
import os
import sqlite3
from pathlib import Path


def record_candidate(path, snapshot, probability):
    # Keep the first qualifying observation, before its result is known.
    if not (snapshot.elapsed < 30 and probability >= .60):
        return
    with sqlite3.connect(path, timeout=10) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS goal_filter_shadow (
            fixture_id INTEGER PRIMARY KEY, minute INTEGER, probability REAL)""")
        db.execute("INSERT OR IGNORE INTO goal_filter_shadow VALUES (?, ?, ?)",
                   (snapshot.fixture_id, snapshot.elapsed, probability))


def record_price(db, fixture_id, market, minute, probability, quote):
    db.execute("""CREATE TABLE IF NOT EXISTS alert_prices (
        fixture_id INTEGER, market TEXT, minute INTEGER, probability REAL,
        odds REAL, feed_updated TEXT, PRIMARY KEY (fixture_id, market))""")
    db.execute("INSERT OR IGNORE INTO alert_prices VALUES (?, ?, ?, ?, ?, ?)",
               (fixture_id, market, minute, probability,
                float(quote[0]) if quote else None,
                quote[1].isoformat() if quote else None))


def report(db):
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    outcomes = {(f, m): r for f, m, r in db.execute("""
        SELECT fixture_id, market,
        CASE WHEN MIN(result)=MAX(result) THEN MIN(result) ELSE 'PENDING' END
        FROM observations GROUP BY fixture_id, market""")}
    if 'alert_prices' in tables:
        for market, in db.execute("SELECT DISTINCT market FROM alert_prices ORDER BY market"):
            rows = db.execute("SELECT fixture_id,odds FROM alert_prices WHERE market=?", (market,)).fetchall()
            settled = [(price, outcomes.get((fid, market))) for fid, price in rows
                       if price is not None and outcomes.get((fid, market)) in ('WIN', 'LOSS')]
            profit = sum(price - 1 if result == 'WIN' else -1 for price, result in settled)
            missing = sum(price is None for _, price in rows)
            roi = f'{100 * profit / len(settled):.1f}%' if settled else 'unavailable'
            print(f'{market} | priced settled {len(settled)} | missing prices {missing}'
                  f' | flat-stake profit {profit:+.2f} units | ROI {roi}')
    else:
        print('No permanent entry prices recorded yet.')
    if 'goal_filter_shadow' in tables:
        results = [outcomes.get((fid, 'FH_OVER_0_5_GOALS'), 'PENDING')
                   for fid, in db.execute("SELECT fixture_id FROM goal_filter_shadow")]
        print(f'Goals before 30 minutes, estimate >=60% | wins {results.count("WIN")}'
              f' | losses {results.count("LOSS")} | pending {results.count("PENDING")}')
    else:
        print('No tighter goal-filter candidates recorded yet.')


if __name__ == '__main__':
    path = Path(os.getenv('SIGNAL_DB_PATH', '/var/data/first_half_v3.sqlite3'))
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as db:
        report(db)
