"""Keep the original alert body and Telegram IDs for in-place updates."""
from html import escape

from win365_daily import PERTH


def alert_body(snapshot, label, probability, goals, *, quote=None):
    score = 'unavailable'
    if all(type(goals.get(side)) is int for side in ('home', 'away')):
        score = f'{goals["home"]}–{goals["away"]}'
    corners = snapshot.half_corners
    corner_text = 'Corners unavailable' if corners is None else f'{corners} {"corner" if corners == 1 else "corners"}'
    odds = 'Odds unverified'
    if quote is not None:
        price, updated = quote
        quote_time = updated.astimezone(PERTH).strftime('%H:%M:%S')
        odds = f'API-Football live odds {price:f} · Over 0.5 matched · {quote_time} Perth'
    separator = '\n' if quote is not None else ' · '
    return (
        f'<b>{escape(label)}</b>\n'
        f'{escape(snapshot.home)} vs {escape(snapshot.away)}\n'
        f'{escape(snapshot.league)}\n\n'
        f'<b>{snapshot.elapsed}′</b> · Score {score} · {corner_text}\n'
        f'Model estimate {probability:.1%}{separator}{odds}'
    )


def alert_text(status, body, record, detail=None):
    heading = f'<b>{status}</b>'
    if detail:
        heading += f'\n{detail}'
    return f'{heading}\n\n{body}\n\n{record}'


def save_message(db, fixture_id, market, chat_id, message_id, body, record):
    db.execute('''CREATE TABLE IF NOT EXISTS alert_messages (
        fixture_id INTEGER NOT NULL, market TEXT NOT NULL, chat_id TEXT NOT NULL,
        message_id INTEGER NOT NULL, body TEXT NOT NULL, record TEXT NOT NULL,
        PRIMARY KEY (fixture_id, market, chat_id))''')
    db.execute('INSERT OR IGNORE INTO alert_messages VALUES (?,?,?,?,?,?)',
               (fixture_id, market, chat_id, message_id, body, record))


def edit_alert(db, token, fixture_id, market, status, *, detail=None, record=None):
    """Return False for legacy alerts that have no recorded Telegram IDs."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='alert_messages'").fetchone():
        return False
    rows = db.execute('''SELECT chat_id, message_id, body, record FROM alert_messages
                         WHERE fixture_id=? AND market=?''', (fixture_id, market)).fetchall()
    from alerts import edit_telegram
    for chat_id, message_id, body, original_record in rows:
        edit_telegram(token, chat_id, message_id,
                      alert_text(status, body, record or original_record, detail),
                      parse_mode='HTML')
    return bool(rows)
