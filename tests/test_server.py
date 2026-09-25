import json
import os
from argparse import Namespace

import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from redimind import cli
from redimind import settings as config
from redimind.server import create_app
from redimind.settings import Settings
from tests.conftest import FakeEmbeddings, lesson


@pytest.fixture
def settings():
    return Settings(
        redis_url="redis://127.0.0.1:16380/0",
        agent_token="agent-key",
        owner_token="owner-key",
        agent_tokens={"builder": "agent-key"},
        embedding_dimension=4,
    )


async def test_http_mcp_and_owner_workflow(redis_client, settings):
    app = create_app(settings, redis_client, FakeEmbeddings())
    transport = httpx2.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=transport,
            base_url="http://127.0.0.1:8000",
            headers={"Authorization": "Bearer agent-key"},
        ) as agent:
            assert (await agent.get("/admin/candidates")).status_code == 403
            assert (await agent.get("/health")).status_code == 200
            async with Client(
                streamable_http_client("http://127.0.0.1:8000/mcp", http_client=agent)
            ) as client:
                tools = await client.list_tools()
                assert {tool.name for tool in tools.tools} == {
                    "memory_search",
                    "memory_get",
                    "memory_propose",
                    "memory_review",
                }
                proposed = await client.call_tool("memory_propose", {"candidate": lesson()})
                assert not proposed.is_error, proposed.content
                candidate_id = json.loads(proposed.content[0].text)["id"]
                async with httpx2.AsyncClient(
                    transport=transport,
                    base_url="http://127.0.0.1:8000",
                    headers={"Authorization": "Bearer owner-key"},
                ) as owner:
                    draft = (await owner.get(f"/admin/entries/{candidate_id}")).json()
                    assert draft["proposer"] == "builder"
                    reviewed = await owner.post(
                        f"/admin/entries/{candidate_id}/approve",
                        json={"revision": draft["revision"]},
                    )
                    assert reviewed.status_code == 200, reviewed.text
                    assert (
                        await owner.post(
                            f"/admin/entries/{candidate_id}/approve",
                            json={"revision": draft["revision"]},
                        )
                    ).status_code == 409
                found = await client.call_tool(
                    "memory_search", {"query": "persist snapshots", "project_id": "project-b"}
                )
                assert not found.is_error, found.content
                assert json.loads(found.content[0].text)["id"] == candidate_id
        async with httpx2.AsyncClient(
            transport=transport, base_url="http://127.0.0.1:8000"
        ) as anonymous:
            assert (await anonymous.post("/mcp", json={})).status_code == 401


def test_distinct_credentials_and_remote_host_allowlist():
    with pytest.raises(ValueError, match="distinct"):
        Settings("redis://localhost/0", "same", "same")
    with pytest.raises(ValueError, match="ALLOWED_HOSTS"):
        Settings("redis://localhost/0", "agent", "owner", host="0.0.0.0")
    with pytest.raises(ValueError, match="database 0"):
        Settings("redis://localhost/15", "agent", "owner")
    with pytest.raises(ValueError, match="loopback"):
        Settings(
            "redis://localhost/0",
            auth_mode="local",
            host="0.0.0.0",
            allowed_hosts=("memory.example.com",),
        )


async def test_token_free_setup_mcp_and_owner_cli(redis_client, monkeypatch, tmp_path):
    config_file = tmp_path / "redimind.local.toml"
    monkeypatch.setattr(cli, "CONFIG_FILE", str(config_file))
    monkeypatch.setattr(config, "CONFIG_FILE", str(config_file))
    monkeypatch.setenv("REDIMIND_EMBEDDING_DIMENSION", "4")
    for name in (
        "REDIMIND_REDIS_URL",
        "REDIMIND_AUTH_MODE",
        "REDIMIND_AGENT_TOKEN",
        "REDIMIND_OWNER_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)
    url = os.environ["REDIMIND_TEST_REDIS_URL"]
    cli.setup(url, force=False)
    assert config_file.stat().st_mode & 0o077 == 0
    settings = Settings.from_env()
    assert settings.auth_mode == "local"
    monkeypatch.setattr(cli, "LocalEmbeddings", lambda model, cache: FakeEmbeddings())
    app = create_app(settings, redis_client, FakeEmbeddings())
    transport = httpx2.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx2.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as agent,
    ):
        assert (await agent.get("/admin/candidates")).status_code == 404
        async with Client(
            streamable_http_client("http://127.0.0.1:8000/mcp", http_client=agent)
        ) as client:
            tools = await client.list_tools()
            proposal_schema = next(
                tool.input_schema for tool in tools.tools if tool.name == "memory_propose"
            )
            assert "source_ref" in json.dumps(proposal_schema)
            assert "project_id" in json.dumps(proposal_schema)
            draft = await client.call_tool(
                "memory_propose",
                {
                    "candidate": {
                        "type": "lesson",
                        "claim": "Persist lessons with snapshots",
                        "source_project": "project-a",
                        "source_reference": "docs/operations.md:22",
                        "applicability_conditions": "Redis 8 with backups",
                        "what_was_tried": "Redis snapshots",
                        "outcome": "worked",
                        "evidence_status": "verified",
                        "proposer": "client-claimed-identity",
                    }
                },
            )
            assert not draft.is_error, draft.content
            entry_id = json.loads(draft.content[0].text)["id"]
            assert (await redis_client.json().get("redimind:entry:" + entry_id))[
                "status"
            ] == "draft"
            pending = await cli.local_command(Namespace(command="review"), settings)
            assert pending
            assert pending[0]["id"] == entry_id
            assert pending[0]["proposer"] == "local-agent"
            assert pending[0]["fields"]["source_ref"] == "docs/operations.md:22"
            approved = await cli.local_command(
                Namespace(command="approve", id=entry_id, revision=1, target=None), settings
            )
            assert approved["status"] == "current"
            result = await client.call_tool(
                "memory_search", {"query": "persist", "project_id": "project-b"}
            )
            assert not result.is_error
            assert json.loads(result.content[0].text)["id"] == entry_id
