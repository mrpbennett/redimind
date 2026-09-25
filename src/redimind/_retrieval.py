"""Project-first retrieval and evidence presentation for memory entries."""

import re
from array import array

from redis.commands.search.query import Query

from redimind._errors import MemoryError
from redimind._index import INDEX, PREFIX, MemoryIndex


def _words(text: str) -> str:
    return " | ".join(re.findall(r"[a-zA-Z0-9_]+", text)[:12])


class MemoryRetrieval:
    """Keeps Redis query details and applicability rules behind one retrieval seam."""

    def __init__(self, redis, index: MemoryIndex):
        self.redis = redis
        self.index = index

    async def duplicates(self, claim: str | None) -> list[str]:
        await self.index.require_searchable()
        if not claim or not _words(claim):
            return []
        response = await self.redis.ft(INDEX).search(
            Query(f"@status:{{current}} @text:({_words(claim)})").no_content().paging(0, 5)
        )
        return [doc.id.removeprefix(PREFIX) for doc in response.docs]

    async def _ids(
        self, query: Query, params: dict | None = None, max_distance: float | None = None
    ) -> list[str]:
        response = await self.redis.ft(INDEX).search(query, query_params=params)
        return [
            doc.id.removeprefix(PREFIX)
            for doc in response.docs
            if max_distance is None or float(doc.distance) <= max_distance
        ]

    async def search(
        self, text: str, project_id: str, include_decisions: bool = False, limit: int = 10
    ) -> list[dict]:
        await self.index.require_searchable()
        if not text.strip() or not project_id.strip():
            raise MemoryError("query and project_id are required")
        limit = max(1, min(limit, 20))
        permitted = "@status:{current}"
        local = permitted + " @project:{" + re.sub(r"([^a-zA-Z0-9_])", r"\\\1", project_id) + "}"
        ranks: dict[str, float] = {}
        term_query = _words(text)
        if term_query:
            for filter_query in (local, permitted):
                found = await self._ids(
                    Query(f"{filter_query} @text:({term_query})").no_content().paging(0, 100)
                )
                for rank, entry_id in enumerate(found):
                    ranks[entry_id] = ranks.get(entry_id, 0) + 1 / (10 + rank)
        vector = await self.index.embedding(text)
        if vector:
            for filter_query in (local, permitted):
                query = (
                    Query(f"({filter_query})=>[KNN 60 @embedding $vec AS distance]")
                    .sort_by("distance")
                    .return_fields("distance")
                    .dialect(2)
                    .paging(0, 60)
                )
                found = await self._ids(
                    query, {"vec": array("f", vector).tobytes()}, max_distance=0.55
                )
                for rank, entry_id in enumerate(found):
                    ranks[entry_id] = ranks.get(entry_id, 0) + 1 / (10 + rank)
        matches = [await self.redis.json().get(PREFIX + entry_id) for entry_id in ranks]
        matches = [
            entry
            for entry in matches
            if entry is not None
            and entry["status"] == "current"
            and (
                entry["kind"] == "lesson" or entry["project_id"] == project_id or include_decisions
            )
        ]
        matches.sort(
            key=lambda entry: (
                entry["project_id"] == project_id,
                ranks[entry["id"]],
            ),
            reverse=True,
        )
        return [
            {
                "id": entry["id"],
                "kind": entry["kind"],
                "claim": entry["claim"],
                "project_id": entry["project_id"],
                "source_ref": entry["source_ref"],
                "conditions": entry["conditions"],
                "outcome": entry["outcome"],
                "evidence_status": entry["evidence_status"],
                "match": "current project"
                if entry["project_id"] == project_id
                else "cross-project lesson"
                if entry["kind"] == "lesson"
                else "cross-project decision (opt-in)",
            }
            for entry in matches[:limit]
        ]
