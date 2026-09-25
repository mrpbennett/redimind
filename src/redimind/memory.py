"""Redis-backed review lifecycle and hybrid retrieval."""

import re
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from redis.commands.search.query import Query
from redis.exceptions import WatchError

from redimind._errors import Conflict, MemoryError
from redimind._index import INDEX, PREFIX, MemoryIndex
from redimind._retrieval import MemoryRetrieval

BLOCKED = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
    r"\b(?:password|api[_-]?key|secret|access[_-]?token)\s*[:=]\s*\S+",
    re.IGNORECASE,
)


class Candidate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    kind: Literal["lesson", "decision"] | None = Field(
        default=None, description="Lesson learned or project-specific decision (also accepts type)."
    )
    claim: str | None = Field(
        default=None, max_length=1000, description="One concise, reusable claim."
    )
    project_id: str | None = Field(
        default=None,
        max_length=250,
        description="Stable source project ID (also accepts source_project).",
    )
    repository_url: str | None = Field(default=None, max_length=500)
    source_ref: str | None = Field(
        default=None,
        max_length=500,
        description="Durable issue, commit, or document reference (also accepts source_reference).",
    )
    conditions: str | None = Field(
        default=None,
        max_length=1000,
        description="When the claim applies (also accepts applicability_conditions).",
    )
    outcome: Literal["worked", "failed", "mixed", "unknown"] | None = None
    evidence_status: Literal["verified", "observed", "unverified"] | None = None
    tried: str | None = Field(
        default=None,
        max_length=1000,
        description="Approach tried for a lesson (also accepts what_was_tried).",
    )
    rationale: str | None = Field(
        default=None, max_length=1000, description="Reason for a decision."
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_agent_fields(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        data = value.copy()
        for alias, name in {
            "type": "kind",
            "what_was_tried": "tried",
            "source_project": "project_id",
            "source_reference": "source_ref",
            "applicability_conditions": "conditions",
        }.items():
            if alias in data:
                if name in data and data[name] != data[alias]:
                    raise ValueError(f"Conflicting values for {name} and {alias}")
                data[name] = data.pop(alias)
        data.pop("proposer", None)  # Caller identity comes from the authenticated or local server.
        return data

    @field_validator("claim", "conditions", "source_ref", "tried", "rationale")
    @classmethod
    def reject_credentials(cls, value: str | None) -> str | None:
        if value and BLOCKED.search(value):
            raise ValueError("entry contains credential-like material")
        return value

    def missing(self) -> list[str]:
        required = (
            "kind",
            "claim",
            "project_id",
            "source_ref",
            "conditions",
            "outcome",
            "evidence_status",
        )
        missing = [field for field in required if not getattr(self, field)]
        if self.kind == "lesson" and not self.tried:
            missing.append("tried")
        if self.kind == "decision" and not self.rationale:
            missing.append("rationale")
        return missing


def timestamp() -> str:
    return datetime.now(UTC).isoformat()


def key(entry_id: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{32}", entry_id):
        raise MemoryError("invalid entry ID")
    return PREFIX + entry_id


class Memory:
    def __init__(self, redis, embeddings, dimension: int = 384):
        self.redis = redis
        self._index = MemoryIndex(redis, embeddings, dimension)
        self._retrieval = MemoryRetrieval(redis, self._index)

    async def initialize(self) -> None:
        await self._index.initialize()

    async def get(self, entry_id: str, history: bool = False) -> dict:
        entry = await self.redis.json().get(key(entry_id))
        if entry is None:
            raise MemoryError("entry not found")
        if not history:
            entry = {
                k: v for k, v in entry.items() if k not in {"history", "embedding", "search_text"}
            }
        return entry

    async def _save(self, entry: dict, expected: int) -> dict:
        await self._commit([(entry, expected)])
        return await self.get(entry["id"], history=True)

    async def _commit(
        self, changes: list[tuple[dict, int]], *, deleted: tuple[str, int] | None = None
    ) -> None:
        expected = [(key(entry["id"]), revision) for entry, revision in changes]
        if deleted:
            expected.append((key(deleted[0]), deleted[1]))
        async with self.redis.pipeline(transaction=True) as pipe:
            try:
                await pipe.watch(*(entry_key for entry_key, _ in expected))
                for entry_key, revision in expected:
                    previous = await pipe.json().get(entry_key)
                    if previous is None or previous["revision"] != revision:
                        raise Conflict("stale entry revision")
                pipe.multi()
                for entry, _ in changes:
                    pipe.json().set(key(entry["id"]), "$", entry)
                if deleted:
                    pipe.delete(key(deleted[0]))
                await pipe.execute()
            except WatchError as exc:
                raise Conflict("stale entry revision") from exc

    async def propose(self, fields: dict, actor: str, entry_id: str | None = None) -> dict:
        if entry_id:
            original = await self.get(entry_id, history=True)
            if original["status"] != "draft" or original["proposer"] != actor:
                raise MemoryError("only the proposer can complete their draft")
            fields = {**original["fields"], **fields}
        else:
            original = None
        data = Candidate.model_validate(fields)
        duplicates = await self.duplicates(data)
        candidate_id = entry_id or uuid4().hex
        now = timestamp()
        entry = {
            "id": candidate_id,
            "fields": data.model_dump(exclude_none=True),
            "status": "draft",
            "kind": data.kind or "unset",
            "proposer": actor,
            "created_at": original["created_at"] if original else now,
            "revision": (original["revision"] + 1) if original else 1,
            "history": [
                *(original["history"] if original else []),
                {
                    "actor": actor,
                    "action": "update_draft" if original else "propose",
                    "at": now,
                    "previous_revision": original["revision"] if original else 0,
                    **({"previous_fields": original["fields"]} if original else {}),
                },
            ],
            "search_text": "",
        }
        if original:
            await self._save(entry, original["revision"])
        else:
            if not await self.redis.json().set(key(candidate_id), "$", entry, nx=True):
                raise Conflict("candidate ID already exists")
        return {"id": candidate_id, "missing": data.missing(), "duplicates": duplicates}

    async def duplicates(self, data: Candidate) -> list[str]:
        return await self._retrieval.duplicates(data.claim)

    async def candidates(self) -> list[dict]:
        await self._index.require_searchable()
        response = await self.redis.ft(INDEX).search(
            Query("@status:{draft}").no_content().paging(0, 100)
        )
        entries = [await self.get(doc.id.removeprefix(PREFIX)) for doc in response.docs]
        return sorted(entries, key=lambda entry: entry["created_at"])

    async def _prepare_approved(self, entry: dict, actor: str, extra: dict | None = None) -> dict:
        fields = Candidate.model_validate(entry["fields"])
        if fields.missing():
            raise MemoryError(f"candidate missing: {', '.join(fields.missing())}")
        now = timestamp()
        text = " ".join(
            filter(None, [fields.claim, fields.conditions, fields.tried, fields.rationale])
        )
        vector = await self._index.embedding(text, for_write=True)
        entry.pop("embedding", None)
        entry = {
            **entry,
            **fields.model_dump(),
            "status": "current",
            "search_text": text,
            **({"embedding": vector} if vector is not None else {}),
            "embedding_model": self._index.model if vector else None,
            "revision": entry["revision"] + 1,
            "history": [
                *entry["history"],
                {
                    "actor": actor,
                    "action": "approve",
                    "at": now,
                    "previous_revision": entry["revision"],
                },
            ],
            **(extra or {}),
        }
        return entry

    async def approve(
        self,
        entry_id: str,
        revision: int,
        actor: str,
        supersedes: str | None = None,
        evidence_for: str | None = None,
    ) -> dict:
        if supersedes and evidence_for:
            raise MemoryError("choose supersede or link evidence")
        draft = await self.get(entry_id, history=True)
        if draft["revision"] != revision:
            raise Conflict("stale entry revision")
        if draft["status"] != "draft":
            raise MemoryError("only drafts can be approved")
        if evidence_for:
            target = await self.get(evidence_for, history=True)
            if target["status"] != "current":
                raise MemoryError("evidence target must be current")
            fields = Candidate.model_validate(draft["fields"])
            if fields.missing():
                raise MemoryError(f"candidate missing: {', '.join(fields.missing())}")
            target["evidence"] = [
                *target.get("evidence", []),
                {
                    "source_ref": fields.source_ref,
                    "project_id": fields.project_id,
                    "candidate_id": draft["id"],
                    "actor": actor,
                    "at": timestamp(),
                },
            ]
            target["revision"] += 1
            target["history"].append(
                {
                    "actor": actor,
                    "action": "link_evidence",
                    "at": timestamp(),
                    "previous_revision": target["revision"] - 1,
                }
            )
            draft["status"] = "linked"
            draft["revision"] += 1
            draft["history"].append(
                {
                    "actor": actor,
                    "action": "linked",
                    "at": timestamp(),
                    "previous_revision": revision,
                }
            )
            await self._commit([(target, target["revision"] - 1), (draft, revision)])
            return await self.get(entry_id, history=True)
        if supersedes:
            previous = await self.get(supersedes, history=True)
            if previous["status"] != "current":
                raise MemoryError("superseded entry must be current")
        approved = await self._prepare_approved(draft, actor, {"supersedes": supersedes})
        if supersedes:
            old_revision = previous["revision"]
            retired = self._prepare_status(
                previous, actor, "superseded", {"superseded_by": entry_id}
            )
            await self._commit([(approved, revision), (retired, old_revision)])
        else:
            await self._save(approved, revision)
        return await self.get(entry_id, history=True)

    @staticmethod
    def _prepare_status(entry: dict, actor: str, status: str, extra: dict | None = None) -> dict:
        previous = entry["revision"]
        entry.update(extra or {})
        entry["status"] = status
        entry["revision"] += 1
        entry["history"].append(
            {"actor": actor, "action": status, "at": timestamp(), "previous_revision": previous}
        )
        return entry

    async def change_status(
        self, entry_id: str, revision: int, actor: str, status: str, extra: dict | None = None
    ) -> dict:
        if status not in {"retired", "rejected"}:
            raise MemoryError("invalid status")
        entry = await self.get(entry_id, history=True)
        if entry["revision"] != revision:
            raise Conflict("stale entry revision")
        if (status == "rejected") != (entry["status"] == "draft") or entry["status"] not in {
            "draft",
            "current",
        }:
            raise MemoryError("invalid status transition")
        return await self._save(self._prepare_status(entry, actor, status, extra), revision)

    async def delete(self, entry_id: str, revision: int) -> None:
        entry = await self.get(entry_id, history=True)
        if entry["revision"] != revision:
            raise Conflict("stale entry revision")
        changed = []
        async for entry_key in self.redis.scan_iter(match=PREFIX + "*"):
            if entry_key.decode() == key(entry_id):
                continue
            other = await self.redis.json().get(entry_key)
            evidence = other.get("evidence", [])
            kept = [item for item in evidence if item.get("candidate_id") != entry_id]
            if len(kept) != len(evidence):
                old_revision = other["revision"]
                other["evidence"] = kept
                other["revision"] += 1
                other["history"].append(
                    {
                        "actor": "owner",
                        "action": "remove_deleted_evidence",
                        "at": timestamp(),
                        "previous_revision": old_revision,
                    }
                )
                changed.append((other, old_revision))
        await self._commit(changed, deleted=(entry_id, revision))

    async def search(
        self, text: str, project_id: str, include_decisions: bool = False, limit: int = 10
    ) -> list[dict]:
        return await self._retrieval.search(text, project_id, include_decisions, limit)

    async def reindex(self) -> dict:
        return await self._index.reindex()
