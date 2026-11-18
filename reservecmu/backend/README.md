# ReserveCMU backend (FastAPI + SQLite)

```bash
pip install -r requirements.txt
python -m app.ingest data/raw_pages.txt data/availability.txt   # builds reservecmu.db
export OPENROUTER_API_KEY=sk-or-...                              # server-side only, never in the frontend
uvicorn app.main:app --reload                                    # docs at /docs
pytest
```

## Endpoints
| | |
|---|---|
| `GET /meta` | filter options (types, buildings, amenities), dates with availability data |
| `GET /spaces` | catalog search; same params as the agent tool (`min_capacity`, `start_time`, `end_time`, `amenities`, `noise_max`, ...) |
| `GET /spaces/{id}` | one space |
| `POST /agent/recommend` | `{"query": "...", "affiliation": "Heinz"?}` returns summary + up to 3 validated recommendations |
| `POST /feedback` | `{space_id, noise?, brightness?, temperature?}` (1 to 3 each) |

## How the agent works
The LLM does not read the database. It calls one tool, `search_spaces`, whose arguments are the same filters as `GET /spaces`. The server runs the query, returns compact results, and the model writes the explanation. Any `space_id` in its answer that the tool did not return is dropped. Event titles are never stored, so they cannot reach the model.

## Data caveats found in the scrape (read before launch)
1. **Availability now covers all 165 spaces** (both pages). Spaces without data for a date are excluded from recommendations by default.
2. **A static file only covers one date.** Use `python -m app.sync` or the background refresher so today and the next few days stay current.
3. **Noise, brightness and temperature do not exist in 25Live.** They stay empty until feedback arrives, and count as "verified" only after `MIN_REPORTS` (3) reports. The agent is instructed to say when a preference is unconfirmed. Consider seeding them from a staff walkthrough or a short survey.
4. **Table type is inferred** from 25Live layouts (flagged `derived_from_25live_layouts`).
5. **No printers, study rooms, or fitness rooms to speak of.** 25Live features are mostly AV/classroom equipment; only 3 gym spaces exist. Library study rooms, Highmark, etc. may be booked elsewhere.
6. `Building` was empty in the scrape, so it is inferred from names (a few oddities: "CUC TENT", "Fifth Ave Neighborhood Commons").
7. Affiliate-only rooms (Heinz, Tepper, Mellon) are hidden unless `affiliation` matches.
8. Verify the 25Live deep-link pattern (`LIVE25_SPACE_URL`).

## Syncing from 25Live
`python -m app.sync` fetches both pages of `listdata.json` and both pages of `availabilitydata.json` (page counts are read from the responses, not hardcoded), validates them against each other, and only then updates the database.

```bash
python -m app.sync                       # today (America/New_York)
python -m app.sync --date 2026-11-20     # a specific date
python -m app.sync --days 3 --dump merged.json   # several days; also write the merged snapshot
```
Safety rules: nothing is written unless every page fetched cleanly, availability covers at least 90% of spaces, there are no duplicate spaces across pages, and the returned date matches. Spaces are upserted (feedback is kept); spaces that vanish from 25Live are marked `active=0`. Event titles are never stored.

Set `REFRESH_ENABLED=1` and the API server runs the same sync every `REFRESH_SECONDS` (default 600) for today plus the next `REFRESH_DAYS-1` days. If a run fails, the previous data keeps being served and `GET /meta` shows `refresh.last_error`.

**Assumption to verify:** page 1 of availability uses your URL verbatim; page 2 appends `&page=2`. If 25Live ignores that parameter the sync stops with a "duplicate spaces" error instead of storing bad data. Get CMU's permission before running this on a schedule.

## Before production
Replace SQLite with Postgres if multiple workers write feedback; add CMU SSO so only people who booked can rate; rate-limit `/agent/recommend` and `/feedback`.
