from datetime import datetime, timedelta

from app.config import Alerts
from app.services import insights
from tests.conftest import P

NOW = datetime(2026, 10, 9, 12, 0)
CFG = Alerts(window_hours=6, min_negative_posts=5, spike_factor=2.0, urgency_threshold=2)


def local(n, urgency=0, sentiment="neg", hours_ago=1, mandal="Durgi", issue="drinking_water"):
    return [P(id=f"{mandal}{issue}{hours_ago}{i}", ac="Macherla", mandal=mandal, issue=issue, sentiment=sentiment,
              urgency=urgency, created_at=NOW - timedelta(hours=hours_ago), summary_en="No water")
            for i in range(n)]


def test_engagement_and_velocity():
    p = P(likes=10, retweets=5, replies=2, quotes=1, created_at=NOW - timedelta(hours=4))
    assert insights.engagement(p) == 23
    assert insights.velocity(p, NOW) == 23 / 4
    assert insights.velocity(P(likes=10, created_at=NOW), NOW) == 10  # age floored at 1h


def test_below_min_negative_is_ignored():
    assert insights.needs_attention(local(4, urgency=3), CFG, NOW) == []


def test_urgent_and_spiking_is_red():
    cards = insights.needs_attention(local(6, urgency=3), CFG, NOW)
    assert len(cards) == 1 and cards[0]["level"] == "red" and cards[0]["negative"] == 6
    assert cards[0]["gist"] == "No water" and len(cards[0]["top_posts"]) == 3


def test_urgent_but_normal_volume_is_amber():
    # A steady baseline of ~2/hour for the past week -> 6 posts in 6h is not a spike.
    history = [p for h in range(7, 7 * 24, 1) for p in local(2, sentiment="neu", hours_ago=h)]
    cards = insights.needs_attention(local(6, urgency=3) + history, CFG, NOW)
    assert cards[0]["level"] == "amber"


def test_spiking_but_low_urgency_is_amber():
    cards = insights.needs_attention(local(6, urgency=1), CFG, NOW)
    assert cards[0]["level"] == "amber"


def test_not_urgent_not_spiking_is_ignored():
    history = [p for h in range(7, 7 * 24, 1) for p in local(2, sentiment="neu", hours_ago=h)]
    assert insights.needs_attention(local(6, urgency=1) + history, CFG, NOW) == []


def test_ranked_by_score():
    cards = insights.needs_attention(local(6, urgency=1, mandal="Durgi") + local(9, urgency=3, mandal="Karempudi"), CFG, NOW)
    assert [c["mandal"] for c in cards] == ["Karempudi", "Durgi"]


def test_share_of_voice_buckets():
    posts = [P(category="tdp", created_at=NOW - timedelta(minutes=30)) for _ in range(3)] + \
            [P(category="ysrcp", created_at=NOW - timedelta(hours=5, minutes=10))]
    sov = insights.share_of_voice(posts, NOW, 24)
    assert len(sov["buckets"]) == 24
    assert sov["totals"] == {"tdp": 3, "ysrcp": 1}
    assert sov["series"]["tdp"]["posts"][-2] == 3  # 11:30 falls in the 11:00 bucket
    assert sov["share"]["tdp"] == 0.75


def test_share_of_voice_ignores_old_posts():
    sov = insights.share_of_voice([P(category="tdp", created_at=NOW - timedelta(days=3))], NOW, 24)
    assert sov["totals"]["tdp"] == 0


def test_attack_lines():
    posts = [P(id="a", category="ysrcp", party_stance="anti_tdp", issue="farmers", likes=100),
             P(id="b", category="ysrcp", party_stance="anti_tdp", issue="power", likes=10),
             P(id="c", category="ysrcp", party_stance="pro_ysrcp", issue="health", likes=999),
             P(id="d", category="tdp", party_stance="anti_tdp", issue="roads", likes=999)]
    lines = insights.attack_lines(posts, NOW)
    assert [l["issue"] for l in lines] == ["farmers", "power"]


def test_centre_link_telugu_and_handles():
    terms = ["Centre", "Union", "PM", "కేంద్ర"]
    keep = P(author_username="AndhraPradeshCM", text="కేంద్ర నిధులతో రహదారి పనులు")
    keep2 = P(author_username="JaiTDP", text="Met the Union Minister today")
    drop = P(author_username="AndhraPradeshCM", text="Pensions distributed in Palnadu")
    other = P(author_username="naralokesh", text="Centre approved")
    no_partial = P(author_username="JaiTDP", text="Meeting at 5PMish in Unionville")
    out = insights.centre_link([keep, keep2, drop, other, no_partial], terms, ["AndhraPradeshCM", "JaiTDP"])
    assert out == [keep, keep2]


def test_issue_heatmap():
    posts = local(3) + local(1, sentiment="pos", issue="roads")
    heat = insights.issue_heatmap(posts, ["Macherla", "Vinukonda"], ["drinking_water", "roads"])
    assert heat["cells"]["Macherla"]["drinking_water"] == {"count": 3, "neg_share": 1.0, "intensity": 1.0}
    assert heat["cells"]["Vinukonda"]["roads"]["count"] == 0


def test_hashtags_and_words_keep_telugu():
    posts = [P(text="#Polavaram పనులు వేగంగా https://t.co/x"), P(text="#polavaram works పనులు")]
    assert insights.top_hashtags(posts) == [("polavaram", 2)]
    words = dict(insights.top_words(posts))
    assert words["పనులు"] == 2 and "https" not in words
