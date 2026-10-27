"""WIN365 daily notified outcome tally. No API calls or database writes."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

PERTH = ZoneInfo('Australia/Perth')


def _day(timestamp):
    if not timestamp:
        return None
    try:
        dt = datetime.fromisoformat(str(timestamp).replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(PERTH).date()
    except (ValueError, TypeError):
        return None


def daily_tally(db, fixture_id, market, outcome, now=None):
    """Returns (wins, losses) for today's verified, alerted fixture+market outcomes.

    Mirrors results_v4._notified_tally(): include already-notified fixtures,
    plus the current result before its notification receipt is written.
    Uses settled_at, not notice delivery time, to determine today's date.
    """
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(PERTH).date()
    rows = db.execute('''
        SELECT n.fixture_id, n.market, MIN(o.result),
               COUNT(DISTINCT o.result),
               SUM(CASE WHEN o.result='PENDING' THEN 1 ELSE 0 END),
               MIN(o.settled_at)
        FROM result_notices n
        JOIN sent_alerts a ON a.fixture_id=n.fixture_id AND a.market=n.market
        JOIN observations o ON o.fixture_id=n.fixture_id AND o.market=n.market
        GROUP BY n.fixture_id, n.market
    ''').fetchall()
    counted = {}
    for fid, mkt, result, distinct_count, pending, settled in rows:
        if distinct_count == 1 and not pending and result in ('WIN', 'LOSS') and _day(settled) == today:
            counted[(fid, mkt)] = result
    # Pending notification's result has already been verified, but not receipted.
    current = db.execute('''
        SELECT MIN(settled_at), COUNT(DISTINCT result),
               SUM(CASE WHEN result='PENDING' THEN 1 ELSE 0 END)
        FROM observations WHERE fixture_id=? AND market=?
    ''', (fixture_id, market)).fetchone()
    if (current and current[1] == 1 and not current[2] and
            _day(current[0]) == today and outcome in ('WIN', 'LOSS')):
        counted[(fixture_id, market)] = outcome
    return sum(v == 'WIN' for v in counted.values()), sum(v == 'LOSS' for v in counted.values())


def daily_line(db, fixture_id, market, outcome, now=None):
    w, l = daily_tally(db, fixture_id, market, outcome, now)
    n = w + l
    return f"Today (Perth): {w} W / {l} L ({100*w/n:.1f}% outcomes)" if n else "Today (Perth): 0 W / 0 L (no settled alerts)"
