"""Live first-half research scanner with explainable, uncalibrated signals."""
import os
import sqlite3
from datetime import datetime, timezone
from provider import FootballAPI, parse_fixture
from engine import predict
from win365_ticket import render_ticket, ticket_body
import halftime_shadow
import corner_odds_audit
from signal_rules import watchlist_reason
from blocked_shot_audit import CORNERS, record_candidate
from tracking_v3 import record, verify_one
from early_wins_v53 import notify_early_win
from results_v4 import alert_record, deliver_result_notices
from win365_alerts import save_message
from win365_live_odds import first_half_goals_quote, corners_quote_diagnostics
from win365_performance import record_candidate as record_goal_candidate, record_price

MAX_FIXTURES_PER_SCAN = max(1, min(30, int(os.getenv('MAX_FIXTURES_PER_SCAN', '20'))))
_scan_number = 0

def eligible_fixtures(fixtures):
    """Rotate through first-half fixtures to maximise coverage."""
    global _scan_number
    eligible = [f for f in fixtures if
                f.get('fixture', {}).get('status', {}).get('short') == '1H'
                and (f.get('fixture', {}).get('status', {}).get('elapsed') or 0) < 45]
    if not eligible:
        return []
    eligible.sort(key=lambda f: f['fixture']['id'])
    start = (_scan_number * MAX_FIXTURES_PER_SCAN) % len(eligible)
    _scan_number += 1
    return [eligible[(start + i) % len(eligible)]
            for i in range(min(MAX_FIXTURES_PER_SCAN, len(eligible)))]

def signal_data_issue(snapshot, market):
    if snapshot.total_shots is None or snapshot.shots_on_goal is None:
        return 'missing live shots / shots on target'
    if snapshot.total_shots < 0 or snapshot.shots_on_goal < 0:
        return 'invalid negative shot counts'
    if snapshot.shots_on_goal > snapshot.total_shots:
        return 'shots on target exceed total shots'
    if market == 'FH_OVER_2_5_CORNERS':
        if snapshot.half_corners is None:
            return 'first-half corners unavailable'
        if snapshot.half_corners >= 3:
            return 'corner target already reached'
    if market == 'FH_OVER_0_5_GOALS' and snapshot.half_goals >= 1:
        return 'goal target already reached'
    return None

STATE_DB = os.getenv('SIGNAL_DB_PATH', '/var/data/first_half_v3.sqlite3')

def claim_alert(fixture_id, market):
    with sqlite3.connect(STATE_DB, timeout=10) as db:
        db.execute('CREATE TABLE IF NOT EXISTS sent_alerts ('
                   'fixture_id INTEGER NOT NULL, market TEXT NOT NULL, '
                   'created_at TEXT NOT NULL, PRIMARY KEY(fixture_id, market))')
        try:
            db.execute('INSERT INTO sent_alerts VALUES (?, ?, ?)',
                       (fixture_id, market, datetime.now(timezone.utc).isoformat()))
            return True
        except sqlite3.IntegrityError:
            return False

def save_alert_details(fixture_id, market, minute, probability):
    """Persist the selected alert minute, independently of research observations."""
    with sqlite3.connect(STATE_DB, timeout=10) as db:
        db.execute('CREATE TABLE IF NOT EXISTS alert_details ('
                   'fixture_id INTEGER NOT NULL, market TEXT NOT NULL, '
                   'selected_minute INTEGER NOT NULL, estimate REAL, '
                   'PRIMARY KEY (fixture_id, market))')
        db.execute('INSERT OR IGNORE INTO alert_details VALUES (?,?,?,?)',
                   (fixture_id, market, int(minute), probability))


def release_alert(fixture_id, market):
    with sqlite3.connect(STATE_DB) as db:
        db.execute('DELETE FROM sent_alerts WHERE fixture_id=? AND market=?',
                   (fixture_id, market))

def _fmt(value, suffix=''):
    return 'unavailable' if value is None else f'{value:g}{suffix}'

def _explanation(snapshot, result):
    base = result['baseline_probability'] * 100
    new = result['probability'] * 100
    return (
        f'Time left (regulation): {result["minutes_remaining"]:.0f} min + possible stoppage\n'
        f'Goals this half: {snapshot.half_goals}; corners: {_fmt(snapshot.half_corners)}\n'
        f'Shots: {_fmt(snapshot.total_shots)}; on target: {_fmt(snapshot.shots_on_goal)}; '
        f'blocked: {_fmt(snapshot.blocked_shots)}\n'
        f'Possession (team order as listed): {_fmt(snapshot.possession_home, "%")} / '
        f'{_fmt(snapshot.possession_away, "%")}\n'
        f'Time-only baseline: {base:.1f}%\n'
        f'Experimental live adjustment: {new-base:+.1f} percentage points '
        f'(multiplier {result["intensity_multiplier"]:.2f})\n'
        f'Inputs affecting estimate: {", ".join(result["inputs_used"]) or "none"}. '
        'Possession is shown for context, not yet a probability input.'
    )

