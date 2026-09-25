"""Environment-backed service configuration."""

import json
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlparse

CONFIG_FILE = "redimind.local.toml"


@dataclass(frozen=True)
class Settings:
    redis_url: str
    agent_token: str | None = None
    owner_token: str | None = None
    auth_mode: str | None = None
    host: str = "127.0.0.1"
    port: int = 8000
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_dimension: int = 384
    embedding_cache: str | None = None
    allowed_hosts: tuple[str, ...] = ()
    allowed_origins: tuple[str, ...] = ()
    agent_tokens: dict[str, str] | None = None

    def __post_init__(self) -> None:
        mode = self.auth_mode or (
            "tokens" if self.agent_token or self.owner_token or self.agent_tokens else "local"
        )
        if mode not in {"local", "tokens"}:
            raise ValueError("REDIMIND_AUTH_MODE must be local or tokens")
        object.__setattr__(self, "auth_mode", mode)
        if mode == "tokens":
            if not self.agent_token or not self.owner_token or self.agent_token == self.owner_token:
                raise ValueError("Set distinct nonempty agent and owner tokens")
            tokens = self.agent_tokens or {"agent": self.agent_token}
            if not tokens or any(not name or not token for name, token in tokens.items()):
                raise ValueError("Agent credentials require names and tokens")
            if len(set(tokens.values()) | {self.owner_token}) != len(tokens) + 1:
                raise ValueError("Agent and owner tokens must be distinct")
        parsed = urlparse(self.redis_url)
        if parsed.scheme not in {"redis", "rediss"}:
            raise ValueError("REDIMIND_REDIS_URL must be a redis:// or rediss:// URL")
        if parsed.path not in {"", "/", "/0"} or parse_qs(parsed.query).get("db", ["0"]) != ["0"]:
            raise ValueError("Redis Search requires database 0")
        if mode == "local" and self.host not in {"127.0.0.1", "::1"}:
            raise ValueError(
                "Token-free local mode must bind to a loopback address (127.0.0.1 or ::1)"
            )
        if (
            mode == "tokens"
            and self.host not in {"127.0.0.1", "localhost", "::1"}
            and not self.allowed_hosts
        ):
            raise ValueError("Non-local service requires REDIMIND_ALLOWED_HOSTS")

    @classmethod
    def from_env(cls) -> "Settings":
        config_path = Path(CONFIG_FILE)
        if config_path.exists():
            if config_path.stat().st_mode & 0o077:
                raise ValueError(f"Restrict {CONFIG_FILE} permissions to the owner (chmod 600)")
            local = tomllib.loads(config_path.read_text())
        else:
            local = {}
        redis_url = os.getenv("REDIMIND_REDIS_URL") or local.get("redis_url")
        if not redis_url:
            raise ValueError("Set REDIMIND_REDIS_URL or run redimind setup --redis-url URL")
        return cls(
            redis_url=redis_url,
            agent_token=os.getenv("REDIMIND_AGENT_TOKEN"),
            owner_token=os.getenv("REDIMIND_OWNER_TOKEN"),
            auth_mode=os.getenv("REDIMIND_AUTH_MODE") or local.get("auth_mode"),
            host=os.getenv("REDIMIND_HOST", "127.0.0.1"),
            port=int(os.getenv("REDIMIND_PORT", "8000")),
            embedding_model=os.getenv(
                "REDIMIND_EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
            ),
            embedding_cache=os.getenv("REDIMIND_EMBEDDING_CACHE"),
            embedding_dimension=int(os.getenv("REDIMIND_EMBEDDING_DIMENSION", "384")),
            allowed_hosts=tuple(filter(None, os.getenv("REDIMIND_ALLOWED_HOSTS", "").split(","))),
            allowed_origins=tuple(
                filter(None, os.getenv("REDIMIND_ALLOWED_ORIGINS", "").split(","))
            ),
            agent_tokens=json.loads(os.environ["REDIMIND_AGENT_TOKENS"])
            if "REDIMIND_AGENT_TOKENS" in os.environ
            else None,
        )
