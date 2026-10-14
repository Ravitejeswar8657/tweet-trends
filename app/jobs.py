"""APScheduler setup. Collecting jobs wake every `burst_minutes`; `collect()` decides per query
whether it is due (base interval, burst, backoff, quiet hours) and whether the budget allows it."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional, Tuple

from apscheduler.schedulers.background import BackgroundScheduler

from app.config import get_config, get_places
from app.models import utcnow
from app.services.collect import collect
from app.services.places import build_ac_queries, build_handle_queries
from app.services.tagger import tag_pending
from app.services.trends import refresh_trends
from app.x_client import XAPIError, get_client

log = logging.getLogger("jobs")

last_errors: Dict[str, Tuple[datetime, str]] = {}
last_runs: Dict[str, Tuple[datetime, str]] = {}
scheduler: Optional[BackgroundScheduler] = None


def _summary(results: List[dict]) -> str:
    ok = [r for r in results if r.get("status") == "ok"]
    budget_skips = [r for r in results if r.get("status") == "budget"]
    new = sum(r.get("new", 0) for r in ok)
    if not ok and not budget_skips:
        return "nothing due yet"
    parts = [f"{len(ok)} searches polled, {new} new posts"]
    if budget_skips:
        parts.append(budget_skips[0].get("reason", "budget"))
    return "; ".join(parts)


class TaggingError(Exception):
    """Raised so run_job records the problem and the dashboard shows a banner."""


def run_job(name: str, fn: Callable[[], str]) -> None:
    try:
        msg = fn()
        last_runs[name] = (utcnow(), msg or "ok")
        last_errors.pop(name, None)
    except XAPIError as e:
        log.warning("%s failed: %s", name, e)
        last_errors[name] = (utcnow(), f"{e.status}: {e.message}")
    except Exception as e:  # noqa: BLE001 - a job must never crash the app
        log.exception("%s crashed", name)
        last_errors[name] = (utcnow(), str(e) if isinstance(e, TaggingError) else f"{type(e).__name__}: {e}")


# ---------- the jobs ----------

def job_local(force: bool = False) -> str:
    cfg, client = get_config(), get_client()
    queries = build_ac_queries(get_places(), cfg.local, cfg.polling.query_max_length)
    results = [collect(client, k, q, "local", "local", cfg.polling.local_minutes, force) for k, q in queries.items()]
    return _summary(results)


def party_queries() -> List[tuple]:
    """(key, query, category, stream, base_minutes)"""
    cfg = get_config()
    p, max_len = cfg.polling, cfg.polling.query_max_length
    out = []
    for party, pc in cfg.parties.items():
        for k, q in build_handle_queries(pc.handles, f"party:{party}", max_len=max_len).items():
            out.append((k, q, party, "party", p.party_minutes))
        for i, kw in enumerate(pc.keywords, start=1):
            out.append((f"party:{party}:kw:{i}", f"{kw} -is:retweet", party, "party", p.party_minutes))
    if cfg.mp.track_mentions:
        h = cfg.mp.handle
        out.append(("mp_mentions", f"@{h} -from:{h} -is:retweet", "mp_mentions", "mp_mentions", p.party_minutes))
    return out


def delhi_queries() -> List[tuple]:
    cfg = get_config()
    d, p, max_len = cfg.delhi, cfg.polling, cfg.polling.query_max_length
    out = []
    for k, q in build_handle_queries(d.parliament_handles, "delhi:parliament", max_len=max_len).items():
        out.append((k, q, "delhi", "parliament", p.delhi_minutes))
    for k, q in build_handle_queries(d.national_handles, "delhi:national", filter=d.ap_filter, max_len=max_len).items():
        out.append((k, q, "delhi", "national", p.delhi_minutes))
    for i, kw in enumerate(d.keywords, start=1):
        out.append((f"delhi:kw:{i}", kw.query, "delhi", "delhi_keywords", p.delhi_minutes))
    return out


def job_party(force: bool = False) -> str:
    client = get_client()
    return _summary([collect(client, k, q, c, s, m, force) for k, q, c, s, m in party_queries()])


def job_delhi(force: bool = False) -> str:
    client = get_client()
    return _summary([collect(client, k, q, c, s, m, force) for k, q, c, s, m in delhi_queries()])


def job_trends(force: bool = False) -> str:
    results = refresh_trends(get_client(), force)
    return f"{sum(1 for r in results if r['status'] == 'ok')} locations refreshed" if results else "not due"


def job_tag(force: bool = False) -> str:
    counts = tag_pending()
    if counts.get("error"):
        raise TaggingError(counts["error"])
    return f"{counts['done']} tagged, {counts['failed']} failed"


JOBS: Dict[str, Callable[..., str]] = {
    "local": job_local,
    "party": job_party,
    "delhi": job_delhi,
    "trends": job_trends,
    "tag": job_tag,
}
LABELS = {"local": "Local", "party": "Party & MP mentions", "delhi": "Delhi", "trends": "Trends", "tag": "Tagging"}


def run_now(name: str) -> None:
    """Manual refresh: ignores 'not due' but still respects the budget. Tags right after."""
    run_job(name, lambda: JOBS[name](force=True))
    if name != "tag":
        run_job("tag", job_tag)


def start() -> BackgroundScheduler:
    global scheduler
    p = get_config().polling
    scheduler = BackgroundScheduler(timezone="UTC", job_defaults={"max_instances": 1, "coalesce": True})
    now = datetime.now(timezone.utc)
    intervals = {
        "local": p.burst_minutes if p.local_minutes else 0,
        "party": p.burst_minutes if p.party_minutes else 0,
        "delhi": p.burst_minutes if p.delhi_minutes else 0,
        "trends": p.burst_minutes if p.trends_minutes else 0,
        "tag": p.tag_minutes,
    }
    for name, minutes in intervals.items():
        if minutes <= 0:
            continue
        # Tagging starts a little later, so the first collection has landed.
        first = now + timedelta(seconds=30) if name == "tag" else now
        scheduler.add_job(run_job, "interval", minutes=minutes, args=[name, JOBS[name]], id=name,
                          next_run_time=first)
    scheduler.start()
    return scheduler


def shutdown() -> None:
    if scheduler and scheduler.running:
        scheduler.shutdown(wait=False)


def next_run(name: str) -> Optional[datetime]:
    if not scheduler:
        return None
    job = scheduler.get_job(name)
    return job.next_run_time if job else None
