"""BetsAPI adapter. Negative fixture/team IDs isolate the provider's namespace."""
import os
import sqlite3
import time
from collections import Counter, deque
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from fractions import Fraction

import requests

from provider import FootballAPI

BASE = 'https://api.b365api.com'
_CALLS = deque()


def number(value):
    try:
        result = int(value)
        return result if result >= 0 else None
    except (ValueError, TypeError):
        return None


def score(value):
    parts = str(value).split('-')
    if len(parts) != 2 or any(number(v) is None for v in parts):
        return {'home': None, 'away': None}
    return dict(zip(('home', 'away'), map(int, parts)))


def period(event):
    timer = event.get('timer') or {}
    minute = number(timer.get('tm'))
    if str(event.get('time_status')) == '3':
        return 'FT', minute
    # MD 0 is first half, MD 1 is second half in the football timer.
    # A stopped clock at 45 may be stoppage or halftime. Require explicit
    # halftime score before declaring HT.
    if str(event.get('time_status')) == '1' and minute is not None:
        half = (event.get('scores') or {}).get('1') or {}
        if (minute == 45 and str(timer.get('tt')) == '0'
                and all(number(half.get(side)) is not None for side in ('home', 'away'))):
            return 'HT', minute
        if str(timer.get('md')) == '1':
            return '2H', minute
        if str(timer.get('md')) == '0' and minute <= 60:
            return '1H', minute
    return 'UNKNOWN', minute


def normalize(event, bet_id):
    status, minute = period(event)
    teams = {side: {'id': -int(event[side]['id']), 'name': event[side]['name']}
             for side in ('home', 'away')}
    half = (event.get('scores') or {}).get('1') or {}
    halftime = {side: number(half.get(side)) for side in ('home', 'away')}
    return {
        'fixture': {'id': -int(bet_id), 'date': datetime.fromtimestamp(
            int(event['time']), timezone.utc).isoformat(),
            'status': {'short': status, 'elapsed': minute}},
        'league': {'name': (event.get('league') or {}).get('name', ''),
                   'country': (event.get('league') or {}).get('cc', '')},
        'teams': teams, 'goals': score(event.get('ss')),
        'score': {'halftime': halftime}, '_betsapi': event,
    }


def statistics(fixture):
    event = fixture['_betsapi']
    raw = event.get('stats') or {}
    first_half = fixture['fixture']['status']['short'] == '1H'
    fields = {'on_target': 'Shots on Goal', 'off_target': 'Shots off Goal',
              'shots_blocked': 'Blocked Shots', 'possession_rt': 'Ball Possession',
              'goalattempts': 'Total Shots'}
    # Full-match corners after halftime cannot settle first-half selections.
    fields['corners' if first_half else 'corner_h'] = 'Corner Kicks'
    output = []
    for index, side in enumerate(('home', 'away')):
        values = []
        for key, label in fields.items():
            pair = raw.get(key)
            if not isinstance(pair, list) or len(pair) != 2:
                continue
            value = number(pair[index])
            if value is not None and (first_half or label == 'Corner Kicks'):
                values.append({'type': label, 'value': value})
        stat_map = {v['type']: v['value'] for v in values}
        if first_half and 'Total Shots' not in stat_map:
            parts = ('Shots on Goal', 'Shots off Goal', 'Blocked Shots')
            if all(k in stat_map for k in parts):
                values.append({'type': 'Total Shots', 'value': sum(stat_map[k] for k in parts)})
        # A minimum count is sufficient for an 'at least five shots' gate.
        # Keep it separate from total shots; unknown blocked shots are never zero.
        if first_half:
            current = {v['type']: v['value'] for v in values}
            parts = ('Shots on Goal', 'Shots off Goal')
            if 'Total Shots' in current:
                values.append({'type': 'Shots Lower Bound', 'value': current['Total Shots']})
            elif all(k in stat_map for k in parts):
                values.append({'type': 'Shots Lower Bound',
                               'value': sum(stat_map[k] for k in parts)})
        output.append({'team': fixture['teams'][side], 'statistics': values})
    # Existing parser sums available values. Avoid treating a missing team's
    # stat as zero by dropping that field from both teams.
    shared = set(v['type'] for v in output[0]['statistics']) & set(
        v['type'] for v in output[1]['statistics'])
    for team in output:
        team['statistics'] = [v for v in team['statistics'] if v['type'] in shared]
    return output


def market_values(groups):
    """Read ordered Bet365 market groups and fractional prices."""
    markets = {49: [], 204: []}
    for group in groups:
        if not isinstance(group, list):
            continue
        market = side = None
        group_active = side_active = False
        for row in group:
            kind = row.get('type')
            if kind == 'MG':
                title = row.get('NA', '')
                market = (49 if title.startswith('1st Half Goal Line') else
                          204 if title == '1st Half Asian Corners' else None)
                group_active = row.get('SU') == '0'
                side = None
            elif kind == 'MA':
                side = row.get('NA')
                side_active = row.get('SU', '0') == '0'
            elif kind == 'PA' and market and side in ('Over', 'Under') and row.get('OD'):
                try:
                    price = Decimal(1) + Decimal(Fraction(row['OD']).numerator) / Decimal(
                        Fraction(row['OD']).denominator)
                    line = Decimal(row.get('HA', ''))
                except (ValueError, ZeroDivisionError, InvalidOperation, TypeError):
                    continue
                if not line.is_finite() or not price.is_finite() or price <= 1:
                    continue
                markets[market].append({'value': side, 'handicap': str(line),
                    'odd': str(price), 'suspended': not (
                        group_active and side_active and row.get('SU') == '0')})
    return [{'id': key, 'name': '1H Goals' if key == 49 else '1H Asian Corners',
             'values': values} for key, values in markets.items() if values]


