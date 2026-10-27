import sqlite3
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from v58_integration import observe_fixture, settle_verified, summary


def fixtures(status='1H', minute=18):
    return {'fixture': {'id': 321, 'status': {'short': status, 'elapsed': minute}},
            'teams': {'home': {'id': 11}, 'away': {'id': 12}}}


def snapshot(corners=1, minute=18):
    return SimpleNamespace(fixture_id=321, status='1H', elapsed=minute,
                           half_corners=corners, total_shots=5.,
                           shots_on_goal=2., blocked_shots=1.)


def sample():
    return {'status': 'SHADOW_ESTIMATE_UNCALIBRATED', 'probability': .62,
            'warning': 'research only'}


def test_default_disabled_never_calls_model(tmp_path):
    with patch('v58_integration.record_prediction') as record:
        assert observe_fixture(fixtures(), snapshot(), journal_db=tmp_path/'a.db')['status']=='DISABLED'
        record.assert_not_called()


def test_missing_corners_abstains(tmp_path):
    assert observe_fixture(fixtures(), snapshot(None), enabled=True, journal_db=tmp_path/'a.db')['status']=='ABSTAIN'


def test_rejects_wrong_half_and_mismatched_fixture(tmp_path):
    assert observe_fixture(fixtures('2H'), snapshot(), enabled=True, journal_db=tmp_path/'a.db')['status']=='ABSTAIN'
    assert observe_fixture(fixtures(), snapshot(minute=17), enabled=True, journal_db=tmp_path/'a.db')['status']=='ABSTAIN'


def test_record_then_dedupe_and_report(tmp_path):
    path = tmp_path/'research.sqlite'
    with patch('v58_recorder.cache_evaluate', return_value=sample()):
        a = observe_fixture(fixtures(), snapshot(), enabled=True, journal_db=path)
        b = observe_fixture(fixtures(), snapshot(), enabled=True, journal_db=path)
    assert a['status']=='RECORDED'
    assert b['status']=='DUPLICATE'
    assert summary(journal_db=path)['prediction_rows'] == 1


def test_verified_settlement_only(tmp_path):
    path=tmp_path/'research.sqlite'
    with patch('v58_recorder.cache_evaluate', return_value=sample()):
        observe_fixture(fixtures(), snapshot(), enabled=True, journal_db=path)
    with pytest.raises(ValueError, match='verified=True'):
        settle_verified(321,3,journal_db=path)
    assert settle_verified(321, 3, verified=True,journal_db=path)==1
    assert summary(journal_db=path)['settled_fixtures']==1


def test_abstains_when_model_lacks_history(tmp_path):
    path=tmp_path/'research.sqlite'
    with patch('v58_recorder.cache_evaluate', return_value={'status':'ABSTAIN','reason':'not enough history'}):
        out=observe_fixture(fixtures(),snapshot(),enabled=True,journal_db=path)
    assert out['status']=='ABSTAIN'
    assert summary(journal_db=path)['prediction_rows']==0
