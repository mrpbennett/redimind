import pytest
from redis.exceptions import ConnectionError

from redimind.memory import Candidate, Conflict, Memory, MemoryError
from tests.conftest import FakeEmbeddings, lesson


async def approved(memory, fields=None):
    result = await memory.propose(fields or lesson(), "agent-a")
    return await memory.approve(result["id"], 1, "owner")


async def test_cross_project_lifecycle_and_history(memory):
    draft = await memory.propose({"kind": "lesson", "claim": "Persist snapshots"}, "agent-a")
    assert "source_ref" in draft["missing"]
    with pytest.raises(MemoryError, match="missing"):
        await memory.approve(draft["id"], 1, "owner")
    result = await memory.propose(lesson(), "agent-a", draft["id"])
    assert not result["missing"]
    assert (await memory.get(draft["id"], history=True))["history"][-1]["action"] == "update_draft"
    with pytest.raises(Conflict):
        await memory.approve(draft["id"], 1, "owner")
    entry = await memory.approve(draft["id"], 2, "owner")
    assert entry["embedding_model"] == "test-embedding-v1"
    results = await memory.search("durable guidance", "project-b")
    assert results[0]["id"] == entry["id"]
    assert results[0]["source_ref"] == lesson()["source_ref"]
    assert results[0]["evidence_status"] == "verified"
    failed = lesson(claim="Persist snapshots failed on read-only storage")
    failed.update(outcome="failed", conditions="Read-only Redis volume")
    contradictory = await approved(memory, failed)
    results = await memory.search("persist snapshots", "project-b")
    assert {item["id"] for item in results} == {entry["id"], contradictory["id"]}
    assert {item["outcome"] for item in results} == {"worked", "failed"}
    retired = await memory.change_status(entry["id"], entry["revision"], "owner", "retired")
    assert retired["history"][-1]["action"] == "retired"
    assert entry["id"] not in {item["id"] for item in await memory.search("persist", "project-b")}
    assert (await memory.get(entry["id"], history=True))["status"] == "retired"
    assert "history" not in await memory.get(entry["id"])
    await memory.delete(entry["id"], retired["revision"])
    with pytest.raises(MemoryError, match="not found"):
        await memory.get(entry["id"])


async def test_duplicate_evidence_and_superseding(memory):
    original = await approved(memory)
    duplicate = await memory.propose(lesson(), "agent-a")
    assert original["id"] in duplicate["duplicates"]
    linked = await memory.approve(duplicate["id"], 1, "owner", evidence_for=original["id"])
    assert linked["status"] == "linked"
    original = await memory.get(original["id"], history=True)
    assert original["evidence"][0]["candidate_id"] == duplicate["id"]
    with pytest.raises(Conflict, match="stale"):
        await memory.delete(duplicate["id"], 1)
    assert (await memory.get(original["id"], history=True))["evidence"][0][
        "candidate_id"
    ] == duplicate["id"]
    await memory.delete(duplicate["id"], linked["revision"])
    original = await memory.get(original["id"], history=True)
    assert original["evidence"] == []
    correction = await memory.propose(
        lesson(claim="Persist using AOF for shorter recovery"), "agent-b"
    )
    successor = await memory.approve(correction["id"], 1, "owner", supersedes=original["id"])
    assert successor["supersedes"] == original["id"]
    assert (await memory.get(original["id"]))["status"] == "superseded"
    with pytest.raises(Conflict):
        await memory.change_status(original["id"], 2, "owner", "retired")


async def test_decisions_stay_in_project_by_default(memory):
    decision = lesson()
    decision.update(kind="decision", rationale="AOF meets our recovery target")
    decision.pop("tried")
    entry = await approved(memory, decision)
    assert (await memory.search("persist", "project-b")) == []
    assert (await memory.search("persist", "project-a"))[0]["id"] == entry["id"]
    cross_project = (await memory.search("persist", "project-b", include_decisions=True))[0]
    assert cross_project["id"] == entry["id"]
    assert cross_project["match"] == "cross-project decision (opt-in)"


async def test_relevance_and_project_first_with_repo_url(memory):
    project = "https://example.com/org/current-repo"
    local = await approved(memory, lesson(project=project, claim="Durable memory snapshots"))
    await approved(memory, lesson(claim="Durable memory snapshots from an older project"))
    result = await memory.search("durable snapshots", project)
    assert result[0]["id"] == local["id"]
    assert result[0]["match"] == "current project"
    assert await memory.search("completely unrelated", project) == []


