# Narasaraopet Pulse: Build Plan

A read-only X (Twitter) monitoring dashboard for the office of the **Narasaraopet Lok Sabha MP** (Palnadu district, Andhra Pradesh). The MP represents **TDP** and is the party's **floor leader in Parliament**. The app tracks three things:

- **Local:** problems and conversations in the 7 assembly constituencies and 28 mandals.
- **Party:** TDP vs YSRCP pulse, and the opposition's attack lines.
- **Delhi:** Parliament, ministries, and national stories that touch Andhra Pradesh.

**Decisions:**
- Python, run in Docker, tracking only (no publishing).
- **X API pay-per-use credits**, under a strict daily budget.
- **OpenCode API** (OpenAI-compatible) for Telugu-aware tagging.
- English UI that shows Telugu content.
- Dashboard only (no push alerts).

---

## 1. Who uses it, and what they need

| User | Where | What they need |
|---|---|---|
| Office desk staff | Laptop, all day | Spot problems early, dig into a place or an issue, collect example posts |
| The MP | Phone, a quick glance | "Is anything blowing up? What's happening in Delhi?" in under 30 seconds |

Each main page answers one question:

| Question | Page |
|---|---|
| What needs attention right now? | **Today** |
| What's happening in my constituency, and where? | **Constituency** |
| How are TDP and YSRCP doing? | **Party Pulse** |
| What's moving in Delhi? | **Delhi** |

---

## 2. Features

| # | Feature | What it does | X API endpoint |
|---|---|---|---|
| 1 | **Local issue tracking** | Searches posts that mention any mandal or assembly constituency (Telugu and English names). Each post gets a place, an issue, a sentiment and an urgency. | `GET /2/tweets/search/recent` |
| 2 | **Party pulse** | Tracks TDP and YSRCP handles and keywords. Shows share of voice, sentiment, top posts, and YSRCP attack lines. | `GET /2/tweets/search/recent` (`from:` queries) |
| 3 | **Delhi watch** | Follows Parliament and ministry handles, AP-in-Delhi keywords, and India and Delhi trends. | `search/recent`, `GET /2/trends/by/woeid/{woeid}` |
| 4 | **Needs attention** | Groups negative local posts by place and issue, and raises the ones that are urgent or spiking. | n/a (computed) |
| 5 | **Explore** | Ad-hoc search or post ID lookup, with the credit cost shown before it runs. | `search/recent`, `GET /2/tweets` |
| + | **Usage & budget** | Shows X credit and LLM spend against the daily budget, and pauses jobs when the limit is reached. | n/a (stored locally) |

**Why search and not trends for local:** X trends are only published per WOEID (country or big city). There is no WOEID for Palnadu or Narasaraopet. So local visibility comes **only** from searching place names.

---

## 3. Tech stack

| Layer | Choice | Why |
|---|---|---|
| Backend | **FastAPI** + Uvicorn | Simple, fast and typed |
| HTTP client | **httpx** | One client for both the X API and the LLM API; easy to mock |
| Database | **SQLite** via **SQLModel** | No server needed; keeps history for charts |
| Scheduler | **APScheduler** (BackgroundScheduler, 3.x) | Polling jobs run inside the app process |
| Tagging | **OpenAI-compatible LLM** (OpenCode by default) behind a `Tagger` interface | Understands Telugu and Telugu-English; returns issue, place, stance and sentiment in one call |
| Fallback sentiment | **vaderSentiment** | Used only when no LLM key is set (English only) |
| Frontend | **Jinja2** + **HTMX** + **Chart.js** (CDN, pinned) | No JS build step |
| Fonts | Inter + **Noto Sans Telugu** (Google Fonts) | Telugu renders cleanly on every device |
| Config | `.env` (secrets), `config.yaml` (what to track), `places.yaml` (geography) | Staff can change targets without editing code |
| Deploy | **Docker** + docker compose | Same setup on any machine; data kept in a volume |
| Tests | **pytest** + **respx** | respx mocks the X and LLM APIs |

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
The Docker image uses Python 3.12. For local development without Docker, Python 3.9 or newer works. On 3.9, use `Optional[...]` rather than `X | None` in SQLModel fields.

