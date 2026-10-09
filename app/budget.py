"""Credit ledger and spend limits. Every X request and LLM call goes through here.

Days and months follow IST, because that is when the office's budget "day" starts.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Tuple

from sqlalchemy import func
from sqlmodel import select

from app.config import get_config
from app.db import session
from app.models import AppFlag, CreditLedger, utcnow

IST = timezone(timedelta(hours=5, minutes=30))

# Streams and the page group they are reported under on the Usage page.
STREAM_GROUPS = {
    "local": "Local",
    "mp_mentions": "Local",
    "party": "Party",
    "parliament": "Delhi",
    "national": "Delhi",
    "delhi_keywords": "Delhi",
    "trends": "Delhi",
    "explore": "Explore",
    "tagging": "LLM tagging",
}


def to_ist(dt_utc: datetime) -> datetime:
    return dt_utc.replace(tzinfo=timezone.utc).astimezone(IST)


def ist_day_start_utc(now_utc: datetime) -> datetime:
    local = to_ist(now_utc)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(timezone.utc).replace(tzinfo=None)


def ist_month_start_utc(now_utc: datetime) -> datetime:
    local = to_ist(now_utc)
    start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(timezone.utc).replace(tzinfo=None)


def record(kind: str, endpoint: str, units: int, cost: float, stream: str) -> None:
    with session() as s:
        s.add(CreditLedger(kind=kind, endpoint=endpoint, units=units, cost=cost, category=stream))
        s.commit()


def _spent_since(start: datetime) -> float:
    with session() as s:
        total = s.exec(select(func.sum(CreditLedger.cost)).where(CreditLedger.at >= start)).one()
    return float(total or 0.0)


def spent_today(now: Optional[datetime] = None) -> float:
    return _spent_since(ist_day_start_utc(now or utcnow()))


def spent_this_month(now: Optional[datetime] = None) -> float:
    return _spent_since(ist_month_start_utc(now or utcnow()))


def spend_by_group(start: datetime) -> Dict[str, float]:
    with session() as s:
        rows = s.exec(
            select(CreditLedger.category, func.sum(CreditLedger.cost))
            .where(CreditLedger.at >= start)
            .group_by(CreditLedger.category)
        ).all()
    out: Dict[str, float] = {}
    for stream, cost in rows:
        group = STREAM_GROUPS.get(stream, stream or "Other")
        out[group] = out.get(group, 0.0) + float(cost or 0.0)
    return out


def x_cost(posts: int, users: int = 0) -> float:
    b = get_config().budget
    cost = posts * b.cost_per_post_read
    if b.bill_expanded_users:
        cost += users * b.cost_per_user_lookup
    return cost


def estimate_cost(max_results: int) -> float:
    return x_cost(max_results, max_results)


def llm_cost(tokens_in: int, tokens_out: int) -> float:
    b = get_config().budget
    return tokens_in / 1e6 * b.llm_cost_per_mtok_in + tokens_out / 1e6 * b.llm_cost_per_mtok_out


# ---------- manual pause (persisted, so a restart does not silently resume) ----------

def is_paused() -> bool:
    with session() as s:
        flag = s.get(AppFlag, "paused")
    return bool(flag and flag.value == "1")


def set_paused(paused: bool) -> None:
    with session() as s:
        flag = s.get(AppFlag, "paused") or AppFlag(key="paused")
        flag.value = "1" if paused else "0"
        s.add(flag)
        s.commit()


# ---------- the gate ----------

def can_spend(
    stream: str,
    estimate: float = 0.0,
    now: Optional[datetime] = None,
    today: Optional[float] = None,
    month: Optional[float] = None,
) -> Tuple[bool, str]:
    """Return (allowed, reason). `today`/`month` can be passed in to avoid DB reads (tests)."""
    b = get_config().budget
    if is_paused():
        return False, "Paused by staff"
    now = now or utcnow()
    today = spent_today(now) if today is None else today
    month = spent_this_month(now) if month is None else month
    if today + estimate > b.daily_credit_limit or today >= b.daily_credit_limit:
        return False, "Paused: daily budget reached"
    if month + estimate > b.monthly_credit_limit or month >= b.monthly_credit_limit:
        return False, "Paused: monthly budget reached"
    if today >= b.soft_limit_share * b.daily_credit_limit and stream not in b.priority_1:
        return False, "Saving budget: only local, MP and Parliament are updating"
    return True, ""


def status(now: Optional[datetime] = None) -> dict:
    """Everything the banner and the Usage page need."""
    b = get_config().budget
    now = now or utcnow()
    today, month = spent_today(now), spent_this_month(now)
    if is_paused():
        mode, message = "paused", "Paused by staff. Nothing is being fetched."
    elif today >= b.daily_credit_limit:
        mode, message = "paused", "Paused: daily budget reached. Resumes at midnight IST."
    elif month >= b.monthly_credit_limit:
        mode, message = "paused", "Paused: monthly budget reached."
    elif today >= b.soft_limit_share * b.daily_credit_limit:
        mode, message = "saving", "Saving budget: only local, MP and Parliament are updating."
    else:
        mode, message = "ok", ""
    return {
        "mode": mode,
        "message": message,
        "today": today,
        "month": month,
        "daily_limit": b.daily_credit_limit,
        "monthly_limit": b.monthly_credit_limit,
        "today_share": today / b.daily_credit_limit if b.daily_credit_limit else 0,
        "month_share": month / b.monthly_credit_limit if b.monthly_credit_limit else 0,
    }
