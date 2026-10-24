"""API-Football v3 adapter. API credentials required for live mode."""
import requests
from engine import Snapshot

BASE = 'https://v3.football.api-sports.io'


class FootballAPI:
    def __init__(self, key: str):
        self.session = requests.Session()
        self.session.headers.update({'x-apisports-key': key})
        self._daily_remaining = None

    def get(self, endpoint: str, **params):
        if self._daily_remaining is not None and self._daily_remaining <= 1500:
            raise RuntimeError('API_DAILY_RESERVE_REACHED')

        response = self.session.get(BASE + endpoint, params=params, timeout=20)
        remaining = response.headers.get('x-ratelimit-requests-remaining')
        minute_remaining = response.headers.get('X-RateLimit-Remaining')

        if remaining is not None:
            self._daily_remaining = int(remaining)
            print('API daily requests remaining:', self._daily_remaining, flush=True)
            if self._daily_remaining <= 1500:
                raise RuntimeError('API_DAILY_RESERVE_REACHED')

        if minute_remaining is not None and int(minute_remaining) <= 1:
            raise RuntimeError('API_MINUTE_LIMIT_NEAR')

        response.raise_for_status()
        data = response.json()
        if data.get('errors'):
            raise RuntimeError(f"API errors: {data['errors']}")
        return data.get('response', [])

    def live(self):
        return self.get('/fixtures', live='all')

    def upcoming(self, date: str):
        return self.get('/fixtures', date=date, status='NS')

    def first_half_stats(self, fixture_id: int):
        """Half-specific statistics; do not use full-match cumulative corners in 2H."""
        return self.get('/fixtures/statistics', fixture=fixture_id, half='true')

    def prior_fixture_goals(self, team_id: int, last=12):
        fixtures = self.get('/fixtures', team=team_id, last=last)
        first_half = []
        for fixture in fixtures:
            h = fixture.get('score', {}).get('halftime') or {}
            if isinstance(h.get('home'), int) and isinstance(h.get('away'), int):
                first_half.append(h['home'] + h['away'])
        return first_half


def _stat_map(teams):
    totals = {}
    for item in teams:
        for s in item.get('statistics', []):
            v = s.get('value')
            if isinstance(v, (int, float)):
                totals[s.get('type')] = totals.get(s.get('type'), 0) + v
    return totals


def parse_fixture(fixture, stats=None, goal_rate=1.15, corner_rate=4.5):
    f = fixture.get('fixture', {})
    status = f.get('status', {})
    code = status.get('short', 'NS')
    elapsed = status.get('elapsed') or 0
    goals = fixture.get('goals', {})
    scores = fixture.get('score', {})
    halftime = scores.get('halftime') or {}
    if code == 'NS':
        half_goals = 0
    elif code == '1H':
        half_goals = (goals.get('home') or 0) + (goals.get('away') or 0)
    else:
        half_goals = (halftime.get('home') or 0) + (halftime.get('away') or 0)
    m = _stat_map(stats or [])
    # Lack of stats is missing data, not zero corners.
    corners = int(m['Corner Kicks']) if 'Corner Kicks' in m else None
    return Snapshot(
        fixture_id=f.get('id'), league=fixture.get('league', {}).get('name', ''),
        home=fixture.get('teams', {}).get('home', {}).get('name', 'Home'),
        away=fixture.get('teams', {}).get('away', {}).get('name', 'Away'),
        elapsed=elapsed, status=code, half_goals=half_goals,
        half_corners=corners, shots_on_goal=m.get('Shots on Goal'),
        blocked_shots=m.get('Blocked Shots'), total_shots=m.get('Total Shots'),
        pre_goal_rate=goal_rate, pre_corner_rate=corner_rate,
        kickoff=f.get('date', ''))
