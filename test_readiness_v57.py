from readiness_v57 import classify, assessment

def test_readiness_no_samples():
    assert classify({}, {}) == 'NO_VERIFIED_CORNER_HISTORY'

def test_readiness_partial():
    assert classify({'corner_samples': 7}, {'corner_samples': 8}) == 'PARTIAL_CORNER_HISTORY'

def test_readiness_ready_and_stats():
    home = {'corner_samples': 8, 'corners_won_avg': 2.2, 'corners_conceded_avg': 1.4}
    away = {'corner_samples': 10, 'corners_won_avg': 1.6, 'corners_conceded_avg': 2.1}
    result = assessment(home, away)
    assert result['status'] == 'DATA_READY_FOR_VALIDATION'
    assert result['away_corners_against_avg'] == 2.1

def test_readiness_does_not_claim_model():
    assert 'probability' not in assessment({}, {})
