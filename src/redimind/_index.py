"""Redis search-index and embedding-model coherence."""

import asyncio
import logging
import math

from redis.commands.search.field import TagField, TextField, VectorField
from redis.commands.search.index_definition import IndexDefinition, IndexType
from redis.exceptions import ResponseError

from redimind._errors import MemoryError

PREFIX = "redimind:entry:"
INDEX = "redimind:entries"
META = "redimind:index-model"
REBUILD = "redimind:index-rebuild"
logger = logging.getLogger(__name__)


class MemoryIndex:
    """Owns search-index readiness and embedding compatibility for all memory paths."""

    def __init__(self, redis, embeddings, dimension: int):
        self.redis = redis
        self.embeddings = embeddings
        self.dimension = dimension
        self.model_changed = False
        self.rebuild_required = False

    @property
    def model(self) -> str:
        return self.embeddings.model

    async def initialize(self) -> None:
        await self.redis.ping()
        self.rebuild_required = bool(await self.redis.get(REBUILD))
        if self.rebuild_required:
            self.model_changed = True
            return  # Reindex may have stopped after dropping the search index.
        model = await self.redis.get(META)
        self.model_changed = bool(model and model.decode() != f"{self.model}:{self.dimension}")
        try:
            await self.redis.ft(INDEX).info()
        except ResponseError as exc:
            if not self._missing_index(exc):
                raise
            await self._create_index()
        await self.redis.set(META, f"{self.model}:{self.dimension}", nx=True)

    @staticmethod
    def _missing_index(exc: ResponseError) -> bool:
        return any(
            phrase in str(exc).lower()
            for phrase in ("unknown index", "no such index", "search_index_not_found")
        )

    async def _create_index(self) -> None:
        await self.redis.ft(INDEX).create_index(
            (
                TextField("$.search_text", as_name="text"),
                TagField("$.status", as_name="status"),
                TagField("$.kind", as_name="kind"),
                TagField("$.project_id", as_name="project"),
                VectorField(
                    "$.embedding",
                    "FLAT",
                    {"TYPE": "FLOAT32", "DIM": self.dimension, "DISTANCE_METRIC": "COSINE"},
                    as_name="embedding",
                ),
            ),
            definition=IndexDefinition(prefix=[PREFIX], index_type=IndexType.JSON),
        )

    async def require_searchable(self) -> None:
        rebuilding, model = await self.redis.mget(REBUILD, META)
        self.rebuild_required = bool(rebuilding)
        self.model_changed = bool(model and model.decode() != f"{self.model}:{self.dimension}")
        if rebuilding:
            raise MemoryError("index rebuild interrupted; owner must reindex before searching")

    async def embedding(self, text: str, *, for_write: bool = False) -> list[float] | None:
        await self.require_searchable()
        if self.model_changed:
            if for_write:
                raise MemoryError("embedding model changed; owner must reindex before approving")
            return None  # Existing indexed text remains available for keyword search.
        return await asyncio.to_thread(self._embed_or_none, text)

    def _embed_or_none(self, text: str) -> list[float] | None:
        try:
            vector = self.embeddings.embed(text)
            if len(vector) != self.dimension or not all(math.isfinite(x) for x in vector):
                raise ValueError("wrong embedding dimensions or nonfinite values")
            return vector
        except Exception:
            logger.warning(
                "Local embedding unavailable; keyword retrieval remains active", exc_info=True
            )
            return None

    async def reindex(self) -> dict:
        """Resume or rebuild vectors; publish the new model only after the index is complete."""
        probe = await asyncio.to_thread(self.embeddings.embed, "model dimension check")
        if not probe or not all(math.isfinite(x) for x in probe):
            raise MemoryError("embedding provider is unavailable")
        self.dimension = len(probe)
        await self.redis.set(REBUILD, f"{self.model}:{self.dimension}")
        self.rebuild_required = True
        try:
            await self.redis.ft(INDEX).dropindex()  # Keep underlying JSON documents.
        except ResponseError as exc:
            if not self._missing_index(exc):
                raise
        count = 0
        async for entry_key in self.redis.scan_iter(match=PREFIX + "*"):
            entry = await self.redis.json().get(entry_key)
            if entry["status"] in {"current", "retired", "superseded"}:
                vector = await asyncio.to_thread(self.embeddings.embed, entry["search_text"])
                if len(vector) != self.dimension or not all(math.isfinite(x) for x in vector):
                    raise MemoryError("embedding provider returned incompatible vectors")
                entry["embedding"] = [float(x) for x in vector]
                entry["embedding_model"] = self.model
                await self.redis.json().set(entry_key, "$", entry)
                count += 1
        await self._create_index()
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.set(META, f"{self.model}:{self.dimension}")
            pipe.delete(REBUILD)
            await pipe.execute()
        self.model_changed = False
        self.rebuild_required = False
        return {"reindexed": count, "model": self.model, "dimension": self.dimension}
