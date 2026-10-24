"""Transparent baseline models, NOT trained/calibrated betting probabilities."""
from dataclasses import dataclass
from math import exp, factorial

@dataclass
class Snapshot:
    fixture_id: int
    league: str
    home: str
    away: str
    elapsed: int
    status: str
    half_goals: int
    half_corners: int | None
    shots_on_goal: int | None = None
    blocked_shots: int | None = None
    total_shots: int | None = None
    pre_goal_rate: float = 1.15
    pre_corner_rate: float = 4.50
    kickoff: str = ""
    data_age_seconds: float = 0
    # Half-time statistics availability / score validity are checked upstream.


def poisson_tail(mean: float, at_least: int) -> float:
    if at_least <= 0:
        return 1.0
    return max(0.0, min(1.0, 1.0 - exp(-mean) * sum(mean**k/factorial(k) for k in range(at_least))))


def predict(s: Snapshot):
    """Conditional success P by half-time. Real-money use needs out-of-time calibration."""
    if s.status not in ('NS', '1H') or s.elapsed >= 45 and s.status != '1H':
        return []
    remaining = max(0.0, 45.0 - max(s.elapsed, 0))
    time_ratio = remaining / 45.0
    # Conservative, bounded attack intensity adjustments; no use of future information.
    intensity = 1.0
    if s.elapsed >= 10 and s.total_shots is not None:
        expected_shots = max(1.0, 10.0 * s.elapsed / 45.0)
        intensity = min(1.35, max(.75, .7 + .3 * s.total_shots / expected_shots))
    gmean = s.pre_goal_rate * time_ratio * intensity
    cmean = s.pre_corner_rate * time_ratio * intensity
    results = []
    if s.half_goals == 0:
        results.append({'market':'FH_OVER_0_5_GOALS', 'label':'1H Over 0.5 Goals',
                        'probability':poisson_tail(gmean, 1), 'needed':1,
                        'forecast_mean':gmean})
    if s.half_corners is not None and s.half_corners < 3:
        need = 3-s.half_corners
        results.append({'market':'FH_OVER_2_5_CORNERS', 'label':'1H Over 2.5 Asian Corners',
                        'probability':poisson_tail(cmean, need), 'needed':need,
                        'forecast_mean':cmean})
    return results


def edge(probability: float, odds: float | None):
    if odds is None or odds <= 1:
        return None
    return {'implied':1.0/odds, 'expected_return': probability*odds-1.0}
