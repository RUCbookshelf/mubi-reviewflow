"""S2-B2a retained team write inputs (internal, no HTTP entry).

This module is deliberately limited to the input-identity / reservation layer
of the team write window.  It does **not** implement business role gates,
eligibility, operation dispatch, trace preparation, notify/SSE, or any HTTP
route; those belong to B2b and later stages.

P10 (2.1.6 single-reserve write window, C07 fixed order):

    canonical RLock -> B1 enter/collab IMMEDIATE -> plan -> provisional
    readers all rollback/close -> one reserve(owner, others) -> final
    re-verification on the retained connections -> yield -> single target
    commit -> others rollback/all close -> unlock (notify stays with the
    caller, outside the lock, after the real commit).

Contract summary
----------------
- Reuse ``review_workflow_api.WorkflowRuntime`` canonical task lock,
  ``context`` and ``sync_catalog``; never import or call global auth/main
  data access.  Before ``context`` (which may lazily create the owner DB),
  check the trusted owner snapshot path is an existing regular readable file;
  the owner catalog must never be recreated from a missing file.  Only the
  current actor's own target DB may follow the existing ``context``
  preparation; absent required member DBs are never created.
- Confirm the task is a team task from the current locked collaboration
  state, never fall back to personal/legacy behavior here.
- After the B1 collaboration ``BEGIN IMMEDIATE`` barrier is entered, build
  the P08 ``KeyedReadPlan`` on the reserved collaboration connection.  The
  plan is used **only** to select the retained set (witness source paths and
  live-voter member DBs); it never grants write permission and never
  authorizes a writer by itself.
- Every provisional reader (plan holder readers, the accounts enumeration,
  the read-only role-state probes) is fully rolled back and closed before the
  single ``reserve(owner, others)``; the window never holds a SHARED lock of
  its own that could block the target commit.
- The reserve set is exactly the necessary owner/accounts/actor connections,
  the plan's selected completion witnesses, and — only when the plan has live
  dependencies — the current required member (live-voter) DBs.  Discovered
  candidate sources are never reserved wholesale.
- Final re-verification happens on the same retained connections: identity,
  roster, task binding, source registration generation/library, resolved
  physical witness paths, the original seal id/hash/time quadruple, and the
  requested keys' canonical (non-duplicate, non-title-pending) status read
  from the retained owner catalog with a bounded keyed SELECT.
- A non-owner actor's member catalog marker must match the owner's current
  catalog revision.  Missing/mismatched markers, and any plan drift that
  would require expanding the retained set (witness disappeared/re-registered,
  seal quadruple no longer readable as planned), trigger at most one full
  pre-writer retry (new B1 reservation, new plan, new enumeration) — at most
  2 complete attempts.  Reserve/writer/commit failures are never replayed,
  and a failed selected witness is never re-sourced by a second reserve in
  the same reservation manager.  Caller writer/commit exceptions are never
  replayed.
"""

from __future__ import annotations

import os
import sqlite3
import stat as stat_mod
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, Iterator, Literal, NoReturn

from fastapi import HTTPException

from coscreen import task_collaboration as collaboration_mod
from coscreen import tasks as tasks_mod
from coscreen.security import sanitize_component
from coscreen.team_flow_types import (
    KeyedReadPlan,
    TeamOperation,
    normalize_item_keys,
)
from coscreen.team_terminal_facts import (
    TerminalFactsUnavailable,
    read_seal_on,
)
from coscreen.team_terminal_registry import (
    TerminalRegistryUnavailable,
    read_source_on,
)
from coscreen.team_write_reservation import (
    ReservedWriteConnections,
    retained_write_connections,
)
from custom_backend.team_keyed_domain import (
    OwnerKeyedDomainUnavailableError,
    canonical_item_rows_on,
)
from custom_backend.team_keyed_inputs import (
    KeyedPlanUnavailableError,
    build_keyed_read_plan,
)
from custom_backend.team_screening_runtime import (
    TeamFlowUnavailableError,
    TeamReadScope,
    borrowed_team_read_scope,
    task_kind,
)
from custom_backend.team_source_lifecycle import (
    SourceIdentityIssue,
    resolve_source_identity,
)

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids import-time coupling
    from custom_backend.review_workflow_api import WorkflowContext, WorkflowRuntime

__all__ = ["TeamWriteInputs", "retained_team_write_inputs"]

_MAX_ATTEMPTS = 2
_SANITIZE_FALLBACK = "_"
_ACCOUNTS_FILENAME = "users.db"


class _CatalogMarkerDrift(Exception):
    """Internal pre-yield signal only; never exposed or retried after yield."""


class _WriteWindowDrift(Exception):
    """Internal pre-yield signal: the plan's facts drifted inside the reserved
    window (witness seal/registration/generation/physical path no longer match
    the plan), so the retained-set selection basis is stale.

    The whole attempt restarts before any writer (new reservation manager, new
    plan, new reserve — at most ``_MAX_ATTEMPTS`` complete attempts).  Never
    raised after the yield; writer/commit exceptions are never replayed and a
    second ``reserve`` is never issued inside the same manager."""


@dataclass(frozen=True)
class TeamWriteInputs:
    """Inputs valid only inside ``retained_team_write_inputs``.

    After the context exits, the reserved connections are committed/closed and
    the scope caches must not be reused for new state.  ``cleanup_errors``
    remains readable because it delegates to the retained reservation object.

    P10 (C07/C08): ``operation``/``item_keys`` carry the server-selected
    operation and the normalized request key domain; ``plan`` is the frozen
    ``KeyedReadPlan`` that selected this window's retained set.  The plan
    never authorizes a writer by itself — business gates belong to the
    ``require_team_write`` layer.
    """

    ctx: WorkflowContext
    scope: TeamReadScope
    collab_conn: sqlite3.Connection
    owner_conn: sqlite3.Connection
    actor_conn: sqlite3.Connection
    target_conn: sqlite3.Connection
    connections: tuple[sqlite3.Connection, ...]
    roster: tuple[dict, ...]
    operation: TeamOperation
    item_keys: tuple[str, ...]
    plan: KeyedReadPlan
    _reserved: ReservedWriteConnections = field(repr=False, compare=False)

    @property
    def cleanup_errors(self) -> tuple[str, ...]:
        return self._reserved.cleanup_errors


def _fail(
    reason: str,
    detail: str,
    *,
    member: str | None = None,
    participant: int | None = None,
) -> NoReturn:
    raise TeamFlowUnavailableError(
        reason, detail=detail, member=member, participant=participant)


def _fail_from(
    reason: str,
    detail: str,
    exc: Exception,
    *,
    member: str | None = None,
    participant: int | None = None,
) -> NoReturn:
    """Raise a typed failure while preserving the original exception object."""
    raise TeamFlowUnavailableError(
        reason, detail=detail, member=member, participant=participant) from exc


def _task_id_from_info(info: object) -> str:
    raw = info if isinstance(info, str) else getattr(info, "task_id", None)
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("info must expose a non-empty task_id")
    return raw


