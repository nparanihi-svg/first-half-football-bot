"""Opt-in V5.8 bridge for real scanner observations; zero API calls or alerts.

Call observe_fixture(fixture, snapshot, ...) only after the scanner has fetched
first-half statistics. The bridge does not modify existing alerts or predictions.
Never settle from scorelines, elapsed time, or full-time corner totals.
"""
import os
from v58_shadow import LiveCorners
from v58_recorder import connect, record_prediction, settle_fixture, report


def observe_fixture(fixture, snapshot, *, journal_db=None, cache_db=None,
                    data_age_seconds=60, enabled=False):
    """Store one real first-half snapshot only when explicitly enabled.

    data_age_seconds must be supplied by caller from known cache age; 60 is
    conservative for the existing 60-second API statistics cache, not a promise
    of data freshness. A missing/stale corner reading always abstains.
    """
    if not enabled:
        return {'status': 'DISABLED'}
    f = fixture.get('fixture') or {}
    status = f.get('status') or {}
    teams = fixture.get('teams') or {}
    if status.get('short') != '1H':
        return {'status': 'ABSTAIN', 'reason': 'not first half'}
    minute = status.get('elapsed')
    if type(minute) is not int or not (0 <= minute < 45):
        return {'status': 'ABSTAIN', 'reason': 'invalid first-half minute'}
    fid = f.get('id')
    home_id = (teams.get('home') or {}).get('id')
    away_id = (teams.get('away') or {}).get('id')
    if any(type(x) is not int or x <= 0 for x in (fid, home_id, away_id)):
        return {'status': 'ABSTAIN', 'reason': 'missing fixture or team IDs'}
    if snapshot.fixture_id != fid or snapshot.status != '1H' or snapshot.elapsed != minute:
        return {'status': 'ABSTAIN', 'reason': 'fixture and snapshot disagree'}
    if snapshot.half_corners is None:
        return {'status': 'ABSTAIN', 'reason': 'first-half corners unavailable'}
    live = LiveCorners(minute=minute, corners=snapshot.half_corners,
                       shots=_integer_or_none(snapshot.total_shots),
                       on_target=_integer_or_none(snapshot.shots_on_goal),
                       blocked=_integer_or_none(snapshot.blocked_shots),
                       data_age_seconds=data_age_seconds)
    try:
        with connect(journal_db) as db:
            write_status, evaluation = record_prediction(
                db, fid, home_id, away_id, live, cache_db=cache_db)
        return {'status': write_status, 'evaluation': evaluation, 'fixture_id': fid}
    except (OSError, ValueError, RuntimeError) as exc:
        return {'status': 'ERROR', 'reason': f'{type(exc).__name__}: {exc}'}


def _integer_or_none(value):
    if value is None:
        return None
    if type(value) is int:
        return value
    if type(value) is float and value.is_integer():
        return int(value)
    return -1  # model abstains on invalid statistics


def settle_verified(fixture_id, first_half_final_corners, *, verified=False, journal_db=None):
    """Explicit verified first-half corner settlement, never automated guessing."""
    with connect(journal_db) as db:
        return settle_fixture(db, fixture_id, first_half_final_corners, verified=verified)


def summary(*, journal_db=None):
    with connect(journal_db) as db:
        return report(db)
