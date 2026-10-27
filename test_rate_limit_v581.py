from unittest.mock import Mock
import pytest
from rate_limit_v571 import APICooldown, guarded_get


def response(status=200, errors=None, headers=None):
    r = Mock()
    r.status_code = status
    r.headers = headers or {}
    r.json.return_value = {'errors': errors or {}, 'response': []}
    return r


def test_json_rate_limit_starts_shared_cooldown(monkeypatch, tmp_path):
    monkeypatch.setenv('SIGNAL_DB_PATH', str(tmp_path / 'state.db'))
    session = Mock()
    session.get.return_value = response(errors={'rateLimit': 'Too many requests.'})
    with pytest.raises(APICooldown, match='JSON_RATELIMIT'):
        guarded_get(session, 'https://example.org')
    with pytest.raises(APICooldown, match='SHARED_COOLDOWN'):
        guarded_get(session, 'https://example.org')
    assert session.get.call_count == 1


def test_other_json_errors_not_swallowed(monkeypatch, tmp_path):
    monkeypatch.setenv('SIGNAL_DB_PATH', str(tmp_path / 'state.db'))
    session = Mock()
    r = response(errors={'token': 'invalid'})
    session.get.return_value = r
    assert guarded_get(session, 'https://example.org') is r


def test_normal_json_succeeds(monkeypatch, tmp_path):
    monkeypatch.setenv('SIGNAL_DB_PATH', str(tmp_path / 'state.db'))
    session = Mock()
    r = response()
    session.get.return_value = r
    assert guarded_get(session, 'https://example.org') is r


def test_non_json_response_still_passes(monkeypatch, tmp_path):
    monkeypatch.setenv('SIGNAL_DB_PATH', str(tmp_path / 'state.db'))
    session = Mock()
    r = Mock(status_code=200, headers={})
    r.json.side_effect = ValueError('not JSON')
    session.get.return_value = r
    assert guarded_get(session, 'https://example.org') is r