def _read_accounts_enumeration(
    accounts_path: Path,
) -> tuple[dict[int, str], dict[str, set[int]]]:
    """Short read-only accounts read used only to enumerate paths.

    This is not the final authority.  The reserved accounts connection is
    re-read through TSR after ``reserve`` and must agree for every relevant
    identity; otherwise the window fails with ``identity_unconfirmed``.
    """
    if not accounts_path.is_file():
        _fail("accounts_unreadable", f"accounts DB missing: {accounts_path}")
    try:
        conn = sqlite3.connect(
            accounts_path.resolve().as_uri() + "?mode=ro",
            uri=True,
            timeout=15.0,
        )
    except (sqlite3.Error, OSError) as exc:
        _fail("accounts_unreadable", f"accounts open failed: {exc}")
    try:
        try:
            table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'"
            ).fetchone()
            if table is None:
                _fail("accounts_unreadable", "accounts DB has no users table")
            rows = conn.execute("SELECT id, username FROM users").fetchall()
        except sqlite3.ProgrammingError:
            raise
        except sqlite3.Error as exc:
            _fail("accounts_unreadable", f"accounts read failed: {exc}")
    finally:
        try:
            conn.close()
        except sqlite3.Error:
            pass

    by_id: dict[int, str] = {}
    by_casefold: dict[str, set[int]] = {}
    for raw_id, username in rows:
        try:
            user_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if not isinstance(username, str) or not username:
            continue
        by_id[user_id] = username
        normalized = sanitize_component(username, _SANITIZE_FALLBACK).casefold()
        by_casefold.setdefault(normalized, set()).add(user_id)
    return by_id, by_casefold


def _read_role_state(
    collab_conn: sqlite3.Connection, task_id: str
) -> dict:
    try:
        return collaboration_mod.read_team_role_state_on(
            collab_conn, task_id=task_id)
    except sqlite3.ProgrammingError:
        raise
    except sqlite3.Error as exc:
        _fail("collab_unreadable", f"collaboration role read failed: {exc}")


def _read_role_state_ro(collab_resolved: Path, task_id: str) -> dict:
    """Short mode=ro collab read; no create/migrate/BEGIN outside SQLite."""
    try:
        conn = sqlite3.connect(
            collab_resolved.as_uri() + "?mode=ro", uri=True, timeout=15.0)
    except (sqlite3.Error, OSError) as exc:
        _fail("collab_unreadable", f"collaboration read-only open failed: {exc}")
    try:
        return _read_role_state(conn, task_id)
    finally:
        try:
            conn.close()
        except sqlite3.Error:
            pass


def _owner_identity(state: dict) -> tuple[int, str]:
    owner = state.get("owner")
    if not isinstance(owner, dict):
        _fail("unconfirmed_task_state", "owner_match state has no owner row")
    owner_user_id = owner.get("owner_user_id")
    owner_username = owner.get("owner_username")
    if (isinstance(owner_user_id, bool)
            or not isinstance(owner_user_id, int)
            or owner_user_id <= 0
            or not isinstance(owner_username, str)
            or not owner_username):
        _fail("unconfirmed_task_state", "owner row fields are invalid")
    return owner_user_id, owner_username


def _slug_path(base_dir: Path, username: str) -> Path:
    slug = tasks_mod.slugify_screener(username)
    if not isinstance(slug, str) or not slug:
        _fail("identity_unconfirmed", "empty screener slug", member=username)
    return base_dir / f"{slug}.db"


def _inspect_task_path(
    path: Path,
    base_dir: Path,
    *,
    reason: str,
    member: str | None = None,
) -> tuple[Path, os.stat_result]:
    """Resolve/contain/stat a task-scoped DB before any reservation."""
    try:
        resolved = path.resolve(strict=True)
    except FileNotFoundError as exc:
        _fail(reason, f"required input missing: {path}", member=member)
    except OSError as exc:
        _fail(reason, f"input resolve failed: {path}: {exc}", member=member)
    if resolved != base_dir and base_dir not in resolved.parents:
        _fail(reason, f"input escapes task directory: {path}", member=member)
    try:
        entry = resolved.stat()
    except OSError as exc:
        _fail(reason, f"input stat failed: {path}: {exc}", member=member)
    if not stat_mod.S_ISREG(entry.st_mode):
        _fail(reason, f"input is not a regular file: {path}", member=member)
    if not os.access(resolved, os.R_OK | os.W_OK):
        _fail(reason, f"input is not readable/writable: {path}", member=member)
    if not entry.st_ino:
        _fail(
            reason,
            f"physical identity unavailable (st_ino=0), refusing path trust: {path}",
            member=member,
        )
    return resolved, entry


def _required_member_path(
    path: Path,
    base_dir: Path,
    *,
    username: str,
) -> tuple[Path, os.stat_result] | None:
    """Return an existing required-member DB, or None only on FileNotFoundError."""
    try:
        path.stat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        _fail(
            "member_db_unreadable",
            f"required member stat failed: {path}: {exc}",
            member=username,
        )
    return _inspect_task_path(
        path, base_dir, reason="member_db_unreadable", member=username)


def _inspect_accounts_path(path: Path) -> tuple[Path, os.stat_result]:
    """Accounts is a host-explicit path: no task containment, but must be a file."""
    try:
        resolved = path.resolve(strict=True)
    except FileNotFoundError:
        _fail("accounts_unreadable", f"accounts input missing: {path}", member=None)
    except OSError as exc:
        _fail("accounts_unreadable", f"accounts resolve failed: {exc}")
    try:
        entry = resolved.stat()
    except OSError as exc:
        _fail("accounts_unreadable", f"accounts stat failed: {exc}")
    if not stat_mod.S_ISREG(entry.st_mode):
        _fail("accounts_unreadable", f"accounts is not a regular file: {path}")
    if not os.access(resolved, os.R_OK | os.W_OK):
        _fail("accounts_unreadable", f"accounts is not readable/writable: {path}")
    return resolved, entry


def _identity_key(entry: os.stat_result, resolved: Path) -> tuple:
    if entry.st_ino:
        return ("inode", entry.st_dev, entry.st_ino)
    return ("path", os.path.normcase(os.fspath(resolved)))


def _claim_path(
    claims: dict[tuple, tuple],
    resolved: Path,
    entry: os.stat_result,
    claim: tuple,
    *,
    member: str | None = None,
) -> None:
    identity = _identity_key(entry, resolved)
    prior = claims.get(identity)
    if prior is not None and prior != claim:
        _fail(
            "storage_conflict",
            f"different purposes share one physical DB: {prior!r} vs {claim!r}",
            member=member,
        )
    claims[identity] = claim


def _reject_accounts_alias(
    accounts_resolved: Path, task_paths: tuple[Path, ...]
) -> None:
    for task_path in task_paths:
        try:
            if accounts_resolved.samefile(task_path):
                _fail(
                    "storage_conflict",
                    f"accounts DB aliases a task DB: {accounts_resolved} / {task_path}",
                )
        except OSError as exc:
            _fail("accounts_unreadable", f"accounts alias check failed: {exc}")


