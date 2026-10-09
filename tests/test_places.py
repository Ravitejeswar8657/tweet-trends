from app.config import get_config, get_places
from app.services.places import build_ac_queries, build_handle_queries, match_places, pack_or_queries


def test_english_match_gives_ac_and_mandal():
    assert match_places("No water in Durgi for 5 days", get_places()) == [("Macherla", "Durgi")]


def test_spelling_variant_and_case():
    assert ("Gurajala", "Gurajala") in match_places("GURAZALA roads are bad", get_places())
    assert ("Chilakaluripet", "Edlapadu") in match_places("yadlapadu farmers", get_places())


def test_telugu_with_suffix():
    # నరసరావుపేటలో = "in Narasaraopet": suffix attached to the name
    assert ("Narasaraopet", "Narasaraopet") in match_places("నరసరావుపేటలో కరెంటు కోతలు", get_places())


def test_word_boundary_for_latin():
    assert match_places("Durginagar is elsewhere", get_places()) == []


def test_ambiguous_alone_does_not_match():
    assert match_places("Amaravati capital works restart", get_places()) == []


def test_ambiguous_with_anchor_matches():
    hits = match_places("Amaravathi mandal, Palnadu: canal breach", get_places())
    assert ("Pedakurapadu", "Amaravathi") in hits


def test_ac_level_only_when_no_mandal():
    hits = match_places("Sattenapalle constituency and Muppalla mandal", get_places())
    assert ("Sattenapalle", "Muppalla") in hits
    assert ("Sattenapalle", None) not in hits


def test_ac_queries_skip_ambiguous_and_fit():
    queries = build_ac_queries(get_places(), get_config().local, 512)
    assert "local:district" in queries
    assert all(len(q) <= 512 for q in queries.values())
    pk = queries["local:Pedakurapadu"]
    assert "Krosuru" in pk and "Amaravati" not in pk and "Bellamkonda" not in pk


def test_ac_queries_split_when_too_long():
    queries = build_ac_queries(get_places(), get_config().local, 90)
    vinukonda = [k for k in queries if k.startswith("local:Vinukonda")]
    assert len(vinukonda) > 1 and all(k.count(":") == 2 for k in vinukonda)
    assert all(len(q) <= 90 for q in queries.values())


def test_handle_queries_pack_national_handles_under_limit():
    d = get_config().delhi
    queries = build_handle_queries(d.national_handles, "delhi:national", filter=d.ap_filter, max_len=512)
    assert 3 <= len(queries) <= 6
    assert list(queries) == [f"delhi:national:{i}" for i in range(1, len(queries) + 1)]
    for q in queries.values():
        assert len(q) <= 512 and "Andhra" in q and q.endswith("-is:retweet")
    joined = " ".join(queries.values())
    assert all(f"from:{h}" in joined for h in d.national_handles)


def test_handle_queries_stable_single_key():
    assert list(build_handle_queries(["a", "b"], "party:tdp")) == ["party:tdp"]


def test_pack_raises_when_filter_too_long():
    import pytest

    with pytest.raises(ValueError):
        pack_or_queries(["from:x"], "{ors} " + "y" * 600, 512)
