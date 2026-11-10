"""Telegram delivery, with optional additional destinations.

Existing callers use send_telegram(token, chat_id, message) unchanged.
Set TELEGRAM_EXTRA_CHAT_IDS to comma-separated secondary chat IDs to enable
fan-out. Never place the bot token in logs.
"""
import os
import requests


def _send_one(token: str, chat_id: str, message: str, *, parse_mode=None):
    payload = {'chat_id': chat_id, 'text': message}
    if parse_mode:
        payload['parse_mode'] = parse_mode
    response = requests.post(
        f'https://api.telegram.org/bot{token}/sendMessage',
        json=payload,
        timeout=15,
    )
    response.raise_for_status()
    data = response.json()
    if not data.get('ok'):
        raise RuntimeError('Telegram rejected message')
    return data


def send_telegram(token: str, chat_id: str, message: str, *, on_delivery=None, parse_mode=None, photo=None):
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
            result = (_send_one(token, destination, message, parse_mode=parse_mode)
                      if photo is None else ticket_photo(token, destination, photo, message))
            if on_delivery:
                on_delivery(destination, result["result"]["message_id"])
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


def edit_telegram(token, chat_id, message_id, message, *, parse_mode=None):
    response = requests.post(
        f"https://api.telegram.org/bot{token}/editMessageText",
        json={
            "chat_id": chat_id,
            "message_id": message_id,
            "text": message,
            **({"parse_mode": parse_mode} if parse_mode else {}),
        },
        timeout=15,
    )
    if response.status_code == 400:
        data = response.json()
        if "message is not modified" in data.get("description", "").lower():
            return data
    response.raise_for_status()
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError("Telegram edit failed")
    return data


def ticket_photo(token, chat_id, photo, caption, *, message_id=None):
    import json

    payload = {'chat_id': chat_id}
    if message_id is None:
        method = 'sendPhoto'
        payload['caption'] = caption
    else:
        method = 'editMessageMedia'
        payload.update(message_id=message_id, media=json.dumps({
            'type': 'photo', 'media': 'attach://photo', 'caption': caption,
        }))
    response = requests.post(
        f'https://api.telegram.org/bot{token}/{method}', data=payload,
        files={'photo': ('win365.png', photo, 'image/png')}, timeout=15,
    )
    if message_id is not None and response.status_code == 400:
        data = response.json()
        if 'message is not modified' in data.get('description', '').lower():
            return data
    response.raise_for_status()
    data = response.json()
    if not data.get('ok'):
        raise RuntimeError('Telegram ticket delivery failed')
    return data
