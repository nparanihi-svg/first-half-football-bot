"""Provisional early-over alerts. Final verified outcomes are settled at HT."""
import os
import sqlite3
from datetime import datetime, timezone

DB_PATH = os.getenv('SIGNAL_DB_PATH', '/var/data/first_half_v3.sqlite3')
MARKETS = (
    ('FH_OVER_0_5_GOALS', '1H Over 0.5 Goals', 'first-half goal'),
    ('FH_OVER_2_5_CORNERS', '1H Over 2.5 Corners', 'third first-half corner'),
)


def notify_early_win(snapshot):
    """Send at most one provisional milestone for each alerted fixture/market."""
    if os.getenv('ENABLE_RESEARCH_ALERTS', 'false').lower() != 'true':
        return 0
    token = os.getenv('TELEGRAM_BOT_TOKEN', '')
    chat_id = os.getenv('TELEGRAM_CHAT_ID', '')
    if not token or not chat_id:
        return 0
    if not isinstance(snapshot.elapsed, int) or snapshot.elapsed >= 45:
        return 0
    seen_markets = []
    if type(snapshot.half_goals) is int and snapshot.half_goals >= 1:
        seen_markets.append(MARKETS[0])
    if type(snapshot.half_corners) is int and snapshot.half_corners >= 3:
        seen_markets.append(MARKETS[1])
    if not seen_markets:
        return 0
    with sqlite3.connect(DB_PATH, timeout=20) as db:
        db.execute('''CREATE TABLE IF NOT EXISTS early_win_notices (
            fixture_id INTEGER NOT NULL, market TEXT NOT NULL, sent_at TEXT NOT NULL,
            PRIMARY KEY (fixture_id, market))''')
        selected_meta = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='alert_details'").fetchone()
        delivered = 0
        for market, label, event in seen_markets:
            has_alert = db.execute('SELECT 1 FROM sent_alerts WHERE fixture_id=? AND market=?',
                                   (snapshot.fixture_id, market)).fetchone()
            prior = db.execute('SELECT 1 FROM early_win_notices WHERE fixture_id=? AND market=?',
                               (snapshot.fixture_id, market)).fetchone()
            if not has_alert or prior:
                continue
            selected = (db.execute('SELECT selected_minute FROM alert_details WHERE fixture_id=? AND market=?',
                                   (snapshot.fixture_id, market)).fetchone() if selected_meta else None)
            selected_label = (f"Selected at {selected[0]}'" if selected else 'Selection minute not recorded')
            message = ('✅ EARLY WIN CONDITION REACHED (provisional)\n'
                       f'{snapshot.home} vs {snapshot.away}\n'
                       f'{label}\n'
                       f'{selected_label} | Confirmed in feed at {snapshot.elapsed}\'\n'
                       f'Live milestone: {event}\n'
                       'Subject to data corrections and final half-time verification. '
                       'Not confirmation of bookmaker settlement.')
            from alerts import send_telegram
            send_telegram(token, chat_id, message)
            db.execute('INSERT OR IGNORE INTO early_win_notices VALUES (?,?,?)',
                       (snapshot.fixture_id, market, datetime.now(timezone.utc).isoformat()))
            db.commit()
            delivered += 1
            print('V5.3 early milestone notice:', snapshot.fixture_id, market, flush=True)
    return delivered
