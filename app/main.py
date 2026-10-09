"""FastAPI app: pages, actions, health. Route handlers are plain `def`, so they run in a threadpool."""
from __future__ import annotations

import base64
import hashlib
import secrets
import logging
import re
from collections import OrderedDict
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlmodel import col, select

from app import budget, jobs, secrets_store, views
from app.config import get_config, get_places, get_settings
from app.db import get_engine, init_db, session
from app.models import Post, utcnow
from app.services.collect import upsert_posts
from app.services.tagger import tag_pending
from app.x_client import XAPIError, get_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
HERE = Path(__file__).parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    if not get_settings().disable_scheduler:
        jobs.start()
    yield
    jobs.shutdown()


app = FastAPI(title="Narasaraopet Pulse", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")


@app.middleware("http")
async def same_origin_posts(request: Request, call_next):
    """Browsers resend Basic-auth credentials to this site even from other sites' forms.
    Reject cross-site POSTs so a malicious page cannot change settings or spend credits."""
    if request.method == "POST":
        origin = request.headers.get("origin") or request.headers.get("referer")
        if origin:
            from urllib.parse import urlparse

            if urlparse(origin).netloc != request.headers.get("host", ""):
                return Response("Cross-site request blocked", status_code=403)
    return await call_next(request)


@app.middleware("http")
async def require_login(request: Request, call_next):
    """HTTP Basic auth when APP_PASSWORD is set. /healthz stays open for the Docker healthcheck."""
    s = get_settings()
    if not s.app_password or request.url.path == "/healthz":
        return await call_next(request)
    header = request.headers.get("authorization", "")
    if header.lower().startswith("basic "):
        try:
            user, _, pw = base64.b64decode(header[6:]).decode().partition(":")
        except ValueError:
            user, pw = "", ""
        if secrets.compare_digest(user, s.app_username) and secrets.compare_digest(pw, s.app_password):
            return await call_next(request)
    return Response("Login required", status_code=401,
                    headers={"WWW-Authenticate": 'Basic realm="Narasaraopet Pulse"'})
templates = Jinja2Templates(directory=HERE / "templates")


# ---------- template helpers ----------

def ist(dt: Optional[datetime], fmt: str = "%d %b, %H:%M") -> str:
    return budget.to_ist(dt).strftime(fmt) if dt else "never"


def ago(dt: Optional[datetime]) -> str:
    if not dt:
        return "never"
    secs = (utcnow() - dt).total_seconds()
    if secs < 0:  # future, e.g. next run
        mins = int(-secs // 60)
        return "now" if mins < 1 else f"in {mins} min" if mins < 60 else f"in {mins // 60}h {mins % 60}m"
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{int(secs // 60)}m ago"
    if secs < 86400:
        return f"{int(secs // 3600)}h ago"
    return f"{int(secs // 86400)}d ago"


def compact(n) -> str:
    n = n or 0
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(int(n))


def money(x: float) -> str:
    return f"${x:,.2f}"


def naive_utc(dt):
    """APScheduler gives tz-aware times; templates use naive UTC."""
    return dt.astimezone(budget.timezone.utc).replace(tzinfo=None) if dt and dt.tzinfo else dt


templates.env.filters.update(ist=ist, ago=ago, label=views.label, compact=compact, money=money,
                             naive_utc=naive_utc)
templates.env.globals.update(
    is_telugu=lambda s: bool(re.search(r"[ఀ-౿]", s or "")),
    WINDOWS=list(views.WINDOWS),
)


def render(request: Request, name: str, ctx: dict) -> HTMLResponse:
    base = {
        "request": request,
        "path": request.url.path,
        "mock": secrets_store.effective().mock_mode,
        "banner": views.banner(),
        "updated": views.freshness(),
        "ac_names": get_places().ac_names(),
        "now": utcnow(),
    }
    return templates.TemplateResponse(request, name, {**base, **ctx})


def back(request: Request, fallback: str = "/") -> RedirectResponse:
    ref = request.headers.get("referer") or fallback
    return RedirectResponse(ref, status_code=303)


def qs(**params) -> str:
    return urlencode({k: v for k, v in params.items() if v})


templates.env.globals["qs"] = qs


# ---------- pages ----------

@app.get("/", response_class=HTMLResponse)
def page_today(request: Request):
    now = utcnow()
    return render(request, "today.html", {"t": views.today(now), "title": "Today"})


@app.get("/constituency", response_class=HTMLResponse)
def page_constituency(request: Request, window: str = "24h"):
    window = window if window in views.WINDOWS else "24h"
    data = views.constituency(utcnow(), window)
    return render(request, "constituency.html", {"c": data, "window": window, "title": "Constituency"})


@app.get("/place", response_class=HTMLResponse)
def page_place(request: Request, ac: str, mandal: Optional[str] = None, issue: Optional[str] = None,
               window: str = "24h"):
    window = window if window in views.WINDOWS else "24h"
    data = views.place(utcnow(), window, ac, mandal or None, issue or None)
    if not data["known"]:
        raise HTTPException(404, f"Unknown assembly constituency: {ac}")
    return render(request, "place.html", {"p": data, "window": window, "title": ac})


@app.get("/party", response_class=HTMLResponse)
def page_party(request: Request, window: str = "24h"):
    window = window if window in views.WINDOWS else "24h"
    return render(request, "party.html", {"p": views.party(utcnow(), window), "window": window,
                                          "title": "Party Pulse"})


@app.get("/delhi", response_class=HTMLResponse)
def page_delhi(request: Request, window: str = "7d", topic: Optional[str] = None,
               account: Optional[str] = None, kw: Optional[str] = None):
    window = window if window in views.WINDOWS else "7d"
    data = views.delhi(utcnow(), window, topic or None, account or None, kw or None)
    return render(request, "delhi.html", {"d": data, "window": window, "title": "Delhi"})


# Explore keeps the IDs of the last few runs in memory, so results show exactly what the run returned.
EXPLORE_RUNS: "OrderedDict[str, dict]" = OrderedDict()
ID_RE = re.compile(r"(?:status/)?(\d{10,25})")


def parse_ids(q: str):
    tokens = q.replace(",", " ").split()
    ids = [m.group(1) for t in tokens if (m := ID_RE.search(t))]
    return ids if ids and len(ids) == len(tokens) else None


@app.get("/explore", response_class=HTMLResponse)
def page_explore(request: Request, q: str = "", run: str = ""):
    cfg = get_config()
    q = q.strip()
    estimate = None
    if q and not run:
        ids = parse_ids(q)
        n = len(ids) if ids else cfg.polling.max_results
        ok, reason = budget.can_spend("explore", budget.estimate_cost(n))
        estimate = {"kind": "ids" if ids else "search", "posts": n, "cost": budget.estimate_cost(n),
                    "ok": ok, "reason": reason}
    results = None
    if run and run in EXPLORE_RUNS:
        info = EXPLORE_RUNS[run]
        with session() as s:
            rows = list(s.exec(select(Post).where(col(Post.id).in_(info["ids"]))))
        order = {pid: i for i, pid in enumerate(info["ids"])}
        results = {"query": info["query"], "at": info["at"],
                   "posts": sorted(rows, key=lambda p: order.get(p.id, 0))}
    return render(request, "explore.html", {"q": q, "estimate": estimate, "results": results,
                                            "title": "Explore", "recent": list(EXPLORE_RUNS.items())[::-1][:8]})


@app.post("/explore")
def run_explore(q: str = Form(...)):
    q = q.strip()
    cfg = get_config()
    ids = parse_ids(q)
    n = len(ids) if ids else cfg.polling.max_results
    ok, reason = budget.can_spend("explore", budget.estimate_cost(n))
    if not ok:
        return RedirectResponse(f"/explore?{qs(q=q)}", status_code=303)
    key = "explore:" + hashlib.sha1(q.encode()).hexdigest()[:10]
    client = get_client()
    try:
        posts = client.tweets_by_ids(ids) if ids else client.search_recent(q, n, stream="explore")["posts"]
    except XAPIError as e:
        jobs.last_errors["explore"] = (utcnow(), f"{e.status}: {e.message}")
        return RedirectResponse(f"/explore?{qs(q=q)}", status_code=303)
    upsert_posts(posts, "explore", key)
    tag_pending()
    run_id = hashlib.sha1(f"{key}{utcnow()}".encode()).hexdigest()[:8]
    EXPLORE_RUNS[run_id] = {"query": q, "ids": [p["id"] for p in posts], "at": utcnow()}
    while len(EXPLORE_RUNS) > 20:
        EXPLORE_RUNS.popitem(last=False)
    return RedirectResponse(f"/explore?{qs(q=q, run=run_id)}", status_code=303)


@app.get("/usage", response_class=HTMLResponse)
def page_usage(request: Request):
    return render(request, "usage.html", {"u": views.usage(utcnow()), "title": "Usage & status",
                                          "cfg": get_config()})


# ---------- actions ----------

@app.post("/refresh/{job}")
def refresh(request: Request, job: str):
    if job not in jobs.JOBS:
        raise HTTPException(404, f"Unknown job {job}")
    jobs.run_now(job)
    return back(request)


@app.post("/pause")
def pause(request: Request):
    budget.set_paused(True)
    return back(request, "/usage")


@app.post("/resume")
def resume(request: Request):
    budget.set_paused(False)
    return back(request, "/usage")


@app.get("/api/usage")
def api_usage():
    now = utcnow()
    st = budget.status(now)
    st["by_group_today"] = budget.spend_by_group(budget.ist_day_start_utc(now))
    st["by_group_month"] = budget.spend_by_group(budget.ist_month_start_utc(now))
    return {k: ({g: round(v, 4) for g, v in val.items()} if isinstance(val, dict) else
                round(val, 4) if isinstance(val, float) else val) for k, val in st.items()}


@app.get("/healthz")
def healthz():
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "db": str(e)}, status_code=503)
    sched_ok = get_settings().disable_scheduler or bool(jobs.scheduler and jobs.scheduler.running)
    return JSONResponse({"ok": sched_ok, "db": "ok", "scheduler": sched_ok}, status_code=200 if sched_ok else 503)


# ---------- settings (credentials entered in the browser) ----------

LAST_TEST: dict = {}  # "x" / "llm" -> {"ok": bool, "message": str, "at": datetime}


@app.get("/settings", response_class=HTMLResponse)
def page_settings(request: Request, saved: str = ""):
    eff = secrets_store.effective()
    host = (request.url.hostname or "").lower()
    insecure = request.url.scheme != "https" and host not in ("localhost", "127.0.0.1")
    return render(request, "settings.html", {
        "title": "Settings", "eff": eff, "masked": {f: secrets_store.mask(getattr(eff, f)) for f in secrets_store.SECRET_FIELDS},
        "tests": LAST_TEST, "saved": saved, "insecure": insecure, "cfg": get_config(),
    })


@app.post("/settings")
def save_settings(
    mode: str = Form("mock"),
    x_bearer_token: str = Form(""),
    llm_base_url: str = Form(""),
    llm_api_key: str = Form(""),
    llm_model: str = Form(""),
    clear_x: str = Form(""),
    clear_llm: str = Form(""),
):
    was_mock = secrets_store.effective().mock_mode
    secrets_store.save(
        {"mock_mode": "1" if mode == "mock" else "0", "x_bearer_token": x_bearer_token,
         "llm_base_url": llm_base_url, "llm_api_key": llm_api_key, "llm_model": llm_model},
        clear=[f for f, flag in (("x_bearer_token", clear_x), ("llm_api_key", clear_llm)) if flag],
    )
    went_live = was_mock and mode == "live"
    if went_live:
        secrets_store.clear_collected_data()  # sample posts and fake since_ids must not mix with live data
        LAST_TEST.clear()
    return RedirectResponse(f"/settings?saved={'live' if went_live else '1'}", status_code=303)


@app.post("/settings/test/{what}")
def test_settings(what: str):
    from app.x_client import XClient

    eff = secrets_store.effective()
    try:
        if what == "x":
            if not eff.x_bearer_token:
                raise ValueError("No X Bearer token saved yet.")
            ok, reason = budget.can_spend("explore", budget.estimate_cost(10))
            if not ok:
                raise ValueError(reason)
            out = XClient(eff.x_bearer_token).search_recent("Narasaraopet -is:retweet", 10, stream="explore")
            n = len(out["posts"])
            msg = f"Connected. Found {n} recent post{'s' if n != 1 else ''} mentioning Narasaraopet (cost about {budget.x_cost(n, n):.3f} USD)."
        elif what == "llm":
            if not (eff.llm_api_key and eff.llm_model):
                raise ValueError("Save an OpenCode API key and model first.")
            import httpx

            r = httpx.post(eff.llm_base_url.rstrip("/") + "/chat/completions", timeout=60,
                           headers={"Authorization": f"Bearer {eff.llm_api_key}"},
                           json={"model": eff.llm_model, "temperature": 0, "messages": [{"role": "user", "content":
                                 "Translate to English, reply with the translation only: దుర్గిలో తాగునీరు రావడం లేదు"}]})
            if r.status_code >= 400:
                raise ValueError(f"OpenCode returned {r.status_code}: {r.text[:160]}")
            answer = r.json()["choices"][0]["message"]["content"].strip()
            msg = f'Connected. Test translation: "{answer[:140]}"'
        else:
            raise HTTPException(404)
        LAST_TEST[what] = {"ok": True, "message": msg, "at": utcnow()}
    except XAPIError as e:
        LAST_TEST[what] = {"ok": False, "message": f"{e.status}: {e.message}", "at": utcnow()}
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 - show any failure to the user
        LAST_TEST[what] = {"ok": False, "message": str(e)[:200] or type(e).__name__, "at": utcnow()}
    return RedirectResponse(f"/settings#{what}", status_code=303)


@app.get("/settings/models")
def list_models():
    """Model IDs from the configured OpenAI-compatible endpoint (for the model picker)."""
    import httpx

    eff = secrets_store.effective()
    if not eff.llm_api_key:
        return JSONResponse({"models": [], "error": "Save an API key first."})
    try:
        r = httpx.get(eff.llm_base_url.rstrip("/") + "/models", timeout=20,
                      headers={"Authorization": f"Bearer {eff.llm_api_key}"})
        r.raise_for_status()
        return JSONResponse({"models": sorted(m.get("id", "") for m in r.json().get("data", []))})
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"models": [], "error": str(e)[:200]})
