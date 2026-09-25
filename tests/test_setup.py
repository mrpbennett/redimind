import sys
from urllib.parse import unquote, urlsplit

import pytest
from redis.exceptions import ConnectionError

from redimind import cli
from redimind import settings as config


def test_password_prompt_encodes_credentials_without_exposing_them(monkeypatch, capsys):
    password = "long @ secret:/"
    assembled = cli.add_password("redis://memory-user@192.168.6.1:6379/0", password)
    parsed = urlsplit(assembled)
    assert parsed.username == "memory-user"
    assert unquote(parsed.password) == password
    assert password not in assembled

    submitted = []
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: password)
    monkeypatch.setattr(cli, "setup", lambda url, force: submitted.append((url, force)))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "redimind",
            "setup",
            "--redis-url",
            "redis://192.168.6.1:6379/0",
            "--ask-password",
            "--force",
        ],
    )
    cli.main()
    assert unquote(urlsplit(submitted[0][0]).password) == password
    assert submitted[0][1] is True
    assert password not in capsys.readouterr().out


def test_failed_setup_preserves_previous_connection(monkeypatch, tmp_path):
    path = tmp_path / "redimind.local.toml"
    path.write_text('redis_url = "redis://127.0.0.1:16379/0"\nauth_mode = "local"\n')
    monkeypatch.setattr(cli, "CONFIG_FILE", str(path))

    class FailingRedis:
        def ping(self):
            raise ConnectionError("remote Redis refused authentication")

        def close(self):
            pass

    monkeypatch.setattr(cli.SyncRedis, "from_url", lambda *args, **kwargs: FailingRedis())
    with pytest.raises(ConnectionError, match="authentication"):
        cli.setup("redis://:bad@192.168.6.1:6379/0", force=True)
    assert "127.0.0.1:16379" in path.read_text()


def test_config_command_redacts_password(monkeypatch, tmp_path, capsys):
    path = tmp_path / "redimind.local.toml"
    path.write_text(
        'redis_url = "redis://:private-password@192.168.6.1:6379/0"\nauth_mode = "local"\n'
    )
    path.chmod(0o600)
    monkeypatch.setattr(config, "CONFIG_FILE", str(path))
    for name in (
        "REDIMIND_REDIS_URL",
        "REDIMIND_AUTH_MODE",
        "REDIMIND_AGENT_TOKEN",
        "REDIMIND_OWNER_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(sys, "argv", ["redimind", "config"])
    cli.main()
    output = capsys.readouterr().out
    assert "192.168.6.1" in output
    assert '"auth_mode": "local"' in output
    assert "private-password" not in output


def test_setup_defaults_to_local_docker_redis(monkeypatch):
    submitted = []
    monkeypatch.setattr(cli, "setup", lambda url, force: submitted.append((url, force)))
    monkeypatch.setattr(sys, "argv", ["redimind", "setup", "--force"])
    cli.main()
    assert submitted == [("redis://127.0.0.1:6379/0", True)]
