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
