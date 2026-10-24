import time
from datetime import datetime, timezone, timedelta

from scanner import scan

INTERVAL = 60
ERROR_COOLDOWN = 300
DAILY_RESERVE_COOLDOWN = 3600
def main():
    next_scan = time.monotonic()
    pause_until = None

    print("First Half Signal Lab worker started", flush=True)

    while True:
        if pause_until:
            if datetime.now(timezone.utc) < pause_until:
                time.sleep(60)
                continue
            pause_until = None

        delay = next_scan - time.monotonic()
        if delay > 0:
            time.sleep(delay)

        try:
            scan()

        except Exception as error:
            message = str(error)
            print("Scanner error:", message, flush=True)

            if "API_DAILY_RESERVE_REACHED" in message:
                pause_until = (
                    datetime.now(timezone.utc)
                    + timedelta(seconds=DAILY_RESERVE_COOLDOWN)
                )

            elif "API_MINUTE_LIMIT_NEAR" in message:
                pause_until = (
                    datetime.now(timezone.utc)
                    + timedelta(seconds=ERROR_COOLDOWN)
                )

            else:
                time.sleep(ERROR_COOLDOWN)

        next_scan = time.monotonic() + INTERVAL


if __name__ == "__main__":
    main()
