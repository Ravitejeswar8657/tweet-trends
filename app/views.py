"""Data for each page. Route handlers in main.py stay thin."""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Sequence

from sqlalchemy import func
from sqlmodel import col, select

from app import budget, jobs
from app.config import get_config, get_places
from app.db import session
from app.models import CreditLedger, Post, QueryState, utcnow
from app.services import insights
from app.services.trends import latest_with_deltas, rank_history

WINDOWS = {"6h": 6, "24h": 24, "7d": 168}

ISSUE_LABELS = {
    "drinking_water": "Drinking water", "irrigation": "Irrigation", "farmers": "Farmers",
    "roads": "Roads", "power": "Power", "health": "Health", "education": "Education",
    "law_and_order": "Law & order", "welfare_schemes": "Welfare schemes", "jobs": "Jobs",
    "corruption": "Corruption", "other": "Other", "highways": "Highways", "railways": "Railways",
    "airports": "Airports", "ports": "Ports", "polavaram": "Polavaram",
    "amaravati_capital": "Amaravati", "funds_budget": "Funds & budget",
    "central_schemes": "Central schemes", "industry_investment": "Industry",
    "agriculture": "Agriculture", "power_energy": "Power & energy",
    "parliament_business": "Parliament",
}
STANCE_LABELS = {"pro_tdp": "Pro-TDP", "anti_tdp": "Anti-TDP", "pro_ysrcp": "Pro-YSRCP",
                 "anti_ysrcp": "Anti-YSRCP", "neutral": "Neutral"}


def label(value: Optional[str]) -> str:
    if not value:
        return ""
    return ISSUE_LABELS.get(value) or STANCE_LABELS.get(value) or value.replace("_", " ").capitalize()


def window_hours(window: str) -> int:
    return WINDOWS.get(window, 24)


def fetch_posts(since: datetime, categories: Sequence[str], ac: Optional[str] = None,
                extra=None, limit: Optional[int] = None) -> List[Post]:
    with session() as s:
        q = select(Post).where(Post.created_at >= since, col(Post.category).in_(list(categories)))
        if ac:
            q = q.where(Post.ac == ac)
        for cond in extra or []:
            q = q.where(cond)
        q = q.order_by(Post.created_at.desc())
        if limit:
            q = q.limit(limit)
        return list(s.exec(q))


def freshness() -> Optional[datetime]:
    with session() as s:
        return s.exec(select(func.max(Post.fetched_at))).one()


def banner() -> List[dict]:
    """Messages shown at the top of every page."""
    out = []
    st = budget.status()
    if st["mode"] != "ok":
        out.append({"level": "amber" if st["mode"] == "saving" else "red", "text": st["message"]})
    for name, (at, msg) in jobs.last_errors.items():
        out.append({"level": "red", "text": f"{jobs.LABELS.get(name, name)}: {msg}"})
    last = freshness()
    if last and utcnow() - last > timedelta(hours=3) and st["mode"] == "ok" and not budget.is_paused():
        out.append({"level": "amber", "text": "No new posts for over 3 hours. Check the Usage page."})
    return out


# ---------- Today ----------

def alert_cards(now: datetime) -> List[dict]:
    cfg = get_config()
    since = now - timedelta(hours=cfg.alerts.window_hours) - timedelta(days=7)
    return insights.needs_attention(fetch_posts(since, ["local"]), cfg.alerts, now)


def today(now: datetime) -> dict:
    cfg = get_config()
    day, prev = now - timedelta(hours=24), now - timedelta(hours=48)
    local_48 = fetch_posts(prev, ["local"])
    local_24 = [p for p in local_48 if p.created_at >= day]
    local_prev = len(local_48) - len(local_24)
    tagged = [p for p in local_24 if p.sentiment]
    neg_share = sum(p.sentiment == "neg" for p in tagged) / len(tagged) if tagged else None
    party = fetch_posts(day, ["tdp", "ysrcp"])
    sov = insights.share_of_voice(party, now, 24)
    delhi = fetch_posts(now - timedelta(days=3), ["delhi"], extra=[Post.ap_relevant.is_not(False)], limit=3)
    trends = []
    for loc in cfg.delhi.trends:
        data = latest_with_deltas(loc.woeid)
        hot = [t for t in data["trends"] if t["status"] in ("NEW", "RISING")][:6]
        trends.append({"location": loc.name, "trends": hot})
    return {
        "cards": alert_cards(now)[:6],
        "local_24": len(local_24),
        "local_change": len(local_24) - local_prev,
        "neg_share": neg_share,
        "sov": sov,
        "budget": budget.status(now),
        "delhi": delhi,
        "trends": trends,
        "window_hours": cfg.alerts.window_hours,
    }


