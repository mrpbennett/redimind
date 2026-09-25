import asyncio
import json
import os
import socket
from contextlib import asynccontextmanager

import httpx2
import pytest
import uvicorn
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import ElicitResult

from redimind.memory import Memory
from redimind.server import create_app
from redimind.settings import Settings
from tests.conftest import FakeEmbeddings, lesson


@asynccontextmanager
async def live_server(app):
    """Exercise stateful legacy elicitation over a real HTTP connection."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="on"))
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        for _ in range(200):
            if server.started:
                break
            if task.done():
                await task
            await asyncio.sleep(0.01)
        else:
            raise TimeoutError("MCP server did not start")
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, timeout=5)
        listener.close()


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (ElicitResult(action="accept", content={"approve": True}), "current"),
        (ElicitResult(action="accept", content={"approve": False}), "rejected"),
        (ElicitResult(action="decline"), "rejected"),
        (ElicitResult(action="cancel"), "draft"),
    ],
)
@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_client_prompt_controls_review(redis_client, response, expected, mode):
    settings = Settings(os.environ["REDIMIND_TEST_REDIS_URL"], embedding_dimension=4)
    memory = Memory(redis_client, FakeEmbeddings(), dimension=4)
    app = create_app(settings, redis_client, FakeEmbeddings())
    prompts = []

    async def answer(context, params):
        prompts.append(params.message)
        return response

    async with (
        live_server(app) as url,
        Client(
            url,
            mode=mode,
            elicitation_callback=answer,
        ) as client,
    ):
        proposed = await client.call_tool("memory_propose", {"candidate": lesson()})
        candidate_id = json.loads(proposed.content[0].text)["id"]
        assert json.loads(proposed.content[0].text)["next_tool"] == "memory_review"
        reviewed = await client.call_tool("memory_review", {"id": candidate_id})
        assert not reviewed.is_error, reviewed.content
        assert json.loads(reviewed.content[0].text)["status"] == expected
        assert len(prompts) == 1
        assert "Persist lessons with snapshots" in prompts[0]
        assert lesson()["source_ref"] in prompts[0]
        assert (await memory.get(candidate_id))["status"] == expected
        if expected != "draft":
            entry = await memory.get(candidate_id, history=True)
            assert entry["history"][-1]["actor"] == "owner-via-mcp-client"


async def test_agent_token_with_trusted_client_can_request_approval(redis_client):
    settings = Settings(
        os.environ["REDIMIND_TEST_REDIS_URL"], "agent-key", "owner-key", embedding_dimension=4
    )
    memory = Memory(redis_client, FakeEmbeddings(), dimension=4)
    app = create_app(settings, redis_client, FakeEmbeddings())

    async def approve(context, params):
        return ElicitResult(action="accept", content={"approve": True})

    async with (
        app.router.lifespan_context(app),
        httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            base_url="http://127.0.0.1:8000",
            headers={"Authorization": "Bearer agent-key"},
        ) as http_client,
        Client(
            streamable_http_client("http://127.0.0.1:8000/mcp", http_client=http_client),
            elicitation_callback=approve,
        ) as client,
    ):
        proposed = await client.call_tool("memory_propose", {"candidate": lesson()})
        candidate_id = json.loads(proposed.content[0].text)["id"]
        reviewed = await client.call_tool("memory_review", {"id": candidate_id})
        assert not reviewed.is_error, reviewed.content
        assert (await memory.get(candidate_id))["status"] == "current"


async def test_missing_client_prompt_keeps_saved_candidate_pending(redis_client):
    settings = Settings(os.environ["REDIMIND_TEST_REDIS_URL"], embedding_dimension=4)
    memory = Memory(redis_client, FakeEmbeddings(), dimension=4)
    app = create_app(settings, redis_client, FakeEmbeddings())
    async with (
        app.router.lifespan_context(app),
        httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://127.0.0.1:8000"
        ) as http_client,
        Client(
            streamable_http_client("http://127.0.0.1:8000/mcp", http_client=http_client)
        ) as client,
    ):
        proposed = await client.call_tool("memory_propose", {"candidate": lesson()})
        candidate_id = json.loads(proposed.content[0].text)["id"]
        with pytest.raises(Exception, match="elicitation capability"):
            await client.call_tool("memory_review", {"id": candidate_id})
        assert (await memory.get(candidate_id))["status"] == "draft"


async def test_incomplete_candidate_does_not_prompt(redis_client):
    settings = Settings(os.environ["REDIMIND_TEST_REDIS_URL"], embedding_dimension=4)
    memory = Memory(redis_client, FakeEmbeddings(), dimension=4)
    app = create_app(settings, redis_client, FakeEmbeddings())
    prompts = []

    async def answer(context, params):
        prompts.append(params.message)
        return ElicitResult(action="accept", content={"approve": True})

    async with (
        app.router.lifespan_context(app),
        httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://127.0.0.1:8000"
        ) as http_client,
        Client(
            streamable_http_client("http://127.0.0.1:8000/mcp", http_client=http_client),
            elicitation_callback=answer,
        ) as client,
    ):
        proposed = await client.call_tool(
            "memory_propose", {"candidate": {"kind": "lesson", "claim": "Partial draft"}}
        )
        details = json.loads(proposed.content[0].text)
        assert "next_tool" not in details
        reviewed = await client.call_tool("memory_review", {"id": details["id"]})
        assert reviewed.is_error
        assert not prompts
        assert (await memory.get(details["id"]))["status"] == "draft"


async def test_changed_candidate_never_approves_without_reviewing_new_text(redis_client):
    settings = Settings(os.environ["REDIMIND_TEST_REDIS_URL"], embedding_dimension=4)
    memory = Memory(redis_client, FakeEmbeddings(), dimension=4)
    app = create_app(settings, redis_client, FakeEmbeddings())
    prompts = []
    candidate_id = None

    async def answer(context, params):
        prompts.append(params.message)
        if len(prompts) == 1:
            await memory.propose({"claim": "New claim after question"}, "local-agent", candidate_id)
            return ElicitResult(action="accept", content={"approve": True})
        return ElicitResult(action="cancel")

    async with (
        app.router.lifespan_context(app),
        httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://127.0.0.1:8000"
        ) as http_client,
        Client(
            streamable_http_client("http://127.0.0.1:8000/mcp", http_client=http_client),
            elicitation_callback=answer,
        ) as client,
    ):
        proposed = await client.call_tool("memory_propose", {"candidate": lesson()})
        candidate_id = json.loads(proposed.content[0].text)["id"]
        result = await client.call_tool("memory_review", {"id": candidate_id})
        assert result.is_error or json.loads(result.content[0].text)["status"] == "draft"
        saved = await memory.get(candidate_id)
        assert saved["status"] == "draft"
        assert saved["revision"] == 2
        assert saved["fields"]["claim"] == "New claim after question"
