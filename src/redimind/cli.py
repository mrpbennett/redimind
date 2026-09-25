"""Local setup and owner review commands."""

import argparse
import asyncio
import getpass
import json
import os
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit, urlunsplit

import httpx
from redis import Redis as SyncRedis
from redis.asyncio import Redis

from redimind.embeddings import LocalEmbeddings
from redimind.memory import Candidate, Conflict, Memory
from redimind.settings import CONFIG_FILE, Settings


def add_password(redis_url: str, password: str) -> str:
    parsed = urlsplit(redis_url)
    if parsed.password is not None:
        raise ValueError("Remove the password from --redis-url when using --ask-password")
    if not password:
        raise ValueError("Redis password cannot be empty")
    username = quote(unquote(parsed.username or ""), safe="")
    host = parsed.netloc.rsplit("@", 1)[-1]
    return urlunsplit(parsed._replace(netloc=f"{username}:{quote(password, safe='')}@{host}"))


def setup(redis_url: str, force: bool) -> None:
    Settings(redis_url=redis_url, auth_mode="local")
    client = SyncRedis.from_url(redis_url, socket_connect_timeout=3, socket_timeout=3)
    try:
        client.ping()
        client.execute_command("FT._LIST")
        client.execute_command("JSON.GET", "redimind:setup:nonexistent")
    finally:
        client.close()
    path = Path(CONFIG_FILE)
    content = f'redis_url = {json.dumps(redis_url)}\nauth_mode = "local"\n'
    if path.exists() and not force:
        if path.read_text() == content:
            print(f"Already configured in {CONFIG_FILE}")
            return
        raise SystemExit(f"{CONFIG_FILE} exists; use --force to replace it")
    temp = Path(f"{CONFIG_FILE}.tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(content)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
    print(f"Saved local Redis configuration to {CONFIG_FILE}")


def _review_choice(entry: dict, position: int, total: int) -> str:
    fields = Candidate.model_validate(entry["fields"])
    missing = fields.missing()
    print(f"\n[{position}/{total}] {fields.kind or 'Draft'}: {fields.claim or '(no claim yet)'}")
    for label, value in (
        ("Project", fields.project_id),
        ("Source", fields.source_ref),
        ("Conditions", fields.conditions),
        ("Tried", fields.tried),
        ("Rationale", fields.rationale),
        ("Outcome", fields.outcome),
        ("Evidence", fields.evidence_status),
    ):
        if value:
            print(f"  {label}: {value}")
    if missing:
        print(f"  Missing for approval: {', '.join(missing)}")
    options = "[a]pprove, " if not missing else ""
    while True:
        try:
            answer = input(f"{options}[r]eject, [s]kip, [q]uit (Enter skips): ").strip().lower()
        except EOFError:
            return "quit"
        choices = {
            "a": "approve",
            "approve": "approve",
            "r": "reject",
            "reject": "reject",
            "s": "skip",
            "skip": "skip",
            "": "skip",
            "q": "quit",
            "quit": "quit",
        }
        choice = choices.get(answer)
        if choice == "approve" and missing:
            print("Complete the missing fields before approving this candidate.")
        elif choice:
            return choice
        else:
            print("Choose approve, reject, skip, or quit.")


async def review_queue(
    drafts: list[dict],
    apply: Callable[[str, dict], Awaitable[None]],
    refresh: Callable[[str], Awaitable[dict]],
) -> None:
    """Show each candidate once; redisplay it if its revision changes mid-review."""
    if not drafts:
        print("No candidates awaiting review.")
        return
    for position, draft in enumerate(drafts, start=1):
        while True:
            choice = _review_choice(draft, position, len(drafts))
            if choice == "quit":
                return
            if choice == "skip":
                break
            try:
                await apply(choice, draft)
            except Conflict:
                draft = await refresh(draft["id"])
                if draft["status"] != "draft":
                    print("Candidate no longer awaits review.")
                    break
                print("Candidate changed during review; inspect the updated draft again.")
                continue
            result = "Approved" if choice == "approve" else "Rejected"
            print(f"{result} {draft['id']}")
            break


async def remote_review(client: httpx.AsyncClient) -> None:
    response = await client.get("/admin/candidates")
    response.raise_for_status()

    async def apply(choice: str, draft: dict) -> None:
        path = f"/admin/entries/{draft['id']}"
        action = "approve" if choice == "approve" else "status"
        payload = {"revision": draft["revision"]}
        if choice == "reject":
            payload["status"] = "rejected"
        result = await client.post(f"{path}/{action}", json=payload)
        if result.status_code == 409:
            raise Conflict("stale entry revision")
        result.raise_for_status()

    async def refresh(entry_id: str) -> dict:
        result = await client.get(f"/admin/entries/{entry_id}")
        result.raise_for_status()
        return result.json()

    await review_queue(response.json(), apply, refresh)


async def local_command(args: argparse.Namespace, settings: Settings) -> dict | list | None:
    redis = Redis.from_url(settings.redis_url)
    try:
        memory = Memory(
            redis,
            LocalEmbeddings(settings.embedding_model, settings.embedding_cache),
            dimension=settings.embedding_dimension,
        )
        await memory.initialize()
        if args.command == "review":
            drafts = await memory.candidates()
            if getattr(args, "json", False) or not sys.stdin.isatty():
                return drafts

            async def apply(choice: str, draft: dict) -> None:
                if choice == "approve":
                    await memory.approve(draft["id"], draft["revision"], "owner")
                else:
                    await memory.change_status(draft["id"], draft["revision"], "owner", "rejected")

            async def refresh(entry_id: str) -> dict:
                return await memory.get(entry_id)

            await review_queue(drafts, apply, refresh)
            return None
        if args.command == "show":
            return await memory.get(args.id, history=True)
        if args.command == "reindex":
            return await memory.reindex()
        if args.command == "delete":
            await memory.delete(args.id, args.revision)
            return {"deleted": True}
        if args.command in {"retire", "reject"}:
            status = "retired" if args.command == "retire" else "rejected"
            return await memory.change_status(args.id, args.revision, "owner", status)
        return await memory.approve(
            args.id,
            args.revision,
            "owner",
            supersedes=args.target if args.command == "supersede" else None,
            evidence_for=args.target if args.command == "link" else None,
        )
    finally:
        await redis.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Review and manage Redimind memory entries")
    parser.add_argument("--url", default=os.getenv("REDIMIND_URL", "http://127.0.0.1:8000"))
    commands = parser.add_subparsers(dest="command", required=True)
    setup_cmd = commands.add_parser(
        "setup", help="Save a local Redis connection for token-free use"
    )
    setup_cmd.add_argument("--redis-url", default="redis://127.0.0.1:6379/0")
    setup_cmd.add_argument(
        "--ask-password",
        action="store_true",
        help="Prompt without showing the password in shell history",
    )
    setup_cmd.add_argument("--force", action="store_true")
    commands.add_parser(
        "config", help="Show the selected Redis target without revealing credentials"
    )
    review = commands.add_parser("review", help="Review proposals interactively")
    review.add_argument(
        "--json", action="store_true", help="Print drafts as JSON instead of prompting"
    )
    show = commands.add_parser("show")
    show.add_argument("id")
    for name in ("approve", "supersede", "link", "retire", "reject", "delete"):
        cmd = commands.add_parser(name)
        cmd.add_argument("id")
        cmd.add_argument("--revision", type=int, required=True)
        if name in {"supersede", "link"}:
            cmd.add_argument("target")
    commands.add_parser("reindex")
    args = parser.parse_args()
    if args.command == "setup":
        redis_url = (
            add_password(args.redis_url, getpass.getpass("Redis password: "))
            if args.ask_password
            else args.redis_url
        )
        setup(redis_url, args.force)
        return
    settings = Settings.from_env()
    if args.command == "config":
        target = urlsplit(settings.redis_url)
        source = "REDIMIND_REDIS_URL" if os.getenv("REDIMIND_REDIS_URL") else CONFIG_FILE
        print(
            json.dumps(
                {
                    "source": source,
                    "redis_host": target.hostname,
                    "redis_port": target.port or 6379,
                    "auth_mode": settings.auth_mode,
                },
                indent=2,
            )
        )
        return
    if settings.auth_mode == "local":
        result = asyncio.run(local_command(args, settings))
        if result is not None:
            print(json.dumps(result, indent=2))
        return
    token = settings.owner_token
    if args.command == "review" and not args.json and sys.stdin.isatty():

        async def run_review() -> None:
            async with httpx.AsyncClient(
                base_url=args.url, headers={"Authorization": f"Bearer {token}"}, timeout=30
            ) as client:
                await remote_review(client)

        asyncio.run(run_review())
        return
    with httpx.Client(
        base_url=args.url, headers={"Authorization": f"Bearer {token}"}, timeout=30
    ) as client:
        if args.command == "review":
            response = client.get("/admin/candidates")
        elif args.command == "show":
            response = client.get(f"/admin/entries/{args.id}")
        elif args.command == "reindex":
            response = client.post("/admin/reindex")
        else:
            path = f"/admin/entries/{args.id}"
            if args.command == "delete":
                response = client.request("DELETE", path, json={"revision": args.revision})
            elif args.command in {"retire", "reject"}:
                status = "retired" if args.command == "retire" else "rejected"
                response = client.post(
                    path + "/status", json={"revision": args.revision, "status": status}
                )
            else:
                field = {"supersede": "supersedes", "link": "evidence_for"}.get(args.command)
                body = {"revision": args.revision}
                if field:
                    body[field] = args.target
                response = client.post(path + "/approve", json=body)
        response.raise_for_status()
        print(json.dumps(response.json(), indent=2))


if __name__ == "__main__":
    main()
