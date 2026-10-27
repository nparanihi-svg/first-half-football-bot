"""V5.8 offline, opt-in first-half CORNERS shadow research.

Reads existing V5.5 cache without API calls. It never sends alerts or modifies
scanner.py / engine.py. Estimates are hypotheses, NOT calibrated odds.
"""
import argparse
import json
import math
from dataclasses import dataclass


@dataclass(frozen=True)
class LiveCorners:
    minute: int
    corners: int | None
    shots: int | None = None
    on_target: int | None = None
    blocked: int | None = None
    data_age_seconds: int = 0


def _valid_avg(value):
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 30


def _valid_count(value):
    return type(value) is int and value >= 0


def poisson_at_least(mean, n):
    if n <= 0:
        return 1.0
    if mean <= 0:
        return 0.0
    cumulative = math.exp(-mean)
    term = cumulative
    for k in range(1, n):
        term *= mean / k
        cumulative += term
    return min(1.0, max(0.0, 1.0 - cumulative))


def evaluate(home, away, live: LiveCorners, min_samples=8):
    """Pure model for offline audit; missing or stale critical data means abstain."""
    if not 0 <= live.minute < 45 or not _valid_count(live.corners):
        return {'status': 'ABSTAIN', 'reason': 'invalid minute or missing first-half corners'}
    if live.data_age_seconds < 0 or live.data_age_seconds > 120:
        return {'status': 'ABSTAIN', 'reason': 'stale live statistics'}
    if live.corners >= 3:
        return {'status': 'ALREADY_REACHED', 'corners': live.corners}
    fields = ('corners_won_avg', 'corners_conceded_avg')
    for team in (home, away):
        if not _valid_count(team.get('corner_samples')) or team['corner_samples'] < min_samples:
            return {'status': 'ABSTAIN', 'reason': 'insufficient verified first-half corner history'}
        if not all(_valid_avg(team.get(field)) for field in fields):
            return {'status': 'ABSTAIN', 'reason': 'invalid or incomplete team corner averages'}
    for name in ('shots', 'on_target', 'blocked'):
        val = getattr(live, name)
        if val is not None and not _valid_count(val):
            return {'status': 'ABSTAIN', 'reason': f'invalid {name}'}
    if live.shots is not None and live.on_target is not None and live.on_target > live.shots:
        return {'status': 'ABSTAIN', 'reason': 'on-target shots exceed total shots'}
    # Mean first-half corners per team, based on matching its attack to opposition defence.
    home_rate = (home['corners_won_avg'] + away['corners_conceded_avg']) / 2.0
    away_rate = (away['corners_won_avg'] + home['corners_conceded_avg']) / 2.0
    raw_total_rate = home_rate + away_rate
    # Shrink small-sample outcomes towards the legacy 4.5 corner research baseline.
    effective_n = min(home['corner_samples'], away['corner_samples'])
    weight = effective_n / (effective_n + 12.0)
    rate = weight * raw_total_rate + (1.0 - weight) * 4.5
    factors = []
    if live.minute >= 10:
        for value, expected_full, strength, label in (
            (live.shots, 10.0, 0.10, 'shots'),
            (live.on_target, 3.0, 0.08, 'on_target'),
            (live.blocked, 2.5, 0.06, 'blocked')):
            if value is not None:
                expected = max(0.75, expected_full * live.minute / 45.0)
                factors.append((label, max(0.9, min(1.1, 1.0 + strength * (value / expected - 1.0)))))
    multiplier = math.prod(v for _, v in factors)
    multiplier = max(0.75, min(1.25, multiplier))
    # No corner-arrival timing is available: assume uniform future rate; test later.
    mean_remaining = rate * (45 - live.minute) / 45.0 * multiplier
    return {'status': 'SHADOW_ESTIMATE_UNCALIBRATED',
            'probability': round(poisson_at_least(mean_remaining, 3 - live.corners), 6),
            'corners_needed': 3 - live.corners,
            'expected_remaining_corners': round(mean_remaining, 4),
            'historical_total_rate': round(raw_total_rate, 4),
            'shrunk_rate': round(rate, 4),
            'verified_samples': [home['corner_samples'], away['corner_samples']],
            'live_adjustment': round(multiplier, 4),
            'live_inputs_used': [name for name, _ in factors],
            'warning': 'Uncalibrated exploratory estimate; not a betting recommendation.'}


def cache_evaluate(home_id, away_id, live, db_path=None):
    """Load two teams from V5.5 cache. No new API calls or writes."""
    import sqlite3
    from pathlib import Path
    from compare_v55 import summarise_team
    import history_v55 as history
    target = Path(db_path) if db_path else history.CACHE
    if not target.is_file():
        return {'status': 'ABSTAIN', 'reason': 'historical cache database unavailable'}
    db = sqlite3.connect(f'file:{target.resolve()}?mode=ro', uri=True)
    try:
        home = summarise_team(db, home_id, 20, 20, None)
        away = summarise_team(db, away_id, 20, 20, None)
    finally:
        db.close()
    return evaluate(home, away, live)


def main():
    p = argparse.ArgumentParser(description='V5.8 offline corner shadow estimate (no API / no alerts)')
    p.add_argument('--home-id', type=int, required=True)
    p.add_argument('--away-id', type=int, required=True)
    p.add_argument('--minute', type=int, required=True)
    p.add_argument('--corners', type=int, required=True)
    p.add_argument('--shots', type=int)
    p.add_argument('--on-target', type=int)
    p.add_argument('--blocked', type=int)
    p.add_argument('--data-age-seconds', type=int, default=0)
    p.add_argument('--cache-db', help='Existing V5.5 historical cache SQLite file')
    a = p.parse_args()
    live = LiveCorners(a.minute, a.corners, a.shots, a.on_target, a.blocked, a.data_age_seconds)
    print(json.dumps(cache_evaluate(a.home_id, a.away_id, live, a.cache_db), indent=2))


if __name__ == '__main__':
    main()