def _validate_roster_identity(
    participants: list[dict],
    accounts_by_id: dict[int, str],
    casefold_ids: dict[str, set[int]],
    owner_username: str,
) -> None:
    """Mirror TSR's existing input-identity reasons; no business role gate."""
    owner_entry: dict | None = None
    for entry in participants:
        user_id = entry.get("user_id")
        username = accounts_by_id.get(user_id)
        if not isinstance(username, str) or not username:
            _fail(
                "member_unresolved",
                "participant has no account row",
                participant=user_id if isinstance(user_id, int) else None,
            )
        conflict_key = sanitize_component(username, _SANITIZE_FALLBACK).casefold()
        if len(casefold_ids.get(conflict_key, ())) > 1:
            _fail(
                "storage_conflict",
                "multiple accounts share one sanitized casefold slug",
                member=username,
            )
        slug = tasks_mod.slugify_screener(username)
        if f"{slug}.db".casefold() == collaboration_mod.DB_FILENAME.casefold():
            _fail(
                "storage_conflict",
                "member slug collides with the reserved collaboration DB name",
                member=username,
            )
        if entry.get("is_owner"):
            owner_entry = entry
            if slug != tasks_mod.slugify_screener(owner_username):
                _fail(
                    "identity_unconfirmed",
                    "owner account slug differs from the collaboration owner snapshot",
                    member=username,
                )
    if owner_entry is None:
        _fail("unconfirmed_task_state", "owner participant entry is missing")


def _actor_participant(participants: list[dict], actor_user_id: int) -> dict:
    for entry in participants:
        if entry.get("user_id") == actor_user_id:
            if entry.get("participant") is not True:
                _fail(
                    "identity_unconfirmed",
                    "actor is listed without a current team grant",
                    participant=actor_user_id,
                )
            return entry
    _fail(
        "identity_unconfirmed",
        "actor is not a current team participant",
        participant=actor_user_id,
    )


def _owner_catalog_revision(owner_conn: sqlite3.Connection) -> int:
    """Read the owner's current revision, matching store.settings' 0 default."""
    try:
        table = owner_conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='workflow_state'"
        ).fetchone()
        if table is None:
            return 0
        row = owner_conn.execute(
            "SELECT catalog_revision FROM workflow_state WHERE singleton=1"
        ).fetchone()
        if row is None:
            _fail(
                "owner_db_unreadable",
                "owner workflow_state has no singleton row; catalog revision unavailable",
            )
        raw = row[0]
        if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
            _fail("owner_db_unreadable", f"owner catalog_revision invalid: {raw!r}")
        return raw
    except sqlite3.ProgrammingError:
        raise
    except sqlite3.Error as exc:
        _fail("owner_db_unreadable", f"owner catalog revision read failed: {exc}")


def _member_marker_revision(
    member_conn: sqlite3.Connection,
    owner_identity: str,
    expected_revision: int,
) -> None:
    """Raise _CatalogMarkerDrift for missing/mismatched marker; typed for reads."""
    try:
        table = member_conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='workflow_catalog_sync'"
        ).fetchone()
        if table is None:
            raise _CatalogMarkerDrift("workflow_catalog_sync table missing")
        row = member_conn.execute(
            "SELECT revision FROM workflow_catalog_sync WHERE owner_identity=?",
            (owner_identity,),
        ).fetchone()
        if row is None:
            raise _CatalogMarkerDrift(
                f"workflow_catalog_sync row missing for {owner_identity}")
        raw = row[0]
        if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
            raise _CatalogMarkerDrift(
                f"workflow_catalog_sync revision invalid: {raw!r}")
        if raw != expected_revision:
            raise _CatalogMarkerDrift(
                f"member catalog marker {raw} != owner revision {expected_revision}")
    except sqlite3.ProgrammingError:
        raise
    except sqlite3.Error as exc:
        _fail("member_db_unreadable", f"member catalog marker read failed: {exc}")


def _connection_map(
    reserved: ReservedWriteConnections,
    paths: tuple[Path, ...],
) -> dict[Path, sqlite3.Connection]:
    result: dict[Path, sqlite3.Connection] = {}
    for path in paths:
        try:
            conn = reserved.connection(path)
        except RuntimeError as exc:
            _fail(
                "connection_contract",
                f"reserved source is not registered: {path}: {exc}",
            )
        if not isinstance(conn, sqlite3.Connection) or not conn.in_transaction:
            _fail(
                "connection_contract",
                f"reserved connection is invalid or lacks a caller transaction: {path}",
            )
        result[path] = conn
    return result


def _prepare_path_present(path: Path, *, username: str) -> bool:
    """Only an absent directory entry may be treated as a new prepare path."""
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        _fail_from(
            "member_db_unreadable",
            f"prepare-pre candidate lstat failed: {path}: {exc}",
            exc,
            member=username,
        )
    # A dangling symlink exists: subsequent strict resolve/stat must reject it.
    return True


def _reject_other_account_prepare_aliases(
    accounts_by_id: dict[int, str],
    base_dir: Path,
    prepared_entries: dict[int, tuple[Path, os.stat_result]],
    *,
    actor_user_id: int,
) -> None:
    """Stat known other uid candidates only; never open or use them as votes.

    Residual/non-participant files are not reservation inputs.  Compare only
    their physical identities with the owner/actor preparation targets, without
    imposing task-input permissions, regular-file checks or roster filtering.
    """
    for other_uid, username in accounts_by_id.items():
        if other_uid == actor_user_id or other_uid in prepared_entries:
            continue
        candidate = _slug_path(base_dir, username)
        if not _prepare_path_present(candidate, username=username):
            continue
        try:
            entry = candidate.stat()
        except OSError as exc:
            _fail_from(
                "member_db_unreadable",
                f"prepare-pre known account candidate stat failed: {candidate}: {exc}",
                exc,
                member=username,
            )
        if not entry.st_ino:
            _fail(
                "member_db_unreadable",
                f"known account candidate physical identity unavailable: {candidate}",
                member=username,
            )
        identity = _identity_key(entry, candidate)
        for prepared_uid, (resolved, prepared_entry) in prepared_entries.items():
            if identity == _identity_key(prepared_entry, resolved):
                _fail(
                    "storage_conflict",
                    "prepare target shares a physical DB with another account uid: "
                    f"{prepared_uid} vs {other_uid}",
                    member=username,
                )


