# Political Trends Tracker: Build Plan

A read-only X (Twitter) tracking app for political branding. It catches regional and national trends early, surfaces viral conversations, watches opponents' high-performing posts, and measures public sentiment.

**Decisions:** Python, a web dashboard, X API Basic or Pro tier, tracking only (no publishing).

---

## 1. Features

| # | Feature | What it does | X API endpoint |
|---|---|---|---|
| 1 | **Location trends** | Polls trends for each configured location every 15 minutes, stores snapshots, and flags topics that are **NEW** or **RISING** since the last snapshot. | `GET /2/trends/by/woeid/{woeid}` |
| 2 | **Viral keyword search** | Searches recent posts for tracked keywords and ranks them by engagement. Shows top hashtags and frequent words, to mirror the audience's language. | `GET /2/tweets/search/recent` |
| 3 | **Opponent tracking** | Polls recent posts from opponent handles, stores engagement snapshots over time, and ranks top posts. Shows per-account averages. | `GET /2/users/by`, `GET /2/users/{id}/tweets` |
| 4 | **Sentiment analysis** | Scores replies and mentions as positive, neutral or negative, charts the split over time, and raises an alert when negative sentiment spikes. | `GET /2/tweets/search/recent` (query `conversation_id:<id>` for replies) |
| + | **Usage panel** | Counts posts read this month against the tier's monthly cap. | n/a (stored locally) |

---

## 2. Tech stack

| Layer | Choice | Why |
|---|---|---|
| Backend | **FastAPI** + Uvicorn | Simple, fast and typed |
| HTTP client | **httpx** | Sync client, easy to mock in tests |
| Database | **SQLite** via **SQLModel** | No server needed; keeps history for charts |
| Scheduler | **APScheduler** (BackgroundScheduler, 3.x) | Polling jobs run inside the app process |
| Sentiment | **vaderSentiment** behind a `SentimentModel` interface | Fast, no GPU. A multilingual transformer can replace it later for Hindi, Telugu or Hinglish. |
| Frontend | **Jinja2** templates + **Chart.js** (CDN) + optional HTMX | No JS build step |
| Config | `.env` (secrets) + `config.yaml` (targets) | Targets can change without editing code |
| Tests | **pytest** + **respx** | respx mocks the X API responses |

`requirements.txt`:
```
fastapi>=0.110,<1.0
uvicorn[standard]>=0.29
jinja2>=3.1
python-multipart>=0.0.9
httpx>=0.27,<1.0
sqlmodel>=0.0.16
apscheduler>=3.10,<4.0
pydantic-settings>=2.2
pyyaml>=6.0
vaderSentiment>=3.3.2
pytest>=8.0
respx>=0.21
```
Python 3.9 or newer. On 3.9, use `Optional[...]` rather than `X | None` in SQLModel fields.

---

## 3. Project layout

```
tweet-trends/
  app/
    __init__.py
    main.py            # FastAPI app, routes, lifespan starts DB + scheduler
    config.py          # Settings (.env) + TrackingConfig (config.yaml)
    db.py              # engine, init_db(), session()
    models.py          # SQLModel tables
    x_client.py        # XClient (real) + MockXClient + get_client()
    jobs.py            # APScheduler setup + job wrappers that record errors
    services/
      __init__.py
      trends.py        # fetch + store snapshots, compute NEW/RISING deltas
      search.py        # fetch, store, engagement ranking, hashtags/words
      opponents.py     # resolve handles, fetch posts, metric snapshots, stats
      sentiment.py     # fetch replies/mentions, score, aggregate, alerts
    templates/
      base.html, dashboard.html, trends.html, search.html,
      opponents.html, sentiment.html
  tests/
    test_scoring.py, test_trends.py, test_sentiment.py, test_x_client.py
  data/                # SQLite file (gitignored)
  config.yaml
  .env.example
  .gitignore           # .env, data/*.db, __pycache__/
  requirements.txt
  README.md
```

---

## 4. Configuration

### `.env.example`
```
X_BEARER_TOKEN=
MOCK_MODE=true
DATABASE_URL=sqlite:///data/tracker.db
```

### `config.yaml`
```yaml
locations:              # WOEIDs
  - {name: India,     woeid: 23424848}
  - {name: Hyderabad, woeid: 2295414}
  - {name: Delhi,     woeid: 20070458}

keywords:               # each is an X search query
  - {name: Budget,  query: "budget -is:retweet lang:en"}
  - {name: Farmers, query: "(farmers OR kisan) -is:retweet"}

opponents:              # handles without @
  - examplehandle1
  - examplehandle2

sentiment_targets:      # kind: keyword (query) | post (tweet ID -> its replies)
  - {kind: keyword, value: "budget -is:retweet lang:en", label: Budget reaction}

polling:                # minutes; 0 disables a job
  trends_minutes: 15
  search_minutes: 30
  opponents_minutes: 60
  sentiment_minutes: 30
  search_max_results: 50      # 10-100 per page
  opponent_max_results: 20
  sentiment_max_results: 50

usage:
  monthly_read_cap: 15000     # set to your tier's post-read cap

alerts:
  negative_share_threshold: 0.4
  window_hours: 3
  min_samples: 10
```