---

## 4. Project layout

```
tweet-trends/
  app/
    __init__.py
    main.py              # FastAPI app, routes, lifespan starts DB + scheduler
    config.py            # Settings (.env) + TrackingConfig (config.yaml) + Places (places.yaml)
    db.py                # engine, init_db(), session()
    models.py            # SQLModel tables
    x_client.py          # XClient (real) + MockXClient + get_client()
    budget.py            # credit ledger, daily limit check, cost estimates
    jobs.py              # APScheduler setup + job wrappers that record errors
    services/
      __init__.py
      places.py          # gazetteer matching, per-AC query builder
      collect.py         # generic fetch: since_id, budget check, upsert, queue for tagging
      tagger.py          # LLMTagger, FallbackTagger, MockTagger
      trends.py          # India/Delhi trend snapshots, NEW/RISING deltas
      insights.py        # needs_attention, share_of_voice, attack_lines, heatmap, scoring
    templates/
      base.html          # shell: sidebar (desktop) / bottom tabs (phone), theme tokens
      _post_card.html    # shared post card
      _filters.html      # time window + AC filter bar
      today.html, constituency.html, place.html, party.html,
      delhi.html, explore.html, usage.html
    static/
      app.css
  tests/
    test_places.py, test_insights.py, test_tagger.py, test_budget.py,
    test_trends.py, test_x_client.py
  data/                  # SQLite file (gitignored, a Docker volume)
  config.yaml
  places.yaml
  .env.example
  .gitignore             # .env, data/*.db, __pycache__/, .venv/
  .dockerignore
  Dockerfile
  docker-compose.yml
  requirements.txt
  README.md
```

---

## 5. Configuration

### `.env.example`
```
# X API (pay-per-use credits)
X_BEARER_TOKEN=
MOCK_MODE=true

# LLM tagging (any OpenAI-compatible endpoint; OpenCode by default)
LLM_BASE_URL=https://opencode.ai/zen/v1
LLM_API_KEY=
LLM_MODEL=

DATABASE_URL=sqlite:///data/tracker.db
```
The OpenCode base URL and model name should be copied from the OpenCode dashboard. When `LLM_API_KEY` is empty, the app uses `FallbackTagger`. When `MOCK_MODE=true`, it uses `MockTagger`.

### `places.yaml`: the geography
Narasaraopet Lok Sabha covers the 7 assembly constituencies (ACs) of Palnadu district, with 28 mandals.

> **Check this list with the office.** Check the mandal-to-AC mapping and the spellings against the district gazette before going live. The file can be edited without touching code.