def _preflight_prepare_identities(
    *,
    canonical_info: object,
    task_id: str,
    user: dict,
    user_id: int,
    accounts_path: Path,
    owner_snapshot: dict,
    owner_username: str,
    base_dir: Path,
    owner_resolved: Path,
    owner_entry: os.stat_result,
) -> None:
    """Read-only prepare-pre identity/alias check before runtime.context.

    ``runtime.context`` may call screener_db(owner/actor, ensure=True) and
    migrate an existing path.  This function resolves the current locked
    identity facts from short mode=ro collab/accounts connections and rejects
    reserved names, cross-uid physical aliases and actor/owner path collisions
    before that side effect.  It never creates, migrates, reserves or yields;
    the later B1/borrowed read remains the final authority.
    """
    owner_uid = owner_snapshot.get("owner_user_id")
    if isinstance(owner_uid, bool) or not isinstance(owner_uid, int) or owner_uid <= 0:
        _fail("unconfirmed_task_state", "owner snapshot user id is invalid")
    if owner_snapshot.get("owner_username") != owner_username:
        _fail("identity_unconfirmed", "owner snapshot username changed")

    collab_path = Path(collaboration_mod.shared_store_path(canonical_info.dir))
    collab_resolved, collab_entry = _inspect_task_path(
        collab_path, base_dir, reason="collab_unreadable")
    state = _read_role_state_ro(collab_resolved, task_id)
    if state.get("classification") != "owner_match":
        _fail(
            "unconfirmed_task_state",
            f"prepare-pre task is not owner_match: {state.get('classification')!r}",
        )
    state_owner_uid, state_owner_username = _owner_identity(state)
    if state_owner_uid != owner_uid or state_owner_username != owner_username:
        _fail("identity_unconfirmed", "collab owner disagrees with the owner snapshot")

    accounts_by_id, casefold_ids = _read_accounts_enumeration(accounts_path)
    participants = state.get("participants") or []
    _validate_roster_identity(
        participants, accounts_by_id, casefold_ids, owner_username)
    _actor_participant(participants, user_id)

    actor_account = accounts_by_id.get(user_id)
    if not isinstance(actor_account, str) or not actor_account:
        _fail("member_unresolved", "actor has no current accounts row", participant=user_id)
    actor_slug = tasks_mod.slugify_screener(actor_account)
    if f"{actor_slug}.db".casefold() == collaboration_mod.DB_FILENAME.casefold():
        _fail(
            "storage_conflict",
            "actor slug collides with the reserved collaboration DB name",
            member=actor_account,
        )
    user_username = user.get("username") if isinstance(user, dict) else None
    if not isinstance(user_username, str) or not user_username:
        _fail("identity_unconfirmed", "authenticated username is missing")
    context_actor_slug = tasks_mod.slugify_screener(user_username)
    if context_actor_slug != actor_slug:
        _fail(
            "identity_unconfirmed",
            "verified username slug differs from the current accounts row",
            member=actor_account,
        )
    owner_slug = tasks_mod.slugify_screener(owner_username)
    if user_id == owner_uid and context_actor_slug != owner_slug:
        _fail(
            "identity_unconfirmed",
            "owner actor would be prepared under a non-owner slug",
            member=actor_account,
        )
    actor_path = base_dir / f"{context_actor_slug}.db"

    claims: dict[tuple, tuple] = {}
    _claim_path(claims, collab_resolved, collab_entry, ("collab",))
    _claim_path(
        claims, owner_resolved, owner_entry, ("account", owner_uid),
        member=owner_username)
    accounts_resolved, accounts_entry = _inspect_accounts_path(accounts_path)
    _claim_path(claims, accounts_resolved, accounts_entry, ("accounts",))

    participant_paths: list[Path] = []
    prepared_entries = {owner_uid: (owner_resolved, owner_entry)}
    if user_id == owner_uid:
        if actor_path != owner_resolved:
            _fail(
                "identity_unconfirmed",
                "owner actor path is not the prechecked owner catalog path",
                member=actor_account,
            )
    elif _prepare_path_present(actor_path, username=actor_account):
        actor_resolved, actor_entry = _inspect_task_path(
            actor_path, base_dir, reason="member_db_unreadable", member=actor_account)
        _claim_path(
            claims, actor_resolved, actor_entry, ("account", user_id),
            member=actor_account)
        participant_paths.append(actor_resolved)
        prepared_entries[user_id] = (actor_resolved, actor_entry)
    # Missing current-actor DB is the only path allowed to be prepared by context.

    for entry in participants:
        participant_id = entry.get("user_id")
        if not isinstance(participant_id, int):
            _fail("unconfirmed_task_state", "participant has no valid user_id")
        username = accounts_by_id.get(participant_id)
        if not isinstance(username, str) or not username:
            _fail(
                "member_unresolved",
                "participant has no current accounts row",
                participant=participant_id,
            )
        path = base_dir / f"{tasks_mod.slugify_screener(username)}.db"
        if participant_id == owner_uid:
            if path != owner_resolved:
                _fail(
                    "identity_unconfirmed",
                    "owner participant path differs from the owner snapshot path",
                    member=username,
                )
            participant_paths.append(owner_resolved)
            continue
        if participant_id == user_id or not _prepare_path_present(path, username=username):
            continue
        resolved, stat_entry = _inspect_task_path(
            path, base_dir, reason="member_db_unreadable", member=username)
        _claim_path(
            claims, resolved, stat_entry, ("account", participant_id),
            member=username)
        participant_paths.append(resolved)

    task_paths = tuple(dict.fromkeys(
        [collab_resolved, owner_resolved, *participant_paths]
    ))
    _reject_accounts_alias(accounts_resolved, task_paths)
    _reject_other_account_prepare_aliases(
        accounts_by_id, base_dir, prepared_entries, actor_user_id=user_id)


def _reserve_failure_match(
    failed_path: object,
    candidates: list[tuple[Path, str, str | None]],
) -> tuple[str, str | None] | None:
    """Map B1's read-only failed_path to a typed input reason.

    B1 FIX-R1 attaches ``failed_path`` to the original exception; unknown/absent
    paths must not be guessed as a member failure.
    """
    if failed_path is None:
        return None
    try:
        failed_key = os.path.normcase(
            os.fspath(Path(failed_path).resolve(strict=False)))
    except (OSError, TypeError, ValueError):
        return None
    for path, reason, member in candidates:
        try:
            key = os.path.normcase(os.fspath(Path(path).resolve(strict=False)))
        except (OSError, TypeError, ValueError):
            continue
        if key == failed_key:
            return reason, member
    return None


def _write_window_plan(
    ctx: WorkflowContext,
    *,
    reserved: ReservedWriteConnections,
    operation: TeamOperation,
    keys: tuple[str, ...],
) -> KeyedReadPlan:
    """Build the P08 keyed read plan on the reserved collab barrier connection.

    Runs strictly after the B1 enter (collab ``BEGIN IMMEDIATE``) and before
    any reserve; every provisional holder reader opened by the planner is
    rolled back and closed inside ``build_keyed_read_plan`` before it returns.
    Task-level discovery unavailability is a typed
    ``completion_source_unresolved`` failure (mirroring the P09 read-scope
    mapping); operation/key shape errors propagate unchanged as programming
    misuse.  The plan only selects the retained set; it grants nothing.
    """
    try:
        return build_keyed_read_plan(
            ctx, collab_conn=reserved.collab_conn, operation=operation,
            item_keys=keys)
    except KeyedPlanUnavailableError as exc:
        _fail(
            "completion_source_unresolved",
            f"write-window keyed plan unavailable: {exc}")
    except RuntimeError as exc:
        # NOTE-2 同型防御：P08 借用连接合同门（native/活动事务/Row 工厂）的
        # 裸 RuntimeError 编程违约转 typed connection_contract；B1 屏障连接
        # 恒满足该门，本支对内不可达。
        _fail(
            "connection_contract",
            f"write-window plan collab connection contract: {exc}")


