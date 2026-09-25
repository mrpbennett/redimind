# Redimind v1: shared agent memory

## Goal and proof

An agent planning work in project B can discover an approved, relevant lesson from project A, see where it came from and when it applies, and cite it when choosing an approach. A conflicting lesson must remain visible under its own conditions; retired guidance must not appear as a current recommendation. Memory is advisory evidence, never an instruction that overrides the user or the current project's rules.

## Boundary

- A Python MCP service runs locally over localhost HTTP and connects by default to Redis 8 in Docker Compose, published on localhost with a persistent named volume. MCP clients do not connect to Redis directly. The MCP service can later be deployed in a container on a VM or in Kubernetes without changing MCP tool semantics.
- For the first single-user localhost setup, MCP clients need no bearer token; the owner CLI connects directly to Redis and owner HTTP routes are absent. This mode is trusted by the OS user, including agents running as that user. For network hosting, enable separate agent/owner bearer tokens and authenticated owner HTTP management as described below.
- Redis is the authoritative store of curated memory and its review history; source project artifacts are the evidence for claims. Persist entries, revisions, and review actions, not raw conversations or logs.
- The Redis deployment must support indexed keyword search and vector similarity search. Embeddings are generated within infrastructure controlled by the owner through a replaceable local provider; persist the embedding model/version so a model change can trigger reindexing. Keyword search remains usable if embedding generation is temporarily unavailable.
- The first release uses one shared knowledge space for agents controlled by the owner. All approved entries may be read by those agents; only the owner approves, retires, supersedes, or deletes them. No web UI or automatic capture is required.

## Entry model

Each approved memory entry has a stable ID, type (`lesson` or `decision`), concise single claim, stable source-project ID (and repository URL when available), specific durable source reference, applicability conditions, proposer identity, creation time, outcome (`worked`, `failed`, `mixed`, or `unknown`), evidence status (`verified`, `observed`, or `unverified`), and revision history. A lesson additionally records what was tried; a decision records the choice and rationale. Cite the entry ID and source reference in results, not merely the text of the claim.

A candidate has an ID and proposer but may lack other fields while in draft; approval requires every field applicable to its type, including a durable source reference. Missing tests do not prevent approval if the evidence status says `unverified`; they must not be portrayed as verified. Entries contain concise reviewed knowledge, not credentials, personal information, or raw conversation logs. A source reference to an issue, commit, document, or saved project note is acceptable; a transient agent assertion is not a durable source.

Lifecycle:

1. An agent proposes a candidate, normally after completed work. An incomplete proposal stays in draft for completion, never in search results for current guidance.
2. The owner reviews it and may reject it, approve it as a new entry, supersede an existing entry, or link it as additional evidence for an existing entry. Likely duplicates are shown during review, but are not merged automatically.
3. An approved entry is current until retired, superseded, or deleted. Contradictory lessons can both remain current if their applicability conditions differ.
4. Retiring or superseding removes an entry from current recommendations while retaining its revisions and relationships for explicit historical inspection. Deleting removes the entry and associated indexes from the live store when information must be removed; backup retention and restoration must be documented so deletion is not misrepresented as immediate removal from every backup.

Every owner mutation records actor, time, action, and previous revision; concurrent owner changes must fail with a revision conflict rather than silently overwrite one another. An agent cannot promote its own candidate through tool arguments; owner approval comes from the CLI or a trusted MCP client's human elicitation.

## Interfaces and permissions

The MCP service exposes at least:

- `memory_search(query, project_id, scope?)`: default scope returns current approved decisions from the current project and current approved lessons, prioritising the current project before cross-project lessons. Cross-project decisions are opt-in. Return ranked, concise results with entry ID, type, claim, source reference, source project, applicability conditions, outcome/evidence status where relevant, and a match explanation. The default excludes candidates and retired entries.
- `memory_get(id, include_history?)`: fetch a specific entry and, when explicitly requested, its source metadata and revision/history links; an agent can explicitly inspect candidates and retired entries without receiving them as default recommendations.
- `memory_propose(candidate)`: create or complete a draft candidate and return its ID, missing approval fields, and possible duplicate IDs. It cannot approve, edit current entries, or claim a write succeeded if Redis rejected it.
- `memory_review(id)`: for a complete, saved candidate, ask the trusted MCP client to present its claim, source, conditions, outcome and evidence to the human. An explicit approval promotes that exact reviewed revision; a rejection records `rejected`; cancellation or lack of elicitation support keeps the draft pending for later review. The agent cannot provide the approval as a tool argument.

A separate owner CLI lists/reviews candidates and can approve, link evidence, supersede, retire, and delete. In local mode it connects to Redis directly using the same ignored, owner-readable config as the service; local agent clients need no token, while any process with the owner's OS access may run the CLI. In token mode the CLI uses authenticated HTTP management operations and agent/owner credentials have distinct permissions. Use TLS and authenticated network access whenever the MCP service moves off-machine. Never check Redis credentials into the repo.

In a terminal, `redimind review` shows each draft and prompts the owner to approve, reject, skip, or quit without copying an ID or revision. Optimistic revision checks still apply; if a draft changes during review, show the updated draft before accepting a decision. Keep `redimind review --json` and piped output machine-readable.

The agent should follow a complete `memory_propose` with `memory_review` while the user is present. A legacy MCP connection needs stateful HTTP handling for client elicitation; a modern connection carries the prompt in a retried request. If the connected client cannot present the prompt, report that the saved candidate is pending instead of claiming approval. In remote mode, this path trusts the connected MCP client to obtain the human response (ADR-0004); the CLI remains an owner-only alternative.

## Retrieval and agent behaviour

Combine indexed keyword matching with semantic similarity when embeddings are available; preserve keyword-only results if the embedding provider fails. Search should rank by relevance and project scope, disclose outcome/evidence status rather than inventing a numerical certainty, and allow conflicting lessons to appear together. Never treat a past project's decision as a transferable rule by default.

Agent instructions in this repo should call for memory search during planning and before a significant implementation choice, and a candidate proposal after completed work when a reusable lesson exists. A result is evidence to assess against the present task, not an instruction to execute. If search returns nothing, continue normally. If the service or Redis is unavailable, continue the project task and say memory was unavailable; failed proposal, approval, or other writes must be reported as failures.

## Operation and acceptance

- Configure Redis persistence and scheduled backups; document how to restore curated entries, revisions, and search indexes. Exercise a restore before calling the service durable. Record write failures visibly and never acknowledge a write before it is persisted.
- Configuration covers the Docker Redis address (or an external Redis address/credentials), service bind address, local or token-based access mode, embedding provider/model, and backup/restore procedure. Agent and owner tokens are required only in network mode. No project-specific Redis secret belongs in the spec or tracked repository files.
- From an agent client, propose a sourced lesson in project A; as owner, review and approve it; from an agent planning project B, search and cite the lesson with its conditions and source.
- Approve a contrary lesson under different conditions; both remain retrievable. Retire one and confirm it disappears from default search but remains visible through explicit history. Propose a duplicate and link its evidence rather than creating a second claim.
- Submit an incomplete proposal and confirm it cannot be approved. Verify that agent credentials cannot invoke owner actions, that a stale owner revision cannot overwrite a newer one, and that a Redis or embedding outage behaves as specified.
