"""Transparent, experimental in-play baselines. NOT trained or calibrated probabilities."""
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
    possession_home: float | None = None
    possession_away: float | None = None


def poisson_tail(mean: float, at_least: int) -> float:
    if at_least <= 0:
        return 1.0
    return max(0.0, min(1.0, 1.0 - exp(-mean) * sum(mean ** k / factorial(k) for k in range(at_least))))


def _intensity(s: Snapshot, market: str):
    """Small, bounded research adjustments. Heuristic weights are NOT learned."""
    factors = []
    if s.elapsed >= 10 and s.total_shots is not None:
        expected = max(1.0, 10.0 * s.elapsed / 45.0)
        factors.append(("total shots", max(.85, min(1.15, 1.0 + .15 * (s.total_shots / expected - 1.0)))))
    if s.elapsed >= 10 and s.shots_on_goal is not None:
        expected = max(.5, 3.0 * s.elapsed / 45.0)
        factors.append(("shots on target", max(.85, min(1.15, 1.0 + .12 * (s.shots_on_goal / expected - 1.0)))))
    if market == 'corners' and s.elapsed >= 10 and s.blocked_shots is not None:
        expected = max(.5, 2.5 * s.elapsed / 45.0)
        factors.append(("blocked shots", max(.90, min(1.10, 1.0 + .08 * (s.blocked_shots / expected - 1.0)))))
    # Possession is descriptive, not directly a scoring multiplier:
    # possession alone is not evidence of goals or corners.
    value = 1.0
    for _, factor in factors:
        value *= factor
    return max(.70, min(1.35, value)), [name for name, _ in factors]


def predict(s: Snapshot):
    """Probability-like research estimates by first-half end; require validation."""
    if s.status not in ('NS', '1H') or (s.elapsed >= 45 and s.status != '1H'):
        return []
    remaining = max(0.0, 45.0 - max(s.elapsed, 0))
    fraction = remaining / 45.0
    results = []
    for market, label, rate, needed, enabled in [
        ('FH_OVER_0_5_GOALS', '1H Over 0.5 Goals', s.pre_goal_rate, 1, s.half_goals == 0),
        ('FH_OVER_2_5_CORNERS', '1H Over 2.5 Asian Corners', s.pre_corner_rate,
         None if s.half_corners is None else 3 - s.half_corners,
         s.half_corners is not None and s.half_corners < 3),
    ]:
        if not enabled:
            continue
        adjustment, inputs = _intensity(s, 'corners' if 'CORNERS' in market else 'goals')
        baseline_mean = rate * fraction
        adjusted_mean = baseline_mean * adjustment
        results.append({
            'market': market, 'label': label,
            'probability': poisson_tail(adjusted_mean, needed),
            'baseline_probability': poisson_tail(baseline_mean, needed),
            'needed': needed, 'forecast_mean': adjusted_mean,
            'intensity_multiplier': adjustment,
            'inputs_used': inputs,
            'minutes_remaining': remaining,
        })
    return results


def edge(probability: float, odds: float | None):
    if odds is None or odds <= 1:
        return None
    return {'implied': 1.0 / odds, 'expected_return': probability * odds - 1.0}