def _verify_plan_binding(
    plan: KeyedReadPlan, *, task_id: str, owner_user_id: int,
) -> None:
    """The plan's task binding must match the reserved collaboration facts."""
    binding = plan.binding
    if binding.task_id != task_id:
        _fail(
            "unconfirmed_task_state",
            f"plan binding task {binding.task_id!r} differs from the canonical "
            f"task {task_id!r}",
        )
    if binding.owner_user_id != owner_user_id:
        _fail(
            "identity_unconfirmed",
            "plan binding owner differs from the reserved roster owner",
            participant=binding.owner_user_id,
        )


def _verify_witnesses(
    plan: KeyedReadPlan,
    *,
    task_dir: Path,
    collab_conn: sqlite3.Connection,
    connections: dict[Path, sqlite3.Connection],
) -> None:
    """Final same-connection re-verification of every selected witness (C07).

    For each plan witness: re-read the F2 registration on the reserved collab
    connection (source identity, generation, library), re-resolve the physical
    source path, then re-read the F1 seal on the retained connection and check
    the original seal id/content-hash/payload-hash/sealed-at quadruple.

    Failure classes (never a second reserve in this manager):
    - registration/seal row gone, generation/library/physical path changed,
      quadruple mismatch, or the F1 table no longer prepared on a holder that
      carried a seal -> ``_WriteWindowDrift`` (pre-writer full-attempt
      restart; the stale plan must not select the retained set);
    - transient read failures (registry/F1 unreadable) -> typed
      ``completion_source_unresolved`` failure with no restart;
    - contract violations (missing reserved connection, F1 connection
      contract) -> typed ``connection_contract`` programming failure.
    """
    instance = plan.binding.task_instance_id
    for (stage, key), witness in plan.witnesses.items():
        if witness.source_path is None:
            conn = collab_conn
        else:
            conn = connections.get(witness.source_path)
            if conn is None:
                _fail(
                    "connection_contract",
                    f"witness source was not reserved: {witness.source_path}",
                )
        if witness.registration_id is not None:
            try:
                strong = read_source_on(
                    collab_conn, registration_id=witness.registration_id)
            except TerminalRegistryUnavailable as exc:
                _fail_from(
                    "completion_source_unresolved",
                    f"witness registration re-read failed for "
                    f"{witness.registration_id!r}: {exc}",
                    exc,
                )
            if strong is None:
                raise _WriteWindowDrift(
                    f"witness registration disappeared: "
                    f"{witness.registration_id!r}")
            if strong.get("generation") != witness.generation:
                raise _WriteWindowDrift(
                    f"witness source generation drifted for "
                    f"{witness.registration_id!r}: "
                    f"{strong.get('generation')!r} != {witness.generation!r}")
            if strong.get("source_library_id") != witness.library_id:
                raise _WriteWindowDrift(
                    f"witness source library drifted for "
                    f"{witness.registration_id!r}")
            try:
                resolved = resolve_source_identity(
                    task_dir=task_dir, source=strong)
            except SourceIdentityIssue as exc:
                raise _WriteWindowDrift(
                    f"witness physical source no longer resolves: "
                    f"{witness.source_path}") from exc
            if resolved != witness.source_path:
                raise _WriteWindowDrift(
                    f"witness physical source path drifted: "
                    f"{resolved} != {witness.source_path}")
        try:
            record = read_seal_on(
                conn, task_instance_id=instance, stage=stage, item_key=key)
        except TerminalFactsUnavailable as exc:
            if exc.reason == "connection_contract":
                raise
            if exc.reason == "not_prepared":
                # The holder carried this seal at plan time; a now-missing F1
                # table means the physical file was replaced/reverted.
                raise _WriteWindowDrift(
                    f"witness F1 storage no longer prepared for "
                    f"({stage}, {key})") from exc
            _fail_from(
                "completion_source_unresolved",
                f"witness seal re-read failed for ({stage}, {key}): {exc}",
                exc,
            )
        if record is None or (
                record["seal_id"] != witness.seal_id
                or record["content_sha256"] != witness.content_sha256
                or record["payload_sha256"] != witness.payload_sha256
                or record["sealed_at"] != witness.sealed_at):
            raise _WriteWindowDrift(
                f"witness seal quadruple drifted for ({stage}, {key})")


def _verify_owner_domain(
    owner_conn: sqlite3.Connection, keys: tuple[str, ...],
) -> None:
    """Requested keys must be canonical owner-domain keys (bounded keyed SELECT).

    C07: canonical/duplicate/title-pending status comes from the retained
    owner connection, never from the client's claimed domain.  Out-of-domain
    keys (unknown, duplicate, or title-pending) are rejected as client-domain
    misuse before the yield, mirroring ``normalize_item_keys``' ValueError
    contract for bad key shapes.
    """
    try:
        rows = canonical_item_rows_on(owner_conn, item_keys=keys)
    except sqlite3.ProgrammingError:
        raise
    except OwnerKeyedDomainUnavailableError as exc:
        _fail_from(
            "owner_db_unreadable",
            f"retained owner keyed domain read failed: {exc}",
            exc,
        )
    except RuntimeError as exc:
        # NOTE-2 同型防御：P00 owner 域读取连接门（非 Row 工厂/无活动事务）
        # 的裸 RuntimeError 转 typed connection_contract。
        _fail_from(
            "connection_contract",
            f"owner keyed domain read connection contract: {exc}",
            exc,
        )
    missing = [key for key in keys if key not in rows]
    if missing:
        raise ValueError(
            f"item keys outside the owner canonical writable domain "
            f"(unknown, duplicate or title-pending): {missing!r}")


