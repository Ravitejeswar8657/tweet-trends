"""India / Delhi trend snapshots with NEW / RISING / FALLING / STEADY deltas."""
from __future__ import annotations

from typing import Dict, List, Optional

from sqlmodel import select

from app import budget
from app.config import get_config
from app.db import session
from app.models import QueryState, TrendSnapshot, utcnow
from app.services.collect import is_due


def compute_deltas(current: List[dict], previous: List[dict]) -> List[dict]:
    """current/previous: [{name, rank, tweet_count}]. Pure, for unit tests."""
    prev = {t["name"]: t for t in previous}
    out = []
    for t in current:
        p = prev.get(t["name"])
        if p is None:
            status = "NEW"
        elif t["rank"] < p["rank"] or (
            t.get("tweet_count") and p.get("tweet_count") and t["tweet_count"] >= 1.2 * p["tweet_count"]
        ):
            status = "RISING"
        elif t["rank"] > p["rank"]:
            status = "FALLING"
        else:
            status = "STEADY"
        out.append({**t, "status": status, "prev_rank": p["rank"] if p else None})
    return out


def refresh_trends(client, force: bool = False) -> List[dict]:
    cfg = get_config()
    now = utcnow()
    results = []
    for loc in cfg.delhi.trends:
        key = f"trends:{loc.woeid}"
        with session() as s:
            state = s.get(QueryState, key) or QueryState(key=key, interval_minutes=cfg.polling.trends_minutes)
        if not force and not is_due(state, now, cfg.polling.quiet_hours):
            continue
        ok, reason = budget.can_spend("trends", cfg.budget.cost_per_trends_call, now=now)
        if not ok:
            results.append({"key": key, "status": "budget", "reason": reason})
            continue
        trends = client.trends_by_woeid(loc.woeid)
        with session() as s:
            for rank, t in enumerate(trends, start=1):
                s.add(TrendSnapshot(woeid=loc.woeid, location=loc.name, name=t["name"], rank=rank,
                                    tweet_count=t.get("tweet_count"), fetched_at=now))
            state.last_run_at = now
            state.interval_minutes = cfg.polling.trends_minutes
            s.merge(state)
            s.commit()
        results.append({"key": key, "status": "ok", "count": len(trends)})
    return results


def _snapshot_times(woeid: int) -> List:
    with session() as s:
        return list(s.exec(
            select(TrendSnapshot.fetched_at).where(TrendSnapshot.woeid == woeid)
            .distinct().order_by(TrendSnapshot.fetched_at.desc()).limit(2)
        ))


def _snapshot(woeid: int, at) -> List[dict]:
    with session() as s:
        rows = s.exec(select(TrendSnapshot).where(TrendSnapshot.woeid == woeid, TrendSnapshot.fetched_at == at)
                      .order_by(TrendSnapshot.rank))
        return [{"name": r.name, "rank": r.rank, "tweet_count": r.tweet_count} for r in rows]


def latest_with_deltas(woeid: int) -> Dict:
    times = _snapshot_times(woeid)
    if not times:
        return {"fetched_at": None, "trends": []}
    current = _snapshot(woeid, times[0])
    previous = _snapshot(woeid, times[1]) if len(times) > 1 else []
    deltas = compute_deltas(current, previous)
    if not previous:  # first snapshot ever: nothing is "new" yet
        for d in deltas:
            d["status"] = "STEADY"
    return {"fetched_at": times[0], "trends": deltas}


def rank_history(woeid: int, name: str, limit: int = 12) -> List[Optional[int]]:
    with session() as s:
        times = list(s.exec(select(TrendSnapshot.fetched_at).where(TrendSnapshot.woeid == woeid)
                            .distinct().order_by(TrendSnapshot.fetched_at.desc()).limit(limit)))
        rows = s.exec(select(TrendSnapshot).where(TrendSnapshot.woeid == woeid, TrendSnapshot.name == name,
                                                  TrendSnapshot.fetched_at.in_(times)))
        by_time = {r.fetched_at: r.rank for r in rows}
    return [by_time.get(t) for t in reversed(times)]
