import httpx
import pytest
import respx

from app.x_client import BASE_URL, XAPIError, XClient

PAGE1 = {
    "data": [{"id": "11", "author_id": "u1", "text": "Durgi water", "lang": "en", "created_at": "2026-10-09T06:00:00.000Z",
              "conversation_id": "11", "public_metrics": {"like_count": 5, "retweet_count": 2, "reply_count": 1,
                                                          "quote_count": 0, "impression_count": 300}}],
    "includes": {"users": [{"id": "u1", "username": "palnadu_voice"}]},
    "meta": {"newest_id": "11", "next_token": "abc"},
}
PAGE2 = {"data": [{"id": "10", "author_id": "u2", "text": "x", "created_at": "2026-10-09T05:00:00Z"}],
         "includes": {"users": [{"id": "u2", "username": "other"}]}, "meta": {"newest_id": "10"}}


def client(sleeps=None):
    return XClient("tok", sleep=(sleeps.append if sleeps is not None else lambda s: None))


@respx.mock
def test_normalises_with_users_join_and_since_id():
    route = respx.get(f"{BASE_URL}/tweets/search/recent").mock(return_value=httpx.Response(200, json=PAGE1))
    out = client().search_recent("Durgi", 5, since_id="9", stream="local")
    req = route.calls[0].request
    assert req.url.params["since_id"] == "9" and req.url.params["max_results"] == "10"  # clamped up
    assert req.headers["authorization"] == "Bearer tok"
    p = out["posts"][0]
    assert out["newest_id"] == "11" and len(out["posts"]) == 1  # one page by default
    assert (p["author_username"], p["likes"], p["retweets"], p["impressions"]) == ("palnadu_voice", 5, 2, 300)
    assert p["created_at"].isoformat() == "2026-10-09T06:00:00"


@respx.mock
def test_pagination_with_next_token():
    route = respx.get(f"{BASE_URL}/tweets/search/recent").mock(
        side_effect=[httpx.Response(200, json=PAGE1), httpx.Response(200, json=PAGE2)])
    out = client().search_recent("q", 20, pages=3)
    assert [p["id"] for p in out["posts"]] == ["11", "10"] and route.call_count == 2
    assert route.calls[1].request.url.params["next_token"] == "abc"


@respx.mock
def test_ledger_records_posts_read():
    from app import budget
    from datetime import datetime

    respx.get(f"{BASE_URL}/tweets/search/recent").mock(return_value=httpx.Response(200, json=PAGE1))
    client().search_recent("q", 10, stream="local")
    assert budget.spend_by_group(datetime(2000, 1, 1))["Local"] == pytest.approx(0.005)


@respx.mock
def test_429_short_wait_retries_once():
    import time

    reset = str(int(time.time()) + 5)
    respx.get(f"{BASE_URL}/tweets/search/recent").mock(side_effect=[
        httpx.Response(429, headers={"x-rate-limit-reset": reset}), httpx.Response(200, json=PAGE1)])
    sleeps = []
    out = client(sleeps).search_recent("q")
    assert len(out["posts"]) == 1 and len(sleeps) == 1


@respx.mock
def test_429_long_wait_raises():
    import time

    reset = str(int(time.time()) + 900)
    respx.get(f"{BASE_URL}/tweets/search/recent").mock(return_value=httpx.Response(429, headers={"x-rate-limit-reset": reset}))
    with pytest.raises(XAPIError) as e:
        client().search_recent("q")
    assert e.value.status == 429 and "Rate limited" in e.value.message


@respx.mock
@pytest.mark.parametrize("status,body,text", [
    (401, "{}", "X_BEARER_TOKEN"),
    (403, '{"title": "Forbidden"}', "does not include this endpoint"),
    (402, "{}", "credits exhausted"),
    (403, '{"title": "CreditsDepleted", "detail": "Your account has no credits"}', "credits exhausted"),
])
def test_readable_errors(status, body, text):
    respx.get(f"{BASE_URL}/tweets/search/recent").mock(return_value=httpx.Response(status, text=body))
    with pytest.raises(XAPIError) as e:
        client().search_recent("q")
    assert text in e.value.message


@respx.mock
def test_trends():
    respx.get(f"{BASE_URL}/trends/by/woeid/23424848").mock(return_value=httpx.Response(200, json={
        "data": [{"trend_name": "#A", "tweet_count": 1000}, {"trend_name": "#B"}]}))
    assert client().trends_by_woeid(23424848) == [{"name": "#A", "tweet_count": 1000}, {"name": "#B", "tweet_count": None}]
