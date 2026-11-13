"""Verified-price alert gates and exact per-scan evidence."""
import json
import math
from datetime import datetime, timezone


def selection_shots(snapshot):
    total = snapshot.total_shots
    return total if total is not None else getattr(snapshot, 'shots_lower_bound', None)


def quote_issue(probability, quote):
    if quote is None:
        return 'verified exact-line odds unavailable'
    price = float(quote[0])
    if not math.isfinite(price) or price < 1.50:
        return 'odds below 1.50 or invalid'
    if not math.isfinite(probability) or not 0 <= probability <= 1:
        return 'invalid model estimate'
    if probability * price <= 1:
        return 'no positive model-estimated value'
    return None


def record_decision(db, snapshot, result, stats, reason, quote=None, *, delivered=False):
    db.execute('''CREATE TABLE IF NOT EXISTS scan_decisions (
        fixture_id INTEGER, market TEXT, recorded_at TEXT, minute INTEGER,
        reason TEXT, payload TEXT)''')
    payload = json.dumps({'snapshot': vars(snapshot), 'forecast': result,
                          'raw_stats': stats, 'quote': quote}, default=str, sort_keys=True)
    now = datetime.now(timezone.utc).isoformat()
    db.execute('INSERT INTO scan_decisions VALUES (?,?,?,?,?,?)',
               (snapshot.fixture_id, result['market'], now, snapshot.elapsed, reason, payload))
    if delivered:
        db.execute('''CREATE TABLE IF NOT EXISTS alert_inputs (
            fixture_id INTEGER, market TEXT, recorded_at TEXT, payload TEXT,
            PRIMARY KEY(fixture_id, market))''')
        db.execute('INSERT OR IGNORE INTO alert_inputs VALUES (?,?,?,?)',
                   (snapshot.fixture_id, result['market'], now, payload))