```yaml
district:
  name: Palnadu
  aliases: [Palnadu, Palnad, పల్నాడు]
lok_sabha:
  name: Narasaraopet
  aliases: [Narasaraopet, Narasaraopeta, NRT, నరసరావుపేట]

# ambiguous: true -> the name has other common meanings (a person, the AP capital,
# a town in another state). Count it only when another Palnadu term is in the same post.
assembly_constituencies:
  - name: Pedakurapadu
    aliases: [Pedakurapadu, పెదకూరపాడు]
    mandals:
      - {name: Pedakurapadu, aliases: [Pedakurapadu, పెదకూరపాడు]}
      - {name: Bellamkonda,  aliases: [Bellamkonda, బెల్లంకొండ], ambiguous: true}
      - {name: Atchampet,    aliases: [Atchampet, Achampet, అచ్చంపేట], ambiguous: true}
      - {name: Krosuru,      aliases: [Krosuru, Krosur, క్రోసూరు]}
      - {name: Amaravathi,   aliases: [Amaravathi, Amaravati, అమరావతి], ambiguous: true}
  - name: Chilakaluripet
    aliases: [Chilakaluripet, Chilakaluripeta, చిలకలూరిపేట]
    mandals:
      - {name: Chilakaluripet, aliases: [Chilakaluripet, చిలకలూరిపేట]}
      - {name: Nadendla,       aliases: [Nadendla, నాదెండ్ల], ambiguous: true}
      - {name: Edlapadu,       aliases: [Edlapadu, Yadlapadu, యడ్లపాడు]}
  - name: Narasaraopet
    aliases: [Narasaraopet, Narasaraopeta, నరసరావుపేట]
    mandals:
      - {name: Narasaraopet, aliases: [Narasaraopet, నరసరావుపేట]}
      - {name: Rompicherla,  aliases: [Rompicherla, రొంపిచర్ల]}
  - name: Sattenapalle
    aliases: [Sattenapalle, Sattenapalli, సత్తెనపల్లి]
    mandals:
      - {name: Sattenapalle, aliases: [Sattenapalle, Sattenapalli, సత్తెనపల్లి]}
      - {name: Rajupalem,    aliases: [Rajupalem, రాజుపాలెం], ambiguous: true}
      - {name: Muppalla,     aliases: [Muppalla, ముప్పాళ్ల]}
      - {name: Nekarikallu,  aliases: [Nekarikallu, Nakarikallu, నకరికల్లు]}
  - name: Vinukonda
    aliases: [Vinukonda, వినుకొండ]
    mandals:
      - {name: Vinukonda,    aliases: [Vinukonda, వినుకొండ]}
      - {name: Nuzendla,     aliases: [Nuzendla, నూజెండ్ల]}
      - {name: Savalyapuram, aliases: [Savalyapuram, శావల్యాపురం]}
      - {name: Ipur,         aliases: [Ipur, Ipuru, ఈపూరు]}
      - {name: Bollapalle,   aliases: [Bollapalle, Bollapalli, బొల్లాపల్లి]}
  - name: Gurajala
    aliases: [Gurajala, Gurazala, గురజాల]
    mandals:
      - {name: Gurajala,    aliases: [Gurajala, Gurazala, గురజాల]}
      - {name: Piduguralla, aliases: [Piduguralla, పిడుగురాళ్ల]}
      - {name: Dachepalle,  aliases: [Dachepalle, Dachepalli, దాచేపల్లి]}
      - {name: Machavaram,  aliases: [Machavaram, మాచవరం], ambiguous: true}
  - name: Macherla
    aliases: [Macherla, మాచర్ల]
    mandals:
      - {name: Macherla,      aliases: [Macherla, మాచర్ల]}
      - {name: Veldurthi,     aliases: [Veldurthi, Veldurti, వెల్దుర్తి], ambiguous: true}
      - {name: Durgi,         aliases: [Durgi, దుర్గి]}
      - {name: Rentachintala, aliases: [Rentachintala, రెంటచింతల]}
      - {name: Karempudi,     aliases: [Karempudi, Karampudi, కారంపూడి]}
```

### `config.yaml`: what to track
```yaml
# Local: one search query per AC is built automatically from places.yaml.
# Ambiguous aliases are left out of the X query (to save credits) and only used for matching.
local:
  extra_terms: ["Palnadu", "పల్నాడు"]
  query_suffix: "-is:retweet"

# Handles are placeholders. The office must fill these in and check them.
parties:
  tdp:
    label: TDP
    color: "#F2C200"
    handles: [JaiTDP, ncbn, naralokesh]     # add the MP's handle and local TDP MLAs
    keywords: ["#TDP", "తెలుగుదేశం"]
  ysrcp:
    label: YSRCP
    color: "#1F6FD1"
    handles: [YSRCParty, ysjagan]           # add local YSRCP leaders
    keywords: ["#YSRCP", "వైఎస్సార్సీపీ"]

delhi:
  handles: []        # Lok Sabha Secretariat, Sansad TV, PIB, key ministries: fill in and check
  keywords:
    - {name: AP in Parliament, query: '("Andhra Pradesh" OR AP) (Lok Sabha OR Parliament) -is:retweet'}
    - {name: Polavaram,        query: 'Polavaram -is:retweet'}
    - {name: Amaravati funds,  query: 'Amaravati (funds OR Centre OR Union) -is:retweet'}
    - {name: Special status,   query: '"special status" Andhra -is:retweet'}
  trends:
    - {name: India, woeid: 23424848}
    - {name: Delhi, woeid: 20070458}

issues:              # fixed categories the tagger must choose from
  - drinking_water
  - irrigation
  - farmers
  - roads
  - power
  - health
  - education
  - law_and_order
  - welfare_schemes
  - jobs
  - corruption
  - other

polling:             # base minutes; 0 disables. Adaptive backoff can stretch these.
  local_minutes: 30
  party_minutes: 30
  delhi_minutes: 60
  trends_minutes: 120
  tag_minutes: 5
  max_backoff_factor: 4
  max_results: 20             # per request, 10-100

budget:
  daily_credit_limit: 2.00     # in your billing currency; jobs pause when reached
  monthly_credit_limit: 50.00
  # Pay-per-use prices change. Copy the current ones from the X developer console.
  cost_per_post_read: 0.0
  cost_per_user_lookup: 0.0
  cost_per_trends_call: 0.0
  # LLM cost per 1M tokens (from your OpenCode dashboard)
  llm_cost_per_mtok_in: 0.0
  llm_cost_per_mtok_out: 0.0

alerts:
  window_hours: 6
  min_negative_posts: 5
  spike_factor: 2.0           # volume vs 7-day hourly baseline
  urgency_threshold: 2        # 0-3
```

