"""Read-only market coverage probe for API-Football in-play odds.

Use on Render Shell: python odds_probe_v53.py
Optional: ODDS_TEST_FIXTURE_ID=123456 python odds_probe_v53.py
Never prints secrets or places bets.
"""
import os
from datetime import datetime, timezone
import requests

BASE = 'https://v3.football.api-sports.io'
TIMEOUT = 15

def get(path, params=None):
    key = os.getenv('API_FOOTBALL_KEY')
    if not key:
        raise RuntimeError('API_FOOTBALL_KEY is not configured')
    r = requests.get(BASE + path, headers={'x-apisports-key': key}, params=params, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    if data.get('errors'):
        raise RuntimeError('Provider returned errors: ' + str(data['errors'])[:300])
    return data.get('response', [])

def main():
    print('ODDS COVERAGE PROBE (read-only)', datetime.now(timezone.utc).isoformat(), flush=True)
    print('Warning: These endpoints consume API requests. No bets are placed.', flush=True)
    bets = get('/odds/live/bets')
    keywords = ('half', 'corner', 'goal', 'over', 'under')
    relevant = [(b.get('id'), b.get('name')) for b in bets if any(k in (b.get('name') or '').lower() for k in keywords)]
    print('Live bet catalog entries:', len(bets), flush=True)
    print('Relevant bet names / IDs (for verification, not automatic matching):', flush=True)
    for bid, name in relevant:
        print(' ', bid, repr(name), flush=True)
    fixture = os.getenv('ODDS_TEST_FIXTURE_ID', '').strip()
    if not fixture:
        print('Fixture odds not queried. Set ODDS_TEST_FIXTURE_ID to an active fixture ID to inspect bookmaker and line coverage.', flush=True)
        return
    if not fixture.isdigit():
        raise ValueError('ODDS_TEST_FIXTURE_ID must be numeric')
    data = get('/odds/live', {'fixture': fixture})
    print('Fixtures returned:', len(data), flush=True)
    for item in data[:2]:
        status = item.get('status', {})
        print('Status:', status, 'Source update:', item.get('update'), flush=True)
        # Print bookmaker labels and candidate market names only; no guessing lines.
        for book in item.get('bookmakers', []):
            name = book.get('name', 'unnamed')
            bets_found = book.get('bets', [])
            candidate = [(b.get('name'), b.get('id')) for b in bets_found if any(k in (b.get('name') or '').lower() for k in ('half','corner','goal'))]
            print('Bookmaker:', name, '| Candidate markets:', candidate[:30], flush=True)
    print('Do not use odds in alerts until exact bookmaker, period, line, side, timestamp, and active status are verified.', flush=True)

if __name__ == '__main__':
    main()
