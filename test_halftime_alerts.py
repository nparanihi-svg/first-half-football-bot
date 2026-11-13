import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import patch

import halftime_alerts as h
from win365_ticket import PREFIX


def setup(monkeypatch,tmp_path):
    path=tmp_path/'test.db'
    monkeypatch.setenv('SIGNAL_DB_PATH',str(path))
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN','test-token')
    monkeypatch.setenv('TELEGRAM_CHAT_ID','primary')
    monkeypatch.setenv('TELEGRAM_EXTRA_CHAT_IDS','secondary')
    monkeypatch.setenv('ENABLE_HALFTIME_H2H_ALERTS','true')
    f={'fixture':{'id':-1,'status':{'short':'1H','elapsed':25}},'goals':{'home':0,'away':0},'league':{}}
    s=SimpleNamespace(status='1H',fixture_id=-1,elapsed=25,home='Home FC',away='Away FC',league='Test')
    p={'entry_score':[0,0],'enhanced':{'result':{'Home':.2,'Draw':.6,'Away':.2}},'odds':None}
    with h.connect() as db:
        db.execute('CREATE TABLE halftime_shadow(fixture_id,checkpoint,minute,payload,home_ht,away_ht)')
        db.execute('INSERT INTO halftime_shadow VALUES(-1,25,25,?,NULL,NULL)',(json.dumps(p),))
    return path,f,s


def test_delivery_retry_and_in_place_settlement(monkeypatch,tmp_path):
    path,f,s=setup(monkeypatch,tmp_path)
    calls=[]
    def send(token,dest,photo,caption,**kw):
        calls.append((dest,kw))
        if dest=='secondary' and len(calls)==2:raise RuntimeError('temporary')
        return {'result':{'message_id':len(calls)}}
    with patch('alerts.ticket_photo',side_effect=send):
        h.send_entry(f,s)
        h.send_entry(f,s)
        assert [d for d,k in calls]==['primary','secondary','secondary']
        api=SimpleNamespace(fixture=lambda fid:{'fixture':{'id':fid,'status':{'short':'2H'}},'score':{'halftime':{'home':0,'away':0}}})
        h.settle_and_update(api,[])
        assert all(k.get('message_id') for d,k in calls[-2:])
        h.settle_and_update(api,[])
        assert len(calls)==5
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT result FROM halftime_live_tickets').fetchone()==('WIN',)
        assert db.execute('SELECT SUM(updated) FROM halftime_live_messages').fetchone()==(2,)


def test_no_backfilled_or_score_changed_alert(monkeypatch,tmp_path):
    path,f,s=setup(monkeypatch,tmp_path)
    with patch('alerts.ticket_photo') as send:
        s.elapsed=26
        h.send_entry(f,s)
        s.elapsed=25
        f['goals']['home']=1
        h.send_entry(f,s)
        assert not send.called


def test_disabled_and_explicit_halftime_required(monkeypatch,tmp_path):
    path,f,s=setup(monkeypatch,tmp_path)
    monkeypatch.setenv('ENABLE_HALFTIME_H2H_ALERTS','false')
    with patch('alerts.ticket_photo') as send:
        h.send_entry(f,s)
        assert not send.called
    monkeypatch.setenv('ENABLE_HALFTIME_H2H_ALERTS','true')
    with patch('alerts.ticket_photo',return_value={'result':{'message_id':1}}):h.send_entry(f,s)
    api=SimpleNamespace(fixture=lambda fid:{'fixture':{'id':fid,'status':{'short':'2H'}},'goals':{'home':2,'away':0}})
    h.settle_and_update(api,[])
    with sqlite3.connect(path) as db:assert db.execute('SELECT result FROM halftime_live_tickets').fetchone()==(None,)
