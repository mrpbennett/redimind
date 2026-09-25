import builtins
import os
from argparse import Namespace
from types import SimpleNamespace

import httpx

from redimind import cli
from redimind.memory import Memory
from redimind.server import create_app
from redimind.settings import Settings
from tests.conftest import FakeEmbeddings, lesson


def _terminal(monkeypatch, answers):
    monkeypatch.setattr(cli.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    responses = iter(answers)
    monkeypatch.setattr(builtins, "input", lambda prompt: next(responses))


async def test_one_command_approves_rejects_and_skips_incomplete_draft(
    redis_client, monkeypatch, capsys
):
    memory = Memory(redis_client, FakeEmbeddings(), dimension=4)
    await memory.initialize()
    approved = await memory.propose(lesson(), "agent-a")
    rejected = await memory.propose(lesson(claim="Another observation"), "agent-a")
    incomplete = await memory.propose({"kind": "lesson", "claim": "Missing source"}, "agent-a")
    _terminal(monkeypatch, ["a", "r", "a", "s"])
    monkeypatch.setattr(cli, "LocalEmbeddings", lambda model, cache: FakeEmbeddings())
    settings = Settings(os.environ["REDIMIND_TEST_REDIS_URL"], embedding_dimension=4)

    assert await cli.local_command(Namespace(command="review", json=False), settings) is None
    assert (await memory.get(approved["id"]))["status"] == "current"
    assert (await memory.get(rejected["id"]))["status"] == "rejected"
    assert (await memory.get(incomplete["id"]))["status"] == "draft"
    output = capsys.readouterr().out
    assert "Approved" in output and "Rejected" in output
    assert "Complete the missing fields" in output
    assert "embedding" not in output

    assert len(await cli.local_command(Namespace(command="review", json=True), settings)) == 1


async def test_changed_candidate_must_be_read_again_before_approval(
    redis_client, monkeypatch, capsys
):
    memory = Memory(redis_client, FakeEmbeddings(), dimension=4)
    await memory.initialize()
    draft = await memory.propose(lesson(), "agent-a")
    _terminal(monkeypatch, ["a", "s"])
    first = True

    async def apply(choice, entry):
        nonlocal first
        if first:
            first = False
            await memory.propose({"claim": "Updated after display"}, "agent-a", entry["id"])
        await memory.approve(entry["id"], entry["revision"], "owner")

    async def refresh(entry_id):
        return await memory.get(entry_id)

    await cli.review_queue([await memory.get(draft["id"])], apply, refresh)
    current = await memory.get(draft["id"])
    assert current["status"] == "draft"
    assert current["revision"] == 2
    assert "Updated after display" in capsys.readouterr().out


async def test_token_mode_interactive_review_uses_owner_routes(redis_client, monkeypatch):
    settings = Settings(
        os.environ["REDIMIND_TEST_REDIS_URL"],
        agent_token="agent-key",
        owner_token="owner-key",
        embedding_dimension=4,
    )
    memory = Memory(redis_client, FakeEmbeddings(), dimension=4)
    app = create_app(settings, redis_client, FakeEmbeddings())
    _terminal(monkeypatch, ["a"])
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1:8000",
            headers={"Authorization": "Bearer owner-key"},
        ) as client,
    ):
        draft = await memory.propose(lesson(), "agent-a")
        await cli.remote_review(client)
        assert (await memory.get(draft["id"]))["status"] == "current"
