from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping


Stage = Literal["stage1", "stage2"]
TargetKind = Literal["actor", "collab"]


@dataclass(frozen=True)
class TeamOperation:
    name: str
    stage: str
    expected_version: int | None = None


@dataclass(frozen=True)
class KeyIssue:
    reason: str
    stage: Stage
    key: str
    source_role: str
    retryable: bool


@dataclass(frozen=True)
class TaskBinding:
    task_id: str
    task_instance_id: str
    owner_user_id: int
    owner_library_id: str


@dataclass(frozen=True)
class CompletionProjection:
    seal_id: str | None
    kind: str
    trigger: str
    sealed_at: str | None
    final_decision: str
    persisted: bool


@dataclass(frozen=True)
class AffectedCell:
    stage: str
    item_key: str


@dataclass(frozen=True)
class MutationFootprint:
    cells: tuple[AffectedCell, ...]
    complete: bool
    reason: str | None


@dataclass(frozen=True)
class SealWitness:
    stage: Stage
    item_key: str
    source_path: Path | None
    registration_id: str | None
    generation: int | None
    library_id: str
    seal_id: str
    content_sha256: str
    payload_sha256: str
    sealed_at: str


@dataclass(frozen=True)
class KeyedReadPlan:
    binding: TaskBinding
    operation: TeamOperation
    item_keys: tuple[str, ...]
    witnesses: Mapping[tuple[str, str], SealWitness]
    live_dependencies: Mapping[tuple[int, str], tuple[str, ...]]
    issues: Mapping[tuple[str, str], KeyIssue]
    selected_paths: tuple[Path, ...]


def normalize_item_keys(item_keys: Iterable[str]) -> tuple[str, ...]:
    """Validate keys, deduplicate them, and preserve their first-seen order."""
    if isinstance(item_keys, (str, bytes)):
        raise ValueError("item_keys must be an iterable of strings, not str/bytes")

    ordered: list[str] = []
    seen: set[str] = set()
    try:
        iterator = iter(item_keys)
    except TypeError as exc:
        raise ValueError("item_keys must be an iterable of strings") from exc

    for key in iterator:
        if not isinstance(key, str):
            raise ValueError("each item key must be a string")
        if not key:
            raise ValueError("item keys must not be empty")
        if "\x00" in key:
            raise ValueError("item keys must not contain NUL")
        try:
            key.encode("utf-8", "strict")
        except UnicodeEncodeError as exc:
            raise ValueError("item keys must contain valid Unicode") from exc
        if key not in seen:
            seen.add(key)
            ordered.append(key)

    if not ordered:
        raise ValueError("item_keys must not be empty")
    return tuple(ordered)