async def test_embedding_failure_preserves_keyword_search(memory, redis_client):
    await approved(memory)

    class BrokenEmbeddings(FakeEmbeddings):
        def embed(self, _text):
            raise RuntimeError("model offline")

    broken = Memory(redis_client, BrokenEmbeddings(), dimension=4)
    await broken.initialize()
    assert (await broken.search("snapshots", "project-b"))[0]["claim"] == lesson()["claim"]
    proposed = await broken.propose(lesson(claim="Snapshots are useful"), "agent-a")
    approved_entry = await broken.approve(proposed["id"], 1, "owner")
    assert "embedding" not in approved_entry
    assert (await broken.search("Snapshots are useful", "project-b"))[0]["id"] == approved_entry[
        "id"
    ]


async def test_reindex_after_provider_change(memory, redis_client):
    entry = await approved(memory)
    updated_model = FakeEmbeddings()
    updated_model.model = "test-embedding-v2"
    changed = Memory(redis_client, updated_model, dimension=4)
    await changed.initialize()
    assert (await changed.search("persist", "project-b"))[0]["id"] == entry["id"]
    proposed = await changed.propose(lesson(), "agent-a")
    with pytest.raises(MemoryError, match="reindex"):
        await changed.approve(proposed["id"], 1, "owner")
    assert (await changed.reindex())["reindexed"] == 1
    assert (await changed.approve(proposed["id"], 1, "owner"))["status"] == "current"
    assert (await changed.get(entry["id"], history=True))["embedding_model"] == "test-embedding-v2"


async def test_interrupted_reindex_requires_resume_before_search(memory, redis_client):
    original = await approved(memory)

    class InterruptingEmbeddings(FakeEmbeddings):
        model = "test-embedding-v2"
        calls = 0

        def embed(self, text):
            self.calls += 1
            if self.calls > 1:
                raise RuntimeError("embedding interrupted")
            return super().embed(text)

    interrupted = Memory(redis_client, InterruptingEmbeddings(), dimension=4)
    await interrupted.initialize()
    assert (await interrupted.search("persist snapshots", "project-b"))[0]["id"] == original["id"]
    with pytest.raises(RuntimeError, match="embedding interrupted"):
        await interrupted.reindex()

    replacement = FakeEmbeddings()
    replacement.model = "test-embedding-v2"
    resumed = Memory(redis_client, replacement, dimension=4)
    await resumed.initialize()
    with pytest.raises(MemoryError, match="reindex"):
        await resumed.search("persist snapshots", "project-b")
    with pytest.raises(MemoryError, match="reindex"):
        await resumed.propose(lesson(claim="Another snapshot observation"), "agent-b")
    with pytest.raises(MemoryError, match="reindex"):
        await resumed.candidates()
    assert (await resumed.reindex())["reindexed"] == 1
    assert (await resumed.search("persist snapshots", "project-b"))[0]["id"] == original["id"]


async def test_index_state_refreshes_after_another_owner_reindexes(memory, redis_client):
    await approved(memory)
    embeddings = FakeEmbeddings()
    embeddings.model = "test-embedding-v2"
    running = Memory(redis_client, embeddings, dimension=4)
    await running.initialize()
    draft = await running.propose(lesson(claim="Persist snapshots after model upgrade"), "agent-a")
    with pytest.raises(MemoryError, match="reindex"):
        await running.approve(draft["id"], 1, "owner")
    owner = Memory(redis_client, embeddings, dimension=4)
    await owner.initialize()
    await owner.reindex()
    assert (await running.approve(draft["id"], 1, "owner"))["status"] == "current"


async def test_invalid_inputs_and_write_outage(memory):
    with pytest.raises(ValueError, match="credential"):
        Candidate(claim="api_key=supersecret")
    with pytest.raises(MemoryError, match="only the proposer"):
        draft = await memory.propose({"claim": "Hello"}, "agent-a")
        await memory.propose({"claim": "Update"}, "agent-b", draft["id"])
    from redis.asyncio import Redis

    failing = Redis(host="127.0.0.1", port=1, socket_connect_timeout=0.1)
    disconnected = Memory(failing, FakeEmbeddings(), dimension=4)
    with pytest.raises(ConnectionError):
        await disconnected.propose(lesson(), "agent-a")
    await failing.aclose()
