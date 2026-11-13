"""Prospective halftime H2H and exact-score research tickets; isolated from goals/corners records."""
import json
import math
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone

from halftime_shadow import CHECKPOINTS, COMPLETED, outcome, score_pair
from win365_ticket import PREFIX, render_ticket


def enabled(kind='h2h'):
    flag = 'ENABLE_HALFTIME_CORRECT_SCORE_ALERTS' if kind == 'score' else 'ENABLE_HALFTIME_H2H_ALERTS'
    return os.getenv(flag, 'true').lower() == 'true'


def names(kind):
    if kind not in ('h2h', 'score'):
        raise ValueError('Unknown halftime market')
    return ('halftime_score_tickets', 'halftime_score_messages', 'HT correct score') if kind == 'score' else ('halftime_live_tickets', 'halftime_live_messages', 'HT H2H')


def connect(kind='h2h'):
    tickets, messages, label = names(kind)
    db = sqlite3.connect(os.getenv('SIGNAL_DB_PATH', '/var/data/first_half_v3.sqlite3'), timeout=10)
    db.execute(f'''CREATE TABLE IF NOT EXISTS {tickets} (
        fixture_id INTEGER, checkpoint INTEGER, minute INTEGER, pick TEXT,
        probability REAL, odds REAL, body TEXT, created_at TEXT,
        result TEXT, home_ht INTEGER, away_ht INTEGER,
        PRIMARY KEY(fixture_id,checkpoint))''')
    db.execute(f'''CREATE TABLE IF NOT EXISTS {messages} (
        fixture_id INTEGER, checkpoint INTEGER, destination TEXT, message_id INTEGER,
        updated INTEGER DEFAULT 0, PRIMARY KEY(fixture_id,checkpoint,destination))''')
    db.commit()
    return db


def destinations():
    return list(dict.fromkeys(x.strip() for x in
        [os.getenv('TELEGRAM_CHAT_ID', '')] + os.getenv('TELEGRAM_EXTRA_CHAT_IDS', '').split(',') if x.strip()))


def tally(db, kind='h2h'):
    tickets, messages, label = names(kind)
    w, l = db.execute(f"SELECT SUM(result='WIN'),SUM(result='LOSS') FROM {tickets} WHERE EXISTS (SELECT 1 FROM {messages} m WHERE m.fixture_id={tickets}.fixture_id AND m.checkpoint={tickets}.checkpoint)").fetchone()
    return f'{label} research only · {w or 0} W / {l or 0} L'


def send_entry(fixture, snapshot, kind='h2h'):
    """Use only a forecast saved during this exact live minute and score state."""
    tickets, messages, market_label = names(kind)
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    if not enabled(kind) or not token or not destinations() or snapshot.status != '1H':
        return
    checkpoint = next((m for m in CHECKPOINTS if m <= snapshot.elapsed < m + 5), None)
    if checkpoint is None:
        return
    fid = snapshot.fixture_id
    with closing(connect(kind)) as db:
        row = db.execute('SELECT minute,payload,home_ht FROM halftime_shadow WHERE fixture_id=? AND checkpoint=?', (fid, checkpoint)).fetchone()
        if not row or row[0] != snapshot.elapsed or row[2] is not None:
            return
        p = json.loads(row[1])
        model = p.get('enhanced')
        score = score_pair(fixture.get('goals') or {})
        if not model or score is None or list(score) != p.get('entry_score'):
            return
        probabilities = model.get('result') or {}
        if set(probabilities) != {'Home','Draw','Away'}:
            return
        if not all(isinstance(v,(int,float)) and math.isfinite(v) and 0 <= v <= 1 for v in probabilities.values()):
            return
        pick = max(probabilities, key=probabilities.get)
        price = ((p.get('odds') or {}).get('result') or {}).get(pick)
        if not isinstance(price,(int,float)) or not math.isfinite(price) or price <= 1:
            price = None
        probability = probabilities[pick]
        if kind == 'score':
            scores = model.get('scores') or []
            if not scores or len(scores[0]) != 3:
                return
            home, away, probability = scores[0]
            if any(type(v) is not int or v < 0 for v in (home, away)):
                return
            if home < score[0] or away < score[1]:
                return
            if not isinstance(probability,(int,float)) or not math.isfinite(probability) or not 0 <= probability <= 1:
                return
            pick = f'{home}-{away}'
            price = ((p.get('odds') or {}).get('scores') or {}).get(pick)
            if not isinstance(price,(int,float)) or not math.isfinite(price) or price <= 1:
                price = None
        label = f'{market_label} · {pick} · {checkpoint}′ checkpoint'
        stats = f'Home {probabilities["Home"]:.0%} · Draw {probabilities["Draw"]:.0%} · Away {probabilities["Away"]:.0%}'
        body = PREFIX + json.dumps(dict(market=label, teams=f'{snapshot.home} vs {snapshot.away}',
            league=snapshot.league,country=(fixture.get('league') or {}).get('country',''),
            entry=f'Selected {snapshot.elapsed}′ · {score[0]}–{score[1]}',stats=stats,
            chance=f'Experimental model chance {probability:.1%}',
            odds=f'Odds {price:.2f}' if price else 'Odds unavailable · check bookmaker',
            value='Research alert · Profitability unproven'))
        db.execute(f'INSERT OR IGNORE INTO {tickets} VALUES (?,?,?,?,?,?,?,?,NULL,NULL,NULL)',
            (fid,checkpoint,snapshot.elapsed,pick,probability,price,body,datetime.now(timezone.utc).isoformat()))
        db.commit()
        saved = db.execute(f'SELECT body,result FROM {tickets} WHERE fixture_id=? AND checkpoint=?',(fid,checkpoint)).fetchone()
        if saved[1] is not None:
            return
        from alerts import ticket_photo
        for destination in destinations():
            if db.execute(f'SELECT 1 FROM {messages} WHERE fixture_id=? AND checkpoint=? AND destination=?',(fid,checkpoint,destination)).fetchone():
                continue
            try:
                result = ticket_photo(token,destination,render_ticket(saved[0],tally(db,kind)),
                    f'WIN365 · {market_label} RESEARCH · {checkpoint}′ checkpoint')
                db.execute(f'INSERT OR IGNORE INTO {messages} VALUES (?,?,?,?,0)',
                    (fid,checkpoint,destination,result['result']['message_id']))
                db.commit()
                print(market_label, 'delivered:',fid,checkpoint,flush=True)
            except Exception as exc:
                print(market_label, 'delivery failed:',type(exc).__name__,flush=True)


