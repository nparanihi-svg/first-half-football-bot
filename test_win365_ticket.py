import io
import sqlite3
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from win365_alerts import edit_alert, save_message
from win365_ticket import render_ticket, ticket_body


def test_ticket_waits_for_settlement_and_edits_original_photo():
    snapshot = SimpleNamespace(half_corners=2, total_shots=7, shots_on_goal=3,
                               blocked_shots=2, league='Süper Lig',
                               home='Galatasaray', away='Fenerbahçe', elapsed=22)
    fixture = {'league': {'country': 'Turkey'}, 'goals': {'home': 0, 'away': 0}}
    body = ticket_body(snapshot, '1H Asian Corners Over 2.5', .674, fixture, None)
    assert '2 blocked' in body and 'Odds unavailable' in body
    assert Image.open(io.BytesIO(render_ticket(body, 'Today 3 W / 1 L'))).size == (880, 920)
    with sqlite3.connect(':memory:') as db, patch('alerts.ticket_photo') as photo:
        save_message(db, 1, 'corners', '123', 45, body, 'Today 3 W / 1 L')
        assert edit_alert(db, 'token', 1, 'corners', '✅ WIN365 · WIN DETECTED')
        photo.assert_not_called()
        assert edit_alert(db, 'token', 1, 'corners', '✅ WIN365 · WON', record='Today 4 W / 1 L')
        assert photo.call_args.kwargs['message_id'] == 45
        assert Image.open(io.BytesIO(photo.call_args.args[2])).size == (880, 920)