def _prepare_inputs(
    *,
    ctx: WorkflowContext,
    task_id: str,
    user_id: int,
    accounts_path: Path,
    target_kind: Literal["actor", "collab"],
    owner_pre_identity: tuple,
    reserved: ReservedWriteConnections,
    operation: TeamOperation,
    keys: tuple[str, ...],
    plan: KeyedReadPlan,
) -> TeamWriteInputs:
    """Build and fully validate one reservation attempt; never yields.

    ``plan`` is the P08 keyed read plan already built on ``reserved``'s collab
    barrier connection by the caller (after the B1 enter, before this call).
    Inside this function the fixed window order continues: identity
    preparation with short provisional readers that are fully closed inside
    their helpers (``readers closed``), then the single ``reserve(owner,
    others)`` with the plan-selected retained set, then the final
    re-verification on the retained connections, target selection, and the
    frozen-``TeamWriteInputs`` construction.
    """
    try:
        base_dir = Path(ctx.info.dir).resolve(strict=True)
    except OSError as exc:
        _fail("unconfirmed_task_state", f"task directory is unreadable: {exc}")

    collab_resolved, collab_entry = _inspect_task_path(
        Path(ctx.collaboration_db), base_dir, reason="collab_unreadable")

    state = _read_role_state(reserved.collab_conn, task_id)
    if state.get("classification") != "owner_match":
        _fail(
            "unconfirmed_task_state",
            f"task is not an owner_match collaboration: {state.get('classification')!r}",
        )
    owner_user_id, owner_username = _owner_identity(state)

    pre_by_id, pre_casefold = _read_accounts_enumeration(accounts_path)
    _validate_roster_identity(
        state.get("participants") or [], pre_by_id, pre_casefold, owner_username)

    owner_account = pre_by_id.get(owner_user_id)
    if not isinstance(owner_account, str) or not owner_account:
        _fail(
            "member_unresolved",
            "owner has no current accounts row",
            participant=owner_user_id,
        )
    owner_path = _slug_path(base_dir, owner_username)
    owner_resolved, owner_entry = _inspect_task_path(
        owner_path, base_dir, reason="owner_db_unreadable", member=owner_username)
    if _identity_key(owner_entry, owner_resolved) != owner_pre_identity:
        _fail(
            "owner_db_unreadable",
            "owner catalog physical identity changed after the pre-context check; "
            "refusing a recreated/missing owner DB",
            member=owner_username,
        )

    actor_account = pre_by_id.get(user_id)
    if not isinstance(actor_account, str) or not actor_account:
        _fail(
            "member_unresolved",
            "actor has no current accounts row",
            participant=user_id,
        )
    if user_id == owner_user_id:
        actor_resolved, actor_entry = owner_resolved, owner_entry
    else:
        actor_path = _slug_path(base_dir, actor_account)
        actor_resolved, actor_entry = _inspect_task_path(
            actor_path, base_dir, reason="member_db_unreadable", member=actor_account)

    try:
        ctx_owner_resolved = Path(ctx.owner_db).resolve(strict=False)
        ctx_actor_resolved = Path(ctx.actor_db).resolve(strict=False)
    except OSError as exc:
        _fail("identity_unconfirmed", f"context DB path resolution failed: {exc}")
    if ctx_owner_resolved != owner_resolved or ctx_actor_resolved != actor_resolved:
        _fail(
            "identity_unconfirmed",
            "WorkflowContext owner/actor path differs from the locked account mapping",
        )

    _verify_plan_binding(plan, task_id=task_id, owner_user_id=owner_user_id)

    # Live voters are reserved only when the plan actually has live
    # dependencies (requested keys without a completion witness).  A fully
    # witnessed request reserves no member DB beyond owner/actor — if a
    # witness then drifts in final verification, live votes become necessary
    # and the retained set would have to expand, which restarts the whole
    # attempt before the writer instead of dynamically widening the lock.
    #
    # 裁定 A（2026-11-20 主控裁，P16 上报缺陷 records/defect_seam_member_coding.md
    # 的接缝修）：**chain 型 operation**（stage 规则 chain_target≠None，如
    # member_coding/coding、member_vote/fulltext）额外无条件保留 required_screener
    # 成员库——P12 链检查（team_write_checks._require_chain → team_chain_view →
    # scope.member_vote_snapshot）在 witnessed 链上仍逐成员读 stage1/stage2 活票，
    # live_dependencies 为空时成员库零保留会 connection_contract 503（修复前
    # member_coding 经真实窗口必然崩死锁：票不全 409 awaiting、票全封版 503）。
    # 非 chain 型（chain_target=None，如 member_vote:screen）与未知 op（P10 原始
    # 窗口直调的未注册名）保持既有"仅 live 依赖才保留"语义——P10 已锁行为零变化。
    member_paths: dict[int, Path] = {}
    member_entries: dict[int, tuple[Path, os.stat_result]] = {}
    missing_members: set[int] = set()
    if plan.live_dependencies or _chain_targeted_operation(operation):
        for entry in state.get("participants") or []:
            if entry.get("required_screener") is not True:
                continue
            participant_id = entry.get("user_id")
            if not isinstance(participant_id, int):
                _fail(
                    "unconfirmed_task_state",
                    "required participant has no valid user_id")
            if participant_id == owner_user_id:
                member_paths[participant_id] = owner_resolved
                member_entries[participant_id] = (owner_resolved, owner_entry)
                continue
            username = pre_by_id.get(participant_id)
            if not isinstance(username, str) or not username:
                _fail(
                    "member_unresolved",
                    "required participant has no current accounts row",
                    participant=participant_id,
                )
            if participant_id == user_id:
                member_paths[participant_id] = actor_resolved
                member_entries[participant_id] = (actor_resolved, actor_entry)
                continue
            candidate = _slug_path(base_dir, username)
            classified = _required_member_path(
                candidate, base_dir, username=username)
            if classified is None:
                missing_members.add(participant_id)
            else:
                member_paths[participant_id] = classified[0]
                member_entries[participant_id] = classified

    accounts_resolved, accounts_entry = _inspect_accounts_path(accounts_path)

    # Plan-selected witness source paths join the reserve set.  Paths already
    # covered by owner/actor/member inputs are skipped (an actor DB holding
    # its own seal is one legal physical DB, not a storage conflict); only
    # genuinely new holder paths are inspected.  No purpose claim is staked:
    # witness holders are actor-source DBs and B1's own physical-identity
    # dedup keeps one connection per physical file.
    witness_entries: dict[Path, tuple[Path, os.stat_result]] = {}
    known_paths = {
        collab_resolved, owner_resolved, actor_resolved, *member_paths.values(),
    }
    for witness_path in plan.selected_paths:
        if witness_path in known_paths or witness_path in witness_entries:
            continue
        witness_resolved, witness_entry = _inspect_task_path(
            witness_path, base_dir, reason="member_db_unreadable")
        witness_entries[witness_path] = (witness_resolved, witness_entry)

    claims: dict[tuple, tuple] = {}
    _claim_path(claims, collab_resolved, collab_entry, ("collab",))
    _claim_path(
        claims, owner_resolved, owner_entry, ("account", owner_user_id),
        member=owner_username)
    _claim_path(claims, accounts_resolved, accounts_entry, ("accounts",))
    _claim_path(
        claims, actor_resolved, actor_entry, ("account", user_id),
        member=actor_account)
    for participant_id, (path, entry) in member_entries.items():
        username = pre_by_id.get(participant_id) or str(participant_id)
        _claim_path(
            claims, path, entry, ("account", participant_id), member=username)

    task_paths = tuple(dict.fromkeys(
        [collab_resolved, owner_resolved, actor_resolved]
        + [path for path, _ in member_entries.values()]
        + list(witness_entries.keys())
    ))
    _reject_accounts_alias(accounts_resolved, task_paths)

    other_paths: list[Path] = []
    for path in [accounts_resolved, actor_resolved, *member_paths.values(),
                 *witness_entries.keys()]:
        if path != owner_resolved and path not in other_paths:
            other_paths.append(path)
    reserve_candidates: list[tuple[Path, str, str | None]] = [
        (collab_resolved, "collab_unreadable", None),
        (owner_resolved, "owner_db_unreadable", owner_username),
        (accounts_resolved, "accounts_unreadable", None),
        (actor_resolved, "member_db_unreadable", actor_account),
    ]
    for participant_id, member_path in member_paths.items():
        reserve_candidates.append((
            member_path,
            "member_db_unreadable",
            pre_by_id.get(participant_id) or str(participant_id),
        ))
    for witness_resolved, _ in witness_entries.values():
        reserve_candidates.append((
            witness_resolved,
            "member_db_unreadable",
            None,
        ))
    try:
        reserved.reserve(owner_resolved, other_paths)
    except sqlite3.ProgrammingError:
        raise
    except Exception as exc:
        # B1 FIX-R1 exposes the read-only failed_path on the reservation
        # session while preserving this original exception object.
        failed_path = getattr(reserved, "failed_path", None)
        match = _reserve_failure_match(failed_path, reserve_candidates)
        if match is None:
            _fail_from(
                "connection_contract",
                "reservation failed without an attributable input path "
                f"(failed_path={failed_path!r}): {type(exc).__name__}: {exc}",
                exc,
            )
        reason, member = match
        _fail_from(
            reason,
            f"reservation failed at {failed_path}: {type(exc).__name__}: {exc}",
            exc,
            member=member,
        )

    connection_paths = tuple(dict.fromkeys(
        [collab_resolved, owner_resolved, accounts_resolved, actor_resolved]
        + list(member_paths.values())
        + list(witness_entries.keys())
    ))
    connections = _connection_map(reserved, connection_paths)

    scope = borrowed_team_read_scope(
        ctx.info,
        accounts_path=accounts_resolved,
        collab_path=collab_resolved,
        owner_path=owner_resolved,
        member_paths=member_paths,
        connections=connections,
        missing_members=frozenset(missing_members),
    )

    # Attach the frozen plan and the retained witness connections so the
    # keyed views (and the later require_team_write layer) read holders and
    # collab-held seals through this window's own transactions.  Every
    # selected witness path is in the reserve set, so each holder read reuses
    # the reserved rw connection — no second reader is ever opened for it.
    scope.attach_keyed_plan(
        plan,
        {path: connections[path] for path in plan.selected_paths},
        frozenset(),
    )

    final_state = scope.role_state()
    if final_state != state:
        _fail(
            "unconfirmed_task_state",
            "collaboration roster changed inside the reserved window",
        )
    final_by_id, final_casefold = scope.accounts()
    relevant_ids = {
        owner_user_id,
        user_id,
        *(entry.get("user_id") for entry in final_state.get("participants") or []
          if isinstance(entry.get("user_id"), int)),
    }
    for participant_id in relevant_ids:
        if pre_by_id.get(participant_id) != final_by_id.get(participant_id):
            _fail(
                "identity_unconfirmed",
                "reserved accounts read differs from the enumeration read",
                participant=participant_id,
            )
    _validate_roster_identity(
        final_state.get("participants") or [],
        final_by_id,
        final_casefold,
        owner_username,
    )
    _actor_participant(final_state.get("participants") or [], user_id)

    final_actor_account = final_by_id.get(user_id)
    final_owner_account = final_by_id.get(owner_user_id)
    if not isinstance(final_actor_account, str) or not final_actor_account:
        _fail("member_unresolved", "actor account row missing after reservation",
              participant=user_id)
    if not isinstance(final_owner_account, str) or not final_owner_account:
        _fail("member_unresolved", "owner account row missing after reservation",
              participant=owner_user_id)
    if _slug_path(base_dir, final_actor_account) != actor_resolved:
        _fail(
            "identity_unconfirmed",
            "actor account slug no longer maps to the reserved actor DB",
            participant=user_id,
        )
    if _slug_path(base_dir, final_owner_account) != owner_resolved:
        _fail(
            "identity_unconfirmed",
            "owner account slug no longer maps to the reserved owner DB",
            participant=owner_user_id,
        )

    owner_latched = scope.owner_latched()
    if task_kind(final_state, owner_latched=owner_latched) != "team":
        _fail(
            "unconfirmed_task_state",
            "reserved collaboration state no longer classifies as a team task",
        )

    if user_id != owner_user_id:
        owner_revision = _owner_catalog_revision(connections[owner_resolved])
        _member_marker_revision(
            connections[actor_resolved], owner_resolved.name, owner_revision)

    # Final same-connection re-verification (C07): requested keys against the
    # retained owner domain, and every plan witness (source generation,
    # physical path, library, original seal id/hash/time) on the retained
    # connections.  Drift raises _WriteWindowDrift and restarts the whole
    # attempt before any writer; read failures fail typed with no restart.
    # P18 接缝补丁（沿用 P16 seam-patch 先例）：``team_management`` 操作的
    # 键域是**服务端构造的机械载体**（P14 哨兵键 ``__team_management__``，
    # 非客户端申报域、绝不进业务票域）——C07 域校验只针对客户端键申报，
    # 对服务端管理操作跳过（成员写路径行为零变化）。
    if operation.name != "team_management":
        _verify_owner_domain(connections[owner_resolved], keys)
    _verify_witnesses(
        plan, task_dir=base_dir,
        collab_conn=reserved.collab_conn, connections=connections)

    target_path = actor_resolved if target_kind == "actor" else collab_resolved
    try:
        reserved.select_target(target_path)
    except RuntimeError as exc:
        _fail(
            "connection_contract",
            f"target is not a registered reservation source: {target_path}: {exc}",
        )
    target_conn = reserved.target_conn
    if target_conn is None:
        _fail("connection_contract", "target connection was not selected")

    return TeamWriteInputs(
        ctx=ctx,
        scope=scope,
        collab_conn=reserved.collab_conn,
        owner_conn=connections[owner_resolved],
        actor_conn=connections[actor_resolved],
        target_conn=target_conn,
        connections=reserved.connections,
        roster=tuple(dict(entry) for entry in final_state["participants"]),
        operation=operation,
        item_keys=keys,
        plan=plan,
        _reserved=reserved,
    )


