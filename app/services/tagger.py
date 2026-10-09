"""Tags posts with sentiment, issue, place, party stance, urgency and an English gist.

LLMTagger    any OpenAI-compatible endpoint (OpenCode by default); understands Telugu.
FallbackTagger  VADER (English only) + gazetteer + a small keyword issue guesser. No key needed.
MockTagger   uses the ground truth the mock X client generated.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Dict, List, Optional, Protocol

import httpx
from pydantic import BaseModel, ValidationError
from sqlmodel import select

from app import budget
from app.config import Places, get_config, get_places, get_settings
from app.db import session
from app.models import Post, utcnow
from app.services.places import match_places

log = logging.getLogger("tagger")

SENTIMENTS = {"pos", "neu", "neg"}
STANCES = {"pro_tdp", "anti_tdp", "pro_ysrcp", "anti_ysrcp", "neutral"}
BATCH = 20


class Tag(BaseModel):
    id: str
    sentiment: str = "neu"
    issue: Optional[str] = "other"
    party_stance: str = "neutral"
    ac: Optional[str] = None
    mandal: Optional[str] = None
    urgency: int = 0
    summary_en: Optional[str] = None
    ap_relevant: Optional[bool] = None


def allowed_issues(category: str) -> List[str]:
    cfg = get_config()
    return cfg.delhi.topics if category == "delhi" else cfg.issues


def clean(tag: Tag, category: str, places: Places) -> Tag:
    """Force every field into its allowed set; unknown values fall back safely."""
    issues = allowed_issues(category)
    tag.sentiment = tag.sentiment if tag.sentiment in SENTIMENTS else "neu"
    tag.issue = tag.issue if tag.issue in issues else "other"
    tag.party_stance = tag.party_stance if tag.party_stance in STANCES else "neutral"
    tag.urgency = max(0, min(3, int(tag.urgency or 0)))
    ac = places.ac(tag.ac) if tag.ac else None
    tag.ac = ac.name if ac else None
    if not ac or tag.mandal not in {m.name for m in ac.mandals}:
        tag.mandal = None
    if category != "delhi":
        tag.ap_relevant = None
    elif tag.ap_relevant is None:
        tag.ap_relevant = True
    if tag.summary_en:
        tag.summary_en = tag.summary_en.strip()[:240]
    return tag


class Tagger(Protocol):
    def tag(self, posts: List[Post]) -> List[Tag]: ...


# ---------- fallback ----------

ISSUE_KEYWORDS = {
    "drinking_water": ["drinking water", "water tanker", "tap water", "తాగునీ", "మంచినీ", "నీటి సమస్య"],
    "irrigation": ["canal", "irrigation", "sagar", "కాలువ", "సాగునీ"],
    "farmers": ["farmer", "crop", "chilli", "mirchi", "రైతు", "మిర్చి", "పంట"],
    "roads": ["road", "pothole", "highway", "రోడ్డు", "రహదారి"],
    "power": ["power cut", "current", "electricity", "కరెంటు", "విద్యుత్"],
    "health": ["hospital", "doctor", "health", "ఆసుపత్రి", "వైద్య"],
    "education": ["school", "college", "student", "పాఠశాల", "కళాశాల"],
    "law_and_order": ["clash", "attack", "police", "murder", "ఘర్షణ", "దాడి", "పోలీసు"],
    "welfare_schemes": ["pension", "ration", "scheme", "పింఛన్", "రేషన్", "పథకం"],
    "jobs": ["job", "unemploy", "ఉద్యోగ"],
    "corruption": ["corruption", "scam", "loot", "అవినీతి", "దోపిడీ"],
}


def guess_issue(text: str, issues: List[str]) -> str:
    low = (text or "").lower()
    for issue, words in ISSUE_KEYWORDS.items():
        if issue in issues and any(w in low for w in words):
            return issue
    return "other"


class FallbackTagger:
    def __init__(self):
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

        self.vader = SentimentIntensityAnalyzer()

    def tag(self, posts: List[Post]) -> List[Tag]:
        places = get_places()
        out = []
        for p in posts:
            score = self.vader.polarity_scores(p.text or "")["compound"]
            sentiment = "pos" if score >= 0.05 else "neg" if score <= -0.05 else "neu"
            hits = match_places(p.text, places)
            ac, mandal = hits[0] if hits else (None, None)
            out.append(clean(Tag(id=p.id, sentiment=sentiment, ac=ac, mandal=mandal,
                                 issue=guess_issue(p.text, allowed_issues(p.category))), p.category, places))
        return out


class MockTagger(FallbackTagger):
    def tag(self, posts: List[Post]) -> List[Tag]:
        from app.mock_data import MOCK_TRUTH

        places = get_places()
        known = [p for p in posts if p.id in MOCK_TRUTH]
        unknown = [p for p in posts if p.id not in MOCK_TRUTH]
        out = [clean(Tag(id=p.id, **MOCK_TRUTH[p.id]), p.category, places) for p in known]
        return out + super().tag(unknown)


# ---------- LLM ----------

def system_prompt(places: Places) -> str:
    cfg = get_config()
    geo = "\n".join(
        f"- {ac.name}: " + ", ".join(m.name for m in ac.mandals) for ac in places.assembly_constituencies
    )
    return f"""You tag X (Twitter) posts for the office of the Narasaraopet Lok Sabha MP (TDP), Palnadu district, Andhra Pradesh.
