<h1 align="center">
    <img src="./assets/logo.png" width="350" alt="Logo"/>
    <br/>
    <sub>RediMind</sub>
</h1>j

Owner-reviewed, Redis-backed memory for coding agents. Docker Compose runs the local
Redis 8 store; a local MCP service lets agents search and propose while the owner
approves or retires entries.
See [the v1 spec](docs/specs/second-brain-v1.md) for the contract and
[the operations guide](docs/operations.md) for Redis, backups, and deployment.

## Start locally

Requires Python 3.12–3.13, `uv`, and Docker Compose. Run once from this repo:

```sh
uv sync
docker compose up -d --wait
unset REDIMIND_REDIS_URL REDIMIND_AUTH_MODE REDIMIND_HOST
uv run redimind setup --force
uv run redimind config
uv run redimind-server
```

Redis is published only to `127.0.0.1:6379` and saves data in the `redis-data` Docker
volume. `--force` replaces a previously saved LXC or SSH-tunnel connection. No Redis
password, SSH tunnel, or MCP token is needed for this local-only setup. `redimind config`
should show Redis at `127.0.0.1:6379` in `local` mode. For subsequent sessions, Docker
restarts Redis automatically; start the MCP service with `uv run redimind-server`.

Restart any server already listening on port 8000 after switching modes. If a prior
shell set `REDIMIND_AUTH_MODE=tokens` or a direct `REDIMIND_REDIS_URL`, unset those
variables before starting to use the saved local configuration. In local mode,
`curl -i http://127.0.0.1:8000/admin/candidates` returns 404; a 401 means the
process on that port is still running in token mode.

Setup checks Redis connectivity, Search, and JSON, then saves the URL in ignored,
mode-0600 `redimind.local.toml`. This localhost-only mode needs no MCP or owner tokens:
point an MCP client at `http://127.0.0.1:8000/mcp` without headers. On first use, the local
embedding model is downloaded to the owner-controlled model cache; if it is unavailable,
keyword search still works.

## Approve or reject in your agent

1. The agent calls `memory_propose`, saving a draft. A complete draft returns its ID
   and `next_tool: memory_review`.
2. The agent calls `memory_review` with that ID. A trusted interactive Claude Code or
   OpenCode client shows the claim, source, conditions, outcome, and evidence.
3. Choose **Approve** to make the entry `current`, or **Reject** to mark it `rejected`.
   Cancelling leaves the draft pending. The agent cannot supply your decision as a
   tool argument, and a changed draft must be shown again before approval.

This prompt happens in the connected client even when Redimind runs on another host:
the owner does not run `uv` on their machine. Connect that client to the hosted HTTPS
`/mcp` endpoint with an agent bearer token. On the host, run Redimind in token mode
(`REDIMIND_AUTH_MODE=tokens`); see [operations](docs/operations.md) for the other
settings and legacy-client routing. Only use this approval path with clients you trust
to present the human prompt.

If the client cannot show an MCP elicitation prompt, the draft stays pending. As a
fallback, run `uv run redimind review` in a terminal to choose **Approve**, **Reject**,
**Skip**, or **Quit**. It handles the ID and revision for you; incomplete drafts cannot
be approved. The local owner CLI uses the saved Redis connection directly, so the MCP
HTTP endpoint exposes no owner-management routes in local mode. For scripts, use
`uv run redimind review --json` to retain machine-readable output. Explicit
`show`, `approve`, `link`, `supersede`, `retire`, `delete`, and `reindex` commands remain
available via `redimind --help`.

## Showcase: reuse a lesson across projects

With the MCP client connected, ask your agent:

> Use Redimind's `memory_propose` tool to submit the candidate below. If it returns
> `next_tool: memory_review`, call `memory_review` with that ID to show me the
> Approve/Reject prompt. Report whether the entry became `current`, was `rejected`,
> or remains pending. Do not claim approval merely because the draft was saved.

```json
{
  "kind": "lesson",
  "claim": "Run Redis integration tests against a disposable instance when tests use FLUSHDB, so persistent memories are not erased.",
  "project_id": "redimind",
  "source_ref": "tests/conftest.py:20-31",
  "conditions": "Applies when test fixtures call FLUSHDB on Redis database 0.",
  "tried": "Ran the integration suite against a separate disposable Redis container.",
  "outcome": "worked",
  "evidence_status": "verified"
}
```

Approve the candidate in your agent client's prompt. If the client cannot prompt you,
use the owner CLI as a fallback from a machine configured for owner review:

```sh
uv run redimind review
```

When the lesson appears in the CLI, press `a`; no ID or revision is needed.

Then ask your agent:

> Use `memory_search` with `project_id="showcase-app"` and query
> `"Redis integration tests FLUSHDB disposable instance"`. Explain the lesson,
> when it applies, and cite its `source_ref`.

The result should appear as a **cross-project lesson** with its source reference,
conditions, and verified outcome. Before approval, the candidate is only a draft
and will not appear in normal search results.

## Inspect a memory in Redis

Replace `<entry-id>` with the ID returned by `memory_propose` or `redimind review`:

```sh
docker compose exec redis redis-cli -n 0 JSON.GET 'redimind:entry:<entry-id>'
docker compose exec redis redis-cli -n 0 JSON.GET 'redimind:entry:<entry-id>' '$.claim'
docker compose exec redis redis-cli -n 0 JSON.GET 'redimind:entry:<entry-id>' '$.fields'
```

The first command returns the full document, including its numeric search embedding.
Use `$.claim` for an approved entry's claim, or `$.fields` for an unapproved draft.
These are Redis JSON documents, so plain `GET` returns a `WRONGTYPE` error.

Processes running as your OS user can also run the owner CLI in local mode. For a
network-hosted MCP endpoint, configure `REDIMIND_AGENT_TOKEN`,
`REDIMIND_OWNER_TOKEN`, and `REDIMIND_REDIS_URL` on the host alongside token mode.

Run the integration suite against a **disposable Redis 8 database 0**, never a production
instance (the tests flush their configured database):

```sh
docker run --rm -d --name redimind-test -p 127.0.0.1:16380:6379 redis:8-alpine &&
  REDIMIND_TEST_REDIS_URL=redis://127.0.0.1:16380/0 uv run pytest
docker stop redimind-test
```
