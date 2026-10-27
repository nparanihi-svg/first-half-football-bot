"""Cross-thread/process API-Sports request guard using the existing signal SQLite DB.

One reservation per actual outbound attempt; shared by scanner, history and pre-match.
The limiter is deliberately conservative, and never retries a 429 immediately.
"""
import os
import sqlite3
import time
from pathlib import Path

import requests


class APICooldown(RuntimeError):
    pass


def _db_path():
    return Path(os.getenv('SIGNAL_DB_PATH', '/var/data/first_half_v3.sqlite3'))


def _limit():
    return max(1, min(120, int(os.getenv('API_SHARED_REQUESTS_PER_MINUTE', '30'))))


def _connection():
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(str(path), timeout=15)


def reserve_request():
    """Atomic fixed rolling-minute reservation; reject excess without an HTTP call."""
    now = time.time()
    with _connection() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS api_guard_v571
                      (id INTEGER PRIMARY KEY, requested_at REAL NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS api_guard_state_v571
                      (name TEXT PRIMARY KEY, value REAL NOT NULL)''')
        db.execute('BEGIN IMMEDIATE')
        cooldown = db.execute("SELECT value FROM api_guard_state_v571 WHERE name='cooldown_until'").fetchone()
        if cooldown and cooldown[0] > now:
            raise APICooldown(f'API_SHARED_COOLDOWN seconds_remaining={int(cooldown[0]-now)+1}')
        db.execute('DELETE FROM api_guard_v571 WHERE requested_at <= ?', (now-60,))
        count = db.execute('SELECT COUNT(*) FROM api_guard_v571').fetchone()[0]
        if count >= _limit():
            raise APICooldown('API_SHARED_MINUTE_BUDGET_REACHED')
        db.execute('INSERT INTO api_guard_v571(requested_at) VALUES (?)', (now,))


def set_cooldown(seconds=None):
    duration = max(60, min(900, int(seconds or os.getenv('API_429_COOLDOWN_SECONDS', '120'))))
    until = time.time() + duration
    with _connection() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS api_guard_state_v571
                      (name TEXT PRIMARY KEY, value REAL NOT NULL)''')
        db.execute('BEGIN IMMEDIATE')
        current = db.execute("SELECT value FROM api_guard_state_v571 WHERE name='cooldown_until'").fetchone()
        db.execute("INSERT OR REPLACE INTO api_guard_state_v571(name,value) VALUES ('cooldown_until',?)",
                   (max(until, current[0] if current else 0),))
    print(f'API shared cooldown activated for at least {duration}s after rate-limit response', flush=True)


def _json_rate_limited(response):
    """Recognise API-Sports rateLimit errors returned with HTTP 200.

    Other application-level errors remain handled by the calling adapter.
    """
    try:
        payload = response.json()
    except (ValueError, TypeError):
        return False
    if not isinstance(payload, dict):
        return False
    errors = payload.get('errors')
    if isinstance(errors, dict):
        return any(str(key).lower() == 'ratelimit' and bool(value)
                   for key, value in errors.items())
    return False


def _retry_seconds(response):
    retry = response.headers.get('Retry-After')
    try:
        return int(retry) if retry is not None else None
    except (ValueError, TypeError):
        return None


def guarded_get(session, url, **kwargs):
    reserve_request()
    response = session.get(url, **kwargs)
    if response.status_code == 429:
        set_cooldown(_retry_seconds(response))
        raise APICooldown('API_HTTP_429_SHARED_COOLDOWN')
    if response.status_code < 400 and _json_rate_limited(response):
        set_cooldown(_retry_seconds(response))
        raise APICooldown('API_JSON_RATELIMIT_SHARED_COOLDOWN')
    return response
