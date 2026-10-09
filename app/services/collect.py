"""Generic fetch: budget check, since_id, upsert, adaptive polling state.

One function covers local, party, Delhi and Explore; callers pass different queries.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

from app import budget
from app.config import Polling, get_config
from app.db import session
from app.models import MetricSnapshot, Post, QueryState, utcnow

log = logging.getLogger("collect")

# Streams whose posts we snapshot for engagement growth.
SNAPSHOT_CATEGORIES = {"tdp", "ysrcp"}


# ---------- polling rules (pure) ----------

def in_quiet_hours(now_utc: datetime, quiet_hours: str) -> bool:
    if not quiet_hours:
        return False
    start_s, end_s = quiet_hours.split("-")
    sh, sm = map(int, start_s.split(":"))
    eh, em = map(int, end_s.split(":"))
    local = budget.to_ist(now_utc)
    minutes = local.hour * 60 + local.minute
    start, end = sh * 60 + sm, eh * 60 + em
    return start <= minutes < end if start < end else minutes >= start or minutes < end


def effective_interval(interval: int, now_utc: datetime, quiet_hours: str) -> int:
    return interval * 2 if in_quiet_hours(now_utc, quiet_hours) else interval


def is_due(state: QueryState, now_utc: datetime, quiet_hours: str) -> bool:
    if state.last_run_at is None:
        return True
    wait = effective_interval(state.interval_minutes, now_utc, quiet_hours)
    # 1 minute of slack so a job waking every 30 min does not miss a 60-min slot by seconds.
    return now_utc - state.last_run_at >= timedelta(minutes=wait - 1)


def next_interval(state: QueryState, new_count: int, base: int, polling: Polling) -> None:
    """Update interval/empty_runs/truncated_runs in place after a poll."""
    burst, cap = polling.burst_minutes, base * polling.max_backoff_factor
    if new_count >= polling.max_results:
        state.interval_minutes = min(burst, base)
        state.empty_runs = 0
        state.truncated_runs += 1
    elif new_count == 0:
        state.empty_runs += 1
        if state.empty_runs >= 2:
            state.interval_minutes = min(max(state.interval_minutes, base) * 2, cap)
            state.empty_runs = 0
    else:
        state.empty_runs = 0
        in_burst = state.interval_minutes < base
        if not (in_burst and new_count >= polling.max_results / 2):
            state.interval_minutes = base
    state.last_new_count = new_count


# ---------- the collector ----------

def upsert_posts(posts: list, category: str, source: str) -> int:
    """Insert new posts, refresh metrics on known ones. Returns how many were new."""
    new = 0
    with session() as s:
        for p in posts:
            existing = s.get(Post, p["id"])
            if existing:
                for k in ("likes", "retweets", "replies", "quotes", "impressions"):
                    setattr(existing, k, p[k])
                s.add(existing)
            else:
                s.add(Post(**p, category=category, source=source))
                new += 1
            if category in SNAPSHOT_CATEGORIES:
                s.add(MetricSnapshot(post_id=p["id"], likes=p["likes"], retweets=p["retweets"],
                                     replies=p["replies"], quotes=p["quotes"]))
        s.commit()
    return new


def collect(
    client,
    query_key: str,
    query: str,
    category: str,
    stream: str,
    base_minutes: int,
    force: bool = False,
    now: Optional[datetime] = None,
) -> dict:
    polling = get_config().polling
    now = now or utcnow()
    with session() as s:
        state = s.get(QueryState, query_key) or QueryState(key=query_key, interval_minutes=base_minutes)
    if not force and not is_due(state, now, polling.quiet_hours):
        return {"key": query_key, "status": "not_due", "new": 0}
    ok, reason = budget.can_spend(stream, budget.estimate_cost(polling.max_results), now=now)
    if not ok:
        return {"key": query_key, "status": "budget", "reason": reason, "new": 0}

    result = client.search_recent(query, polling.max_results, since_id=state.since_id, stream=stream)
    posts = result["posts"]
    new = upsert_posts(posts, category, query_key)

    if result.get("newest_id"):
        state.since_id = result["newest_id"]
    state.last_run_at = now
    next_interval(state, len(posts), base_minutes, polling)
    with session() as s:
        s.merge(state)
        s.commit()
    log.info("%s: %d returned, %d new, next in %d min", query_key, len(posts), new, state.interval_minutes)
    return {"key": query_key, "status": "ok", "returned": len(posts), "new": new}