class BetsAPI:
    def __init__(self, token, legacy_key=None):
        self.token = token.strip()
        self.session = requests.Session()
        self.counts = Counter()
        self.fixtures = {}
        self.legacy = FootballAPI(legacy_key) if legacy_key else None
        self.path = os.getenv('SIGNAL_DB_PATH', '/var/data/first_half_v3.sqlite3')

    def request(self, endpoint, **params):
        now = time.monotonic()
        while _CALLS and now - _CALLS[0] >= 3600:
            _CALLS.popleft()
        limit = max(1, min(1700, int(os.getenv('BETSAPI_REQUESTS_PER_HOUR', '1500'))))
        if len(_CALLS) >= limit:
            raise RuntimeError('API_LOCAL_BUDGET_EXHAUSTED')
        _CALLS.append(now)
        self.counts[endpoint] += 1
        try:
            response = self.session.get(BASE + endpoint,
                params={'token': self.token, **params}, timeout=20)
            if response.status_code == 429:
                raise RuntimeError('API_MINUTE_LIMIT_NEAR')
            if response.status_code != 200:
                raise RuntimeError('BetsAPI HTTP ' + str(response.status_code))
            data = response.json()
        except requests.RequestException:
            raise RuntimeError('BetsAPI network request failed') from None
        if data.get('success') != 1:
            raise RuntimeError('BetsAPI access or response rejected')
        return data.get('results') or []

    def live(self):
        board = self.request('/v1/bet365/inplay_filter', sport_id=1)
        events = self.request('/v3/events/inplay', sport_id=1)
        by_id = {str(e['id']): e for e in events}
        by_bet = {str(e['bet365_id']): e for e in events if e.get('bet365_id')}
        result = []
        mappings = []
        for item in board:
            league = (item.get('league') or {}).get('name', '').lower()
            if 'esoccer' in league or 'virtual' in league:
                continue
            event = by_id.get(str(item.get('our_event_id'))) or by_bet.get(str(item['id']))
            if event is None:
                print('BetsAPI stats link unavailable:', item['id'], flush=True)
                continue
            f = normalize(event, item['id'])
            # Missing scores must not become a synthetic 0-0 in the parser.
            if any(v is None for v in f['goals'].values()):
                continue
            self.fixtures[f['fixture']['id']] = f
            result.append(f)
            mappings.append((f['fixture']['id'], str(event['id'])))
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute('CREATE TABLE IF NOT EXISTS betsapi_event_map (fixture_id INTEGER PRIMARY KEY,event_id TEXT NOT NULL)')
            db.executemany('INSERT OR REPLACE INTO betsapi_event_map VALUES (?,?)', mappings)
        print('BetsAPI Bet365 board:', len(board), '| mapped real football:', len(result), flush=True)
        return result

    def fixture(self, fid, fresh=False):
        if not fresh and fid in self.fixtures:
            return self.fixtures[fid]
        with sqlite3.connect(self.path, timeout=10) as db:
            row = db.execute('SELECT event_id FROM betsapi_event_map WHERE fixture_id=?', (fid,)).fetchone()
        if row is None:
            return None
        events = self.request('/v1/event/view', event_id=row[0])
        event = next((e for e in events if str(e.get('id')) == row[0]), None)
        if event is None:
            return None
        f = normalize(event, -fid)
        self.fixtures[fid] = f
        return f

    def get(self, endpoint, **params):
        fid = int(params.get('fixture', params.get('id', 0)))
        if fid > 0 and self.legacy:
            return self.legacy.get(endpoint, **params)
        if endpoint == '/fixtures' and fid < 0:
            f = self.fixture(fid, fresh=True)
            return [f] if f else []
        if endpoint == '/odds/live' and fid < 0:
            f = self.fixture(fid)
            if f is None or f['fixture']['status']['short'] != '1H':
                return []
            groups = self.request('/v1/bet365/event', FI=-fid)
            # Timestamp denotes receipt of this synchronous price response.
            return [{'fixture': {'id': fid, 'status': {'long': 'First Half'}},
                     'status': {}, 'update': datetime.now(timezone.utc).isoformat(),
                     'odds': market_values(groups)}]
        raise RuntimeError('Unsupported BetsAPI adapter request')

    def first_half_stats(self, fid, fresh=False):
        if fid > 0 and self.legacy:
            return self.legacy.first_half_stats(fid, fresh=fresh)
        f = self.fixture(fid, fresh=fresh)
        return statistics(f) if f else []

    def request_summary(self):
        return {'provider': 'BetsAPI', **dict(self.counts)}
