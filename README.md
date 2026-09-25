<h1 align="center">
    <img src="./assets/logo.png" width="350" alt="Logo"/>
    <br/>
    <sub>RediMind</sub>
</h1>

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

## Host Redimind on a VM

`docker compose up` starts **Redis only**. On the VM, start Redis and the MCP process
from a clone of this repo. If it lives only on your Proxmox LAN, use its **VM IP**;
replace `192.168.6.20` below with that address (not the Proxmox host's IP):

```sh
docker compose up -d --wait
uv sync
export REDIMIND_AUTH_MODE=tokens
export REDIMIND_REDIS_URL=redis://127.0.0.1:6379/0
export REDIMIND_AGENT_TOKEN='YOUR_PERSISTENT_AGENT_TOKEN'
export REDIMIND_OWNER_TOKEN='A_DIFFERENT_PERSISTENT_OWNER_TOKEN'
export REDIMIND_ALLOWED_HOSTS='192.168.6.20,192.168.6.20:*'
uv run redimind-server
```

Replace both token placeholders with independently generated secrets; store them
securely and reuse them across restarts. Leave the MCP process
bound to its default `127.0.0.1:8000`; a TLS-terminating reverse proxy on the **same
VM** can forward HTTPS on the LAN address to it. For example, a Caddy site with an
internal certificate (no public DNS needed):

```caddyfile
https://192.168.6.20 {
    tls internal
    reverse_proxy 127.0.0.1:8000
}
```

Copy Caddy's **local CA root certificate** (`pki/authorities/local/root.crt` in its
data directory) from the VM and trust it on your dev machine; trusting it only on the
VM does not make your dev client's TLS connection trusted. See
[Caddy's local HTTPS guide](https://caddyserver.com/docs/automatic-https#local-https).
Prefer Nginx? Follow the [private-LAN Nginx guide](docs/deployment/nginx-private-lan.md)
with ready-to-copy [proxy](deploy/nginx/redimind.conf.example) and
[certificate](deploy/nginx/openssl-server.cnf.example) config examples instead of
the Caddy site above. These templates live in the **cloned repo**, not the VM's stock
`/etc/nginx/` directory. Run the guide from the repo root: it creates
`/etc/nginx/tls/`, generates the certificate, then copies the proxy config into the
VM's existing `/etc/nginx/conf.d/`. Generate the certificate before running `nginx -t`.
Check `curl https://192.168.6.20/health` from the dev machine after trusting the CA;
it should return `{"status":"ok"}`. The dev machine must be able to reach the VM
on port 443; no internet-facing port or public hostname is required.

Keep Redis on its Compose localhost port; expose HTTPS (typically port 443), not
Redis or the MCP process's port 8000. The server must keep running after you close
your VM terminal, for example under a process manager. Use one MCP worker for legacy
client elicitation; see [operations](docs/operations.md) before scaling it out.

On your **dev machine**, set `REDIMIND_AGENT_TOKEN` to the same agent token, then
configure the trusted client with `https://192.168.6.20/mcp`. For Claude Code:

```sh
claude mcp add --scope user --transport http redimind \
  https://192.168.6.20/mcp \
  --header "Authorization: Bearer $REDIMIND_AGENT_TOKEN"
```

For OpenCode 1.x, add this entry to your existing `opencode.json` under `mcp`
(keep its other settings), then restart OpenCode:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "redimind": {
      "type": "remote",
      "url": "https://192.168.6.20/mcp",
      "oauth": false,
      "headers": {
        "Authorization": "Bearer {env:REDIMIND_AGENT_TOKEN}"
      }
    }
  }
}
```

Keep the owner token on the VM for CLI fallback. Once connected,
`memory_propose` → `memory_review` presents the
Approve/Reject prompt **on your dev machine** while Redis stores entries on the VM.

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

Processes running as your OS user can also run the owner CLI in local mode.

Run the integration suite against a **disposable Redis 8 database 0**, never a production
instance (the tests flush their configured database):

```sh
docker run --rm -d --name redimind-test -p 127.0.0.1:16380:6379 redis:8-alpine &&
  REDIMIND_TEST_REDIS_URL=redis://127.0.0.1:16380/0 uv run pytest
docker stop redimind-test
```