`config.py` loads all three files into pydantic models and caches them with `lru_cache`.

---

## 6. Data model (`models.py`)

| Table | Fields |
|---|---|
| `Post` | id (tweet ID, PK), author_id, author_username, text, lang, created_at, likes, retweets, replies, quotes, impressions, conversation_id, **category** (`local` / `tdp` / `ysrcp` / `delhi` / `explore`), **source** (query key), fetched_at. Tag fields: **ac**, **mandal**, **issue**, **sentiment** (`pos`/`neu`/`neg`), **party_stance** (`pro_tdp`/`anti_tdp`/`pro_ysrcp`/`anti_ysrcp`/`neutral`), **urgency** (0–3), **summary_en**, **tag_status** (`pending`/`done`/`failed`), tagged_at |
| `MetricSnapshot` | id, post_id (index), likes, retweets, replies, quotes, at. Used for party posts' engagement growth. |
| `TrendSnapshot` | id, woeid (index), location, name, rank, tweet_count (nullable), fetched_at (index) |
| `QueryState` | key (PK), since_id, interval_minutes, empty_runs, last_new_count, last_run_at. Drives `since_id` and adaptive polling. |
| `CreditLedger` | id, kind (`x` / `llm`), endpoint, units (posts read, or tokens), cost, category, at. Feeds the budget and the usage page. |

Index `Post` on (category, created_at) and (ac, issue, created_at).

---

## 7. X API client (`x_client.py`)

- Base URL `https://api.x.com/2`, with header `Authorization: Bearer <token>`.
- Each request goes through one `_get(path, params)` method, which:
  - logs `x-rate-limit-remaining`
  - on **429**, reads `x-rate-limit-reset`. If the wait is 30 seconds or less, it sleeps and retries once. Otherwise it raises `XAPIError(429, "Rate limited; resets at HH:MM")`.
  - on **401/403**, raises `XAPIError` with a readable message ("check X_BEARER_TOKEN", or "your plan does not include this endpoint")
  - on **402** or a credits-exhausted error, raises `XAPIError` with "X credits exhausted; top up in the developer console"
  - writes a `CreditLedger` row, with units = number of posts returned and cost = units × configured price
- Supports `since_id` and `next_token` pagination, with `max_results` clamped to 10–100.
- Uses `tweet.fields=created_at,public_metrics,author_id,lang,conversation_id` plus `expansions=author_id&user.fields=username`.
- Every method returns **normalised dicts**: id, author_id, author_username, text, lang, created_at, conversation_id, likes, retweets, replies, quotes, impressions.
- Methods: `trends_by_woeid(woeid, max_trends)`, `search_recent(query, max_results, since_id=None)`, `tweets_by_ids(ids)`.
- **`MockXClient`** has the same methods and returns generated data:
  - Telugu, English and mixed posts that use real mandal names, both parties' handles, and Delhi topics.
  - A mix of positive, neutral and negative posts, with Pareto-distributed likes.
  - Every few runs, one mandal gets a "spike" of negative posts about one issue, so the Needs-attention cards show up.
  - Trends reshuffle every 15 minutes, so NEW/RISING shows up.
- `get_client()` returns the mock client when `MOCK_MODE=true`.

