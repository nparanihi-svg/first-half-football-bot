import time
from datetime import datetime, timedelta, timezone

from scanner import scan

INTERVAL_SECONDS = 60
ERROR_COOLDOWN_SECONDS = 300
MINUTE_LIMIT_COOLDOWN_SECONDS = 120
DAILY_LIMIT_COOLDOWN_SECONDS = 24 * 60 * 60


def main():
    print('First Half Signal Lab worker started', flush=True)
    pause_until = None

    while True:
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
            elif 'API_MINUTE_LIMIT_NEAR' in message:
                pause_until = now + timedelta(seconds=MINUTE_LIMIT_COOLDOWN_SECONDS)
            else:
                pause_until = now + timedelta(seconds=ERROR_COOLDOWN_SECONDS)
        else:
            pause_until = None

        elapsed = time.monotonic() - start
        time.sleep(max(1, INTERVAL_SECONDS - elapsed))


if __name__ == '__main__':
    main()
