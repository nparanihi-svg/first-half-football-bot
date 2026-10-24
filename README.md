# First Half Signal Lab (prototype v0.1)

A runnable **research/paper-trading** dashboard for exactly two markets:

- **FH over 0.5 goals**: one or more total goals in first half.
- **FH over 2.5 Asian corners**: three or more first-half corners. Because the line ends in .5, there is no push.

Includes demo mode, optional licensed API-Football live/pre-match fixtures, simple conditional Poisson baseline forecasts, manual verified odds import, calculated theoretical edges, SQLite signals, and optional Telegram messages. **No automatic bet placement or bet365 scraping.**

## Quick start

Python 3.10+ recommended:

```bash
python -m venv .venv
# macOS/Linux:
source .venv/bin/activate
# Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env  # or copy manually on Windows
streamlit run app.py
```

Open the local URL displayed by Streamlit. Demo mode needs no key. For real live/pre-match fixtures set `API_FOOTBALL_KEY` in `.env`. Select the API mode from the sidebar. Each refresh consumes provider quota; keep fixture limit modest.

To test: `pytest -q`.

## Data and market semantics

- The live adapter requests `/fixtures?live=all`, filters to `1H`, then `/fixtures/statistics?fixture=...&half=true`. The provider's *half-specific* statistics must be available to compute first-half corners. Missing data is **not** zero.
- Pre-match upcoming fixtures use `/fixtures?date=YYYY-MM-DD&status=NS`. No corners have occurred, so 0 is valid. Both pre-match means are currently **manually configured generic assumptions**; historical corner rates are **not** yet fitted.
- Current-score validation means we never signal first-half goals after a goal or first-half over 2.5 corners after three corners. If results disappear or markets suspend, user must recheck.
- This prototype does **not** automatically load or match bookmaker odds. Enter verified decimal odds as CSV by fixture ID and explicit market code. The theoretical EV is `p * decimal_odds - 1`, not realised ROI.
- `API-Football` live and pre-match bet IDs are different, and exact first-half Asian corners coverage cannot be assumed. Do not map bets by guessed IDs or scrape bet365.
- Baseline model `P(at least k more events) = 1 - exp(-mu) * sum(mu**i/i!, i=0..k-1)`. `mu` = assumed full-half event mean × fraction of 45 minutes remaining × capped shot-volume adjustment. This is only an illustrative baseline, not a historical-fit, model-calibrated production prediction. Stoppage time and game state are not modelled.

## Critical limitations / before real use

1. **Historical calibration**: collect provider-licensed first-half goals/corners and point-in-time in-play events for many matches, avoiding future leakage. Fit distinct conditional intensity models for goals and corners, league/team random effects, game states and stoppage time; evaluate by forward-time split, calibration plot, Brier/log-loss, probability buckets and realistic simulated execution.
2. **Better shot trend**: current script only uses cumulative shots as a lightweight proxy; add snapshot deltas (last 5/10 minutes), red cards, possessions and confirmed data freshness.
3. **Odds adapter**: use provider-approved documented market IDs for *each* of the two exact first-half selections. Confirm decimal prices refer to current first-half target, not full match or an adjusted in-play line. Record timestamps, suspension status, market availability and bookmaker; reject stale quotes. Odds endpoint's historical in-play prices may be unavailable: capture as they arrive.
4. **Actual recurring monitoring**: Streamlit refresh/manual reload is not a 24/7 background daemon. Deploy a dedicated scheduled worker (with rate limiting, scheduler/Redis, separate database, logs, exception handling and deduplication) and keep dashboard read-only.
5. **Alert robustness**: add an idempotent queue, durable delivery states, one alert per fixture/market window and retries. Current default is OFF and only attempts eligible alerts on a new database row; failed sends need manual retry handling.
6. **Settlement**: create official halftime outcome ingestion, verify corrections and void/abandonment rules. Database reserves `outcome` but auto settlement is not implemented.
7. **Quality gate**: require per-league verified data coverage and data age limits, disable alerts for unknown first-half corner counts or scoring anomalies, and avoid pseudo-exact probabilities in live use until model backtesting succeeds.
8. **Betting legality and safety**: check local law and provider terms; no bet365 scraping or automated wagering is included. Use risk limits. A high model score is not proof of an edge.

## Database

`signals` records timestamps, fixture, market, minute, model version, probability, odds, EV, source, alert state and nullable outcome. `snapshots` is reserved for point-in-time feature history. Production should include unique event timestamps, league/team IDs, data-provider metadata, bookmaker/market IDs, feed latency, and tracked corrections.

## Sources

- API-Football documentation: https://www.api-football.com/documentation_v3
- API-Football getting started: https://www.api-football.com/news/post/how-to-get-started-with-api-football-the-complete-beginners-guide
- Telegram Bot API: https://core.telegram.org/bots/api#sendmessage
