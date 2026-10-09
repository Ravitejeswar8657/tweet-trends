from app.services.trends import compute_deltas


def t(name, rank, count=None):
    return {"name": name, "rank": rank, "tweet_count": count}


def status(cur, prev):
    return {d["name"]: d["status"] for d in compute_deltas(cur, prev)}


def test_new_rising_falling_steady():
    prev = [t("a", 1), t("b", 2), t("c", 3), t("d", 4)]
    cur = [t("b", 1), t("a", 2), t("c", 3), t("x", 4)]
    assert status(cur, prev) == {"b": "RISING", "a": "FALLING", "c": "STEADY", "x": "NEW"}


def test_rising_by_volume():
    assert status([t("a", 3, 1200)], [t("a", 3, 1000)])["a"] == "RISING"
    assert status([t("a", 3, 1100)], [t("a", 3, 1000)])["a"] == "STEADY"
