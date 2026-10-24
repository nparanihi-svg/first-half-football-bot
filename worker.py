import os
import time
from datetime import datetime, timezone

from scanner import scan

INTERVAL = 60
MAX_SCANS_PER_DAY = 1440


def main():
    scans_today = 0
    current_day = datetime.now(timezone.utc).date()

    print("First Half Signal Lab worker started", flush=True)

    while True:
        today = datetime.now(timezone.utc).date()

        if today != current_day:
            current_day = today
            scans_today = 0

        if scans_today < MAX_SCANS_PER_DAY:
            started = time.monotonic()

            try:
                scan()
                scans_today += 1
            except Exception as error:
                print(
                    "Scan failed:",
                    type(error).__name__,
                    str(error),
                    flush=True
                )

            elapsed = time.monotonic() - started
            time.sleep(max(1, INTERVAL - elapsed))
        else:
            print("Daily scan limit reached", flush=True)
            time.sleep(60)


if __name__ == "__main__":
    main()
