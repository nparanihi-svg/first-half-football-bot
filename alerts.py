"""Telegram delivery, with optional additional destinations.

Existing callers use send_telegram(token, chat_id, message) unchanged.
Set TELEGRAM_EXTRA_CHAT_IDS to comma-separated secondary chat IDs to enable
fan-out. Never place the bot token in logs.
"""
import os
import requests


def _send_one(token: str, chat_id: str, message: str):
    response = requests.post(
        f'https://api.telegram.org/bot{token}/sendMessage',
        json={'chat_id': chat_id, 'text': message},
        timeout=15,
    )
    response.raise_for_status()
    data = response.json()
    if not data.get('ok'):
        raise RuntimeError('Telegram rejected message')
    return data


def send_telegram(token: str, chat_id: str, message: str):
    """Send to the original chat, plus optional additional chats.

    A failed secondary delivery is logged without raising, protecting the
    existing primary alert path. If primary fails, secondary delivery is still
    attempted, but the primary exception is re-raised to preserve caller logic.
    """
    extra = os.getenv('TELEGRAM_EXTRA_CHAT_IDS', '')
    destinations = [str(chat_id).strip()]
    for value in extra.split(','):
        value = value.strip()
        if value and value not in destinations:
            destinations.append(value)

    primary_result = None
    primary_error = None
    for index, destination in enumerate(destinations):
        try:
            result = _send_one(token, destination, message)
            if index == 0:
                primary_result = result
        except Exception as exc:
            if index == 0:
                primary_error = exc
            else:
                # No token, message text or destination ID in logs.
                print(f'Telegram secondary destination {index} failed: '
                      f'{type(exc).__name__}', flush=True)
    if primary_error is not None:
        raise primary_error
    return primary_result
