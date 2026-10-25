"""V5 research quality audit. Read-only; no bookmaker odds, no trading."""
import os
import sqlite3
from pathlib import Path
DB = Path(os.getenv('SIGNAL_DB_PATH', '/var/data/first_half_v3.sqlite3'))

def report():
    if not DB.is_file():
        print('No existing observation database at', DB)
        return
    with sqlite3.connect(f'file:{DB}?mode=ro', uri=True) as con:
        rows = con.execute("""SELECT fixture_id, market, minute, result, probability,
            first_half_goals, first_half_corners, shots, shots_on_target
            FROM observations ORDER BY fixture_id, market, minute""").fetchall()
    groups = {}
    for fid, market, minute, outcome, probability, goals, corners, shots, sot in rows:
        groups.setdefault((fid,market), []).append((minute,outcome,probability,goals,corners,shots,sot))
    print('FIRST HALF SIGNAL LAB | V5 DATA QUALITY AUDIT')
    print('Observations:', len(rows), '| Independent fixture/market groups:', len(groups))
    for market in ('FH_OVER_0_5_GOALS','FH_OVER_2_5_CORNERS'):
        selected={k:v for k,v in groups.items() if k[1]==market}
        settled=[]; incomplete=0; already_decided=0
        for (fid,_),obs in selected.items():
            first=obs[0]
            if first[5] is None or first[6] is None or (market=='FH_OVER_2_5_CORNERS' and first[4] is None):
                incomplete += 1
            if (market=='FH_OVER_2_5_CORNERS' and first[4] is not None and first[4]>=3) or (market=='FH_OVER_0_5_GOALS' and first[3] is not None and first[3]>=1):
                already_decided += 1
            states={r[1] for r in obs}
            if states=={'WIN'} or states=={'LOSS'}:
                y=1 if states=={'WIN'} else 0
                prob=first[2]
                if prob is not None and 0<=prob<=1:
                    settled.append((prob,y))
        print('\n', market, sep='')
        print('  Independent fixtures:',len(selected),'| fully settled:',len(settled))
        print('  Earliest observation missing shot data:',incomplete)
        print('  Earliest observation after target already reached:',already_decided)
        if settled:
            score=sum((prob-y)**2 for prob,y in settled)/len(settled)
            print('  Earliest-estimate Brier:',round(score,4),'(n=',len(settled),')')
        else:
            print('  No fully settled probability estimates yet')
    print('\nExperimental estimates are NOT calibrated or verified bookmaker edges.')
    print('Multiple fixtures can share teams/competitions; independence is approximate.')

if __name__=='__main__':
    report()
