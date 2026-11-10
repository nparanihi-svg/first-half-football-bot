"""Read-only results commands for the configured Telegram owner's private chat."""
import os
import sqlite3
import time
from collections import Counter
from contextlib import closing
from pathlib import Path

import requests

MARKETS = {'FH_OVER_0_5_GOALS': '1H O0.5 goals', 'FH_OVER_2_5_CORNERS': '1H O2.5 corners'}
COMMANDS = {'/results', '/coverage', '/audit'}


def results_reply(db, command):
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'observations' not in tables:
        return 'WIN365 has no saved observations yet.'
    if command == '/coverage':
        rows = db.execute('''WITH minutes AS (
            SELECT fixture_id, minute, MAX(league) AS league,
                MAX(CASE WHEN shots_on_target BETWEEN 0 AND shots THEN 1 ELSE 0 END) AS shots_ok,
                MAX(CASE WHEN first_half_corners >= 0 THEN 1 ELSE 0 END) AS corners_ok
            FROM observations WHERE minute BETWEEN 8 AND 38
              AND julianday(recorded_at) >= julianday('now', '-7 days')
            GROUP BY fixture_id, minute)
            SELECT COALESCE(league,'Unknown'), COUNT(DISTINCT fixture_id), COUNT(*),
                   SUM(shots_ok), SUM(corners_ok)
            FROM minutes GROUP BY league ORDER BY 2 DESC, 1 LIMIT 10''').fetchall()
        return '\n'.join(['📊 WIN365 · Coverage, last 7 days',
                          'Top 10 leagues by fixtures · minutes 8′–38′',
                          'Shots and corners shown per saved match minute.', ''] +
                         [f'{name}: {games} games · shots {shots}/{total} · corners {corners}/{total}'
                          for name, games, total, shots, corners in rows]) if rows else 'No recent coverage data.'
    if command == '/audit':
        if 'blocked_shot_audit' not in tables:
            return 'No blocked-shot audit candidates recorded yet.'
        rows = db.execute('''SELECT a.relaxed_minute, a.strict_minute,
            CASE WHEN MIN(o.result)=MAX(o.result) AND MIN(o.result) IN ('WIN','LOSS')
                 THEN MIN(o.result) ELSE 'Unresolved' END
            FROM blocked_shot_audit a LEFT JOIN observations o
              ON a.fixture_id=o.fixture_id AND o.market='FH_OVER_2_5_CORNERS'
            GROUP BY a.fixture_id''').fetchall()
        counts = Counter()
        for relaxed, strict, outcome in rows:
            group = 'No strict qualifier recorded' if strict is None else 'Delayed' if strict > relaxed else 'Same minute'
            counts[group, outcome] += 1
        if not counts:
            return 'No blocked-shot audit candidates recorded yet.'
        return '\n'.join(['🔎 WIN365 · Blocked-shot audit'] +
                         [f'{group}: {counts[group, "WIN"]} hit / {counts[group, "LOSS"]} missed'
                          f' / {counts[group, "Unresolved"]} unresolved'
                          for group in ('No strict qualifier recorded', 'Delayed', 'Same minute')])
    alert = ("EXISTS (SELECT 1 FROM sent_alerts a WHERE a.fixture_id=o.fixture_id "
             "AND a.market=o.market)" if 'sent_alerts' in tables else '0')
    rows = db.execute(f'''WITH recent AS (
        SELECT fixture_id FROM observations GROUP BY fixture_id
        ORDER BY MAX(recorded_at) DESC, fixture_id DESC LIMIT 20
    )
    SELECT o.fixture_id, o.market, {alert} AS alerted,
        CASE WHEN MIN(result)=MAX(result) AND MIN(result) IN ('WIN','LOSS')
             THEN MIN(result) ELSE 'Unresolved' END
    FROM observations o JOIN recent USING(fixture_id)
    GROUP BY o.fixture_id,o.market''').fetchall()
    if not rows:
        return 'WIN365 has no saved results yet.'
    counts = Counter((market, alerted, outcome) for _, market, alerted, outcome in rows)
    lines = [f'📋 WIN365 · Latest {len({row[0] for row in rows})} recorded fixtures']
    for market, label in MARKETS.items():
        lines.extend(['', label])
        for alerted, status in ((1, 'Alert recorded'), (0, 'No alert record')):
            hits, misses, pending = (counts[market, alerted, result]
                                     for result in ('WIN', 'LOSS', 'Unresolved'))
            rate = f' ({hits / (hits + misses):.0%} settled hit rate)' if hits + misses else ''
            lines.append(f'{status}: {hits} hit / {misses} missed / {pending} unresolved{rate}')
    return '\n'.join(lines)


def private_command(update, owner_id):
    message = update.get('message') or {}
    chat = message.get('chat') or {}
    sender = message.get('from') or {}
    if chat.get('type') != 'private' or chat.get('id') != owner_id or sender.get('id') != owner_id:
        return None
    text = (message.get('text') or '').strip().lower()
    return text if text in COMMANDS else None


def poll_results():
    if os.getenv('ENABLE_TELEGRAM_RESULTS', '').lower() != 'true':
        return
    token = os.getenv('TELEGRAM_BOT_TOKEN', '')
    owner = os.getenv('TELEGRAM_RESULTS_CHAT_ID') or os.getenv('TELEGRAM_CHAT_ID', '')
    if not token or not owner.isdecimal() or int(owner) <= 0:
        print('Telegram results disabled: configure a positive private chat ID.', flush=True)
        return
    owner_id = int(owner)
    path = Path(os.getenv('SIGNAL_DB_PATH', '/var/data/first_half_v3.sqlite3')).resolve()
    base = f'https://api.telegram.org/bot{token}/'
    offset = 0
    with requests.Session() as session:
        while True:
            try:
                response = session.post(base + 'getUpdates', json={
                    'offset': offset, 'timeout': 25, 'allowed_updates': ['message'],
                }, timeout=35)
                response.raise_for_status()
                data = response.json()
                if not data.get('ok'):
                    raise RuntimeError('Telegram rejected polling')
                for update in data['result']:
                    offset = update['update_id'] + 1
                    command = private_command(update, owner_id)
                    if command is None:
                        continue
                    try:
                        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=3)) as db:
                            reply = results_reply(db, command)
                    except sqlite3.Error:
                        reply = 'WIN365 results are temporarily unavailable.'
                    sent = session.post(base + 'sendMessage', json={
                        'chat_id': owner_id, 'text': reply[:4096],
                    }, timeout=10)
                    sent.raise_for_status()
                    if not sent.json().get('ok'):
                        raise RuntimeError('Telegram rejected results reply')
            except (requests.RequestException, ValueError, RuntimeError, KeyError) as exc:
                response = getattr(exc, 'response', None)
                code = response.status_code if response is not None else None
                print('Telegram results polling error:', type(exc).__name__, code, flush=True)
                time.sleep(60 if code == 409 else 10)
