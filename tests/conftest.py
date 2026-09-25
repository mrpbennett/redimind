import os

import pytest
from redis.asyncio import Redis

from redimind.memory import Memory


class FakeEmbeddings:
    model = "test-embedding-v1"

    def embed(self, text: str) -> list[float]:
        # Persistence and durability have the same meaning for these test vectors.
        text = text.lower()
        return (
            [1.0, 0.0, 0.0, 0.0] if "persist" in text or "durable" in text else [0.0, 1.0, 0.0, 0.0]
        )


@pytest.fixture
async def redis_client():
    url = os.getenv("REDIMIND_TEST_REDIS_URL")
    if not url:
        pytest.skip("Set REDIMIND_TEST_REDIS_URL to a disposable Redis 8 instance")
    client = Redis.from_url(url)
    await client.flushdb()
    try:
        yield client
    finally:
        await client.flushdb()
        await client.aclose()


@pytest.fixture
async def memory(redis_client):
    memory = Memory(redis_client, FakeEmbeddings(), dimension=4)
    await memory.initialize()
    return memory


def lesson(project: str = "project-a", claim: str = "Persist lessons with snapshots") -> dict:
    return {
        "kind": "lesson",
        "claim": claim,
        "project_id": project,
        "source_ref": "https://example.com/project-a/issues/4",
        "conditions": "Redis 8 and backups",
        "outcome": "worked",
        "evidence_status": "verified",
        "tried": "Redis snapshots",
    }