Posts may be in Telugu, English, or mixed Telugu-English. Politics: TDP leads the ruling NDA coalition in AP (with JSP and BJP); YSRCP is the main opposition.

Assembly constituencies (ac) and their mandals:
{geo}

For EVERY post return one object with:
- id: the post id, unchanged
- sentiment: "pos" | "neu" | "neg"  (the author's tone)
- issue: for category "delhi" one of {cfg.delhi.topics}; otherwise one of {cfg.issues}
- party_stance: "pro_tdp" | "anti_tdp" | "pro_ysrcp" | "anti_ysrcp" | "neutral"
- ac: assembly constituency name from the list above, or null
- mandal: mandal name from that ac's list, or null
- urgency: 0 none, 1 low, 2 needs follow-up within days, 3 urgent (safety, violence, an outage hurting many people)
- summary_en: one plain English sentence, max 15 words, saying what the post is about
- ap_relevant: for category "delhi" only: true if the post is really about Andhra Pradesh, false if it matched by chance

`place_hint` is what a gazetteer found in the text; confirm or correct it.
Reply with JSON only: {{"tags": [ ... ]}}"""


def extract_json(content: str) -> dict:
    content = content.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", content, re.S)
    if fenced:
        content = fenced.group(1)
    start, end = content.find("{"), content.rfind("}")
    return json.loads(content[start : end + 1])


class LLMTagger:
    def __init__(self, base_url: str, api_key: str, model: str, http: Optional[httpx.Client] = None):
        self.model = model
        self.http = http or httpx.Client(
            base_url=base_url.rstrip("/"), headers={"Authorization": f"Bearer {api_key}"}, timeout=90
        )

    def _call(self, places: Places, posts: List[Post]) -> List[Tag]:
        items = []
        for p in posts:
            hits = match_places(p.text, places)
            items.append({"id": p.id, "category": p.category, "text": p.text,
                          "place_hint": [{"ac": a, "mandal": m} for a, m in hits]})
        r = self.http.post("/chat/completions", json={
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system_prompt(places)},
                {"role": "user", "content": json.dumps(items, ensure_ascii=False)},
            ],
        })
        r.raise_for_status()
        body = r.json()
        usage = body.get("usage", {}) or {}
        tin, tout = usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)
        budget.record("llm", self.model, tin + tout, budget.llm_cost(tin, tout), "tagging")
        data = extract_json(body["choices"][0]["message"]["content"])
        by_cat = {p.id: p.category for p in posts}
        tags = []
        for raw in data.get("tags", []):
            tag = Tag(**raw)
            if tag.id in by_cat:
                tags.append(clean(tag, by_cat[tag.id], places))
        return tags

    def tag(self, posts: List[Post]) -> List[Tag]:
        places = get_places()
        for attempt in (1, 2):
            try:
                return self._call(places, posts)
            except (ValueError, KeyError, ValidationError, json.JSONDecodeError) as e:
                log.warning("LLM tagging parse failed (attempt %d): %s", attempt, e)
        return []  # caller marks these posts failed


def get_tagger() -> Tagger:
    s = get_settings()
    if s.mock_mode:
        return MockTagger()
    if s.llm_api_key and s.llm_model:
        return LLMTagger(s.llm_base_url, s.llm_api_key, s.llm_model)
    return FallbackTagger()


def apply_tags(posts: List[Post], tags: List[Tag]) -> Dict[str, int]:
    by_id: Dict[str, Tag] = {t.id: t for t in tags}
    counts = {"done": 0, "failed": 0}
    with session() as s:
        for p in posts:
            row = s.get(Post, p.id)
            tag = by_id.get(p.id)
            if tag is None:
                row.tag_status = "failed"
                counts["failed"] += 1
            else:
                for k in ("sentiment", "issue", "party_stance", "ac", "mandal", "urgency", "summary_en", "ap_relevant"):
                    setattr(row, k, getattr(tag, k))
                row.tag_status = "done"
                counts["done"] += 1
            row.tagged_at = utcnow()
            s.add(row)
        s.commit()
    return counts


def tag_pending(tagger: Optional[Tagger] = None, limit: int = 400) -> Dict[str, int]:
    tagger = tagger or get_tagger()
    with session() as s:
        pending = list(s.exec(select(Post).where(Post.tag_status == "pending").order_by(Post.fetched_at).limit(limit)))
    totals = {"done": 0, "failed": 0}
    for i in range(0, len(pending), BATCH):
        batch = pending[i : i + BATCH]
        try:
            tags = tagger.tag(batch)
        except httpx.HTTPError as e:
            log.warning("LLM tagging request failed, will retry next run: %s", e)
            break  # leave them pending
        for k, v in apply_tags(batch, tags).items():
            totals[k] += v
    return totals
