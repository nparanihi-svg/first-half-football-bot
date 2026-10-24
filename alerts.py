import requests

def send_telegram(token: str, chat_id: str, message: str):
    resp=requests.post(f'https://api.telegram.org/bot{token}/sendMessage',
                       json={'chat_id':chat_id,'text':message},timeout=15)
    resp.raise_for_status()
    data=resp.json()
    if not data.get('ok'):
        raise RuntimeError('Telegram rejected message')
    return data
