import pytest
import alerts

class FakeResponse:
    def __init__(self, ok=True):
        self.ok = ok
    def raise_for_status(self):
        return None
    def json(self):
        return {'ok': self.ok, 'result': {'message_id': 1}}

def test_original_single_chat(monkeypatch):
    monkeypatch.delenv('TELEGRAM_EXTRA_CHAT_IDS', raising=False)
    calls=[]
    monkeypatch.setattr(alerts.requests, 'post', lambda url, json, timeout: calls.append(json['chat_id']) or FakeResponse())
    assert alerts.send_telegram('secret', '123', 'hello')['ok']
    assert calls == ['123']

def test_two_chats_and_dedup(monkeypatch):
    monkeypatch.setenv('TELEGRAM_EXTRA_CHAT_IDS', ' -1004328451847,123,-1004328451847 ')
    calls=[]
    monkeypatch.setattr(alerts.requests, 'post', lambda url, json, timeout: calls.append(json['chat_id']) or FakeResponse())
    alerts.send_telegram('secret', '123', 'hello')
    assert calls == ['123', '-1004328451847']

def test_secondary_failure_does_not_break_primary(monkeypatch):
    monkeypatch.setenv('TELEGRAM_EXTRA_CHAT_IDS', '-1004328451847')
    calls=[]
    def post(url, json, timeout):
        calls.append(json['chat_id'])
        if json['chat_id'] == '-1004328451847':
            raise ConnectionError('private error')
        return FakeResponse()
    monkeypatch.setattr(alerts.requests, 'post', post)
    assert alerts.send_telegram('secret', '123', 'hello')['ok']
    assert calls == ['123', '-1004328451847']

def test_primary_error_still_attempts_secondary(monkeypatch):
    monkeypatch.setenv('TELEGRAM_EXTRA_CHAT_IDS', '-1004328451847')
    calls=[]
    def post(url, json, timeout):
        calls.append(json['chat_id'])
        if json['chat_id'] == '123':
            raise ConnectionError('primary failure')
        return FakeResponse()
    monkeypatch.setattr(alerts.requests, 'post', post)
    with pytest.raises(ConnectionError):
        alerts.send_telegram('secret', '123', 'hello')
    assert calls == ['123', '-1004328451847']

def test_rejecting_telegram_response(monkeypatch):
    monkeypatch.delenv('TELEGRAM_EXTRA_CHAT_IDS', raising=False)
    monkeypatch.setattr(alerts.requests, 'post', lambda url, json, timeout: FakeResponse(False))
    with pytest.raises(RuntimeError, match='Telegram rejected'):
        alerts.send_telegram('secret', '123', 'hello')
