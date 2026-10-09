import httpx
import respx
from fastapi.testclient import TestClient
from sqlmodel import func, select

from app import secrets_store
from app.db import session
from app.models import Post

SECRET = "AAAAtokenvalue-SECRET-9876"


def client():
    from app.main import app

    return TestClient(app)


def post_count():
    with session() as s:
        return s.exec(select(func.count()).select_from(Post)).one()


def test_saved_values_override_env_and_never_render_in_full():
    with client() as c:
        r = c.post("/settings", data={"mode": "mock", "x_bearer_token": SECRET, "llm_model": "kimi-k2.6"},
                   follow_redirects=False)
        assert r.status_code == 303
        eff = secrets_store.effective()
        assert eff.x_bearer_token == SECRET and eff.source["x_bearer_token"] == "dashboard"
        assert eff.llm_model == "kimi-k2.6"
        page = c.get("/settings").text
        assert SECRET not in page and "••••9876" in page


def test_blank_secret_keeps_existing_and_clear_removes():
    with client() as c:
        c.post("/settings", data={"mode": "mock", "x_bearer_token": SECRET})
        c.post("/settings", data={"mode": "mock", "x_bearer_token": ""})
        assert secrets_store.effective().x_bearer_token == SECRET
        c.post("/settings", data={"mode": "mock", "clear_x": "1"})
        assert secrets_store.effective().source["x_bearer_token"] == ".env"  # conftest sets an env token


def test_switching_to_live_clears_sample_data():
    from app import jobs

    with client() as c:
        jobs.job_local(force=True)
        assert post_count() > 0
        r = c.post("/settings", data={"mode": "live"}, follow_redirects=False)
        assert r.headers["location"] == "/settings?saved=live"
        assert post_count() == 0 and secrets_store.effective().mock_mode is False


def test_cross_site_post_is_blocked():
    with client() as c:
        r = c.post("/settings", data={"mode": "live"}, headers={"origin": "https://evil.example"})
        assert r.status_code == 403 and secrets_store.effective().mock_mode is True
        ok = c.post("/settings", data={"mode": "mock"}, headers={"origin": "http://testserver"}, follow_redirects=False)
        assert ok.status_code == 303


@respx.mock
def test_test_buttons_report_success_and_failure():
    respx.get("https://api.x.com/2/tweets/search/recent").mock(return_value=httpx.Response(401))
    respx.post("https://llm.test/v1/chat/completions").mock(return_value=httpx.Response(200, json={
        "choices": [{"message": {"content": "No drinking water in Durgi"}}]}))
    with client() as c:
        c.post("/settings", data={"mode": "mock", "x_bearer_token": SECRET, "llm_api_key": "k",
                                  "llm_model": "m", "llm_base_url": "https://llm.test/v1"})
        c.post("/settings/test/x")
        c.post("/settings/test/llm")
        page = c.get("/settings").text
        assert "Failed" in page and "X_BEARER_TOKEN" in page
        assert "No drinking water in Durgi" in page


def test_live_mode_uses_saved_token():
    from app.x_client import XClient, get_client

    secrets_store.save({"mock_mode": "0", "x_bearer_token": SECRET})
    c = get_client()
    assert isinstance(c, XClient) and c.http.headers["authorization"] == f"Bearer {SECRET}"
