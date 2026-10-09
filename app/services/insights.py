"""Pure functions over lists of posts. No DB access, so they are easy to unit-test.

Posts can be `Post` rows or anything with the same attributes.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Dict, Iterable, List, Optional, Sequence

from app.config import Alerts

STOP_WORDS = set(
    "the a an and or of to in on for is are was were be been this that with at by from it as not no "
    "but have has had will would can could our your their his her its we you they i he she rt amp "
    "https http co sir garu గారు మరియు ఈ ఆ లో కు కి ను తో ఒక".split()
)
HASHTAG = re.compile(r"#(\w+)", re.UNICODE)
WORD = re.compile(r"[A-Za-zఀ-౿][\wఀ-౿]{2,}", re.UNICODE)
URL = re.compile(r"https?://\S+")
TELUGU = re.compile(r"[ఀ-౿]")


def engagement(p) -> int:
    return p.likes + 2 * p.retweets + p.replies + p.quotes


def velocity(p, now: datetime) -> float:
    age_hours = (now - p.created_at).total_seconds() / 3600
    return engagement(p) / max(age_hours, 1)


def top_by_velocity(posts: Iterable, now: datetime, n: int = 10) -> List:
    return sorted(posts, key=lambda p: velocity(p, now), reverse=True)[:n]


def needs_attention(posts: Sequence, cfg: Alerts, now: datetime) -> List[dict]:
    """Negative local clusters (ac, mandal, issue) in the alert window that are urgent or spiking.

    `posts` should cover the window plus the 7 days before it (the baseline).
    """
    window_start = now - timedelta(hours=cfg.window_hours)
    base_start = window_start - timedelta(days=7)
    window, baseline = defaultdict(list), Counter()
    for p in posts:
        if p.category != "local" or not p.ac or not p.issue:
            continue
        key = (p.ac, p.mandal, p.issue)
        if p.created_at >= window_start:
            window[key].append(p)
        elif p.created_at >= base_start:
            baseline[key] += 1

    out = []
    for key, group in window.items():
        neg = [p for p in group if p.sentiment == "neg"]
        if len(neg) < cfg.min_negative_posts:
            continue
        expected = baseline[key] / (7 * 24) * cfg.window_hours
        spike_ratio = len(group) / max(expected, 1.0)
        max_urgency = max(p.urgency or 0 for p in group)
        urgent = max_urgency >= cfg.urgency_threshold
        spiking = spike_ratio >= cfg.spike_factor
        if not (urgent or spiking):
            continue
        top = top_by_velocity(neg, now, 3)
        out.append({
            "ac": key[0], "mandal": key[1], "issue": key[2],
            "level": "red" if urgent and spiking else "amber",
            "count": len(group), "negative": len(neg), "max_urgency": max_urgency,
            "spike_ratio": round(spike_ratio, 1),
            "score": len(neg) * (1 + max_urgency) * spike_ratio,
            "gist": next((p.summary_en for p in top if p.summary_en), None),
            "top_posts": top,
        })
    return sorted(out, key=lambda c: c["score"], reverse=True)


def buckets(now: datetime, hours: int, size_hours: int = 1) -> List[datetime]:
    end = now.replace(minute=0, second=0, microsecond=0)
    n = max(1, hours // size_hours)
    return [end - timedelta(hours=size_hours * i) for i in range(n - 1, -1, -1)]


def share_of_voice(posts: Sequence, now: datetime, hours: int = 24) -> dict:
    """TDP vs YSRCP post counts and engagement per bucket (1h for <=48h, else 6h)."""
    size = 1 if hours <= 48 else 6
    starts = buckets(now, hours, size)
    series = {party: {"posts": [0] * len(starts), "engagement": [0] * len(starts)} for party in ("tdp", "ysrcp")}
    for p in posts:
        if p.category not in series:
            continue
        i = int((p.created_at - starts[0]).total_seconds() // (size * 3600))
        if not 0 <= i < len(starts):
            continue
        series[p.category]["posts"][i] += 1
        series[p.category]["engagement"][i] += engagement(p)
    totals = {party: sum(v["posts"]) for party, v in series.items()}
    total = sum(totals.values()) or 1
    return {"buckets": starts, "series": series, "totals": totals,
            "share": {k: v / total for k, v in totals.items()}}


def sentiment_split(posts: Iterable, key=lambda p: p.category) -> Dict[str, Dict[str, int]]:
    out: Dict[str, Dict[str, int]] = defaultdict(lambda: {"pos": 0, "neu": 0, "neg": 0})
    for p in posts:
        if p.sentiment in ("pos", "neu", "neg"):
            out[key(p)][p.sentiment] += 1
    return dict(out)


def attack_lines(posts: Sequence, now: datetime, n: int = 5) -> List[dict]:
    """What YSRCP is hitting TDP on: issues of anti-TDP YSRCP posts, by engagement."""
    groups = defaultdict(list)
    for p in posts:
        if p.category == "ysrcp" and p.party_stance == "anti_tdp":
            groups[p.issue or "other"].append(p)
    out = [{"issue": issue, "posts": len(g), "engagement": sum(engagement(p) for p in g),
            "examples": top_by_velocity(g, now, 2)} for issue, g in groups.items()]
    return sorted(out, key=lambda x: x["engagement"], reverse=True)[:n]


def issue_heatmap(posts: Iterable, acs: Sequence[str], issues: Sequence[str]) -> dict:
    counts = {ac: {i: 0 for i in issues} for ac in acs}
    negs = {ac: {i: 0 for i in issues} for ac in acs}
    for p in posts:
        if p.category == "local" and p.ac in counts and p.issue in counts[p.ac]:
            counts[p.ac][p.issue] += 1
            negs[p.ac][p.issue] += p.sentiment == "neg"
    peak = max([c for row in counts.values() for c in row.values()] or [0])
    cells = {ac: {i: {"count": counts[ac][i],
                      "neg_share": negs[ac][i] / counts[ac][i] if counts[ac][i] else 0.0,
                      "intensity": counts[ac][i] / peak if peak else 0.0} for i in issues} for ac in acs}
    return {"acs": list(acs), "issues": list(issues), "cells": cells, "peak": peak}


def _term_pattern(term: str) -> re.Pattern:
    if TELUGU.search(term):
        return re.compile(re.escape(term))
    return re.compile(r"(?<![A-Za-z])" + re.escape(term) + r"(?![A-Za-z])", re.I)


def centre_link(posts: Iterable, terms: Sequence[str], handles: Optional[Sequence[str]] = None) -> List:
    """Posts (optionally only from `handles`) that mention the Centre."""
    pats = [_term_pattern(t) for t in terms]
    allowed = {h.lower() for h in handles} if handles else None
    return [p for p in posts
            if (allowed is None or (p.author_username or "").lower() in allowed)
            and any(pat.search(p.text or "") for pat in pats)]


def delhi_digest(posts: Sequence, now: datetime) -> dict:
    """Delhi posts grouped by topic (velocity-ranked) and counts per account."""
    by_topic = defaultdict(list)
    for p in posts:
        if p.ap_relevant is False:
            continue
        by_topic[p.issue or "other"].append(p)
    topics = sorted(({"topic": t, "count": len(g), "posts": top_by_velocity(g, now, 50)}
                     for t, g in by_topic.items()), key=lambda x: x["count"], reverse=True)
    accounts = Counter(p.author_username for p in posts if p.ap_relevant is not False)
    return {"topics": topics, "accounts": accounts.most_common()}


def top_hashtags(posts: Iterable, n: int = 10) -> List[tuple]:
    c = Counter(h.lower() for p in posts for h in HASHTAG.findall(p.text or ""))
    return c.most_common(n)


def top_words(posts: Iterable, n: int = 15) -> List[tuple]:
    c = Counter()
    for p in posts:
        text = URL.sub(" ", HASHTAG.sub(" ", p.text or ""))
        text = re.sub(r"@\w+", " ", text)
        for w in WORD.findall(text):
            w = w.lower()
            if w not in STOP_WORDS and not w.isdigit():
                c[w] += 1
    return c.most_common(n)
