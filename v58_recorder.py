"""V5.8 shadow prediction journal. No API, alerts or automatic settlement.

Only explicit observed first-half final-corner totals settle entries. To avoid
look-ahead bias, a fixture must be recorded before its outcome is known.
"""
import argparse
import json
import math
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from v58_shadow import LiveCorners, evaluate, cache_evaluate

DEFAULT_DB = os.environ.get('SIGNAL_DB_PATH', 'first_half_v3.sqlite3')


def connect(db_path=None):
    path = Path(db_path or DEFAULT_DB)
    if not path.parent.exists():
        raise FileNotFoundError(f'Database directory missing: {path.parent}')
    db = sqlite3.connect(str(path), timeout=20)
    db.execute('PRAGMA busy_timeout=20000')
    db.execute('''CREATE TABLE IF NOT EXISTS v58_shadow_predictions (
        fixture_id INTEGER NOT NULL, minute INTEGER NOT NULL,
        recorded_at TEXT NOT NULL, home_id INTEGER NOT NULL, away_id INTEGER NOT NULL,
        corners_at_prediction INTEGER NOT NULL,
        probability REAL NOT NULL, model_payload TEXT NOT NULL,
        first_half_final_corners INTEGER,
        settled_at TEXT,
        PRIMARY KEY(fixture_id, minute)
    )''')
    return db


def record_prediction(db, fixture_id, home_id, away_id, live, home=None, away=None, cache_db=None):
    """Return (status, evaluation); require fresh valid model data.

    Call only from contemporaneous live observations; no outcome known at record time.
    Existing fixture/minute entries are immutable and not overwritten.
    """
    ids = (fixture_id, home_id, away_id)
    if any(type(v) is not int or v <= 0 for v in ids):
        raise ValueError('fixture and team IDs must be positive integers')
    result = evaluate(home, away, live) if home is not None and away is not None else cache_evaluate(home_id, away_id, live, cache_db)
    if result['status'] != 'SHADOW_ESTIMATE_UNCALIBRATED':
        return 'ABSTAIN', result
    p = result['probability']
    if not math.isfinite(p) or not 0 <= p <= 1:
        raise ValueError('invalid model probability')
    now = datetime.now(timezone.utc).isoformat()
    cur = db.execute('''INSERT OR IGNORE INTO v58_shadow_predictions
        (fixture_id,minute,recorded_at,home_id,away_id,corners_at_prediction,probability,model_payload)
        VALUES(?,?,?,?,?,?,?,?)''', (fixture_id, live.minute, now, home_id, away_id,
          live.corners, p, json.dumps(result, sort_keys=True)))
    db.commit()
    return ('RECORDED' if cur.rowcount else 'DUPLICATE'), result


def settle_fixture(db, fixture_id, first_half_final_corners, verified=False):
    """Only explicitly verified *first-half* final totals, never full-time totals."""
    if not verified:
        raise ValueError('verified=True required; never settle from unverified data')
    if type(fixture_id) is not int or fixture_id <= 0 or type(first_half_final_corners) is not int or first_half_final_corners < 0:
        raise ValueError('invalid fixture id or first-half corner total')
    rows = db.execute('''SELECT corners_at_prediction, first_half_final_corners
        FROM v58_shadow_predictions WHERE fixture_id=?''', (fixture_id,)).fetchall()
    if not rows:
        return 0
    if any(observed > first_half_final_corners for observed, _ in rows):
        raise ValueError('final corners lower than an earlier observed count')
    if any(existing is not None and existing != first_half_final_corners for _, existing in rows):
        raise ValueError('conflicting prior settlement; no records changed')
    count = db.execute('''UPDATE v58_shadow_predictions SET first_half_final_corners=?, settled_at=?
        WHERE fixture_id=? AND first_half_final_corners IS NULL''', (
          first_half_final_corners, datetime.now(timezone.utc).isoformat(), fixture_id)).rowcount
    db.commit()
    return count


def report(db):
    rows = db.execute('''SELECT fixture_id,minute,probability,first_half_final_corners
        FROM v58_shadow_predictions ORDER BY fixture_id,minute''').fetchall()
    settled = [(fid, minute, p, int(c >= 3)) for fid, minute, p, c in rows if c is not None]
    # One earliest prediction per fixture avoids inflating sample size with minute-by-minute observations.
    earliest = {}
    for fid, minute, p, outcome in settled:
        earliest.setdefault(fid, (p, outcome))
    vals = list(earliest.values())
    if not vals:
        return {'prediction_rows': len(rows), 'settled_rows': len(settled), 'settled_fixtures': 0,
                'note': 'No verified settled fixtures; accuracy cannot be evaluated.'}
    bins = []
    for lower in (0.0, 0.2, 0.4, 0.6, 0.8):
        selected = [(p, y) for p, y in vals if lower <= p < lower + 0.2 or (lower == 0.8 and p == 1.0)]
        if selected:
            bins.append({'range': f'{lower:.1f}-{lower+0.2:.1f}', 'n': len(selected),
                         'mean_predicted': round(sum(p for p, _ in selected)/len(selected), 4),
                         'observed_frequency': round(sum(y for _, y in selected)/len(selected), 4)})
    return {'prediction_rows': len(rows), 'settled_rows': len(settled),
            'settled_fixtures': len(vals), 'brier_score_earliest_per_fixture': round(sum((p-y)**2 for p,y in vals)/len(vals), 6),
            'observed_hit_rate': round(sum(y for _,y in vals)/len(vals), 4),
            'calibration_bins': bins,
            'warning': 'Research-only, uncalibrated. Samples are not independent across leagues or time.'}


def main():
    parser = argparse.ArgumentParser(description='Offline V5.8 shadow journal — no API or alerts')
    parser.add_argument('--db', default=None)
    sub = parser.add_subparsers(dest='action', required=True)
    record = sub.add_parser('record')
    for arg in ('fixture-id','home-id','away-id','minute','corners'):
        record.add_argument('--'+arg, type=int, required=True)
    for arg in ('shots','on-target','blocked'):
        record.add_argument('--'+arg, type=int)
    record.add_argument('--data-age-seconds', type=int, default=0)
    record.add_argument('--cache-db')
    settle = sub.add_parser('settle')
    settle.add_argument('--fixture-id', type=int, required=True)
    settle.add_argument('--first-half-final-corners', type=int, required=True)
    settle.add_argument('--verified-first-half-result', action='store_true', required=True)
    sub.add_parser('report')
    a = parser.parse_args()
    with connect(a.db) as db:
        if a.action == 'record':
            live = LiveCorners(a.minute, a.corners, a.shots, a.on_target, a.blocked, a.data_age_seconds)
            status, result = record_prediction(db, a.fixture_id, a.home_id, a.away_id, live, cache_db=a.cache_db)
            print(json.dumps({'write_status':status, 'evaluation':result}, indent=2))
        elif a.action == 'settle':
            print(json.dumps({'settled_rows':settle_fixture(db,a.fixture_id,a.first_half_final_corners,a.verified_first_half_result)}))
        else:
            print(json.dumps(report(db), indent=2))

if __name__ == '__main__':
    main()
