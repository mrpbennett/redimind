# Docker Redis is the default local store

Keeping a remote LXC and SSH tunnel available made first-run testing depend on infrastructure outside this repo. Use a Redis 8 Docker Compose service with a persistent named volume and a host port bound to localhost as the default local store; the MCP service and owner CLI can keep running locally without tokens or an SSH tunnel. Redis remains replaceable by an authenticated remote deployment later, but the reproducible local path is the starting point for contributors.
