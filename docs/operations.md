# Operating Redimind

## Local Redis with Docker Compose

Run `docker compose up -d --wait` from the repo root. Compose starts Redis 8 with
Search and JSON in database 0, publishes port 6379 on **localhost only**, and keeps
data in its `redis-data` named volume. Append-only persistence is enabled. Verify
with `docker compose ps` and `docker compose exec redis redis-cli -n 0 PING`.
The container restarts after a Docker restart; `docker compose down` stops it but
retains the volume. `docker compose down -v` **deletes stored memory**.

Run `uv run redimind setup --force` once to replace an older LXC/tunnel connection
with `redis://127.0.0.1:6379/0`; new checkouts can omit `--force`. The setup command
checks PING, Search, and JSON before saving the URL. Use `uv run redimind config` to
see the selected target without exposing credentials. Clear old
`REDIMIND_REDIS_URL`, `REDIMIND_AUTH_MODE`, and `REDIMIND_HOST` overrides before
starting the MCP server.

The named volume protects data across container restarts, but is not an off-host
backup. Before relying on memories long term, back up Redis's persistence data
to another host and verify a restore into a separate Redis instance. Do not use the
automated test suite against the Compose volume: tests flush their configured DB 0.

## External Redis (optional)

Use Redis 8 with Search and JSON support; indexed search requires **database 0**. Give
the service a Redis account with access to its `redimind:*` keys and `FT.*`/`JSON.*`
commands, and supply a `redis://` or `rediss://` URL through `REDIMIND_REDIS_URL`.
Protect traffic to a remote Redis instance (prefer `rediss://`, or a private encrypted
network); only the service and local owner CLI hold Redis credentials. The MCP service
does not accept connections to Redis on behalf of agents.

Enable Redis persistence (AOF with `appendfsync everysec` and scheduled RDB snapshots
are a reasonable starting point) and keep scheduled off-host backups of the persistence
files. Do not copy a live AOF directory as if it were a consistent backup: use Redis's
snapshot/backup procedure for your deployment. Record your backup frequency and expiry,
including how long a deleted entry could remain in a backup. Verify a restore into a
**separate** Redis 8 instance: start Redimind against that instance and confirm a lesson,
its review history, and its search results are present. A restore test is the durability
gate; the service cannot configure or verify backups on an external Redis host for you.

## Configuration

For local use with Compose, `redimind setup` defaults to `redis://127.0.0.1:6379/0`.
For another Redis address, pass `--redis-url URL`; add `--ask-password` to be prompted
for its password instead of including it in shell history. Setup writes
`redimind.local.toml` with owner-only file permissions. The file is ignored
by Git and may contain Redis credentials. `redimind-server` and the owner CLI read it
automatically when run from the repository root. `REDIMIND_REDIS_URL` overrides the
saved connection; rerun setup with `--force` to change it. Token-free local mode always
binds MCP to `127.0.0.1` or `::1` and offers no owner HTTP routes. Processes with the
owner's OS access can still run the owner CLI directly against Redis.
`redimind config` reports the selected Redis host, port, and configuration source
without showing the Redis password. If setup cannot connect, the previous saved
configuration stays in place until a successful retry.

For a separately hosted service, set `REDIMIND_AUTH_MODE=tokens`,
`REDIMIND_REDIS_URL`, `REDIMIND_AGENT_TOKEN`, and `REDIMIND_OWNER_TOKEN` to distinct
credentials. For per-agent attribution set
`REDIMIND_AGENT_TOKENS='{"agent-name":"a-distinct-token"}'`; the singular agent
token remains available as a default. `REDIMIND_HOST` defaults to `127.0.0.1`,
`REDIMIND_PORT` to `8000`, and `REDIMIND_URL` controls the remote owner CLI's service URL.

`REDIMIND_EMBEDDING_MODEL` defaults to `sentence-transformers/all-MiniLM-L6-v2`,
`REDIMIND_EMBEDDING_DIMENSION` to 384, and `REDIMIND_EMBEDDING_CACHE` optionally
controls the local model cache. The model runs where the service runs, not on Redis.
If the provider is unavailable, approvals are still indexed for keyword search.
Changing the model pauses approvals until the owner runs `redimind reindex`; run this
during maintenance, after taking a backup, since rebuilding the index is not atomic.
Keyword retrieval remains available until the rebuild starts.

Local MCP endpoint: `http://127.0.0.1:8000/mcp`, without an auth header in local mode
or with an agent bearer token in token mode. After `memory_propose` saves a complete
candidate, the agent calls `memory_review` to ask the connected trusted MCP client to
show the human an Approve/Reject prompt. Approving promotes the reviewed revision;
rejecting records a rejection; cancelling leaves it pending. The client must support
MCP elicitation. If it cannot prompt, the candidate stays pending for the CLI.
Run `redimind review` in a terminal to decide pending candidates with Approve, Reject,
Skip, or Quit; an updated draft must be reviewed again.
`redimind review --json` (or piping the command) keeps the previous JSON output.
Explicit commands remain available for scripting: `show <id>`,
`approve <id> --revision <n>`, `link <id> <target> --revision <n>`,
`supersede <id> <target> --revision <n>`, `retire`, `reject`, `delete`, `reindex`.
In local mode, the owner CLI connects to Redis directly; in token mode it contacts
authenticated HTTP management routes, which agents cannot call using agent credentials.
The `memory_review` elicitation path instead trusts the connected MCP client to
present the question to a person; give remote agent credentials only to clients you
trust to do so. Headless clients without elicitation cannot approve via MCP.

## Moving the service

The image in `Dockerfile` runs the same MCP service as the local command. Set
`REDIMIND_AUTH_MODE=tokens` and `REDIMIND_HOST=0.0.0.0` only behind a TLS-terminating reverse proxy, with a specific
`REDIMIND_ALLOWED_HOSTS='memory.example.com,memory.example.com:*'` and, for
browser-based clients, `REDIMIND_ALLOWED_ORIGINS='https://client.example.com'`.
The MCP transport validates these hosts/origins; keep the public service behind TLS
and pass trusted proxy headers to uvicorn when TLS terminates upstream. Make the model
cache persistent or preload its model. Kubernetes and VM-hosted Docker use the same
environment variables and `/mcp` endpoint. The HTTP transport keeps a legacy
connection stateful so Claude Code and OpenCode can render MCP elicitation. Run one
MCP worker or route legacy sessions to the same worker; an in-flight modern approval
retry also needs a stable request-state key across replicas or restarts.

## Failure behaviour

An unavailable Redis instance makes the service unavailable; agent project work may
continue without memory. An unavailable embedding provider permits keyword-only search
and approval. Writes that Redis rejects are errors, not accepted candidates. A rebuild
marker blocks indexed retrieval, candidate proposals/review, and approval after an
interrupted reindex, even after a restart; rerun `redimind reindex` to complete it. Only
approved, current entries appear in normal results; history and drafts require
explicit inspection by ID. A deletion removes the live JSON entry and its search index
reference; retained backups may still contain it until they expire.
