"""Credentials and mode entered on the Settings page.

Values saved in the dashboard (stored in the SQLite data volume) override .env; an empty
dashboard value falls back to .env. Secrets are never sent back to the browser in full.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable

from sqlmodel import col, delete, select

from app.config import get_settings
from app.db import session
from app.models import AppFlag, CreditLedger, MetricSnapshot, Post, QueryState, TrendSnapshot

PREFIX = "setting:"
FIELDS = ("mock_mode", "x_bearer_token", "llm_base_url", "llm_api_key", "llm_model")
SECRET_FIELDS = {"x_bearer_token", "llm_api_key"}


@dataclass
class Effective:
    mock_mode: bool
    x_bearer_token: str
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    source: Dict[str, str] = field(default_factory=dict)  # field -> "dashboard" | ".env" | "not set"


def saved() -> Dict[str, str]:
    with session() as s:
        rows = s.exec(select(AppFlag).where(col(AppFlag.key).startswith(PREFIX)))
        return {r.key[len(PREFIX):]: r.value for r in rows if r.value != ""}


def effective() -> Effective:
    env, db = get_settings(), saved()
    values, source = {}, {}
    for f in FIELDS:
        if f in db:
            values[f] = db[f] == "1" if f == "mock_mode" else db[f]
            source[f] = "dashboard"
        else:
            values[f] = getattr(env, f)
            source[f] = ".env" if values[f] not in ("", None) else "not set"
    return Effective(**values, source=source)


def save(updates: Dict[str, str], clear: Iterable[str] = ()) -> None:
    """Blank secret fields are ignored (keep the current value); fields in `clear` are removed."""
    with session() as s:
        for f, v in updates.items():
            if f not in FIELDS:
                continue
            v = (v or "").strip()
            if f in SECRET_FIELDS and not v:
                continue
            row = s.get(AppFlag, PREFIX + f) or AppFlag(key=PREFIX + f)
            row.value = v
            s.add(row)
        for f in clear:
            row = s.get(AppFlag, PREFIX + f)
            if row:
                s.delete(row)
        s.commit()


def mask(value: str) -> str:
    if not value:
        return ""
    return "••••" + value[-4:] if len(value) > 8 else "••••"


def clear_collected_data() -> None:
    """Remove posts, trends, polling state and spend records (used when leaving mock mode,
    so sample data and fake since_ids never mix with live data)."""
    with session() as s:
        for model in (Post, MetricSnapshot, TrendSnapshot, QueryState, CreditLedger):
            s.exec(delete(model))
        s.commit()
