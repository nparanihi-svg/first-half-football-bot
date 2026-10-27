"""V4 results delivery and conservative fixture-level performance report.

Uses the existing V3 database. This module does not place bets and its output is
not a model validation or a record of actual wagers.
"""
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from win365_daily import daily_line

DB_PATH = Path(os.getenv('SIGNAL_DB_PATH', '/var/data/first_half_v3.sqlite3'))
LABELS = {'FH_OVER_0_5_GOALS': '1H Over 0.5 Goals',
          'FH_OVER_2_5_CORNERS': '1H Over 2.5 Corners'}


def _connect():
    con = sqlite3.connect(str(DB_PATH), timeout=20)
    con.execute('PRAGMA busy_timeout=20000')
    return con


def _notified_tally(db, current_fixture_id, current_market, current_outcome):
    """Count unique, already-notified fixture/markets plus this pending notice.

    Repeated minute-by-minute observations never inflate the tally. Count only
    verified results attached to previously sent watchlist alerts.
    """
    rows = db.execute('''SELECT n.fixture_id, n.market, MIN(o.result),
             COUNT(DISTINCT o.result),
             SUM(CASE WHEN o.result='PENDING' THEN 1 ELSE 0 END)
        FROM result_notices n
        JOIN sent_alerts a ON a.fixture_id=n.fixture_id AND a.market=n.market
        JOIN observations o ON o.fixture_id=n.fixture_id AND o.market=n.market
        GROUP BY n.fixture_id, n.market''').fetchall()
    seen = {}
    for fid, market, outcome, distinct_results, pending_count in rows:
        if distinct_results == 1 and pending_count == 0 and outcome in ('WIN', 'LOSS'):
            seen[(fid, market)] = outcome
    seen[(current_fixture_id, current_market)] = current_outcome
    total_w = sum(v == 'WIN' for v in seen.values())
    total_l = sum(v == 'LOSS' for v in seen.values())
    market_values = [v for (fid, mkt), v in seen.items() if mkt == current_market]
    market_w = sum(v == 'WIN' for v in market_values)
    market_l = sum(v == 'LOSS' for v in market_values)
    return total_w, total_l, market_w, market_l


