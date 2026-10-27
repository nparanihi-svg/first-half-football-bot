"""Non-network opt-in and failure isolation tests for the scanner V5.8 hook."""
import sys
import types
from unittest.mock import Mock
import pytest
import scanner


def fixture():
    return {'fixture': {'id': 123}}


def test_disabled_does_not_import_or_call(monkeypatch):
    monkeypatch.delenv('ENABLE_V58_SHADOW', raising=False)
    monkeypatch.delitem(sys.modules, 'v58_integration', raising=False)
    scanner._record_v58_shadow(fixture(), object())
    assert 'v58_integration' not in sys.modules


def test_enabled_passes_live_and_database_paths(monkeypatch):
    observe = Mock(return_value={'status': 'RECORDED'})
    monkeypatch.setitem(sys.modules, 'v58_integration', types.SimpleNamespace(observe_fixture=observe))
    monkeypatch.setenv('ENABLE_V58_SHADOW', 'true')
    monkeypatch.setenv('SIGNAL_DB_PATH', '/tmp/state.sqlite')
    monkeypatch.setenv('HISTORY_V55_DB', '/tmp/history.sqlite')
    snap = types.SimpleNamespace(data_age_seconds=35)
    scanner._record_v58_shadow(fixture(), snap)
    observe.assert_called_once_with(fixture(), snap, journal_db='/tmp/state.sqlite', cache_db='/tmp/history.sqlite', data_age_seconds=35, enabled=True)


def test_enabled_bridge_failure_is_nonfatal(monkeypatch, capsys):
    monkeypatch.setenv('ENABLE_V58_SHADOW', 'true')
    monkeypatch.setitem(sys.modules, 'v58_integration', types.SimpleNamespace(observe_fixture=Mock(side_effect=OSError('storage failure'))))
    scanner._record_v58_shadow(fixture(), object())
    assert 'V5.8 SHADOW ERROR:' in capsys.readouterr().out


def test_hook_is_before_current_predict_loop():
    import inspect
    source=inspect.getsource(scanner.scan)
    assert source.index('_record_v58_shadow(fixture, snapshot)') < source.index('for result in predict(snapshot)')
