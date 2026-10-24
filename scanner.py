"""Minute-by-minute first-half research watchlist with Telegram notifications.

Replaces scanner.py. Uses existing provider.py, engine.py and alerts.py.
Alerts are exploratory signals, not validated betting recommendations.
"""
import os
import sqlite3
from datetime import datetime, timezone

from provider import FootballAPI, parse_fixture
from engine import predict
from signal_rules import watchlist_reason

MAX_FIXTURES_PER_SCAN = 3
STATE_DB = os.getenv('SIGNAL_DB_PATH', '/tmp/first_half_signals.sqlite3')


def claim_alert(fixture_id, market):
    """Claim each match/market once using SQLite unique key.

    Note: Render's default /tmp storage is ephemeral; a restart can resend alerts.
    For durable deduplication, connect persistent storage before production.
    """
    with sqlite3.connect(STATE_DB, timeout=10) as db:
        db.execute('CREATE TABLE IF NOT EXISTS sent_alerts ('
                   'fixture_id INTEGER NOT NULL, market TEXT NOT NULL, '
                   'created_at TEXT NOT NULL, '
                   'PRIMARY KEY(fixture_id, market))')
        try:
            db.execute('INSERT INTO sent_alerts VALUES (?, ?, ?)',
                       (fixture_id, market, datetime.now(timezone.utc).isoformat()))
            return True
        except sqlite3.IntegrityError:
            return False


def release_alert(fixture_id, market):
    with sqlite3.connect(STATE_DB) as db:
        db.execute('DELETE FROM sent_alerts WHERE fixture_id=? AND market=?',
                   (fixture_id, market))


def scan():
    key = os.environ.get('API_FOOTBALL_KEY')
    if not key:
        raise RuntimeError('API-Football key missing')
    api = FootballAPI(key)
    fixtures = api.live()
    print('Scan:', datetime.now(timezone.utc).isoformat(), flush=True)
    print('Live matches:', len(fixtures), flush=True)

    checked = 0
    for fixture in fixtures:
        status = fixture.get('fixture', {}).get('status', {})
        minute = status.get('elapsed') or 0
        if status.get('short') != '1H' or minute >= 45:
            continue
        if checked >= MAX_FIXTURES_PER_SCAN:
            break
        fixture_id = fixture['fixture']['id']
        checked += 1
        try:
            stats = api.first_half_stats(fixture_id)
            snapshot = parse_fixture(fixture, stats)
            estimates = predict(snapshot)
            for result in estimates:
                probability = result['probability']
                print(snapshot.home, 'vs', snapshot.away,
                      '| Minute:', minute, '| Market:', result['label'],
                      '| Estimate:', round(probability * 100, 1), '%', flush=True)
                reason = watchlist_reason(snapshot, result['market'])
                if reason is None:
                    continue
                print('RESEARCH WATCHLIST:', result['label'], '-', reason, flush=True)
                if os.getenv('ENABLE_RESEARCH_ALERTS', 'false').lower() != 'true':
                    continue
                token = os.getenv('TELEGRAM_BOT_TOKEN', '')
                chat_id = os.getenv('TELEGRAM_CHAT_ID', '')
                if not token or not chat_id:
                    print('Telegram credentials missing: research alert skipped', flush=True)
                    continue
                if not claim_alert(fixture_id, result['market']):
                    continue
                from alerts import send_telegram
                message = (
                    'IN-PLAY RESEARCH WATCHLIST (not a bet recommendation)\n'
                    f'{snapshot.home} vs {snapshot.away}\n'
                    f'League: {snapshot.league}\n'
                    f'Minute: {minute}\n'
                    f'Market: {result["label"]}\n'
                    f'Experimental estimate: {probability * 100:.1f}%\n'
                    f'Live observations: {reason}\n'
                    'No bookmaker odds verified. Model not calibrated.'
                )
                try:
                    send_telegram(token, chat_id, message)
                except Exception:
                    release_alert(fixture_id, result['market'])
                    raise
        except Exception as error:
            if ('API_DAILY_RESERVE_REACHED' in str(error)
                    or 'API_MINUTE_LIMIT_NEAR' in str(error)):
                raise
            print('Skipped fixture', fixture_id, type(error).__name__,
                  str(error)[:160], flush=True)
    print('First-half matches checked:', checked, flush=True)


if __name__ == '__main__':
    scan()
    if os.getenv('TELEGRAM_TEST') == 'true':
        from alerts import send_telegram
        send_telegram(os.environ['TELEGRAM_BOT_TOKEN'],
                      os.environ['TELEGRAM_CHAT_ID'],
                      'First Half Signal Lab: GitHub scanner connected!')
