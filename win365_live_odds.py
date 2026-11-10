"""Read exact first-half goals quotes from API-Football's in-play feed."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation


def first_half_goals_quote(rows, fixture_id, now=None):
    """Return one active Over 0.5 quote with a feed update within 60 seconds."""
    now = now or datetime.now(timezone.utc)
    quotes = set()
    for row in rows:
        fixture = row.get('fixture') or {}
        if fixture.get('id') != fixture_id or (fixture.get('status') or {}).get('long') != 'First Half':
            continue
        status = row.get('status') or {}
        if any(status.get(flag) is True for flag in ('stopped', 'blocked', 'finished')):
            continue
        timestamp = row.get('update')
        if not isinstance(timestamp, str):
            continue
        try:
            updated = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
        except ValueError:
            continue
        if updated.tzinfo is None or not timedelta(0) <= now - updated <= timedelta(seconds=60):
            continue
        for market in row.get('odds') or []:
            if market.get('id') != 49:
                continue
            for quote in market.get('values') or []:
                if quote.get('value') != 'Over' or quote.get('suspended') is not False:
                    continue
                try:
                    line = Decimal(str(quote.get('handicap')))
                    price = Decimal(str(quote.get('odd')))
                except InvalidOperation:
                    continue
                if line == Decimal('0.5') and price.is_finite() and price > 1:
                    quotes.add((price, updated))
    return next(iter(quotes)) if len(quotes) == 1 else None
