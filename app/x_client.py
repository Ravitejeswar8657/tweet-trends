"""X API v2 client (read-only) and a mock with the same methods.

Every method returns normalised dicts:
  id, author_id, author_username, text, lang, created_at (naive UTC), conversation_id,
  likes, retweets, replies, quotes, impressions
and records what it read in the credit ledger.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

import httpx

from app import budget
from app.config import get_config

log = logging.getLogger("x_client")

BASE_URL = "https://api.x.com/2"
TWEET_FIELDS = "created_at,public_metrics,author_id,lang,conversation_id"


class XAPIError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"{status}: {message}")
        self.status = status
        self.message = message


def _parse_time(value: Optional[str]) -> datetime:
    if not value:
        return datetime.now(timezone.utc).replace(tzinfo=None).replace(microsecond=0)
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None, microsecond=0)


def normalise(payload: dict) -> List[dict]:
    users = {u["id"]: u.get("username", "") for u in payload.get("includes", {}).get("users", [])}
    out = []
    for t in payload.get("data", []) or []:
        m = t.get("public_metrics", {}) or {}
        out.append(
            {
                "id": t["id"],
                "author_id": t.get("author_id", ""),
                "author_username": users.get(t.get("author_id", ""), ""),
                "text": t.get("text", ""),
                "lang": t.get("lang"),
                "created_at": _parse_time(t.get("created_at")),
                "conversation_id": t.get("conversation_id"),
                "likes": m.get("like_count", 0),
                "retweets": m.get("retweet_count", 0),
                "replies": m.get("reply_count", 0),
                "quotes": m.get("quote_count", 0),
                "impressions": m.get("impression_count", 0),
            }
        )
    return out


def clamp_results(n: int) -> int:
    return max(10, min(100, n))


class XClient:
    def __init__(self, token: str, http: Optional[httpx.Client] = None, sleep=time.sleep):
        self.http = http or httpx.Client(
            base_url=BASE_URL, headers={"Authorization": f"Bearer {token}"}, timeout=30
        )
        self._sleep = sleep

    def _get(self, path: str, params: dict, retried: bool = False) -> dict:
        r = self.http.get(path, params=params)
        remaining = r.headers.get("x-rate-limit-remaining")
        if remaining is not None:
            log.info("GET %s -> %s (rate remaining %s)", path, r.status_code, remaining)
        if r.status_code == 429:
            reset = int(r.headers.get("x-rate-limit-reset", "0") or 0)
            wait = reset - time.time()
            if not retried and 0 <= wait <= 30:
                self._sleep(wait + 1)
                return self._get(path, params, retried=True)
            when = datetime.fromtimestamp(reset).strftime("%H:%M") if reset else "later"
            raise XAPIError(429, f"Rate limited; resets at {when}")
        body = r.text.lower()
        if r.status_code == 402 or (r.status_code >= 400 and "credit" in body):
            raise XAPIError(r.status_code, "X credits exhausted; top up in the developer console")
        if r.status_code == 401:
            raise XAPIError(401, "Unauthorized; check X_BEARER_TOKEN")
        if r.status_code == 403:
            raise XAPIError(403, "Forbidden; your plan does not include this endpoint")
        if r.status_code >= 400:
            raise XAPIError(r.status_code, r.text[:200])
        return r.json()

    def search_recent(
        self,
        query: str,
        max_results: int = 20,
        since_id: Optional[str] = None,
        pages: int = 1,
        stream: str = "explore",
    ) -> dict:
        """Returns {"posts": [...], "newest_id": str|None}. One page unless `pages` > 1."""
        params = {
            "query": query,
            "max_results": clamp_results(max_results),
            "tweet.fields": TWEET_FIELDS,
            "expansions": "author_id",
            "user.fields": "username",
        }
        if since_id:
            params["since_id"] = since_id
        posts: List[dict] = []
        newest_id = None
        for _ in range(max(1, pages)):
            payload = self._get("/tweets/search/recent", params)
            batch = normalise(payload)
            users = len(payload.get("includes", {}).get("users", []))
            budget.record("x", "search_recent", len(batch), budget.x_cost(len(batch), users), stream)
            posts.extend(batch)
            meta = payload.get("meta", {})
            newest_id = newest_id or meta.get("newest_id")
            token = meta.get("next_token")
            if not token:
                break
            params["next_token"] = token
        return {"posts": posts, "newest_id": newest_id}

    def tweets_by_ids(self, ids: List[str], stream: str = "explore") -> List[dict]:
        payload = self._get(
            "/tweets",
            {"ids": ",".join(ids[:100]), "tweet.fields": TWEET_FIELDS,
             "expansions": "author_id", "user.fields": "username"},
        )
        batch = normalise(payload)
        users = len(payload.get("includes", {}).get("users", []))
        budget.record("x", "tweets", len(batch), budget.x_cost(len(batch), users), stream)
        return batch

    def trends_by_woeid(self, woeid: int, max_trends: int = 30) -> List[dict]:
        payload = self._get(f"/trends/by/woeid/{woeid}", {"max_trends": max_trends})
        budget.record("x", "trends", 1, get_config().budget.cost_per_trends_call, "trends")
        return [
            {"name": t.get("trend_name", ""), "tweet_count": t.get("tweet_count")}
            for t in payload.get("data", []) or []
        ]


def get_client():
    from app.secrets_store import effective

    s = effective()
    if s.mock_mode:
        from app.mock_data import MockXClient

        return MockXClient()
    if not s.x_bearer_token:
        raise XAPIError(401, "Live mode is on but no X Bearer token is set (Settings page or .env)")
    return XClient(s.x_bearer_token)
