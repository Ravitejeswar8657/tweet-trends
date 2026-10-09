"""Gazetteer matching and X query builders. Pure functions: no API calls, no DB."""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Dict, List, Optional, Sequence, Tuple

from app.config import LocalCfg, Places

TELUGU = re.compile(r"[ఀ-౿]")


def _term(t: str) -> str:
    return f'"{t}"' if " " in t else t


def pack_or_queries(
    terms: Sequence[str], template: str, max_len: int
) -> List[str]:
    """Pack terms into as few `template.format(ors="(a OR b ...)")` queries as fit in max_len.

    Raises ValueError if even a single term does not fit (the fixed part is too long).
    """
    queries: List[str] = []
    current: List[str] = []

    def render(ts):
        return template.format(ors="(" + " OR ".join(ts) + ")").strip()

    for t in terms:
        if len(render([t])) > max_len:
            raise ValueError(f"Query too long even for one term ({len(render([t]))} > {max_len}): {t}")
        if current and len(render(current + [t])) > max_len:
            queries.append(render(current))
            current = []
        current.append(t)
    if current:
        queries.append(render(current))
    return queries


def _keyed(prefix: str, queries: List[str]) -> Dict[str, str]:
    if len(queries) == 1:
        return {prefix: queries[0]}
    return {f"{prefix}:{i}": q for i, q in enumerate(queries, start=1)}


def build_ac_queries(places: Places, local: LocalCfg, max_len: int = 512) -> Dict[str, str]:
    """One query per AC from its unambiguous aliases (AC + mandals), split if too long.
    The district's extra terms get their own query."""
    out: Dict[str, str] = {}
    template = "{ors} " + local.query_suffix
    for ac in places.assembly_constituencies:
        terms: List[str] = []
        for t in ac.aliases + [a for m in ac.mandals if not m.ambiguous for a in m.aliases]:
            t = _term(t)
            if t.lower() not in {x.lower() for x in terms}:
                terms.append(t)
        out.update(_keyed(f"local:{ac.name}", pack_or_queries(terms, template, max_len)))
    if local.extra_terms:
        out.update(_keyed("local:district", pack_or_queries([_term(t) for t in local.extra_terms], template, max_len)))
    return out


def build_handle_queries(
    handles: Sequence[str],
    prefix: str,
    filter: Optional[str] = None,
    suffix: str = "-is:retweet",
    max_len: int = 512,
) -> Dict[str, str]:
    """Pack handles into `(from:a OR from:b ...) <filter> -is:retweet` queries under max_len.
    Keys are stable (prefix, prefix:1, prefix:2 ...) so since_id state survives restarts."""
    if not handles:
        return {}
    filt = " ".join(filter.split()) if filter else ""
    template = "{ors} " + (filt + " " if filt else "") + suffix
    return _keyed(prefix, pack_or_queries([f"from:{h}" for h in handles], template, max_len))


# ---------- matching ----------

def _pattern(alias: str) -> str:
    if TELUGU.search(alias):
        return re.escape(alias)  # Telugu takes suffixes (నరసరావుపేటలో), so match as a substring
    return r"(?<![A-Za-z])" + re.escape(alias) + r"(?![A-Za-z])"


@lru_cache(maxsize=4)
def _compiled(places_json: str):
    places = Places.model_validate_json(places_json)
    entries: List[Tuple[re.Pattern, Optional[str], Optional[str], bool]] = []
    for alias in places.district.aliases + places.lok_sabha.aliases:
        entries.append((re.compile(_pattern(alias), re.I), None, None, False))
    for ac in places.assembly_constituencies:
        for alias in ac.aliases:
            entries.append((re.compile(_pattern(alias), re.I), ac.name, None, False))
        for m in ac.mandals:
            for alias in m.aliases:
                entries.append((re.compile(_pattern(alias), re.I), ac.name, m.name, m.ambiguous))
    return entries


def match_places(text: str, places: Places) -> List[Tuple[str, Optional[str]]]:
    """Return [(ac, mandal_or_None)] mentioned in the text, most specific first.

    Ambiguous names (Amaravati, Bellamkonda ...) only count when another, unambiguous
    Palnadu term is also in the post.
    """
    entries = _compiled(places.model_dump_json())
    hits = [(ac, mandal, amb) for pat, ac, mandal, amb in entries if pat.search(text or "")]
    anchored = any(not amb for _, _, amb in hits)
    found: List[Tuple[str, Optional[str]]] = []
    for ac, mandal, amb in hits:
        if ac is None or (amb and not anchored):
            continue
        if (ac, mandal) not in found:
            found.append((ac, mandal))
    with_mandal = {ac for ac, m in found if m}
    found = [(ac, m) for ac, m in found if m or ac not in with_mandal]
    return sorted(found, key=lambda x: x[1] is None)
