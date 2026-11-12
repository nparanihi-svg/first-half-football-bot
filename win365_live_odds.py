"""Read exact first-half goals quotes from API-Football's in-play feed."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation


def first_half_goals_quote(rows, fixture_id, now=None):
    """Return one active Over 0.5 quote with a feed update within 60 seconds."""
    return _first_half_quote(rows, fixture_id, 49, Decimal("0.5"), now)


def first_half_corners_quote(rows, fixture_id, now=None):
    """Match the current research selection, first-half Over 2.5 corners."""
    return _first_half_quote(rows, fixture_id, 204, Decimal("2.5"), now)


def _first_half_quote(rows, fixture_id, market_id, target_line, now, diagnostics=None):
    now = now or datetime.now(timezone.utc)
    quotes = set()
    def reject(reason):
        if diagnostics is not None:
            diagnostics[reason] = diagnostics.get(reason, 0) + 1
    if not rows:
        reject('empty_response')
    for row in rows:
        fixture = row.get('fixture') or {}
        if fixture.get('id') != fixture_id:
            reject('wrong_fixture')
            continue
        if (fixture.get('status') or {}).get('long') != 'First Half':
            reject('outside_first_half')
            continue
        status = row.get('status') or {}
        if any(status.get(flag) is True for flag in ('stopped', 'blocked', 'finished')):
            reject('fixture_stopped_or_blocked')
            continue
        timestamp = row.get('update')
        if not isinstance(timestamp, str):
            reject('missing_update')
            continue
        try:
            updated = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
        except ValueError:
            reject('invalid_update')
            continue
        if updated.tzinfo is None or not timedelta(0) <= now - updated <= timedelta(seconds=60):
            reject('stale_or_future_update')
            continue
        if not any(m.get('id') == market_id for m in row.get('odds') or []):
            reject('market_missing')
        for market in row.get('odds') or []:
            if market.get('id') != market_id:
                continue
            if not market.get('values'):
                reject('empty_market')
            for quote in market.get('values') or []:
                if quote.get('value') != 'Over':
                    continue
                if quote.get('suspended') is not False:
                    reject('suspended_or_unknown')
                    continue
                try:
                    line = Decimal(str(quote.get('handicap')))
                    price = Decimal(str(quote.get('odd')))
                except InvalidOperation:
                    reject('invalid_line_or_price')
                    continue
                if not line.is_finite() or line != target_line:
                    reject('different_line')
                elif not price.is_finite() or price <= 1:
                    reject('invalid_price')
                else:
                    quotes.add((price, updated))
    if len(quotes) > 1:
        reject('conflicting_quotes')
    elif not quotes and diagnostics is not None and not diagnostics:
        reject('no_over_selection')
    return next(iter(quotes)) if len(quotes) == 1 else None


def corners_quote_diagnostics(rows, fixture_id, now=None):
    reasons = {}
    quote = _first_half_quote(rows, fixture_id, 204, Decimal('2.5'), now, reasons)
    return quote, {'outcome': 'accepted' if quote else 'unavailable', 'reasons': reasons}

