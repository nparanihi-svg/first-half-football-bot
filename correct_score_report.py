"""Read-only matched correct-score audit; no threshold tuning or hindsight rewrite."""
import json
import os
import sqlite3
from collections import defaultdict
from pathlib import Path


def summarize(rows):
    groups = defaultdict(lambda: {'n':0,'model':0,'unchanged':0,'top3':0,'confidence':0.0})
    for checkpoint,raw,h,a in rows:
        if h is None or a is None:
            continue
        p=json.loads(raw)
        if not p.get('enhanced') or p.get('entry_score') is None:
            continue
        scores=p['enhanced'].get('scores') or []
        if not scores:
            continue
        pick=scores[0]
        confidence=pick[2]
        band='under 50%' if confidence<.5 else '50–69%' if confidence<.7 else '70%+'
        for key in [(checkpoint,'all'),(checkpoint,band)]:
            g=groups[key];g['n']+=1
            g['model']+=list(pick[:2])==[h,a]
            g['unchanged']+=p['entry_score']==[h,a]
            g['top3']+=any(list(s[:2])==[h,a] for s in scores)
            g['confidence']+=confidence
    return groups


def report(db):
    rows=db.execute('SELECT checkpoint,payload,home_ht,away_ht FROM halftime_shadow').fetchall()
    print('Checkpoint | confidence band | n | model hits | unchanged-score hits | top3 hits | mean estimate')
    for (cp,band),g in sorted(summarize(rows).items()):
        print(cp,band,g['n'],g['model'],g['unchanged'],g['top3'],f"{g['confidence']/g['n']:.1%}",sep=' | ')
    print('Same settled forecasts compared. Checkpoints from the same game are correlated.')
    print('Confidence bands are diagnostic, not validated selection thresholds. Odds and profit not inferred.')


if __name__=='__main__':
    path=Path(os.getenv('SIGNAL_DB_PATH','/var/data/first_half_v3.sqlite3')).resolve()
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
        report(db)
