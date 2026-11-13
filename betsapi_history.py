"""Bounded BetsAPI history cache; IDs never cross providers."""
import json
import sqlite3
import time
from datetime import datetime, timezone
from halftime_history import team_rates


def historical_fixture(event):
    try:
        half=(event.get('scores') or {}).get('1') or {}
        if str(event.get('time_status')) != '3': return None
        h,a=int(half['home']),int(half['away'])
        if min(h,a)<0:return None
        return {'fixture':{'id':'betsapi-event:'+str(event['id']),
                    'date':datetime.fromtimestamp(int(event['time']),timezone.utc).isoformat(),
                    'status':{'short':'FT'}},
                'teams':{s:{'id':-int(event[s]['id'])} for s in ('home','away')},
                'score':{'halftime':{'home':h,'away':a}}}
    except (KeyError,TypeError,ValueError,OverflowError):return None


def prepare(api,fixture):
    """At most two new team requests per scan; successful/empty cache lasts 12h."""
    if not hasattr(api,'_history_requests'):api._history_requests=0
    with sqlite3.connect(api.path,timeout=2) as db:
        db.execute('CREATE TABLE IF NOT EXISTS betsapi_team_history(team_id INTEGER PRIMARY KEY,fetched REAL,payload TEXT)')
        for side in ('home','away'):
            tid=fixture['teams'][side]['id']
            row=db.execute('SELECT fetched FROM betsapi_team_history WHERE team_id=?',(tid,)).fetchone()
            if row and time.time()-row[0]<43200:continue
            if api._history_requests>=2:break
            api._history_requests+=1
            events=api.request('/v3/events/ended',sport_id=1,team_id=-tid,skip_esports=1,page=1)
            fixtures=[f for e in events if (f:=historical_fixture(e)) is not None]
            db.execute('INSERT OR REPLACE INTO betsapi_team_history VALUES(?,?,?)',(tid,time.time(),json.dumps(fixtures)))
            db.commit()


def cached(fixture,path):
    try:
        kickoff=datetime.fromisoformat(fixture['fixture']['date'].replace('Z','+00:00'))
        rates=[]
        with sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True,timeout=2) as db:
            for side in ('home','away'):
                tid=fixture['teams'][side]['id']
                row=db.execute('SELECT payload,fetched FROM betsapi_team_history WHERE team_id=?',(tid,)).fetchone()
                if row is None or time.time()-row[1]>86400:return None
                r=team_rates(json.loads(row[0]),tid,side,kickoff,fixture['fixture']['id'])
                if r is None:return None
                rates.append(r)
        h,a=rates
        return (h['scored']+a['conceded'])/2,(a['scored']+h['conceded'])/2,{'version':2,'provider':'BetsAPI','home':h,'away':a}
    except (sqlite3.Error,KeyError,ValueError,TypeError):return None
