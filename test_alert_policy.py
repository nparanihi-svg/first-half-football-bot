import json
import sqlite3
from decimal import Decimal
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest
import scanner
from alert_policy import quote_issue, record_decision
from betsapi_provider import normalize, statistics
from provider import parse_fixture
from signal_rules import watchlist_reason
from test_betsapi_provider import event
from win365_ticket import ticket_body


def test_lower_bound_qualifies_without_inventing_total_or_blocked():
    e = event()
    del e['stats']['shots_blocked']
    f = normalize(e, '99')
    s = parse_fixture(f, statistics(f))
    assert s.total_shots is None and s.blocked_shots is None
    assert s.shots_lower_bound == 7
    assert scanner.signal_data_issue(s, 'FH_OVER_0_5_GOALS') is None
    assert watchlist_reason(s, 'FH_OVER_0_5_GOALS')
    assert watchlist_reason(s, 'FH_OVER_2_5_CORNERS') is None
    assert watchlist_reason(s, 'FH_OVER_2_5_CORNERS', require_blocked=False)
    body = ticket_body(s, '1H Over 0.5 Goals', .65, f, None)
    assert 'At least 7 shots' in body
    e['stats']['off_target'][1] = ''
    s = parse_fixture(f, statistics(normalize(e, '99')))
    assert s.shots_lower_bound is None
    assert scanner.signal_data_issue(s, 'FH_OVER_0_5_GOALS')


@pytest.mark.parametrize('price,p,allowed', [(1.5,.70,True),(2,.60,True),(1.49,.9,False),
    (2.01,.9,True),(1.5,.5,False),(2,.5,False),(float('nan'),.8,False)])
def test_price_and_value_boundaries(price,p,allowed):
    assert (quote_issue(p,(price,datetime.now(timezone.utc))) is None) == allowed
    assert quote_issue(p,None)


def test_multiple_scans_same_minute_and_immutable_entry():
    d = sqlite3.connect(':memory:')
    s = parse_fixture(normalize(event(), '99'), statistics(normalize(event(), '99')))
    r = {'market':'FH_OVER_0_5_GOALS','probability':.7}
    record_decision(d,s,r,[], 'first scan')
    s.total_shots = 10
    record_decision(d,s,r,[], 'delivered', delivered=True)
    s.total_shots = 12
    record_decision(d,s,r,[], 'delivered', delivered=True)
    assert d.execute('SELECT COUNT(*) FROM scan_decisions').fetchone()[0] == 3
    entry = json.loads(d.execute('SELECT payload FROM alert_inputs').fetchone()[0])
    assert entry['snapshot']['total_shots'] == 10


@pytest.mark.parametrize('price,expected', [(None,False),(Decimal('4.10'),True),
    (Decimal('1.45'),False),(Decimal('1.75'),True)])
def test_scanner_enforces_gates_before_telegram(monkeypatch,tmp_path,price,expected):
    import alerts
    import shadow_v55
    e=event(); f=normalize(e,'99')
    api=Mock();api.live.return_value=[f];api.first_half_stats.return_value=statistics(f)
    api.get.return_value=[]
    monkeypatch.setenv('LIVE_DATA_PROVIDER','api-football')
    monkeypatch.setenv('API_FOOTBALL_KEY','test')
    monkeypatch.setenv('ENABLE_RESEARCH_ALERTS','true')
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN','test')
    monkeypatch.setenv('TELEGRAM_CHAT_ID','test')
    monkeypatch.setattr(scanner,'FootballAPI',Mock(return_value=api))
    monkeypatch.setattr(scanner,'STATE_DB',str(tmp_path/'state.db'))
    for name in ('record','record_goal_candidate','notify_early_win','verify_one','deliver_result_notices','_record_v58_shadow'):
        monkeypatch.setattr(scanner,name,Mock())
    monkeypatch.setattr(scanner.halftime_shadow,'record_prediction',Mock())
    monkeypatch.setattr(scanner.halftime_shadow,'settle_predictions',Mock())
    monkeypatch.setattr(shadow_v55,'record_shadow',Mock())
    result={'market':'FH_OVER_0_5_GOALS','label':'1H Over 0.5 Goals','probability':.70,
            'baseline_probability':.65,'minutes_remaining':25,'intensity_multiplier':1,'inputs_used':[]}
    monkeypatch.setattr(scanner,'predict',Mock(return_value=[result]))
    quote=None if price is None else (price,datetime.now(timezone.utc))
    monkeypatch.setattr(scanner,'first_half_goals_quote',Mock(return_value=quote))
    monkeypatch.setattr(scanner,'alert_record',Mock(return_value='Test'))
    monkeypatch.setattr(scanner,'render_ticket',Mock(return_value=b'image'))
    send=Mock();monkeypatch.setattr(alerts,'send_telegram',send)
    scanner.scan()
    assert send.called == expected
    with sqlite3.connect(scanner.STATE_DB) as db:
        assert db.execute('SELECT COUNT(*) FROM sent_alerts').fetchone()[0] == int(expected)
        assert db.execute('SELECT COUNT(*) FROM scan_decisions').fetchone()[0] == 2


def test_partial_team_blocked_field_keeps_minimum_count():
    e=event();e['stats']['shots_blocked'][1]=''
    s=parse_fixture(normalize(e,'99'),statistics(normalize(e,'99')))
    assert s.total_shots is None
    assert s.shots_lower_bound == 8
    assert s.blocked_shots is None
