"""API-Football v3 adapter. Keep quota protections and missing-data handling."""
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
        return self.get('/fixtures/statistics', fixture=fixture_id, half='true')

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