def _record_v58_shadow(fixture, snapshot):
    """Opt-in research only: never interrupt existing scanning or alerts."""
    if os.getenv('ENABLE_V58_SHADOW', 'false').lower() != 'true':
        return
    try:
        from v58_integration import observe_fixture
        outcome = observe_fixture(
            fixture, snapshot,
            journal_db=os.getenv('V58_JOURNAL_DB') or os.getenv('SIGNAL_DB_PATH'),
            cache_db=os.getenv('HISTORY_V55_DB'),
            data_age_seconds=getattr(snapshot, 'data_age_seconds', 0),
            enabled=True,
        )
        print('V5.8 SHADOW:', fixture['fixture']['id'],
              outcome.get('status'),
              (outcome.get('reason') or (outcome.get('evaluation') or {}).get('reason', ''))[:120],
              flush=True)
    except Exception as exc:
        # Research must never block production-style signals.
        print('V5.8 SHADOW ERROR:', type(exc).__name__, str(exc)[:120], flush=True)

def scan():
    key = os.getenv('API_FOOTBALL_KEY')
    provider = os.getenv('LIVE_DATA_PROVIDER', 'betsapi' if os.getenv('BETSAPI_TOKEN') else 'api-football')
    if provider.lower() == 'betsapi':
        from betsapi_provider import BetsAPI
        token = os.getenv('BETSAPI_TOKEN')
        if not token:
            raise RuntimeError('BetsAPI token missing')
        api = BetsAPI(token, legacy_key=key)
    else:
        if not key:
            raise RuntimeError('API-Football key missing')
        api = FootballAPI(key)
    try:
        fixtures = api.live()
    except RuntimeError as error:
        if 'API_LOCAL_BUDGET_EXHAUSTED' not in str(error):
            raise
        print('V5.2 local API budget reached; live scan deferred', flush=True)
        return
    print('Scan:', datetime.now(timezone.utc).isoformat(), flush=True)
    print('Live matches:', len(fixtures), flush=True)

    for f in fixtures:
        s = f.get('fixture', {}).get('status', {})
        print("WIN365 FIXTURE:", f.get('fixture', {}).get('id'), s.get('short'), s.get('elapsed'), flush=True)

    selected = eligible_fixtures(fixtures)
    print('V5.2 eligible rotation: selected', len(selected), 'fixtures', flush=True)
    checked = 0
    for fixture in selected:
        status = fixture.get('fixture', {}).get('status', {})
        minute = status.get('elapsed') or 0
        if status.get('short') != '1H' or minute >= 45:
            continue
        fixture_id = fixture['fixture']['id']
        checked += 1
        try:
            stats = api.first_half_stats(fixture_id)
            print("WIN365 RAW STATS:", fixture_id, repr(stats)[:1500], flush=True)
            snapshot = parse_fixture(fixture, stats)
            try:
                halftime_shadow.record_prediction(fixture, snapshot, stats)
            except Exception as exc:
                print('Halftime shadow record failed:', type(exc).__name__, flush=True)
            _record_v58_shadow(fixture, snapshot)
            for result in predict(snapshot):
                record(snapshot, result)
                __import__('shadow_v55').record_shadow(fixture, result)
                probability = result['probability']
                print(snapshot.home, 'vs', snapshot.away,
                      '| Minute:', minute, '| Market:', result['label'],
                      '| Estimate:', round(probability * 100, 1), '%', flush=True)
                print('MODEL EXPLANATION:', _explanation(snapshot, result).replace('\n', ' | '), flush=True)
                issue = signal_data_issue(snapshot, result['market'])
                if issue:
                    print('V5 ALERT FILTER:', fixture_id, result['market'], issue, flush=True)
                    continue
                reason = watchlist_reason(snapshot, result['market'])
                if result['market'] == CORNERS and (
                    reason or watchlist_reason(snapshot, CORNERS, require_blocked=False)
                ):
                    try:
                        record_candidate(STATE_DB, snapshot, bool(reason))
                    except sqlite3.Error as exc:
                        print('Blocked-shot audit write failed:', type(exc).__name__, flush=True)
                if reason and result['market'] == 'FH_OVER_0_5_GOALS':
                    record_goal_candidate(STATE_DB, snapshot, probability)
                if reason is None:
                    continue
                print('RESEARCH WATCHLIST:', result['label'], '-', reason, flush=True)
                if os.getenv('ENABLE_RESEARCH_ALERTS', 'false').lower() != 'true':
                    continue
                token, chat_id = os.getenv('TELEGRAM_BOT_TOKEN', ''), os.getenv('TELEGRAM_CHAT_ID', '')
                if not token or not chat_id:
                    print('Telegram credentials missing: research alert skipped', flush=True)
                    continue
                if not claim_alert(fixture_id, result['market']):
                    continue
                from alerts import send_telegram
                delivered = False

                def remember_delivery(destination, message_id):
                    nonlocal delivered
                    delivered = True
                    with sqlite3.connect(STATE_DB, timeout=10) as db:
                        record_price(db, fixture_id, result['market'], minute, probability, quote)
                        save_message(db, fixture_id, result['market'], destination,
                                     message_id, body, tally)

                try:
                    quote = None
                    if result['market'] in ('FH_OVER_0_5_GOALS', CORNERS):
                        try:
                            is_goals = result['market'] == 'FH_OVER_0_5_GOALS'
                            rows = api.get('/odds/live', fixture=fixture_id, **({'bet': 49} if is_goals else {}))
                            if is_goals:
                                quote = first_half_goals_quote(rows, fixture_id)
                            else:
                                quote, diagnostic = corners_quote_diagnostics(rows, fixture_id)
                                try:
                                    corner_odds_audit.record(STATE_DB, fixture_id, minute, rows, diagnostic)
                                except sqlite3.Error as exc:
                                    print('Corner odds audit write failed:', type(exc).__name__, flush=True)
                        except Exception as exc:
                            if any(code in str(exc) for code in (
                                'API_LOCAL_BUDGET_EXHAUSTED', 'API_DAILY_RESERVE_REACHED',
                                'API_MINUTE_LIMIT_NEAR',
                            )):
                                raise
                            print('Live odds unavailable:', type(exc).__name__, flush=True)
                    body = ticket_body(snapshot, result['label'], probability, fixture, quote)
                    with sqlite3.connect(STATE_DB, timeout=10) as db:
                        tally = alert_record(db)
                    send_telegram(token, chat_id, f"WIN365 · LIVE WATCH · {result['label']}",
                                  on_delivery=remember_delivery, photo=render_ticket(body, tally))
                except Exception as exc:
                    if not delivered:
                        release_alert(fixture_id, result['market'])
                    if any(code in str(exc) for code in (
                        'API_LOCAL_BUDGET_EXHAUSTED', 'API_DAILY_RESERVE_REACHED',
                        'API_MINUTE_LIMIT_NEAR',
                    )):
                        raise
                    print('Watchlist delivery failed:', type(exc).__name__, flush=True)
                if delivered:
                    save_alert_details(fixture_id, result['market'], minute, probability)
            # Live milestone alerts do not change final settlement records.
            notify_early_win(snapshot)
        except Exception as error:
            if 'API_LOCAL_BUDGET_EXHAUSTED' in str(error):
                print('V5.2 local request budget reached; remaining fixture scans deferred', flush=True)
                break
            if ('API_DAILY_RESERVE_REACHED' in str(error)
                    or 'API_MINUTE_LIMIT_NEAR' in str(error)):
                raise
            print('Skipped fixture', fixture_id, type(error).__name__, str(error)[:160], flush=True)
    try:
        halftime_shadow.settle_predictions(fixtures)
    except Exception as exc:
        print('Halftime shadow settlement failed:', type(exc).__name__, flush=True)
    print('First-half matches checked:', checked, flush=True)
    try:
        verify_one(api, fixtures)
    except Exception as error:
        if ('API_LOCAL_BUDGET_EXHAUSTED' in str(error) or
                'API_DAILY_RESERVE_REACHED' in str(error) or
                'API_MINUTE_LIMIT_NEAR' in str(error)):
            raise
        print('V3 outcome check deferred:', type(error).__name__, str(error)[:140], flush=True)
    try:
        deliver_result_notices()
    except Exception as error:
        print('V4 result notification check:', type(error).__name__, str(error)[:140], flush=True)

    print('V5.2 API metrics:', api.request_summary(), flush=True)

if __name__ == '__main__':
    scan()
    if os.getenv('TELEGRAM_TEST') == 'true':
        from alerts import send_telegram
        send_telegram(os.environ['TELEGRAM_BOT_TOKEN'], os.environ['TELEGRAM_CHAT_ID'],
                      'First Half Signal Lab: GitHub scanner connected!')
