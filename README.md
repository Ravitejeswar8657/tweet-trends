# Narasaraopet Pulse

A read-only X (Twitter) monitoring dashboard for the office of the Narasaraopet Lok Sabha MP (Palnadu district, Andhra Pradesh). It tracks:

- **Local:** problems and conversations in the 7 assembly constituencies and 28 mandals.
- **Party:** TDP vs YSRCP share of voice, tone, and the opposition's attack lines.
- **Delhi:** Parliament, plus the PM and Union ministries, but only their posts about Andhra Pradesh.

The full design is in [PLAN.md](PLAN.md).

## Pages

| Page | Question it answers |
|---|---|
| **Today** | What needs attention right now? Urgent or spiking local problems, the day at a glance. |
| **Constituency** | What's happening, and where? 7 constituency cards and an issue heatmap. Tap through to a mandal. |
| **Party Pulse** | How are TDP and YSRCP doing? Share of voice, tone, YSRCP attack lines, reaction to the MP. |
| **Delhi** | What's moving in Delhi for AP? Centre → AP, Parliament, AP ↔ Centre, topics, national trends. |
| **Explore** | Ad-hoc search or post lookup, with the cost shown before it runs. |
| **Usage** | Spend vs budget, where the money goes, job status, Pause all. |

## Run it (Docker)

```bash
cp .env.example .env               # Windows: copy .env.example .env
docker compose up -d --build
# open http://localhost:8000
docker compose logs -f             # watch the jobs
```

It starts in **mock mode**: generated Telugu and English posts and no API keys, so you can try every page for free. To use another port: `PORT=8080 docker compose up -d`.

- **Change what is tracked:** edit `config.yaml` (handles, keywords, budget, polling) or `places.yaml` (mandals and spellings), then run `docker compose restart`.
- **Back up the data:** `docker compose cp tracker:/app/data/tracker.db ./backup-$(date +%F).db`
- The container must run **one worker**. The scheduler runs inside the app, and more workers would poll twice and spend twice.

## Go live

1. **X API:** in the X developer console, buy pay-per-use credits and create a Bearer token.
2. **LLM:** create an OpenCode API key and choose a model. Any OpenAI-compatible endpoint works.
3. In `.env`, set:
   ```
   X_BEARER_TOKEN=...
   MOCK_MODE=false
   LLM_BASE_URL=https://opencode.ai/zen/v1   # check against your OpenCode dashboard
   LLM_API_KEY=...
   LLM_MODEL=...
   ```
4. In `config.yaml`, under `budget:`, check `cost_per_post_read` against the console, and set `llm_cost_per_mtok_in` / `_out` from your OpenCode dashboard.
5. Run `docker compose up -d`.

**Do a $5 trial day first.** Run for a day, then compare the Usage page with the X developer console. If the console charges more than the app estimates, the usernames returned with each post are probably billed as user reads. Set `bill_expanded_users: true` in `config.yaml` so the app's estimate matches.

Without an LLM key, the app still runs. It uses a simple fallback: English-only sentiment, place matching and keyword issues.

## Before going live: check with the office

- **`places.yaml`:** check the mandal → constituency mapping and spellings against the district gazette. Add local spellings as staff spot them. Mark a name `ambiguous: true` if it has other common meanings; it then only counts when another Palnadu place is in the same post.
- **`config.yaml` handles:** open each one on x.com and check the verified badge and bio. The comments list leaders whose handles are still missing: five TDP MLAs and six local YSRCP leaders.

## How the budget works

- **Daily limit $3, monthly $90** (in `config.yaml`).
- Past **80% of the daily limit**, only local, MP mentions and Parliament keep updating.
- At 100% everything pauses until midnight IST.
- Each post is fetched once (`since_id`).
- Searches poll hourly, every 30 minutes when busy, and less often when quiet or overnight (23:00–06:00 IST).
- Union ministry accounts are filtered to AP on X's side, so you never pay for their posts about other states.
- **Pause all** on the Usage page stops everything. It stays paused across restarts.

Expected cost is about **$75–90 a month** for X, plus a few dollars for LLM tagging.

## Development (without Docker)

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
.venv/bin/uvicorn app.main:app --reload
.venv/bin/python -m pytest -q          # 73 tests, no network needed
```

Code map:

| File | What it does |
|---|---|
| `app/main.py` | Routes |
| `app/views.py` | Data for each page |
| `app/jobs.py` | Scheduler |
| `app/budget.py` | Spend limits |
| `app/x_client.py` | X API client |
| `app/mock_data.py` | Mock client |
| `app/services/places.py` | Gazetteer and query builders |
| `app/services/collect.py` | Fetching and polling rules |
| `app/services/tagger.py` | LLM, fallback and mock taggers |
| `app/services/insights.py` | Needs-attention, share of voice and other calculations |
| `app/services/trends.py` | Trend deltas |