# ---------- Constituency ----------

def constituency(now: datetime, window: str) -> dict:
    places, cfg = get_places(), get_config()
    posts = fetch_posts(now - timedelta(hours=window_hours(window)), ["local"])
    alerts = alert_cards(now)
    cards = []
    for ac in places.assembly_constituencies:
        mine = [p for p in posts if p.ac == ac.name]
        split = {"pos": 0, "neu": 0, "neg": 0}
        for p in mine:
            if p.sentiment in split:
                split[p.sentiment] += 1
        issues: Dict[str, int] = {}
        for p in mine:
            if p.issue and p.issue != "other":
                issues[p.issue] = issues.get(p.issue, 0) + 1
        levels = [a["level"] for a in alerts if a["ac"] == ac.name]
        cards.append({
            "name": ac.name, "telugu": next((a for a in ac.aliases if re.search(r"[ఀ-౿]", a)), ""),
            "count": len(mine), "split": split,
            "top_issue": max(issues, key=issues.get) if issues else None,
            "level": "red" if "red" in levels else ("amber" if levels else None),
        })
    unplaced = sum(1 for p in posts if not p.ac)
    heat = insights.issue_heatmap(posts, places.ac_names(), [i for i in cfg.issues if i != "other"])
    return {"cards": cards, "heat": heat, "total": len(posts), "unplaced": unplaced}


