"""First Half Signal Lab worker — scanner plus optional historical collector.

History is strictly opt-in (ENABLE_HISTORY_AUTO_V55=true) and runs in its own
thread at most once every 15 minutes. It never blocks the live scanner.
"""
import os
import time
import threading
from datetime import datetime, timedelta, timezone
from scanner import scan

INTERVAL_SECONDS = max(15, int(os.getenv('SCAN_INTERVAL_SECONDS', '20')))
ERROR_COOLDOWN_SECONDS = 300
MINUTE_LIMIT_COOLDOWN_SECONDS = 120
DAILY_LIMIT_COOLDOWN_SECONDS = 24 * 60 * 60
HISTORY_INTERVAL = 900


def _run_history():
    try:
        if os.getenv('ENABLE_PREMATCH_V56', 'false').lower() == 'true':
            from prematch_v56 import discover
            discover()
        from history_auto_v55 import run
        run()
    except Exception as error:
        print('V5.5 background history:', type(error).__name__, str(error)[:160], flush=True)


def main():
    print(f'First Half Signal Lab worker started; interval={INTERVAL_SECONDS}s', flush=True)
    pause_until = None
    next_history = time.monotonic() + 60
    history_thread = None
    while True:
        # Background work must not delay live scans, including during scanner cooldown.
        if os.getenv('ENABLE_HISTORY_AUTO_V55', 'false').lower() == 'true' and time.monotonic() >= next_history:
            if history_thread is None or not history_thread.is_alive():
                history_thread = threading.Thread(target=_run_history, daemon=True, name='history-v55')
                history_thread.start()
                next_history = time.monotonic() + HISTORY_INTERVAL
        now = datetime.now(timezone.utc)
        if pause_until is not None and now < pause_until:
            time.sleep(min(60, max(1, (pause_until - now).total_seconds())))
            continue
        start = time.monotonic()
        try:
            scan()
        except Exception as error:
            message = str(error)
            print(f'Scanner error: {type(error).__name__}: {message}', flush=True)
            now = datetime.now(timezone.utc)
            if 'API_DAILY_RESERVE_REACHED' in message:
                pause_until = now + timedelta(seconds=DAILY_LIMIT_COOLDOWN_SECONDS)
                print(f'Daily reserve reached; paused until {pause_until.isoformat()}', flush=True)
            elif 'API_MINUTE_LIMIT_NEAR' in message or '429' in message:
                pause_until = now + timedelta(seconds=MINUTE_LIMIT_COOLDOWN_SECONDS)
            else:
                pause_until = now + timedelta(seconds=ERROR_COOLDOWN_SECONDS)
        else:
            pause_until = None
        elapsed = time.monotonic() - start
        time.sleep(max(1, INTERVAL_SECONDS - elapsed))

if __name__ == '__main__':
    main()
