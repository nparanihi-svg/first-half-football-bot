"""Prospective, bounded experimental features. Never used by live alerts."""
import json
import math


def values(fixture,stats):
    out={}
    for side in ('home','away'):
        tid=(fixture.get('teams',{}).get(side) or {}).get('id')
        item=next((r for r in stats if (r.get('team') or {}).get('id')==tid),{})
        fields={s.get('type'):s.get('value') for s in item.get('statistics',[])}
        for name,key in [('Shots on Goal','sot'),('Shots off Goal','off'),('Red Cards','reds')]:
            try:
                n=float(fields[name]);out[side+'_'+key]=n if math.isfinite(n) and n>=0 else None
            except (KeyError,ValueError,TypeError):out[side+'_'+key]=None
    return out


def observe(db,fixture,minute,stats):
    db.execute('CREATE TABLE IF NOT EXISTS halftime_pressure(fixture_id INTEGER,minute INTEGER,payload TEXT,PRIMARY KEY(fixture_id,minute))')
    current=values(fixture,stats);fid=fixture['fixture']['id']
    # Earliest sample of each minute avoids retroactively revising old windows.
    db.execute('INSERT OR IGNORE INTO halftime_pressure VALUES(?,?,?)',(fid,minute,json.dumps(current)))
    windows={}
    for span in (5,10):
        row=db.execute('SELECT minute,payload FROM halftime_pressure WHERE fixture_id=? AND minute BETWEEN ? AND ? ORDER BY minute DESC LIMIT 1',(fid,minute-span-2,minute-span)).fetchone()
        delta={}
        if row:
            old=json.loads(row[1])
            for key in ('home_sot','away_sot','home_off','away_off'):
                x,y=current.get(key),old.get(key)
                delta[key]=x-y if x is not None and y is not None and x>=y else None
        windows[str(span)]={'from_minute':row[0] if row else None,'to_minute':minute,'deltas':delta}
    return {'version':1,'current':current,'recent':windows}


def adjusted_rates(score,minute,history,context):
    base=(history[:2] if history else (.575,.575))
    rates=list(base);used=[]
    # Blend rates per five minutes; overlapping windows are deliberately
    # downweighted and capped, not counted as independent shots.
    for key,weight,low,high,expected in [('sot',.08,.85,1.20,1),('off',.02,.95,1.05,1)]:
        windows=[]
        for span,w in ((5,.65),(10,.35)):
            win=context['recent'][str(span)]
            delta=win['deltas'];start=win['from_minute']
            if start is not None and all(delta.get(s+'_'+key) is not None for s in ('home','away')):
                windows.append((w,[delta[s+'_'+key]*5/(minute-start) for s in ('home','away')]))
        if windows:
            for i in (0,1):
                rate=sum(w*vals[i] for w,vals in windows)/sum(w for w,vals in windows)
                rates[i]*=max(low,min(high,1+weight*(rate-expected)))
            used.append('recent_'+'_'.join(str(span) for span in (5,10) if context['recent'][str(span)]['from_minute'] is not None)+'m_'+key)
    reds=context['current']
    if all(reds.get(s+'_reds') is not None for s in ('home','away')):
        hr,ar=reds['home_reds'],reds['away_reds']
        rates[0]*=max(.5,min(1.5,.8**hr*1.12**ar))
        rates[1]*=max(.5,min(1.5,.8**ar*1.12**hr))
        used.append('red_cards')
    if score[0]!=score[1]:
        behind=0 if score[0]<score[1] else 1
        rates[behind]*=1.08;rates[1-behind]*=.96
        used.append('score_state')
    # Keep the unvalidated adjustments bounded relative to their own prior.
    rates=[max(b*.65,min(b*1.4,r)) for b,r in zip(base,rates)]
    remaining=[r*max(0,45-minute)/45 for r in rates]
    return sum(remaining),rates[0]/sum(rates),used
