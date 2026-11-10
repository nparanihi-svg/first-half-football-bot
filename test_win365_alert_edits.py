import sqlite3
from types import SimpleNamespace

import alerts
from early_wins_v53 import notify_early_win
from results_v4 import deliver_result_notices
from win365_alerts import alert_body, save_message


def test_live_milestone_and_settlement_edit_both_original_messages(tmp_path, monkeypatch):
    path = tmp_path / 'signals.sqlite'
    monkeypatch.setattr('early_wins_v53.DB_PATH', str(path))
    monkeypatch.setattr('results_v4.DB_PATH', path)
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'mock')
    monkeypatch.setenv('TELEGRAM_CHAT_ID', 'primary')
    monkeypatch.setenv('ENABLE_RESEARCH_ALERTS', 'true')
    monkeypatch.setenv('ENABLE_RESULT_ALERTS', 'true')
    edits = []
    monkeypatch.setattr(alerts, 'edit_telegram',
                        lambda token, chat, message_id, text, **kwargs: edits.append((chat, message_id, text)))

    def unexpected_send(*args, **kwargs):
        raise AssertionError('Updates must edit the original messages')

    monkeypatch.setattr(alerts, 'send_telegram', unexpected_send)
    market = 'FH_OVER_2_5_CORNERS'
    snapshot = SimpleNamespace(fixture_id=7, elapsed=16, half_goals=0, half_corners=1,
                               home='A & B', away='C', league='Test League')
    body = alert_body(snapshot, '1H Asian Corners · Over 2.5', .813, {'home': 0, 'away': 0})
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE sent_alerts (fixture_id, market)')
        db.execute('INSERT INTO sent_alerts VALUES (7, ?)', (market,))
        db.execute('''CREATE TABLE observations
            (fixture_id, market, home, away, minute, probability, result, settled_at)''')
        db.execute('INSERT INTO observations VALUES (7, ?, ?, ?, 16, .813, ?, NULL)',
                   (market, snapshot.home, snapshot.away, 'PENDING'))
        for chat, message_id in [('primary', 10), ('channel', 24)]:
            save_message(db, 7, market, chat, message_id, body, 'Today 0 W / 0 L')

    snapshot.elapsed = 21
    snapshot.half_corners = 3
    assert notify_early_win(snapshot) == 1
    assert notify_early_win(snapshot) == 0
    assert all('WIN DETECTED' in text and '21′' in text for _, _, text in edits)

    with sqlite3.connect(path) as db:
        db.execute("UPDATE observations SET result='WIN', settled_at=datetime('now')")
    assert deliver_result_notices() == 1
    assert deliver_result_notices() == 0
    assert sorted((chat, mid) for chat, mid, _ in edits) == [('channel', 24)] * 2 + [('primary', 10)] * 2
    assert all(body in text for _, _, text in edits)
    assert all('WON' in text and 'Overall 1 W / 0 L' in text for _, _, text in edits[2:])
