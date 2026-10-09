"""SQLite tables. All timestamps are stored as naive UTC."""
from datetime import datetime, timezone
from typing import Optional

from pydantic import NaiveDatetime

from sqlalchemy import Index
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None).replace(microsecond=0)


class Post(SQLModel, table=True):
    __table_args__ = (
        Index("ix_post_category_created", "category", "created_at"),
        Index("ix_post_ac_issue_created", "ac", "issue", "created_at"),
    )

    id: str = Field(primary_key=True)  # tweet ID
    author_id: str = ""
    author_username: str = ""
    text: str = ""
    lang: Optional[str] = None
    created_at: NaiveDatetime = Field(default_factory=utcnow)
    conversation_id: Optional[str] = None
    likes: int = 0
    retweets: int = 0
    replies: int = 0
    quotes: int = 0
    impressions: int = 0
    category: str = Field(index=True)  # local / tdp / ysrcp / mp_mentions / delhi / explore
    source: str = ""  # query key
    fetched_at: NaiveDatetime = Field(default_factory=utcnow)

    # Tags
    ac: Optional[str] = None
    mandal: Optional[str] = None
    issue: Optional[str] = None
    sentiment: Optional[str] = None  # pos / neu / neg
    party_stance: Optional[str] = None  # pro_tdp / anti_tdp / pro_ysrcp / anti_ysrcp / neutral
    urgency: int = 0
    summary_en: Optional[str] = None
    ap_relevant: Optional[bool] = None  # Delhi posts only
    tag_status: str = Field(default="pending", index=True)  # pending / done / failed
    tagged_at: Optional[NaiveDatetime] = None


class MetricSnapshot(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    post_id: str = Field(index=True)
    likes: int = 0
    retweets: int = 0
    replies: int = 0
    quotes: int = 0
    at: NaiveDatetime = Field(default_factory=utcnow)


class TrendSnapshot(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    woeid: int = Field(index=True)
    location: str = ""
    name: str
    rank: int
    tweet_count: Optional[int] = None
    fetched_at: NaiveDatetime = Field(default_factory=utcnow, index=True)


class QueryState(SQLModel, table=True):
    key: str = Field(primary_key=True)
    since_id: Optional[str] = None
    interval_minutes: int = 60
    empty_runs: int = 0
    last_new_count: int = 0
    last_run_at: Optional[NaiveDatetime] = None
    truncated_runs: int = 0  # polls that came back full ("possibly truncated")


class AppFlag(SQLModel, table=True):
    """Small persisted switches, e.g. the staff "Pause all" toggle."""

    key: str = Field(primary_key=True)
    value: str = ""


class CreditLedger(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    kind: str = "x"  # x / llm
    endpoint: str = ""
    units: int = 0  # posts read, or tokens
    cost: float = 0.0
    category: str = ""
    at: NaiveDatetime = Field(default_factory=utcnow, index=True)
