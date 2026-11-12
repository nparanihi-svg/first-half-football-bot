import sqlite3
from unittest.mock import patch

from betsapi_provider import BetsAPI, market_values, normalize, period, statistics
from provider import parse_fixture


def event():
    return {'id': '10', 'time': '1794461400', 'time_status': '1',
            'timer': {'tm': 20, 'md': 0, 'tt': '1'}, 'ss': '0-0',
            'home': {'id': '1', 'name': 'Home'}, 'away': {'id': '2', 'name': 'Away'},
            'league': {'name': 'Test'}, 'stats': {
                'on_target': ['2', '1'], 'off_target': ['3', '1'],
                'shots_blocked': ['1', '0'], 'corners': ['1', '1']}}


def test_normalized_stats_and_ids():
    f = normalize(event(), '99')
    s = parse_fixture(f, statistics(f))
    assert s.fixture_id == -99
    assert s.total_shots == 8
    assert s.shots_on_goal == 3
    assert s.half_corners == 2
    assert f['teams']['home']['id'] == -1


def test_missing_stats_never_become_zero_or_fulltime_settlement():
    e = event()
    e['stats']['on_target'][1] = ''
    f = normalize(e, '99')
    assert parse_fixture(f, statistics(f)).shots_on_goal is None
    e['time_status'] = '3'
    f = normalize(e, '99')
    assert parse_fixture(f, statistics(f)).half_corners is None
    e['stats']['corner_h'] = ['2', '1']
    f = normalize(e, '99')
    assert parse_fixture(f, statistics(f)).half_corners == 3


def test_halftime_requires_explicit_score_and_second_half_is_excluded():
    e = event()
    e['timer'].update(tm=45, tt='0')
    assert period(e)[0] == '1H'
    e['scores'] = {'1': {'home': '0', 'away': '0'}}
    assert period(e)[0] == 'HT'
    e['timer'].update(tm=55, md=1, tt='1')
    assert period(e)[0] == '2H'


def test_fractional_prices_exact_line_and_suspension():
    rows = [[{'type': 'MG', 'NA': '1st Half Asian Corners', 'SU': '0'},
             {'type': 'MA', 'NA': 'Over'},
             {'type': 'PA', 'HA': '2.5', 'OD': '3/4', 'SU': '0'},
             {'type': 'MA', 'NA': 'Under', 'SU': '1'},
             {'type': 'PA', 'HA': '2.5', 'OD': '1/1', 'SU': '0'},
             {'type': 'MG', 'NA': 'Asian Corners', 'SU': '0'},
             {'type': 'MA', 'NA': 'Over'},
             {'type': 'PA', 'HA': '2.5', 'OD': '1/1', 'SU': '0'}]]
    values = market_values(rows)[0]['values']
    assert len(values) == 2
    assert values[0]['odd'] == '1.75'
    assert values[0]['suspended'] is False
    assert values[1]['suspended'] is True


def test_durable_mapping_routes_settlement_to_correct_provider(tmp_path):
    api = BetsAPI('secret')
    api.path = str(tmp_path / 'test.db')
    responses = [
        [{'id': '99', 'our_event_id': '10', 'league': {'name': 'Test'}}], [event()]]
    with patch.object(api, 'request', side_effect=responses):
        assert api.live()[0]['fixture']['id'] == -99
    other = BetsAPI('secret')
    other.path = api.path
    e = event()
    e.update(time_status='3', scores={'1': {'home': '1', 'away': '0'}})
    with patch.object(other, 'request', return_value=[e]) as request:
        result = other.get('/fixtures', id=-99)
        assert result[0]['score']['halftime']['home'] == 1
        request.assert_called_once_with('/v1/event/view', event_id='10')


def test_network_errors_do_not_expose_token():
    import requests
    api = BetsAPI('secret-token')
    with patch.object(api.session, 'get', side_effect=requests.RequestException('token=secret-token')):
        try:
            api.request('/v1/test')
        except RuntimeError as exc:
            assert 'secret-token' not in str(exc)
        else:
            raise AssertionError('Expected a sanitized failure')
