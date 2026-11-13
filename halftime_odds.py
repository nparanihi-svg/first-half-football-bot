"""Strict halftime-only quotes. Unknown names/layouts stay unavailable."""
import re
from fractions import Fraction
from datetime import datetime,timezone

RESULT_TITLES={'Half Time Result','Halftime Result','1st Half Result'}
SCORE_TITLES={'Half Time Correct Score','Halftime Correct Score','1st Half Correct Score'}


def parse(groups,fixture):
    result={};scores={};titles=[]
    # A price response must describe the same score and first-half minute.
    # Never attach later odds after a goal to an earlier forecast.
    event=next((r for group in groups if isinstance(group,list) for r in group if r.get('type')=='EV'),None)
    expected=fixture.get('goals') or {}
    try:
        minute=int(event['TM']);observed=int(fixture['fixture']['status']['elapsed'])
        valid=(fixture['fixture']['status']['short']=='1H' and minute<45
               and abs(minute-observed)<=1
               and event.get('SS')==f"{expected['home']}-{expected['away']}")
    except (KeyError,ValueError,TypeError):valid=False
    if not valid:
        return {'result':{},'scores':{},'missing_reason':'price response score/minute not matched'}

    names={fixture['teams'][s]['name'].casefold():label for s,label in [('home','Home'),('away','Away')]}
    names.update({'home':'Home','away':'Away','draw':'Draw'})
    for group in groups:
        if not isinstance(group,list):continue
        market=None;active=False;column=None;column_active=False
        for row in group:
            if row.get('type')=='MG':
                title=row.get('NA','');titles.append(title)
                market='result' if title in RESULT_TITLES else 'score' if title in SCORE_TITLES else None
                active=row.get('SU')=='0';column=None;column_active=False
            elif row.get('type')=='MA':
                column=names.get(row.get('NA','').casefold())
                column_active=row.get('SU','0')=='0'
            elif row.get('type')=='PA' and market and active and column_active and row.get('SU')=='0':
                try:
                    price=float(1+Fraction(row['OD']))
                    if not 1<price<10000:continue
                except (KeyError,ValueError,ZeroDivisionError,TypeError):continue
                label=row.get('NA','').strip()
                if market=='result':
                    side=names.get(label.casefold())
                    if side:result[side]=price
                elif column in ('Home','Away','Draw'):
                    pair=re.fullmatch(r'(\d+)\s*[-:]\s*(\d+)',label)
                    if not pair:continue
                    h,a=map(int,pair.groups())
                    if column=='Away':h,a=a,h
                    if (column=='Home' and h<=a) or (column=='Away' and a<=h) or (column=='Draw' and h!=a):continue
                    scores[f'{h}-{a}']=price
    return {'received_at':datetime.now(timezone.utc).isoformat(),'timestamp_kind':'response_receipt',
            'result':result,'scores':scores,'market_titles':titles,
            'missing_reason':None if result or scores else 'halftime markets absent, suspended, or unmapped'}
