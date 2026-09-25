# Second-brain design documentation

- [x] Record the durable shared-memory architecture and its trade-offs in an ADR.
- [x] Write a v1 specification for the MCP workflow, retrieval, roles, lifecycle, deployment, and acceptance scenarios.
- [x] Check the documents against the agreed design and review the working tree.

## Review

Confirmed the ADR and v1 spec match the approved local-service/remote-Redis architecture, owner-reviewed memory lifecycle, hybrid retrieval, and cross-project acceptance scenario. Checked the new Markdown files for whitespace errors; this documentation-only change has no application tests to run.

## Build v1

- [x] Set up Python packaging, configuration, and local development environment.
- [x] Implement Redis-backed candidates, review history, lifecycle, indexed hybrid search, and replaceable local embeddings.
- [x] Expose authenticated MCP tools and owner CLI; document deployment and restore.
- [x] Verify end-to-end lifecycle, access boundaries, fallback and failure cases against Redis and MCP.

### Build review

Nine tests passed against a disposable Redis 8 instance in database 0, including MCP over HTTP, owner-only approval, cross-project retrieval, revision conflicts, deletion, contradictory and retired guidance, and keyword fallback. Ruff lint and format checks passed; the default local embedding model produced a 384-dimensional vector; the Docker image built and its CLI ran. The remote Proxmox Redis endpoint and its backup/restore procedure have not been provided or exercised; complete that restore check before treating the remote deployment as durable.

## Repair installed package import

- [x] Restore the `src/redimind/` package layout expected by the entry points and build configuration.
- [x] Re-sync the editable install and verify the server imports successfully.
- [x] Run the relevant tests and lint checks; record the packaging lesson.

### Repair review

Confirmed the originally failing `uv run redimind-server` now reaches application startup against disposable Redis 8. The wheel contains `redimind/` at its root, the module import and CLI work, nine integration tests pass, and Ruff lint/format checks pass. Documented the packaging check in `docs/tasks/lessons.md`.

## Simplify local setup

- [x] Record the localhost trust boundary; add an ignored, mode-0600 local Redis configuration and one-time setup command.
- [x] Run MCP without tokens only on loopback, with no owner HTTP routes; use the owner CLI to access Redis directly. Preserve authenticated network mode.
- [x] Update instructions and verify both modes, CLI review/approval, loopback guard, and end-to-end MCP against disposable Redis.

### Local setup review