def _chain_targeted_operation(operation) -> bool:
    """裁定 A 辅助：operation 的 stage 规则是否带链依赖（``chain_target``≠None）。

    规则表属 P12（``team_write_checks._OPERATIONS``，只读复用、绝不复制）；
    未知 name/stage 组合（P10 原始窗口直调的未注册名）返回 False——保持既有
    "仅 live 依赖才保留成员库"语义，不改变 P10 已锁行为。
    """
    try:
        from custom_backend.team_write_checks import _validated_stage
        _, rule = _validated_stage(operation)
    except (TypeError, ValueError, ImportError):
        return False
    return getattr(rule, "chain_target", None) is not None


@contextmanager
def retained_team_write_inputs(
    runtime: WorkflowRuntime,
    info: object,
    user: dict,
    *,
    target_kind: Literal["actor", "collab"],
    operation: TeamOperation,
    item_keys: Iterable[str],
) -> Iterator[TeamWriteInputs]:
    """Reserve and validate team write inputs; commit target only on normal exit.

    ``target_kind`` is an internal server-side mapping ('actor' or 'collab'),
    never a client path; ``operation`` is the server-selected
    ``TeamOperation`` and ``item_keys`` the normalized request key domain
    (shape errors raise ValueError/TypeError before the task lock, mirroring
    the C02 key-domain contract).  The B1 reservation owns
    commit/rollback/close; this function does not commit, notify, or run any
    business writer.  Fixed window order per attempt: B1 enter (collab
    ``BEGIN IMMEDIATE``) -> plan on the barrier connection -> all provisional
    readers closed -> one ``reserve(owner, others)`` -> final re-verification
    -> yield -> the reservation manager's single target commit.  The plan
    never authorizes the writer; only plan/marker drift before the writer may
    restart the whole attempt (at most ``_MAX_ATTEMPTS``).
    """
    if target_kind not in ("actor", "collab"):
        raise ValueError("target_kind must be 'actor' or 'collab'")
    if not isinstance(operation, TeamOperation):
        raise TypeError(
            "operation must be coscreen.team_flow_types.TeamOperation")
    keys = normalize_item_keys(item_keys)
    user_id = user.get("user_id") if isinstance(user, dict) else None
    if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
        _fail("identity_unconfirmed", "authenticated user_id is missing or invalid")

    canonical_info = runtime.task_lookup(_task_id_from_info(info))
    task_id = getattr(canonical_info, "task_id", None)
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("canonical task lookup returned no task_id")
    accounts_path = (Path(runtime.data_dir) / _ACCOUNTS_FILENAME).resolve()

    with runtime.lock(task_id):
        # Zero-recreation guard: context() may call screener_db(owner, ensure=True)
        # and create a missing owner catalog.  Read the trusted owner snapshot
        # path read-only before context; the final locked check also verifies the
        # same physical identity, so a precheck is not final authorization.
        try:
            pre_base = Path(canonical_info.dir).resolve(strict=True)
        except OSError as exc:
            _fail("unconfirmed_task_state", f"task directory is unreadable: {exc}")
        pre_collab_path = Path(collaboration_mod.shared_store_path(canonical_info.dir))
        try:
            owner_snapshot = collaboration_mod.get_owner(
                pre_collab_path,
                task_id=task_id,
            )
        except collaboration_mod.CollaborationStoreCorruptError:
            raise
        except collaboration_mod.OwnerUnavailableError as exc:
            _fail("collab_unreadable", f"collaboration owner lookup failed: {exc}")
        if owner_snapshot is None:
            # Missing collab is collab_unreadable; an existing but unconfirmed
            # owner binding is unconfirmed_task_state.  Only an explicit
            # legacy/personal call may stop here with ValueError.
            collab_resolved, _ = _inspect_task_path(
                pre_collab_path, pre_base, reason="collab_unreadable")
            state = _read_role_state_ro(collab_resolved, task_id)
            classification = state.get("classification")
            if classification == "legacy_uninitialized":
                raise ValueError(
                    "retained_team_write_inputs requires a team task; "
                    "legacy/personal task has no owner catalog to prepare")
            _fail(
                "unconfirmed_task_state",
                "collaboration owner snapshot unavailable; "
                f"classification={classification!r}",
            )
        pre_owner_username = owner_snapshot.get("owner_username")
        if not isinstance(pre_owner_username, str) or not pre_owner_username:
            _fail("roster_unreadable", "owner snapshot username is missing")
        pre_owner_slug = tasks_mod.slugify_screener(pre_owner_username)
        if f"{pre_owner_slug}.db".casefold() == collaboration_mod.DB_FILENAME.casefold():
            _fail(
                "storage_conflict",
                "owner slug collides with the reserved collaboration DB name",
                member=pre_owner_username,
            )
        pre_owner_resolved, pre_owner_entry = _inspect_task_path(
            pre_base / f"{pre_owner_slug}.db",
            pre_base,
            reason="owner_db_unreadable",
            member=pre_owner_username,
        )
        if not os.access(pre_owner_resolved, os.R_OK | os.W_OK):
            _fail(
                "owner_db_unreadable",
                "owner catalog is not readable/writable before context preparation",
                member=pre_owner_username,
            )
        owner_pre_identity = _identity_key(pre_owner_entry, pre_owner_resolved)

        _preflight_prepare_identities(
            canonical_info=canonical_info,
            task_id=task_id,
            user=user,
            user_id=user_id,
            accounts_path=accounts_path,
            owner_snapshot=owner_snapshot,
            owner_username=pre_owner_username,
            base_dir=pre_base,
            owner_resolved=pre_owner_resolved,
            owner_entry=pre_owner_entry,
        )

        ctx = runtime.context(task_id, user)
        if ctx.task_kind != "team":
            raise ValueError(
                f"retained_team_write_inputs requires a team task, got {ctx.task_kind!r}")
        if ctx.info.task_id != task_id:
            _fail("unconfirmed_task_state", "WorkflowContext task id differs from canonical id")

        for attempt in range(_MAX_ATTEMPTS):
            # Path-only catalog sync is allowed only before any reservation.
            runtime.sync_catalog(ctx)
            manager = retained_write_connections(ctx.collaboration_db)
            try:
                reserved = manager.__enter__()
            except FileNotFoundError as exc:
                _fail("collab_unreadable", f"collaboration store missing: {exc}")
            except sqlite3.ProgrammingError:
                raise
            except (sqlite3.Error, OSError) as exc:
                _fail("collab_unreadable", f"collaboration store open failed: {exc}")

            try:
                # Fixed window order: the plan is built strictly after the
                # B1 enter (collab BEGIN IMMEDIATE) and before any reserve;
                # build_keyed_read_plan opens and fully releases its own
                # provisional holder readers before returning.
                plan = _write_window_plan(
                    ctx, reserved=reserved, operation=operation, keys=keys)
                inputs = _prepare_inputs(
                    ctx=ctx,
                    task_id=task_id,
                    user_id=user_id,
                    accounts_path=accounts_path,
                    target_kind=target_kind,
                    owner_pre_identity=owner_pre_identity,
                    reserved=reserved,
                    operation=operation,
                    keys=keys,
                    plan=plan,
                )
            except (_CatalogMarkerDrift, _WriteWindowDrift) as exc:
                # Pre-writer drift only: the whole attempt (reservation
                # manager included) restarts with a fresh plan.  Reserve,
                # writer and commit failures are never replayed, and a
                # witness failure inside one manager never triggers a second
                # reserve in that same manager.
                try:
                    manager.__exit__(type(exc), exc, exc.__traceback__)
                except (_CatalogMarkerDrift, _WriteWindowDrift):
                    pass
                cleanup_errors = reserved.cleanup_errors
                if cleanup_errors:
                    _fail_from(
                        "connection_contract",
                        "drift retry aborted after reservation cleanup errors: "
                        f"{cleanup_errors!r}",
                        exc,
                    )
                if attempt + 1 >= _MAX_ATTEMPTS:
                    if isinstance(exc, _WriteWindowDrift):
                        raise HTTPException(
                            status_code=409,
                            detail={
                                "code": "write_plan_drift",
                                "message_key": "team.flow.write_plan_drift",
                                "params": {},
                            },
                        ) from exc
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "code": "catalog_sync_stale",
                            "message_key": "team.flow.catalog_sync_stale",
                            "params": {},
                        },
                    ) from exc
                continue
            except BaseException:
                manager.__exit__(*sys.exc_info())
                raise

            # Yield is deliberately outside the marker retry try/except:
            # caller writer/commit exceptions are never replayed.
            try:
                yield inputs
            except BaseException:
                manager.__exit__(*sys.exc_info())
                raise
            else:
                manager.__exit__(None, None, None)
                return