def deliver_result_notices(limit=3):
    """Notify once per settled fixture/market that was sent as a watchlist alert.

    A unique receipt is committed after Telegram delivery. In the rare event of
    a process interruption between delivery and commit, a repeat is possible.
    """
    if os.getenv('ENABLE_RESULT_ALERTS', 'false').lower() != 'true':
        return 0
    token = os.getenv('TELEGRAM_BOT_TOKEN', '')
    chat_id = os.getenv('TELEGRAM_CHAT_ID', '')
    if not token or not chat_id:
        print('V4: result alerts enabled but Telegram credentials missing', flush=True)
        return 0
    with _connect() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS result_notices (
            fixture_id INTEGER NOT NULL, market TEXT NOT NULL, sent_at TEXT NOT NULL,
            PRIMARY KEY (fixture_id, market))''')
        # Only notify markets for which a watchlist Telegram alert was claimed;
        # avoid flooding users for routine model observations.
        rows = db.execute('''SELECT o.fixture_id, o.market, MIN(o.home), MIN(o.away),
             MIN(o.minute), MIN(o.probability), MIN(o.result), COUNT(*)
            FROM observations AS o
            JOIN sent_alerts AS a ON a.fixture_id=o.fixture_id AND a.market=o.market
            LEFT JOIN result_notices AS n ON n.fixture_id=o.fixture_id AND n.market=o.market
            WHERE o.result IN ('WIN','LOSS') AND n.fixture_id IS NULL
            GROUP BY o.fixture_id,o.market
            HAVING COUNT(DISTINCT o.result)=1 AND
                   (SELECT COUNT(*) FROM observations q
                    WHERE q.fixture_id=o.fixture_id AND q.market=o.market AND q.result='PENDING')=0
            ORDER BY MIN(o.settled_at) ASC LIMIT ?''', (limit,)).fetchall()
    from alerts import send_telegram
    delivered = 0
    for fixture_id, market, home, away, minute, _minprob, outcome, count in rows:
        # Earliest prediction probability, not the lowest from the fixture.
        with _connect() as db:
            first = db.execute('''SELECT probability, minute FROM observations
                WHERE fixture_id=? AND market=? ORDER BY minute ASC LIMIT 1''',
                (fixture_id, market)).fetchone()
        first_probability, first_minute = first if first else (None, minute)
        # If an alert was sent, show its actual selection minute rather than
        # the research model's earliest recorded observation.
        with _connect() as db:
            table_exists = db.execute("""SELECT 1 FROM sqlite_master
                WHERE type='table' AND name='alert_details'""").fetchone()
            selected = (db.execute("""SELECT selected_minute, estimate
                FROM alert_details WHERE fixture_id=? AND market=?""",
                (fixture_id, market)).fetchone() if table_exists else None)
        selected_minute = selected[0] if selected else None
        alert_probability = selected[1] if selected else first_probability
        estimate = ('unavailable' if alert_probability is None
                    else f'{alert_probability*100:.1f}% (experimental)')
        with _connect() as db:
            total_w, total_l, market_w, market_l = _notified_tally(
                db, fixture_id, market, outcome)
        total_count = total_w + total_l
        tally = (f'\n\nRUNNING ALERT RECORD (unique fixture + market)\n'
                 f'All alerted markets: {total_w} W - {total_l} L '
                 f'({100 * total_w / total_count:.1f}% outcome frequency)\n'
                 f'{LABELS.get(market, market)}: {market_w} W - {market_l} L\n'
                 'These are verified market outcomes, not placed bets or profit.')
        minute_label = (f"Selected at {selected_minute}'" if selected_minute is not None
                        else f"First recorded at {first_minute}' (older alert)")

        # Include the currently verified outcome before its notice receipt.
        # The day is determined by settlement time in Perth.
        with _connect() as db:
            today_record = daily_line(db, fixture_id, market, outcome)

        quick_record = (
            f'🏆 WIN365 — RESULTS\n'
            f'{today_record}\n'
            f'\nOVERALL RECORD\n'
            f'All alerts: {total_w} W / {total_l} L '
            f'({100*total_w/total_count:.1f}% outcomes)\n'
            f'This market: {market_w} W / {market_l} L'
        )
        message = (f'{"✅" if outcome == "WIN" else "❌"} FIRST-HALF {outcome}\n'
                   f'{home} vs {away}\n'
                   f'{LABELS.get(market, market)}\n'
                   f'{minute_label} | Estimated chance: {estimate}\n'
                   f'{quick_record}\n'
                   'Verified market outcome, not a confirmed placed bet. '
                   'No bookmaker odds verified.')
        try:
            send_telegram(token, chat_id, message)
            with _connect() as db:
                db.execute('INSERT OR IGNORE INTO result_notices VALUES (?,?,?)',
                           (fixture_id, market, datetime.now(timezone.utc).isoformat()))
            delivered += 1
            print('V4 result notification:', fixture_id, market, outcome, flush=True)
        except Exception as exc:
            print('V4 Telegram delivery failed:', type(exc).__name__, str(exc)[:120], flush=True)
            break
    return delivered


def report():
    """Distinct fixture/market outcomes and first-observation forecast quality."""
    with _connect() as db:
        rows = db.execute('''SELECT fixture_id, market, result, probability, minute
            FROM observations
            WHERE result IN ('WIN', 'LOSS', 'PENDING')
            ORDER BY fixture_id, market, minute ASC''').fetchall()
    first_by_market = {}
    for fixture_id, market, outcome, prob, minute in rows:
        first_by_market.setdefault((fixture_id, market), []).append((minute, outcome, prob))
    print('FIRST HALF SIGNAL LAB | V4 RESEARCH PERFORMANCE')
    print('One fixture + market = one independent outcome; not actual bets.')
    for market in LABELS:
        wins = losses = pending = 0
        brier_errors = []
        for (fid, mkt), observations in first_by_market.items():
            if mkt != market:
                continue
            states = {v[1] for v in observations}
            if states == {'WIN'}:
                wins += 1
                outcome = 1
            elif states == {'LOSS'}:
                losses += 1
                outcome = 0
            else:
                pending += 1
                continue
            first_prob = observations[0][2]
            if first_prob is not None and 0 <= first_prob <= 1:
                brier_errors.append((first_prob-outcome)**2)
        settled = wins + losses
        print(f'\n{LABELS[market]}')
        print(f'  Unique settled fixtures: {settled} | WIN: {wins} | LOSS: {losses} | pending: {pending}')
        if settled:
            print(f'  Outcome frequency: {100*wins/settled:.1f}% (not betting ROI)')
        if brier_errors:
            print(f'  First-estimate Brier score: {sum(brier_errors)/len(brier_errors):.4f} (n={len(brier_errors)})')
    print('\nWarning: observations of the same match are correlated; small samples do not validate the model.')


if __name__ == '__main__':
    report()
