from v58_shadow import LiveCorners, evaluate, poisson_at_least

H = dict(corner_samples=15, corners_won_avg=2.2, corners_conceded_avg=1.6)
A = dict(corner_samples=14, corners_won_avg=1.9, corners_conceded_avg=2.0)

def test_no_history_abstains():
    assert evaluate({}, A, LiveCorners(12, 1))['status'] == 'ABSTAIN'

def test_missing_corners_abstains():
    assert evaluate(H, A, LiveCorners(12, None))['status'] == 'ABSTAIN'

def test_stale_abstains():
    assert evaluate(H, A, LiveCorners(12, 1, data_age_seconds=121))['status'] == 'ABSTAIN'

def test_inconsistent_shots_abstains():
    assert evaluate(H, A, LiveCorners(12, 1, shots=2, on_target=4))['status'] == 'ABSTAIN'

def test_completed_target_not_an_alert():
    assert evaluate(H, A, LiveCorners(15, 3))['status'] == 'ALREADY_REACHED'

def test_probability_increases_with_one_more_corner():
    one = evaluate(H, A, LiveCorners(15, 1))
    two = evaluate(H, A, LiveCorners(15, 2))
    assert one['status'] == 'SHADOW_ESTIMATE_UNCALIBRATED'
    assert two['probability'] > one['probability']

def test_probability_decreases_with_time():
    early = evaluate(H, A, LiveCorners(10, 1))
    late = evaluate(H, A, LiveCorners(35, 1))
    assert early['probability'] > late['probability']

def test_no_goal_model_or_alert_side_effects():
    r = evaluate(H, A, LiveCorners(18, 1, 5, 2, 1))
    assert 'warning' in r and 'probability' in r
    assert not ('send_telegram' in r or 'bet' in r)

def test_poisson_known():
    assert abs(poisson_at_least(2, 1) - (1 - 2.718281828459045 ** -2)) < 1e-10