def settle_and_update(api, fixtures, kind='h2h'):
    """Recover departed fixtures, verify explicit halftime scores, edit per destination."""
    tickets, messages, market_label = names(kind)
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    if not token:
        return
    with closing(connect(kind)) as db:
        pending = [r[0] for r in db.execute(f'SELECT DISTINCT fixture_id FROM {tickets} WHERE result IS NULL')]
        available = {f['fixture']['id']:f for f in fixtures}
        # Shadow settlements can be completed by the existing tracking path.
        table = db.execute("SELECT 1 FROM sqlite_master WHERE name='halftime_shadow'").fetchone()
        recovered = 0
        for fid in pending:
            score = None
            if table:
                r = db.execute('SELECT home_ht,away_ht FROM halftime_shadow WHERE fixture_id=? AND home_ht IS NOT NULL LIMIT 1',(fid,)).fetchone()
                if r:
                    score = score_pair(dict(zip(('home','away'),r)))
            f = available.get(fid)
            if score is None and f is None and recovered < 3:
                recovered += 1
                try:
                    f = api.fixture(fid)
                except Exception as exc:
                    print('HT H2H settlement deferred:',fid,type(exc).__name__,flush=True)
            if score is None and f and (f.get('fixture',{}).get('status') or {}).get('short') in COMPLETED:
                score = score_pair((f.get('score') or {}).get('halftime') or {})
            if score is not None:
                db.execute(f"UPDATE {tickets} SET result=CASE WHEN pick=? THEN 'WIN' ELSE 'LOSS' END,home_ht=?,away_ht=? WHERE fixture_id=? AND result IS NULL",(f'{score[0]}-{score[1]}' if kind == 'score' else outcome(*score),*score,fid))
                db.commit()
        from alerts import ticket_photo
        for fid,cp,destination,mid,body,result,h,a in db.execute(f'''SELECT t.fixture_id,t.checkpoint,m.destination,m.message_id,t.body,t.result,t.home_ht,t.away_ht
            FROM {tickets} t JOIN {messages} m USING(fixture_id,checkpoint)
            WHERE t.result IS NOT NULL AND m.updated=0 LIMIT 12''').fetchall():
            status = 'WON' if result == 'WIN' else 'LOST'
            try:
                ticket_photo(token,destination,render_ticket(body,tally(db,kind),status=status,detail=f'Halftime confirmed · {h}–{a}'),
                    f'WIN365 · {market_label} · {status}',message_id=mid)
                db.execute(f'UPDATE {messages} SET updated=1 WHERE fixture_id=? AND checkpoint=? AND destination=?',(fid,cp,destination))
                db.commit()
            except Exception as exc:
                print('HT H2H result edit deferred:',fid,cp,type(exc).__name__,flush=True)