def place(now: datetime, window: str, ac: str, mandal: Optional[str], issue: Optional[str]) -> dict:
    places = get_places()
    ac_cfg = places.ac(ac)
    hours = window_hours(window)
    posts = fetch_posts(now - timedelta(hours=hours), ["local", "mp_mentions"], ac=ac)
    mandal_counts = {m.name: 0 for m in ac_cfg.mandals} if ac_cfg else {}
    for p in posts:
        if p.mandal in mandal_counts:
            mandal_counts[p.mandal] += 1
    scoped = [p for p in posts if not mandal or p.mandal == mandal]
    issue_counts: Dict[str, int] = {}
    for p in scoped:
        if p.issue:
            issue_counts[p.issue] = issue_counts.get(p.issue, 0) + 1
    feed = [p for p in scoped if not issue or p.issue == issue]
    size = 1 if hours <= 48 else 6
    starts = insights.buckets(now, hours, size)
    volume = [0] * len(starts)
    negative = [0] * len(starts)
    for p in feed:
        i = int((p.created_at - starts[0]).total_seconds() // (size * 3600))
        if 0 <= i < len(starts):
            volume[i] += 1
            negative[i] += p.sentiment == "neg"
    return {
        "ac": ac, "mandal": mandal, "issue": issue, "known": ac_cfg is not None,
        "mandal_counts": mandal_counts,
        "issue_counts": dict(sorted(issue_counts.items(), key=lambda x: -x[1])),
        "feed": insights.top_by_velocity(feed, now, 60),
        "chart": {"labels": [budget.to_ist(s).strftime("%d %b %H:%M" if size > 1 else "%H:%M") for s in starts],
                  "volume": volume, "negative": negative},
    }


# ---------- Party ----------

def party(now: datetime, window: str) -> dict:
    cfg = get_config()
    hours = window_hours(window)
    posts = fetch_posts(now - timedelta(hours=hours), ["tdp", "ysrcp", "mp_mentions"])
    party_posts = [p for p in posts if p.category in ("tdp", "ysrcp")]
    mentions = [p for p in posts if p.category == "mp_mentions"]
    sov = insights.share_of_voice(party_posts, now, hours)
    size = 1 if hours <= 48 else 6
    return {
        "sov": sov,
        "chart": {
            "labels": [budget.to_ist(b).strftime("%d %b %H:%M" if size > 1 else "%H:%M") for b in sov["buckets"]],
            "tdp": sov["series"]["tdp"]["posts"], "ysrcp": sov["series"]["ysrcp"]["posts"],
            "tdp_color": cfg.parties["tdp"].color if "tdp" in cfg.parties else "#F2C200",
            "ysrcp_color": cfg.parties["ysrcp"].color if "ysrcp" in cfg.parties else "#1F6FD1",
        },
        "sentiment": insights.sentiment_split(party_posts),
        "attacks": insights.attack_lines(party_posts, now),
        "top_tdp": insights.top_by_velocity([p for p in party_posts if p.category == "tdp"], now, 8),
        "top_ysrcp": insights.top_by_velocity([p for p in party_posts if p.category == "ysrcp"], now, 8),
        "mentions": insights.top_by_velocity(mentions, now, 10),
        "mentions_split": insights.sentiment_split(mentions, key=lambda p: "mp").get("mp", {"pos": 0, "neu": 0, "neg": 0}),
        "mentions_count": len(mentions),
        "mp_handle": cfg.mp.handle,
    }


# ---------- Delhi ----------

def delhi(now: datetime, window: str, topic: Optional[str], account: Optional[str], kw: Optional[str]) -> dict:
    cfg = get_config()
    since = now - timedelta(hours=window_hours(window))
    posts = fetch_posts(since, ["delhi"])
    national = [p for p in posts if p.source.startswith("delhi:national") and p.ap_relevant is not False]
    digest = insights.delhi_digest(national, now)
    shown = [p for p in national if (not topic or p.issue == topic)
             and (not account or p.author_username.lower() == account.lower())]
    parliament = [p for p in posts if p.source.startswith("delhi:parliament")]
    state_posts = fetch_posts(since, ["tdp"])
    link = insights.centre_link(state_posts, cfg.delhi.centre_filter, cfg.delhi.state_handles)
    keywords = []
    for i, k in enumerate(cfg.delhi.keywords, start=1):
        key = f"delhi:kw:{i}"
        keywords.append({"key": key, "name": k.name,
                         "posts": [p for p in posts if p.source == key and p.ap_relevant is not False]})
    active_kw = kw if any(k["key"] == kw for k in keywords) else (keywords[0]["key"] if keywords else None)
    trends = []
    for loc in cfg.delhi.trends:
        data = latest_with_deltas(loc.woeid)
        rows = data["trends"][:15]
        for t in rows:
            t["history"] = rank_history(loc.woeid, t["name"])
        trends.append({"location": loc.name, "fetched_at": data["fetched_at"], "trends": rows})
    return {
        "digest": digest, "national": insights.top_by_velocity(shown, now, 40),
        "topic": topic, "account": account,
        "parliament": sorted(parliament, key=lambda p: p.created_at, reverse=True)[:20],
        "centre_link": sorted(link, key=lambda p: p.created_at, reverse=True)[:12],
        "keywords": keywords, "active_kw": active_kw,
        "trends": trends,
    }


# ---------- Usage ----------

def usage(now: datetime) -> dict:
    day_start, month_start = budget.ist_day_start_utc(now), budget.ist_month_start_utc(now)
    with session() as s:
        llm_month = s.exec(select(func.sum(CreditLedger.cost))
                           .where(CreditLedger.kind == "llm", CreditLedger.at >= month_start)).one() or 0.0
        llm_tokens = s.exec(select(func.sum(CreditLedger.units))
                            .where(CreditLedger.kind == "llm", CreditLedger.at >= month_start)).one() or 0
        posts_today = s.exec(select(func.sum(CreditLedger.units))
                             .where(CreditLedger.kind == "x", CreditLedger.at >= day_start)).one() or 0
        states = list(s.exec(select(QueryState).order_by(QueryState.key)))
        pending = s.exec(select(func.count()).select_from(Post).where(Post.tag_status == "pending")).one()
        failed = s.exec(select(func.count()).select_from(Post).where(Post.tag_status == "failed")).one()
    job_rows = []
    for name in jobs.JOBS:
        job_rows.append({
            "name": name, "label": jobs.LABELS[name],
            "last": jobs.last_runs.get(name), "error": jobs.last_errors.get(name),
            "next": jobs.next_run(name),
        })
    return {
        "status": budget.status(now),
        "by_group_today": budget.spend_by_group(day_start),
        "by_group_month": budget.spend_by_group(month_start),
        "llm_month": float(llm_month), "llm_tokens": int(llm_tokens),
        "posts_today": int(posts_today),
        "jobs": job_rows, "states": states,
        "truncated": [st for st in states if st.truncated_runs],
        "pending": pending, "failed": failed,
        "paused": budget.is_paused(),
    }
