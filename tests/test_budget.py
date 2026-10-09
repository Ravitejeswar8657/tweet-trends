from datetime import datetime, timedelta

from app import budget
from app.config import get_config
from app.models import QueryState
from app.services.collect import collect, effective_interval, in_quiet_hours, is_due, next_interval

NOON_IST = datetime(2026, 10, 9, 6, 30)  # 12:00 IST in naive UTC
NIGHT_IST = datetime(2026, 10, 9, 18, 30)  # 00:00 IST


def test_can_spend_under_limit():
    assert budget.can_spend("party", 0.1, now=NOON_IST, today=1.0, month=10) == (True, "")


def test_hard_daily_limit_blocks_everything():
    ok, reason = budget.can_spend("local", 0.1, now=NOON_IST, today=3.0, month=10)
    assert not ok and "daily" in reason
    ok, _ = budget.can_spend("local", 0.2, now=NOON_IST, today=2.9, month=10)
    assert not ok  # the estimate would cross the limit


def test_monthly_limit():
    ok, reason = budget.can_spend("local", 0.1, now=NOON_IST, today=0, month=90)
    assert not ok and "monthly" in reason


def test_soft_limit_keeps_only_priority_streams():
    for stream in ("local", "mp_mentions", "parliament"):
        assert budget.can_spend(stream, 0.1, now=NOON_IST, today=2.5, month=10)[0]
    for stream in ("party", "national", "delhi_keywords", "trends", "explore"):
        ok, reason = budget.can_spend(stream, 0.1, now=NOON_IST, today=2.5, month=10)
        assert not ok and "Saving budget" in reason


def test_manual_pause():
    budget.set_paused(True)
    assert budget.can_spend("local", 0, now=NOON_IST, today=0, month=0) == (False, "Paused by staff")
    budget.set_paused(False)
    assert budget.can_spend("local", 0, now=NOON_IST, today=0, month=0)[0]


def test_ist_day_boundary():
    # 23:59 IST on the 9th is 18:29 UTC; the IST day started at 18:30 UTC on the 8th.
    assert budget.ist_day_start_utc(datetime(2026, 10, 9, 18, 29)) == datetime(2026, 10, 8, 18, 30)
    assert budget.ist_day_start_utc(datetime(2026, 10, 9, 18, 30)) == datetime(2026, 10, 9, 18, 30)
    assert budget.ist_month_start_utc(datetime(2026, 10, 9)) == datetime(2026, 9, 30, 18, 30)


def test_spent_today_resets_at_ist_midnight():
    from app.db import session
    from app.models import CreditLedger

    with session() as s:
        s.add(CreditLedger(cost=1.0, at=datetime(2026, 10, 9, 18, 0)))  # 23:30 IST on the 9th
        s.add(CreditLedger(cost=0.5, at=datetime(2026, 10, 9, 19, 0)))  # 00:30 IST on the 10th
        s.commit()
    assert budget.spent_today(datetime(2026, 10, 9, 20, 0)) == 0.5
    assert budget.spent_this_month(datetime(2026, 10, 9, 20, 0)) == 1.5


def test_quiet_hours_double_interval():
    assert in_quiet_hours(NIGHT_IST, "23:00-06:00") and not in_quiet_hours(NOON_IST, "23:00-06:00")
    assert effective_interval(60, NIGHT_IST, "23:00-06:00") == 120
    assert effective_interval(60, NOON_IST, "23:00-06:00") == 60


def test_is_due():
    st = QueryState(key="k", interval_minutes=60, last_run_at=NOON_IST - timedelta(minutes=45))
    assert not is_due(st, NOON_IST, "23:00-06:00")
    st.last_run_at = NOON_IST - timedelta(minutes=60)
    assert is_due(st, NOON_IST, "23:00-06:00")
    st.last_run_at = NIGHT_IST - timedelta(minutes=60)
    assert not is_due(st, NIGHT_IST, "23:00-06:00")  # doubled to 120 at night
    assert is_due(QueryState(key="new"), NOON_IST, "")


def test_burst_on_full_poll_and_back():
    p = get_config().polling  # max_results 20, burst 30
    st = QueryState(key="k", interval_minutes=60)
    next_interval(st, 20, 60, p)
    assert st.interval_minutes == 30 and st.truncated_runs == 1
    next_interval(st, 12, 60, p)  # still busy (>= half full): stay in burst
    assert st.interval_minutes == 30
    next_interval(st, 3, 60, p)  # calmed down
    assert st.interval_minutes == 60


def test_backoff_after_two_empty_runs_and_reset():
    p = get_config().polling  # max_backoff_factor 4
    st = QueryState(key="k", interval_minutes=60)
    next_interval(st, 0, 60, p)
    assert st.interval_minutes == 60
    next_interval(st, 0, 60, p)
    assert st.interval_minutes == 120
    for _ in range(6):
        next_interval(st, 0, 60, p)
    assert st.interval_minutes == 240  # capped at 4x
    next_interval(st, 2, 60, p)
    assert st.interval_minutes == 60


class FakeClient:
    def __init__(self, posts):
        self.posts, self.calls = posts, []

    def search_recent(self, query, max_results, since_id=None, stream=""):
        self.calls.append({"query": query, "since_id": since_id, "stream": stream})
        return {"posts": self.posts, "newest_id": self.posts[0]["id"] if self.posts else None}


def post(i):
    return {"id": str(i), "author_id": "1", "author_username": "u", "text": "Durgi water", "lang": "en",
            "created_at": NOON_IST, "conversation_id": None, "likes": 1, "retweets": 0, "replies": 0,
            "quotes": 0, "impressions": 0}


def test_collect_passes_since_id_and_respects_due():
    client = FakeClient([post(200), post(199)])
    r = collect(client, "local:Macherla", "q", "local", "local", 60, now=NOON_IST)
    assert r["status"] == "ok" and r["new"] == 2 and client.calls[0]["since_id"] is None
    r = collect(client, "local:Macherla", "q", "local", "local", 60, now=NOON_IST + timedelta(minutes=10))
    assert r["status"] == "not_due" and len(client.calls) == 1
    collect(client, "local:Macherla", "q", "local", "local", 60, now=NOON_IST + timedelta(minutes=61))
    assert client.calls[1]["since_id"] == "200"


def test_collect_skips_when_budget_says_no():
    budget.set_paused(True)
    client = FakeClient([post(1)])
    r = collect(client, "k", "q", "local", "local", 60, now=NOON_IST)
    assert r["status"] == "budget" and client.calls == []