`config.py` loads these into pydantic models (`Location`, `Keyword`, `SentimentTarget` with a `key` of `"{kind}:{value}"`, `Polling`, `Usage`, `Alerts`). It caches them with `lru_cache`.

---

## 5. Data model (`models.py`)

| Table | Fields |
|---|---|
| `TrendSnapshot` | id, woeid (index), location, name, rank, tweet_count (nullable), fetched_at (index) |
| `Post` | id (tweet ID, PK), author_id, author_username, text, lang, created_at, likes, retweets, replies, quotes, impressions, source (`search:<name>` or `opponent:<handle>`), fetched_at |
| `MetricSnapshot` | id, post_id (index), likes, retweets, replies, quotes, at |
| `SentimentScore` | id, target (index), tweet_id, text, compound, label, created_at, scored_at. Upsert on (target, tweet_id) so a reply is never scored twice. |
| `ApiCall` | id, endpoint, status, posts_read, rate_remaining, at. Feeds the usage panel. |

---

## 6. X API client (`x_client.py`)

- Base URL `https://api.x.com/2`, with header `Authorization: Bearer <token>`.
- Each request goes through one `_get(path, params)` method, which:
  - logs `x-rate-limit-remaining`
  - on **429**, reads `x-rate-limit-reset`. If the wait is 30 seconds or less, it sleeps and retries once. Otherwise it raises `XAPIError(429, "Rate limited; resets at HH:MM")`.
  - on **401/403**, raises `XAPIError` with a readable message ("check X_BEARER_TOKEN" or "your tier does not include this endpoint")
  - records an `ApiCall` row, with `posts_read` = number of tweets returned
- Pagination: `next_token` for search and `pagination_token` for user timelines, with `max_results` clamped to 10–100.
- `tweet.fields=created_at,public_metrics,author_id,lang,conversation_id`, plus `expansions=author_id&user.fields=username` so usernames come back.
- Every method returns **normalised dicts**: id, author_id, author_username, text, lang, created_at, likes, retweets, replies, quotes, impressions.
- Methods: `trends_by_woeid(woeid, max_trends)`, `search_recent(query, max_results)`, `users_by_usernames(list)`, `user_tweets(user_id, username, max_results)`.
- **`MockXClient`** has the same methods and returns generated data. Trends reshuffle every 15 minutes, so NEW/RISING shows up. Tweets mix positive, neutral and negative templates and use Pareto-distributed likes.
- `get_client()` returns the mock client when `MOCK_MODE=true`.

---

## 7. Services (core logic)

### 7.1 `trends.py`
- `refresh_trends()`: for each location, fetch trends and store one `TrendSnapshot` row per trend, ranked 1..N, with a shared `fetched_at`.
- `compute_deltas(current, previous)` is a pure function, so it can be unit-tested:
  - **NEW**: the topic is absent from the previous snapshot
  - **RISING**: the rank improved, or tweet_count grew by 20% or more
  - **FALLING** / **STEADY**: otherwise
- `latest_with_deltas(woeid)`: compares the latest snapshot with the one before it.
- `rank_history(woeid, name)`: series for a small chart per topic.

### 7.2 `search.py`
- `refresh_keyword(keyword)`: search, then upsert `Post` rows with `source="search:<name>"`.
- **Engagement score** (pure function):
  `engagement = likes + 2*retweets + replies + quotes`
  `velocity = engagement / max(age_hours, 1)`
  Rank by velocity by default; sorting by total engagement is an option.
- v2 recent search has **no `min_likes:` operator**, so the minimum-likes filter runs after fetching (a UI input).
- `top_hashtags(posts, n)` and `top_words(posts, n)`: regex tokenise, drop stop-words and URLs, count with `Counter`.

### 7.3 `opponents.py`
- Resolve handles to IDs once with `users_by_usernames`, and cache the IDs in memory.
- `refresh_opponents()`: fetch each timeline (excluding retweets and replies), upsert `Post` rows, and append a `MetricSnapshot` for every post on every poll.
- `account_stats(handle)`: post count, average engagement, best posting hour (UTC, also shown in IST), most-used hashtags.
- `engagement_growth(post_id)`: series from `MetricSnapshot`.

