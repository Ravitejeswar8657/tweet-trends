import json
from datetime import datetime

import httpx
import pytest
import respx

from app.db import session
from app.models import Post
from app.services import tagger
from app.services.tagger import FallbackTagger, LLMTagger, Tag, clean, get_tagger, tag_pending
from app.config import get_places

BASE = "https://llm.test/v1"


def make_posts():
    posts = [Post(id="1", category="local", text="దుర్గిలో తాగునీరు రావడం లేదు", created_at=datetime(2026, 10, 9)),
             Post(id="2", category="delhi", text="NH-16 widening in Andhra Pradesh", created_at=datetime(2026, 10, 9))]
    with session() as s:
        for p in posts:
            s.add(p)
        s.commit()
    return posts


def llm_reply(content, tokens=(100, 50)):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}],
                                     "usage": {"prompt_tokens": tokens[0], "completion_tokens": tokens[1]}})


GOOD = json.dumps({"tags": [
    {"id": "1", "sentiment": "neg", "issue": "drinking_water", "party_stance": "neutral", "ac": "Macherla",
     "mandal": "Durgi", "urgency": 3, "summary_en": "No drinking water in Durgi"},
    {"id": "2", "sentiment": "pos", "issue": "highways", "party_stance": "neutral", "ac": None, "mandal": None,
     "urgency": 0, "summary_en": "NH-16 widening", "ap_relevant": True},
]})


@respx.mock
def test_valid_json_tags_posts():
    route = respx.post(f"{BASE}/chat/completions").mock(return_value=llm_reply("```json\n" + GOOD + "\n```"))
    posts = make_posts()
    counts = tag_pending(LLMTagger(BASE, "key", "model-x"))
    assert counts == {"done": 2, "failed": 0} and route.call_count == 1
    body = json.loads(route.calls[0].request.content)
    assert body["temperature"] == 0 and body["model"] == "model-x"
    sent = json.loads(body["messages"][1]["content"])
    assert sent[0]["place_hint"] == [{"ac": "Macherla", "mandal": "Durgi"}]  # gazetteer hint included
    with session() as s:
        p1, p2 = s.get(Post, "1"), s.get(Post, "2")
    assert (p1.issue, p1.mandal, p1.urgency, p1.tag_status) == ("drinking_water", "Durgi", 3, "done")
    assert p2.issue == "highways" and p2.ap_relevant is True


@respx.mock
def test_bad_json_retries_once_then_fails():
    route = respx.post(f"{BASE}/chat/completions").mock(return_value=llm_reply("sorry, I cannot"))
    make_posts()
    counts = tag_pending(LLMTagger(BASE, "key", "m"))
    assert route.call_count == 2 and counts == {"done": 0, "failed": 2}


@respx.mock
def test_http_error_leaves_posts_pending():
    respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(503))
    make_posts()
    counts = tag_pending(LLMTagger(BASE, "key", "m"))
    assert (counts["done"], counts["failed"]) == (0, 0) and "503" in counts["error"]
    with session() as s:
        assert s.get(Post, "1").tag_status == "pending"


@respx.mock
def test_llm_cost_recorded():
    from app import budget
    from app.config import get_config

    get_config().budget.llm_cost_per_mtok_in = 1.0
    get_config().budget.llm_cost_per_mtok_out = 2.0
    respx.post(f"{BASE}/chat/completions").mock(return_value=llm_reply(GOOD, tokens=(1_000_000, 500_000)))
    make_posts()
    tag_pending(LLMTagger(BASE, "key", "m"))
    assert budget.spend_by_group(datetime(2000, 1, 1))["LLM tagging"] == pytest.approx(2.0)


def test_clean_forces_allowed_values():
    t = clean(Tag(id="x", sentiment="angry", issue="aliens", party_stance="pro_bjp", ac="Macherla",
                  mandal="Vinukonda", urgency=9), "local", get_places())
    assert (t.sentiment, t.issue, t.party_stance, t.ac, t.mandal, t.urgency) == \
           ("neu", "other", "neutral", "Macherla", None, 3)
    assert clean(Tag(id="y", issue="drinking_water"), "delhi", get_places()).issue == "other"
    assert clean(Tag(id="z", ac="Nowhere", mandal="Durgi"), "local", get_places()).ac is None


def test_fallback_when_no_key(monkeypatch):
    from app import config

    monkeypatch.setenv("MOCK_MODE", "false")
    config.get_settings.cache_clear()
    t = get_tagger()
    assert isinstance(t, FallbackTagger) and not isinstance(t, tagger.MockTagger)
    tags = t.tag([Post(id="1", category="local", text="Terrible power cuts in Durgi, awful")])
    assert (tags[0].sentiment, tags[0].ac, tags[0].mandal, tags[0].issue) == ("neg", "Macherla", "Durgi", "power")


def test_llm_tagger_when_key_and_model(monkeypatch):
    from app import config

    monkeypatch.setenv("MOCK_MODE", "false")
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_MODEL", "m")
    config.get_settings.cache_clear()
    assert isinstance(get_tagger(), LLMTagger)


def test_only_one_tagging_pass_at_a_time():
    make_posts()
    tagger._tag_lock.acquire()
    try:
        assert tag_pending(tagger.MockTagger()) == {"done": 0, "failed": 0}
    finally:
        tagger._tag_lock.release()
    assert tag_pending(tagger.MockTagger())["done"] == 2


@respx.mock
def test_out_of_credits_shows_in_banner():
    from app import jobs, views
    from app.services import tagger as tg

    respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(402, json={"error": "Payment Required"}))
    make_posts()
    jobs.last_errors.clear()
    orig = tg.get_tagger
    jobs.tag_pending = lambda: tg.tag_pending(LLMTagger(BASE, "key", "m"))
    try:
        jobs.run_job("tag", jobs.job_tag)
    finally:
        jobs.tag_pending = tg.tag_pending
    texts = [b["text"] for b in views.banner()]
    assert any("OpenCode credits exhausted" in t for t in texts), texts
    with session() as s:
        assert s.get(Post, "1").tag_status == "pending"  # nothing lost; retried after top-up
    jobs.last_errors.clear()