---

## 8. Credit budget (`budget.py`)

Pay-per-use means **every post read costs money**. The app treats the budget as a hard limit.

- `spent_today()` and `spent_this_month()` sum `CreditLedger.cost`. Days and months are in IST.
- `can_spend(estimate)`: every job calls this before making a request. If the spend would cross the daily or monthly limit, the job skips the run and records "Paused: daily budget reached". The UI shows this as a banner.
- `estimate_cost(max_results)`: used by Explore to show the price before running.
- Ways the app saves credits:
  1. **`since_id` on every query** (stored in `QueryState`), so it never pays twice for a post it has already seen.
  2. **Small pages:** `max_results` defaults to 20.
  3. **Priority tiers:** local and party queries poll every 30 minutes, Delhi every 60, trends every 120.
  4. **Adaptive polling:** after 2 empty runs, the interval doubles, up to `max_backoff_factor` times the base. Any new posts reset it to the base interval.
  5. **Batch handles:** one search with `from:a OR from:b OR …` per party, not one timeline call per handle.
  6. **Ambiguous names stay out of the X query.** They are only used for matching posts that are already fetched.
  7. A **Pause all** switch on the Usage page.

---

## 9. Services (core logic)

### 9.1 `places.py`
- `build_ac_queries(places) -> {ac: query}`: ORs together each AC's unambiguous aliases (mandals and AC names) and appends `-is:retweet`. If a query is longer than X's query length limit (512 characters on lower access levels; check your account), it is split into parts (`ac:1`, `ac:2`).
- `match_places(text) -> [(ac, mandal)]`: a case-insensitive regex built from all aliases, with word boundaries for Latin script and plain substring matching for Telugu. Ambiguous aliases only count when another Palnadu or Narasaraopet term is in the same post. This is a pure function, it costs nothing, and it runs before the LLM.

### 9.2 `collect.py`
- `collect(query_key, query, category)`:
  1. checks the budget
  2. reads `since_id` from `QueryState`
  3. calls `search_recent`
  4. upserts the posts with `tag_status=pending`
  5. updates `QueryState` (since_id, empty_runs, interval)
- Party collection also appends a `MetricSnapshot` for each party post on every poll, for engagement growth.
- One function covers local, party, Delhi and Explore. The callers just pass different queries and categories.

### 9.3 `tagger.py`
- `Tagger` protocol: `tag(posts) -> list[Tag]`.
- **`LLMTagger`**:
  - Calls `POST {LLM_BASE_URL}/chat/completions` with httpx. Batches of 20 posts, temperature 0, and JSON response format where the provider supports it.
  - The system prompt includes the issue list, the AC and mandal list, and the party definitions. Each post comes with its `match_places` hint.
  - It must return, for every post: `{id, sentiment, issue, party_stance, ac, mandal, urgency, summary_en}`.
  - `summary_en` is a one-line English gist, so staff can scan Telugu posts quickly.
  - Validates the output with pydantic and drops unknown ACs and issues to `null`/`other`. On a bad parse it retries the batch once, then marks the posts `failed`. It never crashes the job.
  - Records tokens and cost in `CreditLedger` (`kind=llm`).
- **`FallbackTagger`**: VADER sentiment and `match_places` only (no issue or stance). Used when no LLM key is set.
- **`MockTagger`**: deterministic tags generated from the mock posts.
- `tag_pending()`: a job that tags all `pending` posts, oldest first.

### 9.4 `trends.py` (unchanged logic, Delhi page only)
- `refresh_trends()`: for India and Delhi, fetch trends and store `TrendSnapshot` rows ranked 1..N.
- `compute_deltas(current, previous)`, a pure function:
  - **NEW**: the topic is absent from the previous snapshot
  - **RISING**: the rank improved, or tweet_count grew by 20% or more
  - **FALLING** / **STEADY**: otherwise
- `latest_with_deltas(woeid)` and `rank_history(woeid, name)`.

