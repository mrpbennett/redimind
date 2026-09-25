# Local trust mode without MCP tokens

For an owner running agents on the same machine, separate agent and owner bearer tokens add setup work without isolating processes that already share a Unix account. Local mode therefore binds MCP to loopback without a token and exposes no owner-management HTTP routes; the owner CLI connects to Redis using an ignored, user-readable-only local configuration. Local processes with the owner's OS access can still run that CLI, so this mode relies on trust in those processes; token-enforced MCP and owner HTTP management remain available for a future network deployment.

[ADR-0004](0004-trusted-client-approval.md) also permits explicit owner approval through a trusted MCP client's human prompt; local mode still exposes no owner HTTP routes.
