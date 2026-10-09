import pytest


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Fresh SQLite DB, mock mode, no scheduler, no LLM key for every test."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("MOCK_MODE", "true")
    monkeypatch.setenv("DISABLE_SCHEDULER", "true")
    monkeypatch.setenv("LLM_API_KEY", "")
    monkeypatch.setenv("X_BEARER_TOKEN", "test-token")
    # Never let a developer's real .env leak into tests.
    monkeypatch.setenv("APP_PASSWORD", "")
    monkeypatch.setenv("LLM_MODEL", "")
    monkeypatch.setenv("LLM_BASE_URL", "https://opencode.ai/zen/v1")
    from app import config, db

    for f in (config.get_settings, config.get_config, config.get_places, db.get_engine):
        f.cache_clear()
    db.init_db()
    yield
    db.get_engine().dispose()


class P:
    """A stand-in post for pure-function tests."""

    def __init__(self, **kw):
        from datetime import datetime

        defaults = dict(id="1", category="local", ac=None, mandal=None, issue=None, sentiment=None,
                        party_stance=None, urgency=0, summary_en=None, text="", author_username="u",
                        likes=0, retweets=0, replies=0, quotes=0, created_at=datetime(2026, 10, 9, 12),
                        ap_relevant=None, source="")
        defaults.update(kw)
        self.__dict__.update(defaults)
