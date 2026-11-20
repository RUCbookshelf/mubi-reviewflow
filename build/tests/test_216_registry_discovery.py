"""P03/C04 narrow-discovery contract tests for the team terminal registry.

Test-only file.  It is authored statically: the author does not import the
product, execute SQL, run any test, or touch the real database/product/API.
Execution is left to an independent runner after re-impact and review.

Product under test (review-PASSED candidate, staged box):

- E:/rf216-scratch/p03-terminal-registry-n214716ac9a/candidate/
  team_terminal_registry.py
- SHA256 1d85c05d3aaf5d40902665a46b865a929f5ee0199cc60265be30ae2b28bd9904
  (verified by the author; re-verified at load time by the runner when the
  staged box path is used).

Authoritative expectation sources (must be read together):

- docs/superpowers/plans/2026-11-16-2.1.6-team-work-packages.md (P03, the
  five named cases, the valid-or-issue xor rule)
- docs/superpowers/plans/2026-11-16-2.1.6-team-contracts.md (C04 narrow
  reads; C02 keys/connections/errors common contract)

Runner notes:

- Module resolution order: ``RF216_P03_REGISTRY_PATH`` env override, then
  the staged candidate box (with a hard SHA256 pin), then the in-tree
  ``coscreen.team_terminal_registry`` (post-merge runs).
- Baseline expectation wording (candidate2): when resolution lands on a
  baseline module -- a pre-merge in-tree ``coscreen.team_terminal_registry``
  or an override that lacks the four C04 discovery symbols -- the expected
  failure mode is an ``AssertionError`` raised by ``_load_registry_module``'s
  ``hasattr`` guard, firing inside ``setUp`` for every test of this class
  (a module-contract authoring error, not a per-test typed registry error).
  A drifted staged-candidate SHA256 pin fails the same way (load-time
  ``AssertionError`` in ``setUp``).
- Repo root for ``coscreen.team_flow_types``: ``RF216_REPO_ROOT`` env
  override, else ``Path(__file__).resolve().parents[2]``.
- Every test class/method/helper symbol in this file was run through the
  real GitNexus CLI ``impact --direction upstream`` against
  ``--file build/tests/test_216_registry_discovery.py`` (24/24 returned
  not-found/UNKNOWN; the original 23 raws in the author box
  E:/rf216-scratch/p03-registry-tests-20261118-b7c4e2a1/impact-raw/,
  the one candidate2 addition in
  E:/rf216-scratch/p03-registry-tests-20261118-b7c4e2a1/candidate2/impact-raw/).
  Disclosure: 20 symbols were checked before the first write; the three
  dunder methods ``__init__``/``__getattr__``/``execute`` were missed in
  the initial planning pass and were checked immediately after the first
  write, before the static freeze.  Candidate2 (this revision, fixing the
  OC-DS review findings) adds exactly one new symbol, the Method
  ``test_bad_type_generation_rows_keyset_coverage``; its CLI impact check
  was run BEFORE the method was written (raw
  24-Method-test_bad_type_generation_rows_keyset_coverage.raw in
  candidate2/impact-raw/), and no new helper, nested def, or lambda was
  added, so no further CLI checks were required.

Frozen behavior pins (from the reviewed candidate):

- Non-native connection objects, connections without an active caller
  transaction, and connections without ``row_factory is sqlite3.Row``
  uniformly raise ``TerminalRegistryUnavailable`` with reason
  ``connection_contract`` (a ``RuntimeError``).  This deliberately pins the
  candidate's uniform typed-error shape; unlike the P00 owner-domain
  module, this module does not raise a bare ``TypeError`` for non-native
  connections.  Closed native connections propagate
  ``sqlite3.ProgrammingError`` unchanged (C02).
- Coverage-side damage (adoption document/time/type damage) is classified
  through ``coverage_status``/``coverage_issue``/``coverage_evidence`` and
  never denies the already-proven binding; identity-side damage is a typed
  ``corrupt`` rejection.

Honest unreachable-branch declaration (reachable only by violating the
module's own layout contract, therefore not asserted and not claimed):

1. ``_source_discovery_row``'s ``registration_id is unavailable`` /
   ``source coordinates are unavailable`` branches (IndexError/KeyError on
   row item access) cannot fire through the public API: the scoped layout
   check pins the exact table SQL before any row is read, so ``SELECT *``
   always carries all contract columns.
2. The header's ``roster_revision`` issue channel ("roster_revision is not
   a readable integer") cannot fire through the public API for the same
   reason: the exact table SQL includes the ``roster_revision`` CHECK, and
   the scoped layout check runs first.
3. ``_record``'s missing-column corrupt branch is unreachable through the
   public API (exact table SQL pinned by the layout check).
4. ``_select``'s ``sqlite3.DatabaseError`` -> ``corrupt`` wrap is not
   exercised (it needs a mid-read database-level fault); the
   ``OperationalError`` -> ``unreadable`` wrap of the same wrapper is
   exercised via injected page failure.
5. The strong path ``_validate_stored_adoption`` does not type-check the
   evidence ``owner_user_id`` (Python ``True == 1``/``1.0 == 1`` passes its
   binding equality); only the narrow document check rejects bool/float
   impersonation.  Candidate2 stores integer 1 in the record column and
   gives each label its own bound source registration, so the impersonation
   equality genuinely holds and the bogus documents are valid in every
   other respect -- which equally means the strong path would ACCEPT those
   documents; per the task coverage spec the counterexample is asserted on
   the narrow header only and the strong-path behavior on that input is
   intentionally NOT pinned.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

STAGED_CANDIDATE_PATH = (
    "E:/rf216-scratch/p03-terminal-registry-n214716ac9a/candidate/"
    "team_terminal_registry.py"
)
CANDIDATE_SHA256 = (
    "1d85c05d3aaf5d40902665a46b865a929f5ee0199cc60265be30ae2b28bd9904"
)
EVIDENCE_SCHEMA_TEXT = "team_terminal_legacy_adoption.v1"

# 32 lowercase hex library identities (owner/source variants).
_OWNER_LIBRARY = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
_SOURCE_LIBRARY = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

# RFC3339 UTC millisecond Z timestamps (24 characters, product format).
_T_REGISTERED = "2026-11-16T09:00:00.000Z"
_T_ADOPTED_EARLIER = "2026-11-16T10:00:00.000Z"
_T_CREATED = "2026-11-16T12:00:00.000Z"
_T_ADOPTED_OK = "2026-11-16T13:00:00.000Z"
_T_RETIRED = "2026-11-16T14:00:00.000Z"

_REGISTRY_MODULE = None


class _FacadeConn:
    """Delegating wrapper that is NOT a ``sqlite3.Connection`` subclass.

    The realistic proxy shape C02 demands type checks to reject: even with
    ``__getattr__`` delegating ``in_transaction``/``row_factory`` to a real
    connection, ``issubclass(type(conn), sqlite3.Connection)`` is False.
    """

    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name):
        return getattr(self._inner, name)


class _ProbeConnection(sqlite3.Connection):
    """Real ``sqlite3.Connection`` subclass (C02-legal real subclass).

    Records every cursor the borrowed APIs create through ``execute`` and
    can inject one page-read failure.  Attributes are set after connect:
    ``cursors_log`` (list), ``fail_state`` (None or
    ``{"count": int, "fail_after": int}``).
    """

    def execute(self, sql, parameters=()):
        fail_state = getattr(self, "fail_state", None)
        if fail_state is not None:
            fail_state["count"] = fail_state.get("count", 0) + 1
            if fail_state["count"] > fail_state["fail_after"]:
                raise sqlite3.OperationalError("injected page-read failure")
        cursor = super().execute(sql, parameters)
        getattr(self, "cursors_log", []).append(cursor)
        return cursor


def _import_module_from_path(stem, path):
    """Import a module file with the repo root importable for coscreen.

    Candidate4 loader fix: register the module in ``sys.modules`` under
    ``stem`` BEFORE ``exec_module`` (importlib's documented recipe); on
    failure the registration is rolled back (only if it is still this
    module) before re-raising, leaving no half-registered module.  The
    registry module combines ``from __future__ import annotations`` with
    ``@dataclass`` fields, and CPython 3.12 ``dataclasses._is_type`` does
    an unguarded ``sys.modules.get(cls.__module__).__dict__`` lookup for
    string annotations, so executing it without the registration kills
    the first dataclass with ``AttributeError`` on ``None`` (all 9 tests
    of this file died that way in setUp under the packaged run; the
    product itself imports fine normally, 39/39 old regression green).
    """
    repo_root = os.environ.get("RF216_REPO_ROOT")
    if not repo_root:
        repo_root = str(Path(__file__).resolve().parents[2])
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    spec = importlib.util.spec_from_file_location(stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[stem] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        if sys.modules.get(stem) is module:
            del sys.modules[stem]
        raise
    return module


def _load_registry_module():
    """Load the reviewed registry candidate (or in-tree module post-merge)."""
    global _REGISTRY_MODULE
    if _REGISTRY_MODULE is not None:
        return _REGISTRY_MODULE
    override = os.environ.get("RF216_P03_REGISTRY_PATH")
    if override:
        module = _import_module_from_path(
            "team_terminal_registry_candidate", Path(override)
        )
    elif Path(STAGED_CANDIDATE_PATH).is_file():
        staged = Path(STAGED_CANDIDATE_PATH)
        actual = hashlib.sha256(staged.read_bytes()).hexdigest()
        assert actual == CANDIDATE_SHA256, (
            "staged registry candidate SHA256 drifted: %s != %s"
            % (actual, CANDIDATE_SHA256)
        )
        module = _import_module_from_path(
            "team_terminal_registry_candidate", staged
        )
    else:
        module = importlib.import_module("coscreen.team_terminal_registry")
    for name in (
        "read_task_discovery_header_on",
        "iter_source_discovery_on",
        "TaskDiscoveryHeader",
        "SourceDiscoveryRow",
    ):
        if not hasattr(module, name):
            raise AssertionError(
                "registry module lacks C04 discovery symbol: %s" % name
            )
    _REGISTRY_MODULE = module
    return module


def _open_registry_connection(db_path, factory=None):
    """Open a native connection honoring the C02 caller contract."""
    if factory is None:
        conn = sqlite3.connect(str(db_path), isolation_level=None)
    else:
        conn = sqlite3.connect(str(db_path), factory=factory,
                               isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("BEGIN")
    return conn


def _canonical_json_text(document):
    """Recompute the product's canonical JSON text for a document."""
    return json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _sha256_hex(text):
    """SHA256 of UTF-8 text as lowercase hex."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _evidence_document(*, task_id, task_instance_id, owner_user_id,
                       owner_library_id, sources, forward_coverage_start):
    """Build an adoption evidence document with the exact 11-key contract."""
    return {
        "schema": EVIDENCE_SCHEMA_TEXT,
        "task_instance_id": task_instance_id,
        "task_id": task_id,
        "owner_user_id": owner_user_id,
        "owner_library_id": owner_library_id,
        "sources": list(sources),
        "verification_scope": "full-history-check",
        "verification_method": "manual-row-comparison",
        "conflict_handling": "quarantine-diverged",
        "prior_history_unknown": True,
        "forward_coverage_start": forward_coverage_start,
    }


def _insert_protocol_row(conn, *, task_instance_id, task_id,
                         owner_user_id=411, owner_library_id=_OWNER_LIBRARY,
                         coverage_state="new_covered", roster_revision=0,
                         created_at=_T_CREATED, updated_at=None,
                         adopted_at=None, evidence_ref=None,
                         evidence_json=None, evidence_sha=None):
    """Directly INSERT one protocol row (synthetic seed, bypassing the API)."""
    if updated_at is None:
        updated_at = created_at
    conn.execute(
        "INSERT INTO main.team_task_protocol "
        "(task_instance_id, task_id, owner_user_id, owner_library_id, "
        " protocol_version, coverage_state, roster_revision, adopted_at, "
        " adoption_evidence_ref, adoption_evidence_json, "
        " adoption_evidence_sha256, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (task_instance_id, task_id, owner_user_id, owner_library_id,
         1, coverage_state, roster_revision, adopted_at, evidence_ref,
         evidence_json, evidence_sha, created_at, updated_at),
    )


def _insert_source_row(conn, *, registration_id, task_instance_id,
                       source_user_id, generation=1,
                       source_library_id=_SOURCE_LIBRARY,
                       source_path_identity="seed-path.bin", status="active",
                       registered_at=_T_REGISTERED, retired_at=None,
                       retired_reason=None, archive_watermark=None):
    """Directly INSERT one source registry row (synthetic seed)."""
    conn.execute(
        "INSERT INTO main.team_source_registry "
        "(registration_id, task_instance_id, source_user_id, "
        " source_library_id, source_path_identity, generation, status, "
        " registered_at, retired_at, retired_reason, archive_watermark) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (registration_id, task_instance_id, source_user_id, source_library_id,
         source_path_identity, generation, status, registered_at, retired_at,
         retired_reason, archive_watermark),
    )


class RegistryDiscoveryTest(unittest.TestCase):
    """P03/C04 discovery contract tests (statically authored)."""

    maxDiff = None

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="rf216-p03-")
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.registry = _load_registry_module()
        self.tft = importlib.import_module("coscreen.team_flow_types")

    # ------------------------------------------------------------------
    # Named case 1 (+ counterexample 1: time inversion)
    # ------------------------------------------------------------------

    def test_identity_survives_unrelated_bad_adoption(self):
        registry = self.registry

        # Scenario A (verified counterexample): time inversion.  A
        # legacy_verified row whose fields/hash are all valid but whose
        # adopted_at precedes created_at.  No SQL CHECK intercepts this, so
        # the row is inserted directly without any bypass.
        task_id = "batch-x-task-one"
        instance = "inst-identity-survives"
        db_path = self.base / "identity.db"
        db_path.write_bytes(b"")
        registry.prepare_team_terminal_registry(str(db_path))
        conn = _open_registry_connection(db_path)
        self.addCleanup(conn.close)
        _insert_source_row(
            conn, registration_id="reg-identity-a", task_instance_id=instance,
            source_user_id=501, generation=1,
            source_library_id=_SOURCE_LIBRARY)
        doc = _evidence_document(
            task_id=task_id, task_instance_id=instance, owner_user_id=411,
            owner_library_id=_OWNER_LIBRARY,
            sources=[{
                "registration_id": "reg-identity-a",
                "source_user_id": 501,
                "generation": 1,
                "source_library_id": _SOURCE_LIBRARY,
            }],
            forward_coverage_start=_T_ADOPTED_EARLIER)
        doc_text = _canonical_json_text(doc)
        _insert_protocol_row(
            conn, task_instance_id=instance, task_id=task_id,
            coverage_state="legacy_verified",
            created_at=_T_CREATED, updated_at=_T_CREATED,
            adopted_at=_T_ADOPTED_EARLIER, evidence_ref="evidence/identity-a",
            evidence_json=doc_text, evidence_sha=_sha256_hex(doc_text))

        header = registry.read_task_discovery_header_on(conn, task_id=task_id)
        self.assertIsNotNone(header)
        self.assertIsInstance(header, registry.TaskDiscoveryHeader)
        self.assertEqual(
            header.binding,
            self.tft.TaskBinding(
                task_id=task_id, task_instance_id=instance,
                owner_user_id=411, owner_library_id=_OWNER_LIBRARY))
        self.assertEqual(header.roster_revision, 0)
        self.assertEqual(header.coverage_status, "unknown")
        self.assertIsNone(header.coverage_evidence)
        self.assertIsNotNone(header.coverage_issue)
        self.assertIn("stored adoption evidence is corrupt",
                      header.coverage_issue)
        self.assertIn("adopted_at precedes", header.coverage_issue)
        # Unknown task id stays None (narrow read returns no identity).
        self.assertIsNone(registry.read_task_discovery_header_on(
            conn, task_id="batch-x-task-other"))

        # Strong-path positive control: the same damaged row is a typed
        # corrupt rejection on the old strong read (never silent).
        with self.assertRaises(
                registry.TerminalRegistryUnavailable) as strong_ctx:
            registry.read_protocol_for_task_on(conn, task_id=task_id)
        self.assertEqual(strong_ctx.exception.reason, "corrupt")
        self.assertIsNotNone(strong_ctx.exception.__cause__)
        self.assertIn("adopted_at precedes created_at",
                      str(strong_ctx.exception.__cause__))

        # Scenario B: the adoption document references a source registration
        # that is absent from team_source_registry.  The narrow header never
        # consults the source registry, so the known binding survives with
        # healthy document-side coverage; the strong path rejects.
        task_id_b = "batch-y-task-two"
        instance_b = "inst-unrelated-source-damage"
        db_path_b = self.base / "unrelated.db"
        db_path_b.write_bytes(b"")
        registry.prepare_team_terminal_registry(str(db_path_b))
        conn_b = _open_registry_connection(db_path_b)
        self.addCleanup(conn_b.close)
        doc_b = _evidence_document(
            task_id=task_id_b, task_instance_id=instance_b, owner_user_id=411,
            owner_library_id=_OWNER_LIBRARY,
            sources=[{
                "registration_id": "reg-absent-xyz",
                "source_user_id": 502,
                "generation": 1,
                "source_library_id": _SOURCE_LIBRARY,
            }],
            forward_coverage_start=_T_ADOPTED_OK)
        doc_text_b = _canonical_json_text(doc_b)
        _insert_protocol_row(
            conn_b, task_instance_id=instance_b, task_id=task_id_b,
            coverage_state="legacy_verified",
            created_at=_T_CREATED, updated_at=_T_CREATED,
            adopted_at=_T_ADOPTED_OK, evidence_ref="evidence/unrelated-b",
            evidence_json=doc_text_b, evidence_sha=_sha256_hex(doc_text_b))

        header_b = registry.read_task_discovery_header_on(
            conn_b, task_id=task_id_b)
        self.assertIsNotNone(header_b)
        self.assertEqual(
            header_b.binding,
            self.tft.TaskBinding(
                task_id=task_id_b, task_instance_id=instance_b,
                owner_user_id=411, owner_library_id=_OWNER_LIBRARY))
        self.assertEqual(header_b.coverage_status, "legacy_forward_only")
        self.assertEqual(header_b.coverage_evidence, doc_b)
        self.assertIsNone(header_b.coverage_issue)
        with self.assertRaises(
                registry.TerminalRegistryUnavailable) as strong_ctx_b:
            registry.read_protocol_for_task_on(conn_b, task_id=task_id_b)
        self.assertEqual(strong_ctx_b.exception.reason, "corrupt")
        self.assertIn("missing source registration", str(strong_ctx_b.exception))

    # ------------------------------------------------------------------
    # Named case 2
    # ------------------------------------------------------------------

    def test_each_bad_row_is_reported(self):
        registry = self.registry
        task_id = "batch-x-task-bad-rows"
        instance = "inst-each-bad-row"
        db_path = self.base / "badrows.db"
        db_path.write_bytes(b"")
        registry.prepare_team_terminal_registry(str(db_path))
        conn = _open_registry_connection(db_path)
        self.addCleanup(conn.close)
        _insert_protocol_row(conn, task_instance_id=instance, task_id=task_id)
        _insert_source_row(conn, registration_id="src-v10-1",
                           task_instance_id=instance, source_user_id=10,
                           generation=1)
        # DDL-legal / validation-illegal seeds: no SQL CHECK blocks these.
        # src-empty-path is seeded retired (with the retirement fields the
        # DDL CHECK demands) so it does not collide with the active
        # src-v10-1 row on the partial unique index over
        # (task_instance_id, source_user_id) WHERE status='active'; the
        # empty-path defect is status-independent, so the bad row stays
        # enumerated with the same locating issue either way.
        _insert_source_row(conn, registration_id="src-empty-path",
                           task_instance_id=instance, source_user_id=10,
                           generation=2, status="retired",
                           retired_at=_T_RETIRED, retired_reason="superseded",
                           source_path_identity="")
        _insert_source_row(conn, registration_id="src-v11-1",
                           task_instance_id=instance, source_user_id=11,
                           generation=1, status="retired",
                           retired_at=_T_RETIRED, retired_reason="superseded")
        _insert_source_row(conn, registration_id="src-bad-registered-at",
                           task_instance_id=instance, source_user_id=12,
                           generation=1,
                           registered_at="2026-11-16 09:00:00.000Z")
        _insert_source_row(conn, registration_id="src-nul-reason",
                           task_instance_id=instance, source_user_id=13,
                           generation=1, status="retired",
                           retired_at=_T_RETIRED, retired_reason="bad\x00reason")
        # The watermark seed must be a BLOB: an int 123 would be coerced to
        # the string '123' by the column's TEXT affinity and would then
        # PASS the strict-text check (the issue would never fire); bytes are
        # never converted by affinity, come back as ``bytes`` and genuinely
        # fail strict text, which is what this row exists to provoke.
        _insert_source_row(conn, registration_id="src-blob-watermark",
                           task_instance_id=instance, source_user_id=14,
                           generation=1, status="retired",
                           retired_at=_T_RETIRED, retired_reason="archived",
                           archive_watermark=b"123")
        _insert_source_row(conn, registration_id="src-bad-retired-at",
                           task_instance_id=instance, source_user_id=15,
                           generation=1, status="retired",
                           retired_at="not-a-timestamp",
                           retired_reason="archived")

        rows = list(registry.iter_source_discovery_on(
            conn, task_instance_id=instance))
        self.assertEqual(len(rows), 7)
        seen = {}
        for row in rows:
            # Spec sentence, verbatim semantics: source/issue exactly one.
            self.assertNotEqual((row.source is None), (row.issue is None))
            seen[row.registration_id] = row
        self.assertEqual(
            set(seen),
            {"src-v10-1", "src-empty-path", "src-v11-1",
             "src-bad-registered-at", "src-nul-reason", "src-blob-watermark",
             "src-bad-retired-at"})
        # SQL order preserved; nothing skipped.
        self.assertEqual(
            [row.registration_id for row in rows],
            ["src-v10-1", "src-empty-path", "src-v11-1",
             "src-bad-registered-at", "src-nul-reason", "src-blob-watermark",
             "src-bad-retired-at"])
        # Valid rows come through as full source mappings.
        self.assertEqual(seen["src-v10-1"].source["status"], "active")
        self.assertEqual(seen["src-v10-1"].source["source_user_id"], 10)
        self.assertIsNone(seen["src-v10-1"].issue)
        self.assertEqual(seen["src-v11-1"].source["status"], "retired")
        self.assertIsNone(seen["src-v11-1"].issue)
        # Each bad row: typed corrupt issue with its own locating
        # coordinates and its own reason.
        self.assertEqual(seen["src-empty-path"].registration_id,
                         "src-empty-path")
        self.assertIn("registration_id='src-empty-path'",
                      seen["src-empty-path"].issue)
        self.assertIn("source_user_id=10", seen["src-empty-path"].issue)
        self.assertIn("generation=2", seen["src-empty-path"].issue)
        self.assertIn("corrupt:", seen["src-empty-path"].issue)
        self.assertIn("source_path_identity must be non-empty",
                      seen["src-empty-path"].issue)
        self.assertIn("registration_id='src-bad-registered-at'",
                      seen["src-bad-registered-at"].issue)
        self.assertIn("source_user_id=12", seen["src-bad-registered-at"].issue)
        self.assertIn("generation=1", seen["src-bad-registered-at"].issue)
        self.assertIn("registered_at must be RFC3339 UTC millisecond Z",
                      seen["src-bad-registered-at"].issue)
        self.assertIn("registration_id='src-nul-reason'",
                      seen["src-nul-reason"].issue)
        self.assertIn("source_user_id=13", seen["src-nul-reason"].issue)
        self.assertIn("generation=1", seen["src-nul-reason"].issue)
        self.assertIn("retired_reason must not contain NUL",
                      seen["src-nul-reason"].issue)
        self.assertIn("registration_id='src-blob-watermark'",
                      seen["src-blob-watermark"].issue)
        self.assertIn("source_user_id=14", seen["src-blob-watermark"].issue)
        self.assertIn("generation=1", seen["src-blob-watermark"].issue)
        self.assertIn("archive_watermark must be strict text",
                      seen["src-blob-watermark"].issue)
        self.assertIn("registration_id='src-bad-retired-at'",
                      seen["src-bad-retired-at"].issue)
        self.assertIn("source_user_id=15", seen["src-bad-retired-at"].issue)
        self.assertIn("generation=1", seen["src-bad-retired-at"].issue)
        self.assertIn("retired_at must be RFC3339 UTC millisecond Z",
                      seen["src-bad-retired-at"].issue)

    # ------------------------------------------------------------------
    # Named case 3
    # ------------------------------------------------------------------

    def test_retired_generations_and_page_failure(self):
        registry = self.registry
        task_id = "batch-x-task-retired"
        instance = "inst-retired-generations"
        db_path = self.base / "retired.db"
        db_path.write_bytes(b"")
        registry.prepare_team_terminal_registry(str(db_path))
        conn = _open_registry_connection(db_path)
        self.addCleanup(conn.close)
        _insert_protocol_row(conn, task_instance_id=instance, task_id=task_id)
        _insert_source_row(conn, registration_id="src-u07-g1",
                           task_instance_id=instance, source_user_id=7,
                           generation=1, status="retired",
                           retired_at=_T_RETIRED, retired_reason="superseded")
        _insert_source_row(conn, registration_id="src-u07-g2",
                           task_instance_id=instance, source_user_id=7,
                           generation=2)
        _insert_source_row(conn, registration_id="src-u07-g3",
                           task_instance_id=instance, source_user_id=7,
                           generation=3, status="retired",
                           retired_at=_T_RETIRED, retired_reason="archived",
                           archive_watermark="wm-0001")
        _insert_source_row(conn, registration_id="src-u09-g1",
                           task_instance_id=instance, source_user_id=9,
                           generation=1)
        _insert_source_row(conn, registration_id="src-u09-g2",
                           task_instance_id=instance, source_user_id=9,
                           generation=2, status="retired",
                           retired_at=_T_RETIRED, retired_reason="archived")

        # batch_size=2 over 5 rows: three pages, keyset order
        # (source_user_id, generation) across page boundaries, retired and
        # active generations all enumerated.
        seen_order = []
        for row in registry.iter_source_discovery_on(
                conn, task_instance_id=instance, batch_size=2):
            self.assertIsNone(row.issue)
            self.assertIsNotNone(row.source)
            seen_order.append((row.source["source_user_id"],
                               row.source["generation"]))
        self.assertEqual(seen_order, [(7, 1), (7, 2), (7, 3), (9, 1), (9, 2)])

        # Page failure: a real sqlite3.Connection subclass injects an
        # OperationalError on the second page SELECT.  The iterator must
        # raise TerminalRegistryUnavailable instead of silently ending with
        # partial coverage.
        fail_db = self.base / "pagefail.db"
        fail_db.write_bytes(b"")
        registry.prepare_team_terminal_registry(str(fail_db))
        fail_conn = _open_registry_connection(fail_db,
                                              factory=_ProbeConnection)
        self.addCleanup(fail_conn.close)
        fail_conn.cursors_log = []
        fail_conn.fail_state = None
        # Candidate5 defensive twin of the cleanup.db leak fix (same
        # probe-with-cursors_log shape, latent here because this test
        # passed its teardown in the rerun): registered after
        # fail_conn.close so it fires earlier (LIFO); clearing the log
        # finalizes the logged cursors deterministically before the
        # connection close and the temp-dir teardown on Windows.
        self.addCleanup(fail_conn.cursors_log.clear)
        _insert_protocol_row(fail_conn, task_instance_id=instance,
                             task_id=task_id)
        # src-p1 is seeded retired so the two uid-21 rows do not collide on
        # the partial unique index (only one active row per
        # (task_instance_id, source_user_id)); retired generations are
        # enumerated exactly like active ones, so the page-failure script
        # below is unchanged.
        _insert_source_row(fail_conn, registration_id="src-p1",
                           task_instance_id=instance, source_user_id=21,
                           generation=1, status="retired",
                           retired_at=_T_RETIRED, retired_reason="superseded")
        _insert_source_row(fail_conn, registration_id="src-p2",
                           task_instance_id=instance, source_user_id=21,
                           generation=2)
        _insert_source_row(fail_conn, registration_id="src-p3",
                           task_instance_id=instance, source_user_id=22,
                           generation=1)
        iterator = registry.iter_source_discovery_on(
            fail_conn, task_instance_id=instance, batch_size=2)
        # Candidate5: close the generator handle before fail_conn.close
        # fires (registered later, LIFO).  No-op once the page failure
        # below finishes the iterator; deterministic force-close if an
        # assertion fails while it is suspended mid-page.
        self.addCleanup(iterator.close)
        fail_conn.fail_state = {"count": 0, "fail_after": 1}
        first = next(iterator)
        self.assertEqual(first.registration_id, "src-p1")
        self.assertIsNone(first.issue)
        second = next(iterator)
        self.assertEqual(second.registration_id, "src-p2")
        self.assertIsNone(second.issue)
        with self.assertRaises(
                registry.TerminalRegistryUnavailable) as page_ctx:
            next(iterator)
        self.assertEqual(page_ctx.exception.reason, "unreadable")
        self.assertIsInstance(page_ctx.exception.__cause__,
                              sqlite3.OperationalError)

    # ------------------------------------------------------------------
    # Named case 4
    # ------------------------------------------------------------------

    def test_strong_apis_stay_strong(self):
        registry = self.registry
        task_id = "batch-x-task-strong"
        instance = "inst-strong-apis"
        db_path = self.base / "strong.db"
        db_path.write_bytes(b"")
        registry.prepare_team_terminal_registry(str(db_path))
        conn = _open_registry_connection(db_path)
        self.addCleanup(conn.close)
        _insert_protocol_row(conn, task_instance_id=instance, task_id=task_id)
        _insert_source_row(conn, registration_id="src-strong-ok",
                           task_instance_id=instance, source_user_id=31,
                           generation=1)
        _insert_source_row(conn, registration_id="src-strong-bad",
                           task_instance_id=instance, source_user_id=32,
                           generation=1, source_path_identity="")

        # The old full list rejects the whole request on any bad row.
        with self.assertRaises(
                registry.TerminalRegistryUnavailable) as list_ctx:
            registry.list_sources_on(conn, task_instance_id=instance)
        self.assertEqual(list_ctx.exception.reason, "corrupt")
        # Narrow contrast: the same table yields valid-or-issue rows.
        discovered = list(registry.iter_source_discovery_on(
            conn, task_instance_id=instance))
        self.assertEqual(len(discovered), 2)
        self.assertEqual(
            {(row.registration_id, row.issue is not None) for row in discovered},
            {("src-strong-ok", False), ("src-strong-bad", True)})
        # A selected registration re-read with the strong API: valid row
        # returns the full 11-column record.
        record = registry.read_source_on(conn, registration_id="src-strong-ok")
        self.assertEqual(len(record), 11)
        self.assertEqual(record["registration_id"], "src-strong-ok")
        self.assertEqual(record["source_user_id"], 31)
        self.assertEqual(record["generation"], 1)
        self.assertEqual(record["status"], "active")
        self.assertEqual(record["source_library_id"], _SOURCE_LIBRARY)
        # ... and the damaged selection is still strongly rejected.
        with self.assertRaises(
                registry.TerminalRegistryUnavailable) as read_ctx:
            registry.read_source_on(conn, registration_id="src-strong-bad")
        self.assertEqual(read_ctx.exception.reason, "corrupt")
        # The old strong protocol read is scoped to the protocol row: it
        # succeeds here despite the unrelated bad source row.
        strong_protocol = registry.read_protocol_for_task_on(
            conn, task_id=task_id)
        self.assertEqual(strong_protocol["task_instance_id"], instance)
        self.assertEqual(strong_protocol["coverage_state"], "new_covered")

        # prepare controls: fresh empty file creates the full layout; a
        # missing file is never created; a partial layout is never repaired.
        fresh_db = self.base / "fresh.db"
        fresh_db.write_bytes(b"")
        registry.prepare_team_terminal_registry(str(fresh_db))
        with self.assertRaises(FileNotFoundError):
            registry.prepare_team_terminal_registry(
                str(self.base / "absent.db"))
        partial_db = self.base / "partial.db"
        partial_db.write_bytes(b"")
        registry.prepare_team_terminal_registry(str(partial_db))
        partial_conn = _open_registry_connection(partial_db)
        self.addCleanup(partial_conn.close)
        partial_conn.execute("DROP TRIGGER main.team_source_registry_no_delete")
        partial_conn.execute("COMMIT")
        with self.assertRaises(
                registry.TerminalRegistryUnavailable) as partial_ctx:
            registry.prepare_team_terminal_registry(str(partial_db))
        self.assertEqual(partial_ctx.exception.reason, "schema_mismatch")

        # The public name set is unchanged: 17 legacy names + 4 new ones.
        self.assertEqual(
            frozenset(registry.__all__),
            frozenset({
                "TerminalRegistryPayloadInvalid", "TerminalRegistryConflict",
                "TerminalRegistryStateError", "TerminalRegistryUnavailable",
                "prepare_team_terminal_registry",
                "read_protocol_for_task_on", "read_protocol_on",
                "coverage_status_on", "register_protocol_on",
                "mark_legacy_verified_on", "set_roster_revision_on",
                "register_source_on", "retire_source_on", "read_source_on",
                "list_sources_on", "active_source_on", "source_status_on",
                "TaskDiscoveryHeader", "SourceDiscoveryRow",
                "read_task_discovery_header_on", "iter_source_discovery_on",
            }))

    # ------------------------------------------------------------------
    # Named case 5
    # ------------------------------------------------------------------

    def test_connection_contract_and_cleanup(self):
        """Borrowed-connection contract plus cursor cleanup of a full run.

        Cursor-count caliber (candidate3): ``iter_source_discovery_on``
        validates the scoped source-registry layout eagerly, before the
        iterator is created, so the probe's cursor log first gains the 2
        closed layout-check SELECT cursors and only then the page SELECTs.
        Page-cursor counts slice the log from a snapshot taken after the
        iterator is obtained (layout cursors excluded); the closedness
        pins slice from before the call, so layout cursors stay covered.

        Deterministic teardown (candidate5): the packaged rerun passed
        every assertion of this test yet TemporaryDirectory cleanup
        failed with WinError 32 on cleanup.db.  The probe deliberately
        keeps every cursor it created alive in ``cursors_log`` (page
        cursors, the two PRAGMA-read cursors left stepped-but-not-reset,
        and the open ``SELECT 1`` control cursor), and cursors reference
        the connection, so after ``probe.close()`` the underlying sqlite
        handles survived inside a reference cycle that only a gc pass --
        one that had not run before teardown -- would release.  The fix
        registers deterministic handle release on the cleanup chain,
        which runs LIFO and entirely ahead of setUp's
        TemporaryDirectory cleanup: close the named open cursor first,
        close the kept generator handle (a suspended generator would
        otherwise keep a page cursor alive into teardown), drop the
        logged cursor references so CPython refcounting finalizes them
        immediately, and only then the already-registered
        ``probe.close`` runs on a connection with no live cursors.
        Close errors are not swallowed and never mask assertion results:
        any exception surfaces in the cleanup chain itself, after the
        assertions already recorded.
        """
        registry = self.registry
        task_id = "batch-x-task-contract"
        instance = "inst-contract"
        db_path = self.base / "contract.db"
        db_path.write_bytes(b"")
        registry.prepare_team_terminal_registry(str(db_path))
        conn = _open_registry_connection(db_path)
        self.addCleanup(conn.close)
        _insert_protocol_row(conn, task_instance_id=instance, task_id=task_id)

        # Non-native delegating wrapper: typed connection_contract
        # rejection (candidate pins TerminalRegistryUnavailable, a
        # RuntimeError, for all three contract violations).
        facade = _FacadeConn(conn)
        with self.assertRaises(
                registry.TerminalRegistryUnavailable) as facade_ctx:
            registry.read_task_discovery_header_on(facade, task_id=task_id)
        self.assertEqual(facade_ctx.exception.reason, "connection_contract")
        self.assertIsInstance(facade_ctx.exception, RuntimeError)
        with self.assertRaises(
                registry.TerminalRegistryUnavailable) as facade_iter_ctx:
            registry.iter_source_discovery_on(facade,
                                              task_instance_id=instance)
        self.assertEqual(facade_iter_ctx.exception.reason,
                         "connection_contract")

        # Closed native connection: ProgrammingError passes through
        # unchanged, never wrapped.
        closed_conn = sqlite3.connect(str(db_path), isolation_level=None)
        closed_conn.row_factory = sqlite3.Row
        closed_conn.execute("BEGIN")
        closed_conn.close()
        with self.assertRaises(sqlite3.ProgrammingError) as closed_ctx:
            registry.read_task_discovery_header_on(closed_conn,
                                                   task_id=task_id)
        self.assertIn("closed", str(closed_ctx.exception))
        self.assertNotIsInstance(closed_ctx.exception,
                                 registry.TerminalRegistryUnavailable)
        with self.assertRaises(sqlite3.ProgrammingError):
            registry.iter_source_discovery_on(closed_conn,
                                              task_instance_id=instance)

        # Native connection without an active caller transaction.
        no_tx_conn = sqlite3.connect(str(db_path), isolation_level=None)
        no_tx_conn.row_factory = sqlite3.Row
        self.addCleanup(no_tx_conn.close)
        with self.assertRaises(
                registry.TerminalRegistryUnavailable) as no_tx_ctx:
            registry.read_task_discovery_header_on(no_tx_conn,
                                                   task_id=task_id)
        self.assertEqual(no_tx_ctx.exception.reason, "connection_contract")
        self.assertIsInstance(no_tx_ctx.exception, RuntimeError)
        no_tx_conn.execute("BEGIN")
        self.assertIsNone(registry.read_task_discovery_header_on(
            no_tx_conn, task_id="batch-x-task-nobody"))

        # Native connection in a transaction but without sqlite3.Row
        # factory; then the paired positive control on the same connection.
        factory_conn = sqlite3.connect(str(db_path), isolation_level=None)
        self.addCleanup(factory_conn.close)
        factory_conn.execute("BEGIN")
        with self.assertRaises(
                registry.TerminalRegistryUnavailable) as factory_ctx:
            registry.read_task_discovery_header_on(factory_conn,
                                                   task_id=task_id)
        self.assertEqual(factory_ctx.exception.reason, "connection_contract")
        factory_conn.row_factory = sqlite3.Row
        self.assertIsNone(registry.read_task_discovery_header_on(
            factory_conn, task_id="batch-x-task-nobody"))

        # Cleanup contract of the borrowed connection across a full
        # multi-page iteration.
        cleanup_db = self.base / "cleanup.db"
        cleanup_db.write_bytes(b"")
        registry.prepare_team_terminal_registry(str(cleanup_db))
        probe = _open_registry_connection(cleanup_db, factory=_ProbeConnection)
        self.addCleanup(probe.close)
        probe.cursors_log = []
        probe.fail_state = None
        # Candidate5: registered after probe.close, so (LIFO) it fires
        # BEFORE the connection close and before the temp-dir teardown.
        # Clearing drops the last external references to the logged
        # cursors; refcounting then finalizes their sqlite3 statements
        # at once instead of waiting for a gc pass that may not run
        # before TemporaryDirectory cleanup on Windows.
        self.addCleanup(probe.cursors_log.clear)
        probe.execute("PRAGMA busy_timeout=4321")
        _insert_protocol_row(probe, task_instance_id="inst-cleanup",
                             task_id="batch-x-task-cleanup")
        # src-c-1 is seeded retired so the two uid-41 rows do not collide
        # on the partial unique index over (task_instance_id,
        # source_user_id) WHERE status='active'; enumeration order and the
        # page structure below are unchanged.
        _insert_source_row(probe, registration_id="src-c-1",
                           task_instance_id="inst-cleanup", source_user_id=41,
                           generation=1, status="retired",
                           retired_at=_T_RETIRED, retired_reason="superseded")
        _insert_source_row(probe, registration_id="src-c-2",
                           task_instance_id="inst-cleanup", source_user_id=41,
                           generation=2)
        _insert_source_row(probe, registration_id="src-c-3",
                           task_instance_id="inst-cleanup", source_user_id=42,
                           generation=1)
        self.assertEqual(
            probe.execute("PRAGMA busy_timeout").fetchone()[0], 4321)
        before = len(probe.cursors_log)
        # iter_source_discovery_on is a plain function, not a generator:
        # it performs the scoped layout check eagerly, before creating the
        # iterator, so the call below has already logged the 2 closed
        # layout SELECT cursors when it returns.  Snapshot the log length
        # here -- after the layout phase, before the first next() -- so
        # cursors logged past this point are page SELECTs only.
        discovery_iter = registry.iter_source_discovery_on(
            probe, task_instance_id="inst-cleanup", batch_size=2)
        # Candidate5: close the generator handle before the connection
        # (registered later, so it fires earlier).  When the loop below
        # exhausts the iterator this is a no-op; if an assertion fails
        # mid-loop the suspended generator is force-closed here instead
        # of keeping its page cursor alive into teardown.
        self.addCleanup(discovery_iter.close)
        page_start = len(probe.cursors_log)
        consumed = 0
        for row in discovery_iter:
            self.assertNotEqual((row.source is None), (row.issue is None))
            consumed += 1
            # Close-TIMING pin (candidate2): this loop body runs immediately
            # after next() returned and before the next page is fetched, so
            # every page cursor created so far must ALREADY be closed here
            # -- "closed before yield", not merely "closed by the end".  A
            # closed cursor raises ProgrammingError on any operation; an
            # open cursor would not raise (exhausted -> None, pending ->
            # would consume a row), so assertRaises pins closedness now.
            # Slicing from ``before`` (not the layout snapshot) keeps the 2
            # layout cursors inside this per-yield closedness pin as well.
            for created_so_far in probe.cursors_log[before:]:
                with self.assertRaises(sqlite3.ProgrammingError):
                    created_so_far.fetchone()
        self.assertEqual(consumed, 3)
        # Exactly two page SELECTs for batch_size=2 over 3 rows -- counted
        # from the post-layout snapshot, so the 2 layout-check cursors are
        # excluded from the page count -- and the same closedness still
        # holds after the final next() raised StopIteration (re-checked
        # here for the complete page cursor set).
        page_cursors = probe.cursors_log[page_start:]
        self.assertEqual(len(page_cursors), 2)
        for cursor in page_cursors:
            with self.assertRaises(sqlite3.ProgrammingError):
                cursor.fetchone()
        # The borrowed connection was not mutated: no PRAGMA reset, no
        # factory change, no isolation change, no commit/rollback.
        self.assertEqual(
            probe.execute("PRAGMA busy_timeout").fetchone()[0], 4321)
        self.assertIs(probe.row_factory, sqlite3.Row)
        self.assertIsNone(probe.isolation_level)
        self.assertTrue(probe.in_transaction)
        # Probe discrimination control: an open exhausted cursor returns
        # None instead of raising, so the assertions above really pin
        # "closed", not "exhausted".
        open_exhausted_cursor = probe.execute("SELECT 1")
        # Candidate5: explicit deterministic close of the one cursor this
        # test leaves open on purpose; runs first on the cleanup chain
        # (last registered, LIFO), ahead of the generator close, the
        # cursors_log clear, probe.close and the temp-dir teardown.
        self.addCleanup(open_exhausted_cursor.close)
        open_exhausted_cursor.fetchall()
        self.assertIsNone(open_exhausted_cursor.fetchone())

    # ------------------------------------------------------------------
    # Scoped layout matrices (dynamic corruption matrix)
    # ------------------------------------------------------------------

    def test_header_scoped_layout_matrix(self):
        registry = self.registry
        task_id = "batch-x-task-layout"
        instance = "inst-header-layout"

        # Missing protocol table: typed not_prepared.
        db_a = self.base / "hdr-missing-table.db"
        db_a.write_bytes(b"")
        registry.prepare_team_terminal_registry(str(db_a))
        conn_a = _open_registry_connection(db_a)
        self.addCleanup(conn_a.close)
        conn_a.execute("DROP TABLE main.team_task_protocol")
        with self.assertRaises(
                registry.TerminalRegistryUnavailable) as ctx_a:
            registry.read_task_discovery_header_on(conn_a, task_id=task_id)
        self.assertEqual(ctx_a.exception.reason, "not_prepared")

        # Missing owning trigger: typed schema_mismatch naming the object.
        db_b = self.base / "hdr-missing-trigger.db"
        db_b.write_bytes(b"")
        registry.prepare_team_terminal_registry(str(db_b))
        conn_b = _open_registry_connection(db_b)
        self.addCleanup(conn_b.close)
        conn_b.execute("DROP TRIGGER main.team_task_protocol_no_delete")
        with self.assertRaises(
                registry.TerminalRegistryUnavailable) as ctx_b:
            registry.read_task_discovery_header_on(conn_b, task_id=task_id)
        self.assertEqual(ctx_b.exception.reason, "schema_mismatch")
        self.assertIn("trigger layout differs", str(ctx_b.exception))
        self.assertIn("team_task_protocol_no_delete", str(ctx_b.exception))

        # Extra trigger on the read table: typed schema_mismatch.  The
        # scoped object-set scan reports the intruder first (the message is
        # the candidate's "unexpected registry object" wording; the later
        # per-kind "extra trigger" branch is unreachable for it), so the
        # assertion matches that actual message without over-pinning.
        db_c = self.base / "hdr-extra-trigger.db"
        db_c.write_bytes(b"")
        registry.prepare_team_terminal_registry(str(db_c))
        conn_c = _open_registry_connection(db_c)
        self.addCleanup(conn_c.close)
        conn_c.execute(
            "CREATE TRIGGER main.team_task_protocol_extra_audit "
            "BEFORE UPDATE ON team_task_protocol "
            "BEGIN SELECT RAISE(ABORT, 'extra'); END")
        with self.assertRaises(
                registry.TerminalRegistryUnavailable) as ctx_c:
            registry.read_task_discovery_header_on(conn_c, task_id=task_id)
        self.assertEqual(ctx_c.exception.reason, "schema_mismatch")
        self.assertIn("unexpected registry object", str(ctx_c.exception))
        self.assertIn("team_task_protocol_extra_audit", str(ctx_c.exception))

        # TEMP shadow over a registry object name: typed schema_mismatch.
        db_d = self.base / "hdr-temp-shadow.db"
        db_d.write_bytes(b"")
        registry.prepare_team_terminal_registry(str(db_d))
        conn_d = _open_registry_connection(db_d)
        self.addCleanup(conn_d.close)
        conn_d.execute(
            "CREATE TEMP TABLE team_task_protocol (task_instance_id TEXT)")
        with self.assertRaises(
                registry.TerminalRegistryUnavailable) as ctx_d:
            registry.read_task_discovery_header_on(conn_d, task_id=task_id)
        self.assertEqual(ctx_d.exception.reason, "schema_mismatch")
        self.assertIn("temp schema shadows", str(ctx_d.exception))

        # Scoped isolation: the other registry table dropped does not deny
        # the header; the source iterator on the same connection is the one
        # that fails as not_prepared.
        db_e = self.base / "hdr-isolation.db"
        db_e.write_bytes(b"")
        registry.prepare_team_terminal_registry(str(db_e))
        conn_e = _open_registry_connection(db_e)
        self.addCleanup(conn_e.close)
        _insert_protocol_row(conn_e, task_instance_id=instance,
                             task_id=task_id)
        conn_e.execute("DROP TABLE main.team_source_registry")
        header_e = registry.read_task_discovery_header_on(conn_e,
                                                          task_id=task_id)
        self.assertEqual(header_e.coverage_status, "new_covered")
        self.assertIsNone(header_e.coverage_issue)
        self.assertEqual(header_e.roster_revision, 0)
        with self.assertRaises(
                registry.TerminalRegistryUnavailable) as ctx_iter_e:
            registry.iter_source_discovery_on(conn_e,
                                              task_instance_id=instance)
        self.assertEqual(ctx_iter_e.exception.reason, "not_prepared")

        # Identity damage denies with a typed corrupt rejection (the
        # asymmetry control to coverage-side damage classification).
        db_f = self.base / "hdr-identity.db"
        db_f.write_bytes(b"")
        registry.prepare_team_terminal_registry(str(db_f))
        conn_f = _open_registry_connection(db_f)
        self.addCleanup(conn_f.close)
        _insert_protocol_row(conn_f, task_instance_id="", task_id=task_id)
        with self.assertRaises(
                registry.TerminalRegistryUnavailable) as ctx_f:
            registry.read_task_discovery_header_on(conn_f, task_id=task_id)
        self.assertEqual(ctx_f.exception.reason, "corrupt")
        self.assertIn("protocol identity row is corrupt", str(ctx_f.exception))

        # Healthy layout positive control + payload task_id contract.
        db_g = self.base / "hdr-healthy.db"
        db_g.write_bytes(b"")
        registry.prepare_team_terminal_registry(str(db_g))
        conn_g = _open_registry_connection(db_g)
        self.addCleanup(conn_g.close)
        _insert_protocol_row(conn_g, task_instance_id=instance,
                             task_id=task_id)
        header_g = registry.read_task_discovery_header_on(conn_g,
                                                          task_id=task_id)
        self.assertEqual(header_g.coverage_status, "new_covered")
        self.assertIsNone(header_g.coverage_issue)
        self.assertEqual(header_g.roster_revision, 0)
        with self.assertRaises(registry.TerminalRegistryPayloadInvalid):
            registry.read_task_discovery_header_on(conn_g, task_id="")

    def test_source_scoped_layout_matrix(self):
        registry = self.registry
        instance = "inst-source-layout"
        task_id = "batch-x-task-source-layout"

        # Missing source table: typed not_prepared, raised eagerly at call.
        db_a = self.base / "src-missing-table.db"
        db_a.write_bytes(b"")
        registry.prepare_team_terminal_registry(str(db_a))
        conn_a = _open_registry_connection(db_a)
        self.addCleanup(conn_a.close)
        conn_a.execute("DROP TABLE main.team_source_registry")
        with self.assertRaises(
                registry.TerminalRegistryUnavailable) as ctx_a:
            registry.iter_source_discovery_on(conn_a,
                                              task_instance_id=instance)
        self.assertEqual(ctx_a.exception.reason, "not_prepared")

        # Missing owning trigger.
        db_b = self.base / "src-missing-trigger.db"
        db_b.write_bytes(b"")
        registry.prepare_team_terminal_registry(str(db_b))
        conn_b = _open_registry_connection(db_b)
        self.addCleanup(conn_b.close)
        conn_b.execute("DROP TRIGGER main.team_source_registry_no_delete")
        with self.assertRaises(
                registry.TerminalRegistryUnavailable) as ctx_b:
            registry.iter_source_discovery_on(conn_b,
                                              task_instance_id=instance)
        self.assertEqual(ctx_b.exception.reason, "schema_mismatch")
        self.assertIn("trigger layout differs", str(ctx_b.exception))
        self.assertIn("team_source_registry_no_delete", str(ctx_b.exception))

        # Extra trigger on the read table: reported by the object-set scan
        # as "unexpected registry object" (actual candidate message; the
        # per-kind "extra trigger" branch fires only for objects the set
        # scan does not see first).
        db_c = self.base / "src-extra-trigger.db"
        db_c.write_bytes(b"")
        registry.prepare_team_terminal_registry(str(db_c))
        conn_c = _open_registry_connection(db_c)
        self.addCleanup(conn_c.close)
        conn_c.execute(
            "CREATE TRIGGER main.team_source_registry_extra_audit "
            "BEFORE UPDATE ON team_source_registry "
            "BEGIN SELECT RAISE(ABORT, 'extra'); END")
        with self.assertRaises(
                registry.TerminalRegistryUnavailable) as ctx_c:
            registry.iter_source_discovery_on(conn_c,
                                              task_instance_id=instance)
        self.assertEqual(ctx_c.exception.reason, "schema_mismatch")
        self.assertIn("unexpected registry object", str(ctx_c.exception))
        self.assertIn("team_source_registry_extra_audit", str(ctx_c.exception))

        # Missing partial unique index.
        db_d = self.base / "src-missing-index.db"
        db_d.write_bytes(b"")
        registry.prepare_team_terminal_registry(str(db_d))
        conn_d = _open_registry_connection(db_d)
        self.addCleanup(conn_d.close)
        conn_d.execute("DROP INDEX main.team_source_registry_one_active")
        with self.assertRaises(
                registry.TerminalRegistryUnavailable) as ctx_d:
            registry.iter_source_discovery_on(conn_d,
                                              task_instance_id=instance)
        self.assertEqual(ctx_d.exception.reason, "schema_mismatch")
        self.assertIn("index layout differs", str(ctx_d.exception))
        self.assertIn("team_source_registry_one_active", str(ctx_d.exception))

        # Extra index on the read table: same "unexpected registry object"
        # wording from the object-set scan (actual candidate message).
        db_e = self.base / "src-extra-index.db"
        db_e.write_bytes(b"")
        registry.prepare_team_terminal_registry(str(db_e))
        conn_e = _open_registry_connection(db_e)
        self.addCleanup(conn_e.close)
        conn_e.execute(
            "CREATE INDEX main.team_source_registry_extra_by_reason "
            "ON team_source_registry (retired_reason)")
        with self.assertRaises(
                registry.TerminalRegistryUnavailable) as ctx_e:
            registry.iter_source_discovery_on(conn_e,
                                              task_instance_id=instance)
        self.assertEqual(ctx_e.exception.reason, "schema_mismatch")
        self.assertIn("unexpected registry object", str(ctx_e.exception))
        self.assertIn("team_source_registry_extra_by_reason",
                      str(ctx_e.exception))

        # TEMP shadow over a source registry object name.
        db_f = self.base / "src-temp-shadow.db"
        db_f.write_bytes(b"")
        registry.prepare_team_terminal_registry(str(db_f))
        conn_f = _open_registry_connection(db_f)
        self.addCleanup(conn_f.close)
        conn_f.execute(
            "CREATE TEMP TABLE team_source_registry (registration_id TEXT)")
        with self.assertRaises(
                registry.TerminalRegistryUnavailable) as ctx_f:
            registry.iter_source_discovery_on(conn_f,
                                              task_instance_id=instance)
        self.assertEqual(ctx_f.exception.reason, "schema_mismatch")
        self.assertIn("temp schema shadows", str(ctx_f.exception))

        # Scoped isolation: the protocol table dropped does not block the
        # source enumeration; the header on the same connection is the one
        # that fails as not_prepared.
        db_g = self.base / "src-isolation.db"
        db_g.write_bytes(b"")
        registry.prepare_team_terminal_registry(str(db_g))
        conn_g = _open_registry_connection(db_g)
        self.addCleanup(conn_g.close)
        _insert_source_row(conn_g, registration_id="src-iso-1",
                           task_instance_id=instance, source_user_id=61,
                           generation=1)
        _insert_source_row(conn_g, registration_id="src-iso-2",
                           task_instance_id=instance, source_user_id=62,
                           generation=1)
        conn_g.execute("DROP TABLE main.team_task_protocol")
        enumerated = list(registry.iter_source_discovery_on(
            conn_g, task_instance_id=instance))
        self.assertEqual(len(enumerated), 2)
        for row in enumerated:
            self.assertIsNone(row.issue)
            self.assertIsNotNone(row.source)
        with self.assertRaises(
                registry.TerminalRegistryUnavailable) as ctx_hdr_g:
            registry.read_task_discovery_header_on(conn_g, task_id=task_id)
        self.assertEqual(ctx_hdr_g.exception.reason, "not_prepared")

        # batch_size input contract and the batch_size=1 multi-page
        # positive control (every keyset boundary exercised).
        db_h = self.base / "src-batch.db"
        db_h.write_bytes(b"")
        registry.prepare_team_terminal_registry(str(db_h))
        conn_h = _open_registry_connection(db_h)
        self.addCleanup(conn_h.close)
        _insert_source_row(conn_h, registration_id="src-batch-1",
                           task_instance_id=instance, source_user_id=51,
                           generation=1)
        _insert_source_row(conn_h, registration_id="src-batch-2",
                           task_instance_id=instance, source_user_id=52,
                           generation=1)
        for bad_size in (0, -1, True, 1.5, "200"):
            with self.assertRaises(registry.TerminalRegistryPayloadInvalid):
                registry.iter_source_discovery_on(
                    conn_h, task_instance_id=instance, batch_size=bad_size)
        seen_uids = []
        for row in registry.iter_source_discovery_on(
                conn_h, task_instance_id=instance, batch_size=1):
            self.assertIsNone(row.issue)
            seen_uids.append(row.source["source_user_id"])
        self.assertEqual(seen_uids, [51, 52])
        # Empty instance id: payload rejection, not data unavailability.
        with self.assertRaises(registry.TerminalRegistryPayloadInvalid):
            registry.iter_source_discovery_on(conn_h, task_instance_id="")

    # ------------------------------------------------------------------
    # Bad-type generation sort keys (candidate2 addition)
    # ------------------------------------------------------------------

    def test_bad_type_generation_rows_keyset_coverage(self):
        """Bad-type generation sort keys stay fully covered by keyset pages.

        Honest seeding disclosure: the ``generation`` column's DDL CHECK
        (``typeof(generation) = 'integer'`` with a 1..MAX range) blocks
        every legal write of a TEXT or REAL generation, so this scenario
        cannot be seeded through a normal INSERT at all.  The seeding below
        deliberately runs ``PRAGMA ignore_check_constraints = ON`` -- the
        only CHECK bypass in this file -- to insert one row whose
        generation column holds the REAL 2.5 and one whose holds the TEXT
        'two', then restores CHECK enforcement with ``OFF`` before any
        product call.  What must hold regardless: the keyset pagination's
        ORDER BY and its page predicate use the same SQLite cross-type
        comparison rules (numeric < text), so no row may be skipped or
        duplicated when a page boundary lands on a bad-type key, and both
        bad-type rows are enumerated as corrupt issue rows carrying their
        locating coordinates instead of being silently dropped.
        """
        registry = self.registry
        instance = "inst-badtype-keys"
        db_path = self.base / "badtype-keys.db"
        db_path.write_bytes(b"")
        registry.prepare_team_terminal_registry(str(db_path))
        conn = _open_registry_connection(db_path)
        self.addCleanup(conn.close)
        _insert_source_row(conn, registration_id="src-k-u71-g1",
                           task_instance_id=instance, source_user_id=71,
                           generation=1)
        _insert_source_row(conn, registration_id="src-k-u72-g1",
                           task_instance_id=instance, source_user_id=72,
                           generation=1)
        # The two bad-type rows are retired so the uid-72 trio keeps at most
        # one active row for the partial unique index; their only defect is
        # the generation type itself.
        conn.execute("PRAGMA ignore_check_constraints = ON")
        _insert_source_row(conn, registration_id="src-k-u72-g2r5",
                           task_instance_id=instance, source_user_id=72,
                           generation=2.5, status="retired",
                           retired_at=_T_RETIRED, retired_reason="superseded")
        _insert_source_row(conn, registration_id="src-k-u72-gtwo",
                           task_instance_id=instance, source_user_id=72,
                           generation="two", status="retired",
                           retired_at=_T_RETIRED, retired_reason="archived")
        conn.execute("PRAGMA ignore_check_constraints = OFF")
        _insert_source_row(conn, registration_id="src-k-u73-g1",
                           task_instance_id=instance, source_user_id=73,
                           generation=1)

        rows = list(registry.iter_source_discovery_on(
            conn, task_instance_id=instance, batch_size=2))
        seen = {}
        for row in rows:
            # Spec sentence, verbatim semantics: source/issue exactly one.
            self.assertNotEqual((row.source is None), (row.issue is None))
            seen[row.registration_id] = row
        # Full coverage in exact SQL order with batch_size=2: three pages
        # whose boundaries cross an INTEGER key, then a REAL key, then a
        # TEXT key; nothing skipped, nothing enumerated twice.
        self.assertEqual(len(seen), len(rows))
        self.assertEqual(
            [row.registration_id for row in rows],
            ["src-k-u71-g1", "src-k-u72-g1", "src-k-u72-g2r5",
             "src-k-u72-gtwo", "src-k-u73-g1"])
        # Healthy integer keys come through as full source mappings.
        for good_id in ("src-k-u71-g1", "src-k-u72-g1", "src-k-u73-g1"):
            self.assertIsNone(seen[good_id].issue)
            self.assertIsNotNone(seen[good_id].source)
            self.assertEqual(seen[good_id].source["status"], "active")
        # The REAL and TEXT generation rows are corrupt issue rows whose
        # locating coordinates quote the stored bad-type key verbatim.
        self.assertIsNone(seen["src-k-u72-g2r5"].source)
        self.assertIn("corrupt:", seen["src-k-u72-g2r5"].issue)
        self.assertIn("generation must be an integer",
                      seen["src-k-u72-g2r5"].issue)
        self.assertIn("generation=2.5", seen["src-k-u72-g2r5"].issue)
        self.assertIsNone(seen["src-k-u72-gtwo"].source)
        self.assertIn("corrupt:", seen["src-k-u72-gtwo"].issue)
        self.assertIn("generation must be an integer",
                      seen["src-k-u72-gtwo"].issue)
        self.assertIn("generation='two'", seen["src-k-u72-gtwo"].issue)

    # ------------------------------------------------------------------
    # Counterexample 2: bool/float impersonating the evidence owner id
    # ------------------------------------------------------------------

    def test_evidence_owner_type_impersonation(self):
        registry = self.registry
        task_id = "batch-x-task-owner-type"
        instance = "inst-owner-type"
        db_path = self.base / "owner-type.db"
        db_path.write_bytes(b"")
        registry.prepare_team_terminal_registry(str(db_path))
        conn = _open_registry_connection(db_path)
        self.addCleanup(conn.close)
        # One source registration under EACH label's own task instance
        # (bool/float/int alike), so every evidence document references a
        # registration that really exists for its instance: apart from the
        # owner type, each document is exactly what the contract wants.
        _insert_source_row(conn, registration_id="reg-owner-type-bool",
                           task_instance_id=instance + "-bool",
                           source_user_id=63, generation=1,
                           source_library_id=_SOURCE_LIBRARY)
        _insert_source_row(conn, registration_id="reg-owner-type-float",
                           task_instance_id=instance + "-float",
                           source_user_id=64, generation=1,
                           source_library_id=_SOURCE_LIBRARY)
        # Separate source registration for the healthy integer-1 instance,
        # so the strong positive control passes its source binding check.
        _insert_source_row(conn, registration_id="reg-owner-type-int",
                           task_instance_id=instance + "-int",
                           source_user_id=62, generation=1,
                           source_library_id=_SOURCE_LIBRARY)
        evidence_sources_ok = [{
            "registration_id": "reg-owner-type-int",
            "source_user_id": 62,
            "generation": 1,
            "source_library_id": _SOURCE_LIBRARY,
        }]

        # Verified counterexample: evidence JSON with owner_user_id written
        # as true / 1.0 while the record column holds integer 1 (explicit
        # owner_user_id=1 below -- the default 411 of the first round made
        # the impersonation equality never hold, so the case did not really
        # exercise the type check).  With 1 stored, True == 1 and 1.0 == 1,
        # so a binding-equality-only check would PASS; only the narrow
        # document check's strict-integer rejection can classify these
        # rows.  Recomputing the legal canonical SHA is enough; the outer
        # DDL never inspects JSON internals, so the rows insert cleanly.
        # The narrow header must classify coverage as unknown instead of
        # exposing the document.
        for bogus_owner, label, label_uid in (
                (True, "bool", 63), (1.0, "float", 64)):
            row_instance = instance + "-" + label
            row_task = task_id + "-" + label
            doc = _evidence_document(
                task_id=row_task, task_instance_id=row_instance,
                owner_user_id=bogus_owner, owner_library_id=_OWNER_LIBRARY,
                sources=[{
                    "registration_id": "reg-owner-type-" + label,
                    "source_user_id": label_uid,
                    "generation": 1,
                    "source_library_id": _SOURCE_LIBRARY,
                }],
                forward_coverage_start=_T_ADOPTED_OK)
            doc_text = _canonical_json_text(doc)
            _insert_protocol_row(
                conn, task_instance_id=row_instance, task_id=row_task,
                owner_user_id=1,
                coverage_state="legacy_verified",
                created_at=_T_CREATED, updated_at=_T_CREATED,
                adopted_at=_T_ADOPTED_OK,
                evidence_ref="evidence/owner-" + label,
                evidence_json=doc_text, evidence_sha=_sha256_hex(doc_text))
            header = registry.read_task_discovery_header_on(
                conn, task_id=row_task)
            self.assertIsNotNone(header)
            # The record side of the impersonation really is integer 1.
            self.assertEqual(header.binding.owner_user_id, 1)
            self.assertEqual(header.coverage_status, "unknown")
            self.assertIsNone(header.coverage_evidence)
            self.assertIsNotNone(header.coverage_issue)
            self.assertIn("stored adoption evidence is corrupt",
                          header.coverage_issue)
            self.assertIn("evidence owner_user_id must be a strict integer",
                          header.coverage_issue)
            self.assertEqual(header.roster_revision, 0)

        # Positive control: the real integer 1 yields the healthy
        # legacy_forward_only classification with the complete evidence
        # document, and the strong path succeeds on the same row.
        row_instance_ok = instance + "-int"
        row_task_ok = task_id + "-int"
        ok_doc = _evidence_document(
            task_id=row_task_ok, task_instance_id=row_instance_ok,
            owner_user_id=1, owner_library_id=_OWNER_LIBRARY,
            sources=evidence_sources_ok, forward_coverage_start=_T_ADOPTED_OK)
        ok_text = _canonical_json_text(ok_doc)
        _insert_protocol_row(
            conn, task_instance_id=row_instance_ok, task_id=row_task_ok,
            owner_user_id=1, coverage_state="legacy_verified",
            created_at=_T_CREATED, updated_at=_T_CREATED,
            adopted_at=_T_ADOPTED_OK, evidence_ref="evidence/owner-int",
            evidence_json=ok_text, evidence_sha=_sha256_hex(ok_text))
        ok_header = registry.read_task_discovery_header_on(
            conn, task_id=row_task_ok)
        self.assertEqual(ok_header.coverage_status, "legacy_forward_only")
        self.assertEqual(ok_header.coverage_evidence, ok_doc)
        self.assertIsNone(ok_header.coverage_issue)
        self.assertEqual(
            ok_header.binding,
            self.tft.TaskBinding(
                task_id=row_task_ok, task_instance_id=row_instance_ok,
                owner_user_id=1, owner_library_id=_OWNER_LIBRARY))
        strong_ok = registry.read_protocol_for_task_on(conn,
                                                       task_id=row_task_ok)
        self.assertEqual(strong_ok["coverage_state"], "legacy_verified")
        self.assertEqual(strong_ok["adopted_at"], _T_ADOPTED_OK)


if __name__ == "__main__":
    unittest.main()
