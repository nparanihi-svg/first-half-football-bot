from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from win365_alerts import alert_body
from win365_live_odds import first_half_goals_quote

NOW = datetime(2026, 11, 9, 16, 36, tzinfo=timezone.utc)
UPDATED = NOW - timedelta(seconds=32)


@pytest.fixture
def live_row():
    return {
        'fixture': {'id': 1509214, 'status': {'long': 'First Half'}},
        'update': UPDATED.isoformat(),
        'odds': [{'id': 49, 'values': [
            {'value': 'Over', 'handicap': '0.5', 'odd': '2.75', 'suspended': False},
        ]}],
    }


def test_matching_quote_appears_in_telegram_body(live_row):
    quote = first_half_goals_quote([live_row, live_row], 1509214, NOW)
    assert quote == (Decimal('2.75'), UPDATED)
    snapshot = SimpleNamespace(home='Home', away='Away', league='League',
                               elapsed=18, half_corners=1)
    body = alert_body(snapshot, '1H Over 0.5 Goals', .65, {'home': 0, 'away': 0}, quote=quote)
    assert 'API-Football live odds 2.75 · Over 0.5 matched · 00:35:28 Perth' in body
    assert 'Score 0–0' in body


@pytest.mark.parametrize('changes', [
    {'value': 'Under'}, {'handicap': '1.5'}, {'suspended': True},
    {'suspended': None}, {'odd': 'NaN'}, {'odd': '1.0'},
])
def test_wrong_or_unusable_selection(live_row, changes):
    live_row['odds'][0]['values'][0].update(changes)
    assert first_half_goals_quote([live_row], 1509214, NOW) is None


def test_rejects_wrong_fixture_market_period_and_stale_update(live_row):
    assert first_half_goals_quote([live_row], 999, NOW) is None
    assert first_half_goals_quote([live_row], 1509214, NOW + timedelta(seconds=29)) is None
    live_row['odds'][0]['id'] = 51
    assert first_half_goals_quote([live_row], 1509214, NOW) is None
    live_row['odds'][0]['id'] = 49
    live_row['fixture']['status']['long'] = 'Halftime'
    assert first_half_goals_quote([live_row], 1509214, NOW) is None


def test_rejects_conflicting_active_prices(live_row):
    values = live_row['odds'][0]['values']
    values.append({**values[0], 'odd': '2.80'})
    assert first_half_goals_quote([live_row], 1509214, NOW) is None


def test_corners_require_exact_first_half_line(live_row):
    from win365_live_odds import first_half_corners_quote
    live_row['odds'][0]['id'] = 204
    selection = live_row['odds'][0]['values'][0]
    selection['handicap'] = '2.5'
    assert first_half_corners_quote([live_row], 1509214, NOW)[0] == Decimal('2.75')
    for wrong_line in ('2.0', '3.5'):
        selection['handicap'] = wrong_line
        assert first_half_corners_quote([live_row], 1509214, NOW) is None
    selection['handicap'] = '2.5'
    selection['suspended'] = True
    assert first_half_corners_quote([live_row], 1509214, NOW) is None


def test_corner_diagnostics_explain_rejection_without_substituting_line(live_row):
    from win365_live_odds import corners_quote_diagnostics
    assert corners_quote_diagnostics([], 1509214, NOW)[1]['reasons'] == {'empty_response': 1}
    live_row['odds'][0]['id'] = 204
    value = live_row['odds'][0]['values'][0]
    value['handicap'] = '3.5'
    quote, diagnostic = corners_quote_diagnostics([live_row], 1509214, NOW)
    assert quote is None
    assert diagnostic['reasons'] == {'different_line': 1}
    value['handicap'] = '2.5'
    quote, diagnostic = corners_quote_diagnostics([live_row], 1509214, NOW)
    assert quote[0] == Decimal('2.75')
    assert diagnostic['outcome'] == 'accepted'
    quote, diagnostic = corners_quote_diagnostics([live_row], 1509214, NOW + timedelta(seconds=90))
    assert quote is None
    assert 'stale_or_future_update' in diagnostic['reasons']
