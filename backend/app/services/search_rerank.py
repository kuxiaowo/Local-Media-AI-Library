from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime


_QUERY_SPLIT_RE = re.compile(r"[\s,，.。;；:：、!?！？]+")
_CJK_RE = re.compile(r"[\u3400-\u9fff]")


@dataclass(frozen=True)
class RerankInput:
    vector_score: float
    query: str
    text: str
    captured_at: datetime | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None
    folder_match: bool = False


def _query_terms(query: str) -> list[str]:
    terms: list[str] = []
    for token in _QUERY_SPLIT_RE.split(query.lower()):
        token = token.strip()
        if not token:
            continue
        if _CJK_RE.search(token) and len(token) > 2:
            terms.extend(token[index : index + 2] for index in range(len(token) - 1))
        else:
            terms.append(token)

    deduped: list[str] = []
    seen: set[str] = set()
    for term in terms:
        if term not in seen:
            deduped.append(term)
            seen.add(term)
    return deduped


def keyword_score(query: str, text: str) -> float:
    words = _query_terms(query)
    if not words:
        return 0.0
    haystack = text.lower()
    matched = sum(1 for word in words if word in haystack)
    return matched / len(words)


def time_score(captured_at: datetime | None, start: datetime | None, end: datetime | None) -> float:
    if start is None and end is None:
        return 0.0
    if captured_at is None:
        return 0.0
    if start is not None and captured_at < start:
        return 0.0
    if end is not None and captured_at > end:
        return 0.0
    return 1.0


def final_score(item: RerankInput) -> float:
    score = (
        0.65 * max(0.0, min(1.0, item.vector_score))
        + 0.15 * keyword_score(item.query, item.text)
        + 0.10 * time_score(item.captured_at, item.date_from, item.date_to)
        + 0.10 * (1.0 if item.folder_match else 0.0)
    )
    return round(score, 6)
