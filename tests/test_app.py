"""End-to-end in mock mode: run every job, then every page must render."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app_client():
    from app import jobs
    from app.main import app

    with TestClient(app) as c:
        for name in ("local", "party", "delhi", "trends", "tag"):
            jobs.JOBS[name](force=True)
        yield c


@pytest.mark.parametrize("path", ["/", "/constituency", "/place?ac=Macherla", "/place?ac=Macherla&mandal=Durgi&issue=drinking_water&window=7d",
                                  "/party?window=7d", "/delhi", "/delhi?window=6h&topic=highways", "/explore",
                                  "/explore?q=Polavaram", "/usage", "/api/usage", "/healthz"])
def test_pages_render(app_client, path):
    r = app_client.get(path)
    assert r.status_code == 200, r.text[:500]


def test_today_shows_needs_attention(app_client):
    assert "card alert" in app_client.get("/").text  # the mock always has one spiking mandal


def test_unknown_place_404(app_client):
    assert app_client.get("/place?ac=Hyderabad").status_code == 404


def test_explore_run_and_pause(app_client):
    r = app_client.post("/explore", data={"q": "Varikapudisela"}, follow_redirects=False)
    assert r.status_code == 303 and "run=" in r.headers["location"]
    assert "result" in app_client.get(r.headers["location"]).text
    app_client.post("/pause")
    assert "Paused by staff" in app_client.get("/").text
    app_client.post("/resume")
    assert "Paused by staff" not in app_client.get("/").text


def test_refresh_redirects_back(app_client):
    r = app_client.post("/refresh/party", headers={"referer": "http://testserver/party"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("/party")


def test_login_required_when_password_set(monkeypatch):
    from app import config
    from app.main import app

    monkeypatch.setenv("APP_PASSWORD", "s3cret")
    config.get_settings.cache_clear()
    with TestClient(app) as c:
        assert c.get("/").status_code == 401
        assert c.get("/", auth=("office", "wrong")).status_code == 401
        assert c.get("/healthz").status_code == 200
        assert c.get("/usage", auth=("office", "s3cret")).status_code == 200
        assert c.post("/pause").status_code == 401
