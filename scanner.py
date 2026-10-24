import os
from datetime import datetime, timezone

from provider import FootballAPI, parse_fixture
from engine import predict


MAX_FIXTURES_PER_SCAN = 3


def scan():
    key = os.environ.get("API_FOOTBALL_KEY")
    if not key:
        raise RuntimeError("API-Football key missing")

    api = FootballAPI(key)
    fixtures = api.live()
    print("Scan:", datetime.now(timezone.utc).isoformat(), flush=True)
    print("Live matches:", len(fixtures), flush=True)

    checked = 0
    for fixture in fixtures:
        status = fixture["fixture"]["status"]["short"]
        minute = fixture["fixture"]["status"].get("elapsed") or 0

        if status != "1H" or minute >= 45:
            continue
        if checked >= MAX_FIXTURES_PER_SCAN:
            break

        fixture_id = fixture["fixture"]["id"]
        checked += 1

        try:
            stats = api.first_half_stats(fixture_id)
            snapshot = parse_fixture(fixture, stats)
            estimates = predict(snapshot)

            for result in estimates:
                print(
                    snapshot.home, "vs", snapshot.away,
                    "| Minute:", minute,
                    "| Market:", result["label"],
                    "| Estimate:", round(result["probability"] * 100, 1),
                    "%",
                    flush=True,
                )

        except Exception as error:
            if ("API_DAILY_RESERVE_REACHED" in str(error)
                    or "API_MINUTE_LIMIT_NEAR" in str(error)):
                raise
            print("Skipped fixture", fixture_id, type(error).__name__, flush=True)

    print("First-half matches checked:", checked, flush=True)


if __name__ == "__main__":
    scan()

    if os.environ.get("TELEGRAM_TEST") == "true":
        from alerts import send_telegram

        token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        if not token or not chat_id:
            raise RuntimeError("Telegram credentials missing")

        send_telegram(token, chat_id, "First Half Signal Lab: GitHub scanner connected!")
        print("Telegram connection test completed", flush=True)