### 9.5 `insights.py` (pure functions over query results, unit-tested)
- `engagement = likes + 2*retweets + replies + quotes`, and `velocity = engagement / max(age_hours, 1)`.
- `needs_attention(posts, baseline, cfg)`:
  - Groups local posts from the last `window_hours` by (ac, mandal, issue).
  - Flags a group when negative posts ≥ `min_negative_posts`, **and** either max urgency ≥ `urgency_threshold` or volume ≥ `spike_factor` × the 7-day baseline.
  - Level: **red** if both conditions hold, **amber** if one does.
  - Returns groups ranked by `neg_count × (1 + max_urgency) × spike_ratio`, each with its 3 top posts.
- `share_of_voice(posts, bucket="hour")`: TDP vs YSRCP post count and engagement per bucket.
- `sentiment_split(posts, by)`: pos/neu/neg counts by party, AC or hour.
- `attack_lines(posts)`: among `ysrcp` posts with `party_stance=anti_tdp`, the top issues by engagement, with 2 example posts each. This is what the opposition is hitting TDP on.
- `issue_heatmap(posts)`: AC × issue counts, plus each cell's negative share.
- `top_hashtags(posts, n)` and `top_words(posts, n)`, which keep Telugu words and drop stop-words and URLs.

---

## 10. Scheduler (`jobs.py`)

- `BackgroundScheduler` with these jobs:
  - `local` (each AC query)
  - `party` (TDP and YSRCP)
  - `delhi` (handles and keywords)
  - `trends`
  - `tag` (tags pending posts)
- Intervals come from `polling.*`, and a job is skipped when its interval is 0. `next_run_time=now` makes each job run once at startup.
- Adaptive polling: the job runs at the base interval, and `collect()` skips any query whose `QueryState.interval_minutes` has not passed since `last_run_at`.
- Each job is wrapped in `run_job(name, fn)`. It catches `XAPIError` and other exceptions, and stores `{name: (time, message)}` in `last_errors`, so pages show "Delhi: 403, your plan does not include this endpoint" instead of crashing. A budget pause is recorded the same way.
- `max_instances=1, coalesce=True` stops jobs from overlapping.

---

## 11. UI: designed from first principles

### 11.1 Principles
1. **One question per page.** Each page title is the question it answers.
2. **The most urgent thing goes at the top.** Today opens with Needs-attention cards, not charts.
3. **Read the gist, not the tweet.** Every post shows a bold one-line English summary, with the original Telugu below it.
4. **Always say where.** Every local item carries its AC › mandal chip. Clicking the chip filters by that place.
5. **Always say how fresh.** Times are in IST, and each page shows "Updated 4 min ago". A banner shows if data is stale, a job is failing, or the budget is paused.
6. **Glanceable on a phone.** Big tap targets, no tables wider than the screen, and charts that fit 360px.
7. **Colour means something.** Red and amber only for alert levels. TDP yellow only for TDP data. YSRCP blue only for YSRCP data. Green, grey and red only for sentiment. Everything else is neutral.

### 11.2 Layout
- **Desktop:** a left sidebar with Today, Constituency, Party Pulse, Delhi, Explore and Usage. The page content sits to the right, max width about 1200px.
- **Phone:** a bottom tab bar with Today, Constituency, Party and Delhi. Explore and Usage go under a "More" menu.
- **Filter bar** (sticky, under the page title): time window (6h / 24h / 7d) and AC. The filters live in the URL, so a filtered view can be shared or bookmarked.
- **Theme:** CSS-variable tokens in `:root`, with dark mode via `prefers-color-scheme` and a manual toggle. Neutral surfaces, an 8px spacing grid, Inter for UI text, and Noto Sans Telugu for Telugu text.

### 11.3 Pages