### 7.4 `sentiment.py`
- `SentimentModel` protocol with `score(text) -> float` (compound -1..1). `VaderModel` implements it.
- Labels: compound ≥ 0.05 → positive, ≤ −0.05 → negative, otherwise neutral.
- `refresh_target(target)`: the query is `conversation_id:<id> -is:retweet` for a post, or the keyword query itself. Score each result and skip ones already scored.
- `hourly_breakdown(target_key, hours=48)`: counts per hour bucket for a stacked bar chart.
- `alerts()`: a target triggers an alert when, within the last `window_hours`, samples ≥ `min_samples` and the negative share ≥ `negative_share_threshold`.
- An "Analyze now" form takes any post ID or keyword and runs it on demand.

---

## 8. Scheduler (`jobs.py`)
- `BackgroundScheduler`, with one interval job per feature taken from `polling.*_minutes` (skipped when 0). `next_run_time=now` makes each job run once at startup.
- Each job is wrapped in `run_job(name, fn)`, which catches `XAPIError` and other exceptions. It stores `{name: (time, message)}` in an in-memory `last_errors` dict, so the pages can show "Trends: 403 Forbidden, your tier does not include this endpoint" instead of crashing.
- `max_instances=1, coalesce=True` stops jobs from overlapping.

---

## 9. Web app (`main.py` + templates)

FastAPI `lifespan` calls `init_db()` and starts the scheduler, and shuts the scheduler down on exit. Route handlers are plain `def` functions, so they run in a threadpool.

| Route | Page |
|---|---|
| `GET /` | **Dashboard**: breaking trends (NEW/RISING) per location, top 5 viral posts, sentiment alerts, usage meter (posts read this month / cap), last job errors, MOCK MODE badge |
| `GET /trends?woeid=` | Location tabs and a table of rank, topic, volume and a delta badge (🆕 NEW, ▲ RISING, ▼ FALLING), with a rank-history mini chart |
| `GET /search?keyword=&min_likes=&sort=` | Ranked post table (text, author, likes/RT/replies, velocity, link to `https://x.com/i/status/<id>`), plus top hashtags and words |
| `GET /opponents?handle=` | Per-account stat cards, top posts table, and an engagement-growth chart |
| `GET /sentiment?target=` | Target picker, stacked bar chart (pos/neu/neg per hour), recent scored replies, and an "Analyze now" form |
| `POST /refresh/{job}` | Runs a job now (`trends`, `search`, `opponents`, `sentiment`), then redirects back with a 303 |
| `POST /sentiment/analyze` | Runs on-demand analysis for a post ID or keyword |
| `GET /api/usage` | JSON usage numbers |

Templates: `base.html` holds the nav, a CSS-variable theme with dark mode, and the Chart.js CDN `<script>` (pin an exact version, e.g. `chart.js@4.4.1`). Each page passes chart data to the template as JSON through `|tojson`.

---

## 10. Build order

1. Scaffold the folders, `requirements.txt`, `.env.example`, `.gitignore`, `config.yaml`
2. `config.py`, `models.py`, `db.py`
3. `x_client.py` (real and mock)
4. Services: trends → search → opponents → sentiment
5. `jobs.py`
6. `main.py` + templates
7. Tests
8. README (setup, finding WOEIDs, tier notes)

---

## 11. Setup and run (on the new machine)

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # Windows: copy .env.example .env
uvicorn app.main:app --reload
# open http://localhost:8000
```
To use live data, set `X_BEARER_TOKEN=...` and `MOCK_MODE=false` in `.env`, then restart.

---

## 12. Verification

1. **Mock mode:** with `MOCK_MODE=true`, every page renders with data. Press "Refresh now" twice on Trends a few minutes apart and NEW/RISING badges appear.
2. **Unit tests (`pytest`):**
   - `test_scoring.py`: engagement and velocity math, min-likes filter, hashtag extraction
   - `test_trends.py`: `compute_deltas` NEW/RISING/FALLING cases
   - `test_sentiment.py`: label thresholds, hourly aggregation, alert trigger
   - `test_x_client.py` (respx): normalisation with the `includes.users` join, pagination via `next_token`, 429 retry, 403 → readable `XAPIError`
3. **Live:** with a real token, use "Refresh now" on each page. Check the logs for `rate remaining` and confirm the usage meter goes up.
4. **Tier check:** if trends returns 403 on your tier, the Trends page shows the error banner and every other page keeps working.

---

## 13. Notes and limits
- **Tier quotas:** Basic has a small monthly post-read cap. Keep `max_results` and the poll intervals conservative and watch the usage meter. Each tweet returned counts as a read.
- **Trends endpoint access** varies by tier. Check it in your developer portal.
- **Recent search** covers only the last 7 days. Full-archive search needs Pro.
- **VADER is English-focused.** For Hindi, Telugu or Hinglish, add a `TransformerModel` (e.g. a multilingual XLM-R sentiment model) that implements the same `SentimentModel` interface, then switch to it in config.
- **Future extensions:** the filtered stream (`/2/tweets/search/stream`) for real-time sentiment on Pro, Telegram or email alerts, and a publishing module (needs OAuth 2.0 user context).
