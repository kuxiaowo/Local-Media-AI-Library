from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

from app.models.schemas import SearchRequest
from app.services.search_service import search_media


class FakeOllama:
    async def embed_text(self, *, model: str, text: str) -> list[float]:
        return [1.0, 0.0, 0.0]


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class FakeSession:
    def __init__(self) -> None:
        self._scalar_calls = 0
        self._profile_id = uuid.uuid4()
        self.execute_calls = []

    def scalar(self, statement):
        self._scalar_calls += 1
        if self._scalar_calls == 1:
            return SimpleNamespace(id=self._profile_id)
        return None

    def execute(self, statement):
        self.execute_calls.append(statement)
        if len(self.execute_calls) == 1:
            return FakeResult([(uuid.uuid4(), "f:/photos")])
        return FakeResult([])


def test_search_media_does_not_limit_candidates_before_scoring() -> None:
    db = FakeSession()

    asyncio.run(search_media(db, SearchRequest(query="照片", candidate_k=1), FakeOllama()))

    main_search_statement = db.execute_calls[-1]
    assert main_search_statement._limit_clause is None