| Route | Page | Contents (top to bottom) |
|---|---|---|
| `GET /` | **Today**: *What needs attention right now?* | 1. **Needs attention** cards (red/amber stripe, `Macherla › Durgi`, "Drinking water", "12 negative posts · 3× usual", 1-line gist, *View posts*). Empty state: "Nothing urgent in the last 6 hours ✓". 2. **4 KPI tiles**: local mentions (24h, change vs yesterday); TDP vs YSRCP share of voice; local negative %; credits spent today / limit. 3. **Delhi strip**: latest 3 Delhi items. 4. **Breaking trends**: NEW/RISING in India and Delhi. The page refreshes via HTMX every 2 minutes. |
| `GET /constituency` | **Constituency**: *What's happening, and where?* | 1. A grid of **7 AC cards** (post volume, a pos/neu/neg bar, top issue, a red/amber dot if any alert). 2. An **AC × issue heatmap** (colour = volume, red outline = negative share above 50%). Clicking a cell opens that AC and issue. |
| `GET /place?ac=&mandal=&issue=` | **Place drill-down** | Breadcrumb `Constituency › Macherla › Durgi`. Mandal chips with counts. Issue chips. A volume-by-hour chart. A post feed sorted by velocity. |
| `GET /party` | **Party Pulse**: *How are TDP and YSRCP doing?* | 1. A **share-of-voice** line chart (TDP yellow, YSRCP blue). 2. A **sentiment by party** split bar. 3. **YSRCP attack lines**: issue, engagement, and 2 example posts, so staff see what needs a response. 4. **Top TDP posts** and **top YSRCP posts** by velocity, side by side on desktop and stacked on a phone. |
| `GET /delhi` | **Delhi**: *What's moving in Delhi?* | 1. **Parliament and ministries** feed. 2. **AP in Delhi** keyword feeds (Polavaram, Amaravati funds, …) as tabs. 3. **India and Delhi trends** with 🆕 NEW / ▲ RISING / ▼ FALLING badges and a rank-history sparkline. |
| `GET /explore` | **Explore** | A query or post-ID box. Shows "≈ N posts, ≈ cost X" and a confirm button. Results come back tagged, as post cards. |
| `GET /usage` | **Usage & status** | Credit and LLM spend today and this month vs the limits (meters). Spend by category (local / party / Delhi / explore). A table of jobs (last run, next run, interval, last error). A Pause/Resume all button. A MOCK MODE badge. |
| `POST /refresh/{job}` | | Runs a job now (`local`, `party`, `delhi`, `trends`, `tag`), then redirects back with a 303 |
| `POST /explore` | | Runs an ad-hoc query after confirmation |
| `POST /pause`, `POST /resume` | | Toggle all jobs |
| `GET /api/usage` | | JSON spend numbers |
| `GET /healthz` | | 200 when the DB is reachable and the scheduler is running (used by the Docker healthcheck) |

### 11.4 The post card (`_post_card.html`, used everywhere)
```
┌───────────────────────────────────────────────────────┐
│ ● neg   Macherla › Durgi   Drinking water   2h ago    │
│ **No drinking water supply for 5 days in Durgi**      │  <- summary_en
│ దుర్గిలో 5 రోజులుగా తాగునీరు రావడం లేదు...                │  <- original text
│ @username · ♥ 120  ⟲ 45  💬 30        Open on X ↗    │
└───────────────────────────────────────────────────────┘
```
The card is used for local, party and Delhi posts. A party post shows a stance chip instead of a place chip.

### 11.5 Frontend tech
- Jinja2 templates. Each page passes chart data through `|tojson`.
- Chart.js `chart.js@4.4.1` and `htmx.org@1.9.12` from a CDN, with pinned versions.
- No JS build step. One `static/app.css`.

---

## 12. Run with Docker

### `Dockerfile`
```dockerfile
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TZ=Asia/Kolkata
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ app/
COPY config.yaml places.yaml ./
RUN useradd -m appuser && mkdir -p /app/data && chown -R appuser /app/data
USER appuser
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz')"
# Exactly 1 worker: the scheduler runs in-process; more workers would double-poll and double-spend credits.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
```

### `docker-compose.yml`
```yaml
services:
  tracker:
    build: .
    env_file: .env
    environment:
      TZ: Asia/Kolkata
    ports:
      - "8000:8000"
    volumes:
      - tracker-data:/app/data
      - ./config.yaml:/app/config.yaml:ro
      - ./places.yaml:/app/places.yaml:ro
    restart: unless-stopped
volumes:
  tracker-data:
```

### `.dockerignore`
```
.env
data/
.venv/
__pycache__/
.git/
tests/
```

