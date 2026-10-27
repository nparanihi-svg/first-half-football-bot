import sqlite3
from unittest.mock import Mock
import pytest
from rate_limit_v571 import APICooldown, guarded_get, reserve_request, set_cooldown


def test_shared_limit_across_calls(monkeypatch, tmp_path):
    monkeypatch.setenv('SIGNAL_DB_PATH', str(tmp_path / 'state.db'))
    monkeypatch.setenv('API_SHARED_REQUESTS_PER_MINUTE', '2')
    reserve_request()
    reserve_request()
    with pytest.raises(APICooldown, match='MINUTE_BUDGET'):
        reserve_request()


def test_429_starts_cooldown_and_avoids_second_request(monkeypatch, tmp_path):
    monkeypatch.setenv('SIGNAL_DB_PATH', str(tmp_path / 'state.db'))
    session = Mock()
    session.get.return_value.status_code = 429
    session.get.return_value.headers = {'Retry-After': '65'}
    with pytest.raises(APICooldown, match='HTTP_429'):
        guarded_get(session, 'https://example.com')
    with pytest.raises(APICooldown, match='SHARED_COOLDOWN'):
        guarded_get(session, 'https://example.com')
    assert session.get.call_count == 1


def test_cooldown_is_shared_between_connections(monkeypatch, tmp_path):
    path = tmp_path / 'state.db'
    monkeypatch.setenv('SIGNAL_DB_PATH', str(path))
    set_cooldown(60)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT value FROM api_guard_state_v571 WHERE name='cooldown_until'").fetchone()
    with pytest.raises(APICooldown):
        reserve_request()


def test_successful_http_request(monkeypatch, tmp_path):
    monkeypatch.setenv('SIGNAL_DB_PATH', str(tmp_path / 'state.db'))
    session = Mock()
    session.get.return_value.status_code = 200
    assert guarded_get(session, 'https://example.com') is session.get.return_value
