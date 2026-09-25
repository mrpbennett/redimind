# Curated memory in remote Redis behind an MCP service

Agents need transferable knowledge from past projects without treating old transcripts or project decisions as universal instructions. We will store owner-approved, attributable memory entries durably in a remote Redis instance and expose them through an authenticated MCP service, rather than giving agents direct Redis access or making unreviewed project records the shared index. Redis provides the shared search and persistence boundary, while source project artifacts remain the evidence for each claim; this choice requires persistence, backups, and explicit handling of stale or contradictory entries. The service starts locally and connects to the remote Redis instance, so it can later move to a VM container or Kubernetes without changing the clients' memory tools.

For the later localhost trust-mode exception, see [ADR-0002](0002-local-trust-mode.md).
The remote-LXC default was replaced by [ADR-0003](0003-docker-redis-for-local-setup.md).