### Run
```bash
cp .env.example .env               # Windows: copy .env.example .env
docker compose up -d --build
# open http://localhost:8000
docker compose logs -f             # watch the jobs
```
- **Edit what is tracked:** change `config.yaml` or `places.yaml`, then run `docker compose restart`.
- **Go live:** set `X_BEARER_TOKEN`, `LLM_API_KEY`, `LLM_MODEL` and `MOCK_MODE=false` in `.env`, set real prices in `config.yaml`, then run `docker compose up -d`.
- **Back up the data:** `docker compose cp tracker:/app/data/tracker.db ./backup-$(date +%F).db`

### Without Docker (development)
```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
```

---

## 13. Build order

1. Scaffold the folders, `requirements.txt`, `.env.example`, `.gitignore`, `.dockerignore`, `Dockerfile`, `docker-compose.yml`, `config.yaml` and `places.yaml`
2. `config.py` (all three config files), `models.py`, `db.py`
3. `x_client.py` (real and mock) and `budget.py`
4. `services/places.py`, then `collect.py`
5. `services/tagger.py` (LLM, fallback and mock)
6. `services/trends.py`, then `insights.py`
7. `jobs.py`
8. `main.py`, `base.html`, `_post_card.html`, then the pages in order: Today, Constituency/Place, Party, Delhi, Explore, Usage
9. Tests
10. README (Docker setup, filling in handles, checking places, setting prices)

---

## 14. Verification

1. **Docker, mock mode:** `docker compose up --build` with `MOCK_MODE=true` and no keys. Every page renders with data, `/healthz` returns 200, and `docker compose ps` shows the container as healthy.
2. **Data persists:** after `docker compose restart`, the history and charts are still there.
3. **Needs attention:** within a few mock runs, a red or amber card appears for a mandal, and clicking it opens the Place page filtered to that mandal and issue.
4. **Phone layout:** at 360px width the bottom tabs show, there is no horizontal scroll, and Telugu renders in Noto Sans Telugu. Dark mode works.
5. **Budget:** set `daily_credit_limit` very low with non-zero prices in mock mode. Jobs pause, the banner shows, and Pause/Resume works.
6. **Unit tests (`pytest`):**
   - `test_places.py`: English, spelling-variant and Telugu matching; ambiguous names (e.g. "Amaravati" alone → no match, "Amaravathi mandal, Palnadu" → match); the AC query builder splits queries over the length limit
   - `test_insights.py`: `needs_attention` thresholds and levels, `share_of_voice` buckets, `attack_lines`, engagement and velocity math
   - `test_tagger.py` (respx on the LLM endpoint): valid JSON → tags; bad JSON → one retry, then `failed`; unknown issue → `other`; no key → FallbackTagger
   - `test_budget.py`: `can_spend` at and over the limit, IST day boundary, `since_id` passed through, adaptive backoff and reset
   - `test_trends.py`: `compute_deltas` NEW/RISING/FALLING cases
   - `test_x_client.py` (respx): normalisation with the `includes.users` join, `since_id` and `next_token`, 429 retry, 403 and credits-exhausted → readable `XAPIError`
7. **Live:** with real keys and a small budget, use "Refresh now" on each page. Check that the ledger and Usage page match the X developer console within a day, and that Telugu posts get sensible English gists.

---

## 15. Notes and limits

- **No local trends:** X has no WOEID for Palnadu or Narasaraopet. Local visibility depends entirely on the place-name search, so keep `places.yaml` accurate and add common local spellings as staff spot them.
- **Recent search covers only the last 7 days.**
- **Pay-per-use prices and rules change.** Keep the prices in `config.yaml` up to date from the developer console. The app's cost figures are estimates; the console is the source of truth.
- **Handles must be checked.** The handles in `config.yaml` are starting points. The office should confirm the MP's own handle, local MLAs, YSRCP local leaders, and official Delhi handles.
- **LLM tags are a guide, not a verdict.** Staff should open the original post before acting on it. Keep `temperature=0` and the fixed issue list so tags stay consistent.
- **Privacy:** the app reads public posts only. Do not export or share individual citizens' posts outside the office.
- **Future extensions:** a Telegram or WhatsApp digest, a weekly PDF report for the MP, full-archive search, the filtered stream for real-time coverage, and a publishing module (needs OAuth 2.0 user context).
