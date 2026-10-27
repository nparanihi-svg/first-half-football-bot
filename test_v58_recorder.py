import sqlite3
import pytest
from v58_shadow import LiveCorners
from v58_recorder import connect, record_prediction, settle_fixture, report

TEAM = {'corner_samples': 12, 'corners_won_avg':2.0,'corners_conceded_avg':2.3}

@pytest.fixture
def db(tmp_path):
    with connect(tmp_path/'journal.sqlite3') as con:
        yield con

def sample(db, fixture=123, minute=12, corners=1):
    return record_prediction(db, fixture, 10, 11, LiveCorners(minute, corners, 3, 1, 1), TEAM, TEAM)

def test_record_and_duplicate(db):
    assert sample(db)[0]=='RECORDED'
    assert sample(db)[0]=='DUPLICATE'
    assert db.execute('SELECT count(*) FROM v58_shadow_predictions').fetchone()[0] == 1

def test_missing_history_abstains_without_writing(db):
    assert record_prediction(db,321,10,11,LiveCorners(12,0), {'corner_samples':0},TEAM)[0]=='ABSTAIN'
    assert report(db)['prediction_rows']==0

def test_settle_requires_verified(db):
    sample(db)
    with pytest.raises(ValueError,match='verified'):
        settle_fixture(db,123,3)
    assert report(db)['settled_fixtures']==0

def test_settle_checks_consistency(db):
    sample(db, corners=2)
    with pytest.raises(ValueError, match='lower'):
        settle_fixture(db,123,1,True)
    assert settle_fixture(db,123,4,True)==1
    with pytest.raises(ValueError,match='conflicting'):
        settle_fixture(db,123,3,True)

def test_calibration_earliest_only(db):
    sample(db, minute=12)
    sample(db, minute=15)
    sample(db, fixture=124, minute=12)
    settle_fixture(db,123,3,True)
    settle_fixture(db,124,1,True)
    r=report(db)
    assert r['settled_fixtures']==2
    assert r['settled_rows']==3
    assert 0<=r['brier_score_earliest_per_fixture']<=1
    assert sum(x['n'] for x in r['calibration_bins'])==2

def test_empty_report(db):
    assert report(db)['settled_fixtures']==0
