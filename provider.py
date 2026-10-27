"""API-Football v3 adapter with configurable quota reserve and live-stat TTL."""
import os
import time
import requests
from collections import deque, Counter
from engine import Snapshot
from rate_limit_v571 import guarded_get

BASE = 'https://v3.football.api-sports.io'
# Shared across FootballAPI instances created by scanner.scan() in a single worker.
_STAT_CACHE = {}
_REQUEST_TIMES = deque()  # shared local rolling-minute budget across scanner scans

def _cache_ttl():
    return max(0, int(os.getenv('STATS_CACHE_SECONDS', '60')))

class FootballAPI:
    def __init__(self, key: str):
        self.session = requests.Session()
        self.session.headers.update({'x-apisports-key': key})
        self._daily_remaining = None
        self._counts = Counter()

    def get(self, endpoint: str, **params):
        reserve = max(0, int(os.getenv('API_DAILY_RESERVE', '5000')))
        if self._daily_remaining is not None and self._daily_remaining <= reserve:
            raise RuntimeError('API_DAILY_RESERVE_REACHED')
        # Conservative local budget (~57,600/day at 40/min if sustained).
        now = time.monotonic()
        while _REQUEST_TIMES and now - _REQUEST_TIMES[0] >= 60:
            _REQUEST_TIMES.popleft()
        limit = max(5, min(200, int(os.getenv('API_LOCAL_REQUESTS_PER_MINUTE', '40'))))
        if len(_REQUEST_TIMES) >= limit:
            raise RuntimeError('API_LOCAL_BUDGET_EXHAUSTED')
        _REQUEST_TIMES.append(now)
        category = ('statistics' if endpoint == '/fixtures/statistics'
                    else 'live' if endpoint == '/fixtures' and params.get('live') == 'all'
                    else 'settlement_or_other')
        self._counts[category] += 1
        response = guarded_get(self.session, BASE + endpoint, params=params, timeout=20)
        remaining = response.headers.get('x-ratelimit-requests-remaining')
        minute_remaining = response.headers.get('X-RateLimit-Remaining')
        if remaining is not None:
            self._daily_remaining = int(remaining)
            print('API daily requests remaining:', self._daily_remaining, flush=True)
            if self._daily_remaining <= reserve:
                raise RuntimeError('API_DAILY_RESERVE_REACHED')
        if minute_remaining is not None and int(minute_remaining) <= 1:
            raise RuntimeError('API_MINUTE_LIMIT_NEAR')
        response.raise_for_status()
        data = response.json()
        if data.get('errors'):
            raise RuntimeError(f"API errors: {data['errors']}")
        return data.get('response', [])

    def request_summary(self):
        return dict(self._counts)

    def live(self):
        return self.get('/fixtures', live='all')

    def upcoming(self, date: str):
        return self.get('/fixtures', date=date, status='NS')

    def first_half_stats(self, fixture_id: int, fresh=False):
        ttl = _cache_ttl()
        now = time.monotonic()
        key = int(fixture_id)
        saved = _STAT_CACHE.get(key)
        if not fresh and ttl and saved and now - saved[0] < ttl:
            self._counts['stats_cache_hits'] += 1
            return saved[1]
        stats = self.get('/fixtures/statistics', fixture=fixture_id, half='true')
        if ttl:
            _STAT_CACHE[key] = (time.monotonic(), stats)
        if len(_STAT_CACHE) > 500:
            cutoff = time.monotonic() - max(ttl, 60) * 2
            for old_id, (when, _) in list(_STAT_CACHE.items()):
                if when < cutoff:
                    _STAT_CACHE.pop(old_id, None)
        return stats

    def prior_fixture_goals(self, team_id: int, last=12):
        fixtures = self.get('/fixtures', team=team_id, last=last)
        out = []
        for fixture in fixtures:
            half = fixture.get('score', {}).get('halftime') or {}
            if isinstance(half.get('home'), int) and isinstance(half.get('away'), int):
                out.append(half['home'] + half['away'])
        return out

def _number(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip().replace('%', ''))
        except ValueError:
            return None
    return None

def _stat_map(teams):
    totals = {}
    team_stats = []
    for item in teams:
        current = {}
        for stat in item.get('statistics', []):
            val = _number(stat.get('value'))
            if val is None:
                continue
            name = stat.get('type')
            current[name] = val
            if name != 'Ball Possession':
                totals[name] = totals.get(name, 0) + val
        team_stats.append(current)
    return totals, team_stats

def parse_fixture(fixture, stats=None, goal_rate=1.15, corner_rate=4.5):
    f = fixture.get('fixture', {})
    status = f.get('status', {})
    code = status.get('short', 'NS')
    elapsed = status.get('elapsed') or 0
    goals = fixture.get('goals') or {}
    halftime = (fixture.get('score') or {}).get('halftime') or {}
    if code == 'NS':
        half_goals = 0
    elif code == '1H':
        half_goals = (goals.get('home') or 0) + (goals.get('away') or 0)
    else:
        half_goals = (halftime.get('home') or 0) + (halftime.get('away') or 0)
    totals, teams = _stat_map(stats or [])
    corners = int(totals['Corner Kicks']) if 'Corner Kicks' in totals else None
    home_pos = teams[0].get('Ball Possession') if len(teams) > 0 else None
    away_pos = teams[1].get('Ball Possession') if len(teams) > 1 else None
    return Snapshot(
        fixture_id=f.get('id'), league=fixture.get('league', {}).get('name', ''),
        home=fixture.get('teams', {}).get('home', {}).get('name', 'Home'),
        away=fixture.get('teams', {}).get('away', {}).get('name', 'Away'),
        elapsed=elapsed, status=code, half_goals=half_goals,
        half_corners=corners, shots_on_goal=totals.get('Shots on Goal'),
        blocked_shots=totals.get('Blocked Shots'), total_shots=totals.get('Total Shots'),
        pre_goal_rate=goal_rate, pre_corner_rate=corner_rate,
        kickoff=f.get('date', ''), possession_home=home_pos, possession_away=away_pos)