Ten tests passed against disposable Redis 8 on port 16380 (separate from the user's SSH tunnel), including authenticated mode, token-free MCP proposal, local owner CLI review/approval, keyword fallback, and loopback-only validation. Ruff lint/format and the Docker build passed. Ran `redimind setup` against the user's existing tunnel; `redimind.local.toml` is ignored, mode 0600, and selects local mode on 127.0.0.1. Starting the server on an alternate port reached application startup against that Redis. Restart an already-running server for the new mode to take effect.

## Switch from SSH tunnel to direct Redis

- [x] Add a password prompt to `redimind setup` and show the selected Redis host (never the password) when startup cannot connect.
- [ ] Update the local setup instructions to replace the stale tunnel configuration in one command. (Superseded by Docker setup.)
- [ ] Verify password encoding, failed-setup behavior, and both MCP access modes against disposable Redis; run lint/format checks. (Superseded by Docker setup.)

### Direct Redis review

The direct-LXC connection is no longer the chosen local path. Retain the general interactive password support for optional external Redis deployments; complete verification as part of Docker onboarding.

## Docker-first local Redis

- [x] Add Redis 8 Docker Compose service with persistent volume, health check, and localhost-only published port.
- [x] Make `redimind setup` default to its local Docker Redis address; update README, operations guide, and architecture docs.
- [x] Verify Compose startup, persistent storage, CLI setup, MCP startup, automated tests on an isolated disposable Redis, and formatting/linting.

### Docker review

Docker Compose Redis 8 started healthy on localhost, retained a test value across a restart, and kept its named volume. `redimind setup --force` changed the ignored local config to `127.0.0.1:6379`; MCP startup created `redimind:entries` in this Docker Redis and `redimind review` connected without tokens. Fourteen tests passed against a separate disposable Redis on port 16380, which was then stopped. Ruff lint/format and Compose validation passed. The persistent Compose service remains running; the external LXC was not modified or migrated.

## Fix agent-proposed field names

- [x] Reproduce the agent's proposed lesson with descriptive field names through MCP.
- [x] Expose the candidate's typed schema and accept descriptive aliases while preserving server-owned attribution.
- [x] Verify proposals, review/approval, prior tool behavior, and lint on disposable Redis; record the lesson.

### Proposal review

The user's descriptive field names reproduced `UnexpectedToolError` before the fix and successfully created a candidate through the real MCP HTTP client afterward. The resulting review entry has canonical `source_ref`, is attributed to the server's `local-agent` instead of the client-supplied `proposer`, and can be approved and searched. All 14 tests passed against disposable Redis on port 16380; Ruff lint/format checks passed. Restart a running MCP server to load the new tool schema.

## Deepen memory modules

- [x] Establish a baseline on disposable Redis, preserving existing owner-review and local/token access behavior.
- [x] Use one revision-guarded Redis commit implementation for candidate edits, approval, evidence linking, superseding, status changes, and deletion with evidence cleanup; test stale and linked changes through `Memory`.
- [x] Concentrate index creation, embedding compatibility, keyword fallback, and reindex recovery in one internal module; verify interrupted rebuild can be resumed through `Memory.reindex`.
- [x] Concentrate cross-project keyword/vector ranking, filtering, duplicate lookup, and result explanation in one retrieval module; test through `Memory.search` and `Memory.propose`.
- [x] Run the complete Redis/MCP suite on a disposable instance, Ruff, Docker build/startup checks, and review the working-tree diff.

### Deepening review

All three deepening changes are implemented behind the existing `Memory` interface. A single revision-guarded commit now owns approval, evidence linking, superseding, status changes, and deletion. The index module records interrupted rebuilds, blocks indexed operations until reindex completes, and refreshes model state when another owner process reindexes. The retrieval module keeps keyword/vector matching, project-first ranking, duplicate lookup, and evidence presentation together. Sixteen tests passed against disposable Redis on port 16380, including interruption/recovery and running-process refresh; Ruff lint/format, Docker image build, and a separate-port MCP startup against the persistent Compose Redis passed. Existing persistent Redis data was not flushed or migrated.

## One-command owner review

- [x] Capture the current draft review behavior and keep owner approval out of MCP.
- [x] Make `redimind review` interactive for a terminal: show one candidate's evidence and missing fields, then approve/reject/skip/quit using the displayed revision. Keep piped/`--json` output and explicit commands working in local and token modes.
- [x] Update the README showcase to use one review command; verify approve, reject, incomplete drafts, stale revisions, JSON compatibility, and both access modes using disposable Redis.

### Review UX outcome

`uv run redimind review` now presents each candidate's source, conditions, outcome, and missing fields, then accepts Approve, Reject, Skip, or Quit without copying IDs or revisions. If an agent edits a draft mid-review, the owner sees its updated content before another decision. Existing explicit commands and piped/`--json` output remain available; local and token modes share the same owner interaction while approval stays outside MCP. Nineteen tests passed against disposable Redis on port 16380, Ruff lint/format and the Docker image build passed, and `redimind review --help` exposes the JSON option. The persistent Compose container was already stopped during final verification and was left untouched.

## MCP-client human approval

- [x] Record the trusted-client approval decision and the fallback when elicitation is unavailable, preserving existing owner CLI behavior.
- [x] Add an agent-callable MCP review tool that asks the client to show the complete candidate, checks the reviewed revision, and applies explicit approve/reject; cancellation or unsupported elicitation leaves the candidate pending.
- [x] Test approval, rejection, cancellation, unsupported clients, changed drafts, and local/token connections using disposable Redis.
- [x] Update agent instructions and local/remote operating docs; run lint, full tests, and Docker build.

### Client approval review

`memory_propose` saves a draft and directs the agent to call `memory_review`; the latter elicits a flat Approve/Reject decision in trusted MCP clients, checks the reviewed revision, and records approval or rejection. Cancellation and unsupported elicitation leave the saved draft pending; incomplete drafts cannot prompt. Enabled stateful legacy HTTP so the prompt works with the classic MCP handshake used by OpenCode, and verified both legacy and modern protocol flows over real or in-memory HTTP. All 31 tests passed against disposable Redis on port 16380, including agent-token mode and changed-draft protection. Ruff lint/format and a Docker image build/import passed. The owner CLI remains available as a fallback; actual Claude Code/OpenCode prompt rendering requires testing in those clients.

## Nginx for a private-LAN VM

- [x] Add an Nginx HTTPS reverse-proxy example for the VM's LAN IP, forwarding MCP streams and preserving the Host header.
- [x] Add an OpenSSL server-certificate config with an IP subject alternative name and document a local CA, certificate trust, VM installation, and client verification.
- [x] Link the new guide from the README without replacing the existing Caddy path; validate certificates and Nginx syntax in isolation.

### Nginx review

The Nginx config passes `nginx -t` in an isolated official Nginx container. The documented OpenSSL sequence created a private CA and IP-SAN server certificate, and `openssl verify -verify_ip` passed for the example VM address. Temporary validation keys/certificates were removed. The README links both config templates and the deployment guide; the user's VM has not been modified.
