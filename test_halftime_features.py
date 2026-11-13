import json
import sqlite3
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
from unittest.mock import Mock

from betsapi_history import prepare,cached,historical_fixture
from halftime_features import observe,adjusted_rates
from halftime_odds import parse
import halftime_shadow as shadow
from test_betsapi_provider import event
from betsapi_provider import normalize,statistics
from provider import parse_fixture


def fixture():return normalize(event(),'99')


def stats(home_sot=1,away_sot=1,hr=None,ar=None):
    rows=[]
    for tid,sot,red in [(-1,home_sot,hr),(-2,away_sot,ar)]:
        values=[{'type':'Shots on Goal','value':sot},{'type':'Shots off Goal','value':2}]
        if red is not None:values.append({'type':'Red Cards','value':red})
        rows.append({'team':{'id':tid},'statistics':values})
    return rows


def test_pressure_uses_only_prior_windows_and_excludes_corrections():
    d=sqlite3.connect(':memory:');f=fixture()
    observe(d,f,15,stats(1,0));observe(d,f,20,stats(2,1))
    c=observe(d,f,25,stats(4,2))
    assert c['recent']['5']['deltas']['home_sot']==2
    assert c['recent']['10']['deltas']['home_sot']==3
    revised=observe(d,f,26,stats(0,2))
    assert revised['recent']['5']['deltas']['home_sot'] is None
    assert observe(d,f,40,stats())['recent']['5']['from_minute'] is None


def test_red_cards_and_trailing_state_change_shadow_only():
    d=sqlite3.connect(':memory:');f=fixture()
    c=observe(d,f,25,stats(2,1,1,0))
    mean,share,used=adjusted_rates((0,1),25,None,c)
    assert share<.5
    assert 'red_cards' in used and 'score_state' in used
    assert mean>0
    missing=observe(d,f,26,stats())
    assert 'red_cards' not in adjusted_rates((0,0),26,None,missing)[2]


def past_event(fid,days,team=1):
    e=event();e.update(id=str(fid),time_status='3',time=str(int((datetime(2026,11,10,tzinfo=timezone.utc)-timedelta(days=days)).timestamp())),scores={'1':{'home':'1','away':'0'}})
    e['home']['id']=str(team);e['away']['id']=str(3 if team==2 else 2);return e


def test_betsapi_history_cache_ids_pre_kickoff_and_budget(tmp_path,monkeypatch):
    f=fixture();api=SimpleNamespace(path=str(tmp_path/'db'),request=Mock())
    api.request.side_effect=[[past_event(i,i,1) for i in range(1,8)]+[past_event(99,-20,1)], [past_event(i+20,i,2) for i in range(1,8)]]
    prepare(api,f)
    assert api.request.call_count==2
    rates=cached(f,api.path)
    assert rates and rates[2]['provider']=='BetsAPI'
    assert rates[2]['home']['matches']==7
    monkeypatch.setenv('SIGNAL_DB_PATH',api.path)
    from halftime_history import cached_rates
    assert cached_rates(f)==rates
    prepare(api,f)
    assert api.request.call_count==2
    assert historical_fixture(past_event(1,-20)) is not None # filtered by team_rates against kickoff
    bad=past_event(2,1);bad['scores']={}
    assert historical_fixture(bad) is None
    other=fixture();other['teams']['home']['id']=-88
    prepare(api,other);assert api.request.call_count==2


def test_halftime_quotes_reject_fulltime_and_suspended_and_orient_away():
    f=fixture();groups=[[
        {'type':'EV','SS':'0-0','TM':'20'},
        {'type':'MG','NA':'Fulltime Result','SU':'0'}, {'type':'MA'},
        {'type':'PA','NA':'Draw','OD':'1/1','SU':'0'},
        {'type':'MG','NA':'Half Time Result','SU':'0'},{'type':'MA'},
        {'type':'PA','NA':'Draw','OD':'3/4','SU':'0'},
        {'type':'PA','NA':'Home','OD':'2/1','SU':'1'},
        {'type':'MG','NA':'Half Time Correct Score','SU':'0'},
        {'type':'MA','NA':'Away'}, {'type':'PA','NA':'1-0','OD':'4/1','SU':'0'}]]
    q=parse(groups,f)
    assert q['result']=={'Draw':1.75}
    assert q['scores']=={'0-1':5}
    assert parse([[{'type':'MG','NA':'Fulltime Result','SU':'0'}]],f)['missing_reason']


def test_shadow_capture_features_history_and_prices_once(tmp_path,monkeypatch):
    monkeypatch.setattr(shadow,'DB_PATH',str(tmp_path/'db'))
    monkeypatch.setattr(shadow,'cached_rates',lambda f:(.8,.4,{'provider':'BetsAPI'}))
    f=fixture();s=parse_fixture(f,statistics(f));s.elapsed=20
    api=SimpleNamespace(prepare_halftime_shadow=Mock(),halftime_quotes=Mock(return_value={'result':{'Draw':1.8},'scores':{}}))
    shadow.record_prediction(f,s,stats(1,0),api=api)
    s.elapsed=25;shadow.record_prediction(f,s,stats(3,1,0,0),api=api)
    shadow.record_prediction(f,s,stats(5,1,0,0),api=api)
    with sqlite3.connect(shadow.DB_PATH) as d:
        p=json.loads(d.execute('SELECT payload FROM halftime_shadow').fetchone()[0])
        assert p['features']['recent']['5']['deltas']['home_sot']==2
        assert 'enhanced' in p and p['entry_score']==[0,0]
        assert p['history_status']=='available'
        assert p['odds']['result']['Draw']==1.8
    api.halftime_quotes.assert_called_once()


def test_shadow_missing_history_or_odds_does_not_stop_forecast(tmp_path,monkeypatch):
    monkeypatch.setattr(shadow,'DB_PATH',str(tmp_path/'db'))
    monkeypatch.setattr(shadow,'cached_rates',lambda f:None)
    f=fixture();s=parse_fixture(f,statistics(f));s.elapsed=25
    api=SimpleNamespace(prepare_halftime_shadow=Mock(side_effect=RuntimeError('quota')),halftime_quotes=Mock(side_effect=RuntimeError('unavailable')))
    shadow.record_prediction(f,s,[],api=api)
    with sqlite3.connect(shadow.DB_PATH) as d:
        p=json.loads(d.execute('SELECT payload FROM halftime_shadow').fetchone()[0])
    assert p['history'] is None and p['odds'] is None and p['enhanced']


def test_shadow_quotes_do_not_attach_after_score_change():
    f=fixture()
    q=parse([[{'type':'EV','SS':'1-0','TM':'20'},
              {'type':'MG','NA':'Half Time Result','SU':'0'},
              {'type':'MA'}, {'type':'PA','NA':'Draw','OD':'1/1','SU':'0'}]],f)
    assert not q['result'] and 'not matched' in q['missing_reason']
