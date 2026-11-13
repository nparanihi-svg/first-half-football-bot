"""Read-only first-entry comparison. Missing historical prices stay unknown."""
import json
import os
import sqlite3
from collections import Counter
from types import SimpleNamespace

from signal_rules import watchlist_reason


def report(db):
    outcomes = {(f,m): lo if lo == hi and lo in ('WIN','LOSS') else 'UNRESOLVED'
                for f,m,lo,hi in db.execute('SELECT fixture_id,market,MIN(result),MAX(result) FROM observations GROUP BY fixture_id,market')}
    entries = {}
    for f,m,when,payload in db.execute('SELECT fixture_id,market,recorded_at,payload FROM scan_decisions ORDER BY recorded_at'):
        data = json.loads(payload)
        snap = SimpleNamespace(**data['snapshot'])
        for threshold in (3,4,5):
            key = (f,m,threshold)
            if key not in entries and watchlist_reason(snap,m,min_shots=threshold):
                entries[key] = {'outcome':outcomes.get((f,m),'UNRESOLVED'), 'quote':None,
                                'scan_id':data['forecast'].get('_scan_id'), 'when':when}
            # Multiple decision rows belong to a scan. Attach only that same
            # instant's quote, never a later market price to an earlier entry.
            if key in entries and (entries[key]['when'] == when or (entries[key]['scan_id'] is not None and entries[key]['scan_id'] == data['forecast'].get('_scan_id'))) and data.get('quote'):
                entries[key]['quote'] = float(data['quote'][0])
            if key in entries: entries[key].setdefault('when',when)
    print('Stats-only comparison; five shots remains the live threshold.')
    print('One first qualifying entry per game, market and threshold; markets reported separately.')
    for market in ('FH_OVER_0_5_GOALS','FH_OVER_2_5_CORNERS'):
        for n in (3,4,5):
            rows = [v for (f,m,t),v in entries.items() if m==market and t==n]
            c=Counter(r['outcome'] for r in rows)
            priced=[r for r in rows if r['quote'] is not None and r['outcome'] in ('WIN','LOSS')]
            pnl=sum(r['quote']-1 if r['outcome']=='WIN' else -1 for r in priced)
            print(market,n,'shots |',dict(c),'| priced settled',len(priced),
                  '| missing prices',sum(r['quote'] is None for r in rows),
                  '| priced-only profit',f'{pnl:+.2f}u' if priced else 'unavailable')
    print('Missing prices prevent a complete profitability comparison; these estimates are not calibration evidence.')


if __name__ == '__main__':
    path=os.getenv('SIGNAL_DB_PATH','/var/data/first_half_v3.sqlite3')
    with sqlite3.connect('file:'+path+'?mode=ro',uri=True) as db:
        report(db)
