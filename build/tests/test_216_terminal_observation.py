# -*- coding: utf-8 -*-
"""F1 v2 current-observation storage tests (static freeze; not run yet).

Authority: docs/superpowers/plans/2026-11-16-2.1.6-team-work-packages.md (P02)
+ docs/superpowers/plans/2026-11-16-2.1.6-team-contracts.md (C03) + the OC-DS
product-review dynamic matrix for P02 (v2 observation validation, host item
keys, source bindings, binding/protocol/opinion consistency, idempotence and
conflict behaviour, rollback zero-persistence, v2 archiving, v1 hash freeze).

Product target: coscreen/team_terminal_facts.py, candidate_v3 (frozen
2026-11-18 by the P02 coordinator):
E:/rf216-scratch/p02-terminal-facts-20261117-v4n8s1/candidate_v3/
team_terminal_facts.py, SHA256 81803e31387bcbb52470a45bff5f4a68
f9bd7e480fc189f8f28f0daf5e250659.  Sole delta vs candidate_v2
(df314798205b325aa051534e208ddb88ff22b81096e72fa497e78b88264140d4): the
duplicated-registration_id message appends " (current uid {uid})" after
"already used by uid {first_uid}".  Counterexample A below asserts both uids
via robust uid-regexes (never the whole sentence).
Baseline source for the hash mirror: coscreen/team_terminal_facts.py at
73c58e0ae6b86d5430e7921e7f47acc01ab5e3d5afd53fd9b531f7aa6d73149c (untouched by
P02; the canonical-JSON + hash derivation algorithm is frozen there).

Honest declarations for this file (P02 test author, 2026-11-18):

1. Mirror-hash method.  ``test_v1_hashes_unchanged`` does NOT pin arbitrary
   hard-coded hash literals.  Expected seal_id / content_sha256 /
   payload_sha256 values are computed at runtime by the ``mirror_*`` functions
   below -- an independent re-implementation of the canonical serialization and
   hash-derivation algorithm (json.dumps ensure_ascii=False/sort_keys/
   separators=(",",":")/allow_nan=False; sha256 over UTF-8; content digest
   excludes "sealed_at"; seal_id digests the seal-id preimage object), derived
   by read-only parsing of the baseline source named above.  The test asserts
   candidate output == mirror output, i.e. algorithm equivalence with the
   frozen baseline algorithm.  A structural dual-path pin (hand-assembled
   canonical text) plus discriminating-content negative controls keep the
   mirror load-bearing; this is still an algorithm mirror, not a literal
   oracle, and is declared as such.
2. Static authorship.  This file was authored without importing the product,
   without executing SQL, and without running any test.  Execution is left to
   an independent runner after re-impact and review.  Every def/class symbol
   in this file went through a real GitNexus CLI ``impact --direction
   upstream`` check before writing (all "not found" / risk UNKNOWN, as
   expected for a new file; raws archived in the P02 scratch box
   p02-facts-tests-20261118-k3m7v2/impact_raw/).
3. v1 regressions are not rewritten.  The 54 existing v1 tests in
   build/tests/test_s2_terminal_facts.py stay untouched; the manifest is
   pinned by ``V1RegressionManifestTests`` below and listed in the P02 freeze
   report.  Run command (after P02 lands, from the repo root):
   python -m pytest build/tests/test_216_terminal_observation.py
           build/tests/test_s2_terminal_facts.py -v
   (the checked-in build/tests/conftest.py pins the process data dir; this
   file itself only ever touches tempfile-owned databases).
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from coscreen import team_terminal_facts as tf


STAMP = "2026-11-16T10:00:00.000Z"
STAMP2 = "2026-11-16T10:00:01.000Z"
ARCHIVE_STAMP = "2026-11-17T09:30:00.000Z"
ARCHIVE_STAMP_LATER = "2026-11-17T10:00:00.000Z"

# OC-DS dynamic matrix items are annotated on each test below: M1..M10 map to
# the ten numbered must-cover entries of the P02 test-author tasking.

# M10: the 54 existing v1 regression tests, in file order (not rewritten).
V1_REGRESSION_MANIFEST = (
    "test_prepare_creates_complete_layout_and_is_idempotent",
    "test_prepare_missing_db_fails_without_creating_it",
    "test_prepare_rejects_wrong_table_without_repair",
    "test_prepare_rejects_missing_trigger_without_repair",
    "test_extra_schema_objects_rejected_everywhere",
    "test_reserved_name_other_owner_objects_rejected",
    "test_prepare_cleanup_faults_observable_and_close_attempted",
    "test_read_list_require_native_active_row",
    "test_append_archive_require_native_active_row",
    "test_borrowed_fetch_stage_faults_typed_with_cause",
    "test_temp_namespace_shadow_and_temp_trigger_rejected",
    "test_borrowed_authorizer_faults_typed_with_cause",
    "test_read_list_do_not_begin_rollback_or_connect",
    "test_append_archive_do_not_commit_or_ddl",
    "test_valid_unanimous_roundtrip",
    "test_valid_adjudicated_roundtrip_is_self_contained",
    "test_source_request_operation_mapping",
    "test_task_id_and_instance_are_independent",
    "test_item_key_has_no_300_character_cap",
    "test_full_roster_and_non_required_members",
    "test_single_required_unanimous_is_legal",
    "test_roster_required_and_opinion_coverage_rejections",
    "test_owner_role_and_roster_field_rejections",
    "test_decision_rules",
    "test_source_row_and_hash_requirements_for_both_kinds",
    "test_opinion_text_fields_strict_both_kinds",
    "test_mixed_unknown_keys_and_surrogate_payload_invalid",
    "test_persisted_bad_opinions_and_surrogate_are_corrupt",
    "test_strict_schema_and_unknown_fields",
    "test_adjudicated_proof_binding_rejections",
    "test_origin_kind_trigger_restrictions",
    "test_append_idempotent_same_content_new_sealed_at",
    "test_append_idempotence_requires_same_origin",
    "test_append_conflict_does_not_overwrite_or_rollback",
    "test_append_rollback_and_commit_visibility",
    "test_read_joins_caller_transaction_and_does_not_open_snapshot",
    "test_read_absent_key_and_list_filters",
    "test_missing_table_is_not_prepared",
    "test_missing_trigger_is_schema_mismatch",
    "test_wrong_schema_is_schema_mismatch",
    "test_corrupt_json_hash_and_row_binding",
    "test_list_does_not_skip_one_bad_row",
    "test_triggers_block_update_and_delete",
    "test_archive_exact_copy_and_idempotent",
    "test_archive_does_not_reorigin_existing_actor_row",
    "test_archive_batch_prevalidation_zero_dml",
    "test_archive_batch_second_insert_storage_failure_rollback",
    "test_archive_same_content_collision_preserves_old_archived_at",
    "test_archive_conflict_does_not_overwrite",
    "test_archive_rejects_non_actor_or_non_last_vote_sources",
    "test_archive_target_missing_table_is_not_prepared",
    "test_archive_transaction_rollback_and_commit",
    "test_canonical_json_parity_with_arbitration_serializer",
    "test_expected_hashes_are_stdlib_computed",
)


# ---------------------------------------------------------------------------
# Mirror of the baseline (73c58e0a) canonical-JSON + hash derivation algorithm.
#
# Honesty note: this is an ALGORITHM MIRROR, re-implemented independently in
# the test from a read-only parse of the baseline source.  It is not a table
# of arbitrary frozen literals; expected values are recomputed at runtime and
# asserted equal to the candidate's output (algorithm equivalence).
# ---------------------------------------------------------------------------

def mirror_canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def mirror_sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def mirror_sha256_json(value):
    return mirror_sha256_text(mirror_canonical_json(value))


def mirror_content_sha256(payload):
    content = {key: item for key, item in payload.items() if key != "sealed_at"}
    return mirror_sha256_json(content)


def mirror_seal_id(task_instance_id, stage, item_key, content_sha256):
    return mirror_sha256_json({
        "schema": "team_terminal_seal_id.v1",
        "task_instance_id": task_instance_id,
        "stage": stage,
        "item_key": item_key,
        "content_sha256": content_sha256,
    })


def mirror_expected_metadata(payload):
    content_sha256 = mirror_content_sha256(payload)
    payload_json = mirror_canonical_json(payload)
    return {
        "content_sha256": content_sha256,
        "seal_id": mirror_seal_id(
            payload["task"]["task_instance_id"], payload["stage"],
            payload["item_key"], content_sha256),
        "payload_json": payload_json,
        "payload_sha256": mirror_sha256_text(payload_json),
    }


# ---------------------------------------------------------------------------
# Fixture builders (stdlib only; no product internals imported).
# ---------------------------------------------------------------------------

def fixture_opinion_hash(opinion):
    return mirror_sha256_json({
        "decision": str(opinion["decision"]).strip().lower(),
        "exclusion_reason": str(opinion["exclusion_reason"]),
        "notes": str(opinion["notes"]),
    })


def fixture_source_row_hash(source_row):
    return mirror_sha256_json(source_row)


def fixture_default_roster():
    return [
        {
            "uid": 1, "username": "owner", "role": "reconciler",
            "is_owner": True, "screening_enabled": False,
            "required_screener": False, "source": "owner-source",
            "config": "ok",
        },
        {
            "uid": 2, "username": "alice", "role": "coder",
            "is_owner": False, "screening_enabled": None,
            "required_screener": True, "source": "import-source",
            "config": "ok",
        },
        {
            "uid": 3, "username": "bob", "role": "coder",
            "is_owner": False, "screening_enabled": None,
            "required_screener": True, "source": "grant-source",
            "config": "ok",
        },
    ]


def fixture_make_opinion(uid, decision):
    return {
        "uid": uid,
        "decision": decision,
        "exclusion_reason": "",
        "notes": "",
        "library_id": "%032x" % uid,
        "revision": uid + 10,
        "source_row": {
            "tags": "tag-%s" % uid,
            "screened_at": "2026-01-02 03:04:05",
            "updated_at": None if uid % 2 else "2026-01-03 04:05:06",
        },
        "opinion_sha256": "",
        "source_row_sha256": "",
    }


def fixture_make_v1_payload(*, item_key="K1", task_instance_id="instance-1",
                            stage="stage1", sealed_at=STAMP, roster=None):
    roster = copy.deepcopy(
        fixture_default_roster() if roster is None else roster)
    owner = roster[0]
    for entry in roster:
        if entry["is_owner"]:
            owner = entry
    required_uids = sorted(
        entry["uid"] for entry in roster if entry["required_screener"])
    opinions = [
        fixture_make_opinion(uid, "include") for uid in required_uids]
    for opinion in opinions:
        opinion["opinion_sha256"] = fixture_opinion_hash(opinion)
        opinion["source_row_sha256"] = fixture_source_row_hash(
            opinion["source_row"])
    return {
        "schema": "team_terminal_seal.v1",
        "task": {
            "task_id": "task-1",
            "task_instance_id": task_instance_id,
            "owner_user_id": owner["uid"],
            "owner_username": owner["username"],
            "owner_library_id": "a" * 32,
        },
        "stage": stage,
        "item_key": item_key,
        "round_id": None,
        "kind": "unanimous",
        "trigger": "last_vote",
        "final_decision": "include",
        "roster": roster,
        "required_uids": required_uids,
        "opinions": opinions,
        "arbitration": None,
        "protocol": {
            "source_registration_id": "registration-1",
            "roster_revision": 7,
        },
        "source_request": {
            "operation": "vote_save",
            "request_id": "request-1",
            "request_sha256": "b" * 64,
        },
        "sealed_at": sealed_at,
    }


def fixture_make_bindings(uids):
    return [
        {
            "source_user_id": uid,
            "registration_id": "registration-%d" % uid,
            "generation": 1,
            "source_library_id": "%032x" % uid,
        }
        for uid in sorted(uids)
    ]


def fixture_make_v2_observation(*, observer_uid=1, item_key="K1",
                                host_item_keys=None, host_stage="fulltext",
                                task_instance_id="instance-1", stage="stage1",
                                sealed_at=STAMP, roster=None):
    payload = fixture_make_v1_payload(
        item_key=item_key, task_instance_id=task_instance_id, stage=stage,
        sealed_at=sealed_at, roster=roster)
    uids = sorted(set(payload["required_uids"]) | {observer_uid})
    payload["schema"] = tf.OBSERVATION_PAYLOAD_SCHEMA
    payload["trigger"] = "current_observation"
    payload["observation"] = {
        "host_operation": "completion_observe",
        "host_stage": host_stage,
        "host_item_keys": (
            list(host_item_keys) if host_item_keys is not None else [item_key]),
        "observed_by_user_id": observer_uid,
        "basis": "current_snapshot",
    }
    payload["source_bindings"] = fixture_make_bindings(uids)
    payload["protocol"]["source_registration_id"] = (
        "registration-%d" % observer_uid)
    payload["source_request"] = {
        "operation": "completion_observe",
        "request_id": "request-obs-1",
        "request_sha256": "b" * 64,
    }
    return payload


def fixture_extract_v1_regression_names(source_text):
    return re.findall(r"(?m)^    def (test_\w+)\(", source_text)


# ---------------------------------------------------------------------------
# Shared base
# ---------------------------------------------------------------------------

class TerminalObservationTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="rf216_f1_obs_")
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.db = self.base / "actor.db"
        conn = sqlite3.connect(str(self.db))
        conn.close()
        tf.prepare_team_terminal_facts(self.db)

    def _open(self, path=None):
        conn = sqlite3.connect(str(path or self.db), timeout=10)
        self.addCleanup(TerminalObservationTestBase._close_quietly, conn)
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        return conn

    def _prepared_db(self, name):
        path = self.base / name
        conn = sqlite3.connect(str(path))
        conn.close()
        tf.prepare_team_terminal_facts(path)
        return path

    @staticmethod
    def _close_quietly(conn):
        try:
            if conn.in_transaction:
                conn.rollback()
        except sqlite3.Error:
            pass
        try:
            conn.close()
        except sqlite3.Error:
            pass


# ---------------------------------------------------------------------------
# M1: v1 hash freeze via the baseline algorithm mirror
# ---------------------------------------------------------------------------

class V1HashFreezeTests(TerminalObservationTestBase):
    def test_v1_hashes_unchanged(self):
        """M1: fixed v1 payload keeps seal_id/content_sha256/payload_sha256.

        Expected values come from the mirror implementation of the baseline
        algorithm (see honesty note in the module docstring), not from
        arbitrary literals.
        """
        payload = fixture_make_v1_payload()
        expected = mirror_expected_metadata(payload)
        with closing(self._open()) as conn, conn:
            record = tf.append_seal_on(conn, payload, origin="actor")
            stored = tf.read_seal_on(
                conn, task_instance_id="instance-1", stage="stage1",
                item_key="K1")
            listed = tf.list_seals_on(
                conn, task_instance_id="instance-1")
        self.assertEqual(len(listed), 1)
        for record_to_check in (record, stored, listed[0]):
            self.assertEqual(record_to_check["seal_id"], expected["seal_id"])
            self.assertEqual(
                record_to_check["content_sha256"], expected["content_sha256"])
            self.assertEqual(
                record_to_check["payload_sha256"], expected["payload_sha256"])
            self.assertEqual(record_to_check["payload"], payload)
        probes = (
            {"z": 1, "a": {"c": [1, 2], "b": None}},
            {"k": "中文-screen", "nested": {"x": [True, False, None]}},
            [1, [2, {"n": 3}], {"m": []}],
        )
        for probe in probes:
            self.assertEqual(
                tf.canonical_json(probe), mirror_canonical_json(probe))
            self.assertEqual(tf.sha256(probe), mirror_sha256_json(probe))
        self.assertEqual(
            mirror_canonical_json({"b": 2, "a": 1}), '{"a":1,"b":2}')
        self.assertEqual(
            mirror_sha256_text('{"a":1,"b":2}'),
            mirror_sha256_json({"b": 2, "a": 1}))

    def test_v1_hashes_discriminate_changed_content(self):
        """M1 negative control: the mirror is load-bearing, not vacuous."""
        base = fixture_make_v1_payload(item_key="K1")
        meta_base = mirror_expected_metadata(base)
        later = copy.deepcopy(base)
        later["sealed_at"] = STAMP2
        meta_later = mirror_expected_metadata(later)
        self.assertEqual(
            meta_base["content_sha256"], meta_later["content_sha256"])
        self.assertEqual(meta_base["seal_id"], meta_later["seal_id"])
        self.assertNotEqual(
            meta_base["payload_sha256"], meta_later["payload_sha256"])
        other_key = mirror_expected_metadata(
            fixture_make_v1_payload(item_key="K2"))
        self.assertNotEqual(meta_base["seal_id"], other_key["seal_id"])
        changed = copy.deepcopy(base)
        changed["opinions"][0]["decision"] = "exclude"
        meta_changed = mirror_expected_metadata(changed)
        self.assertNotEqual(
            meta_base["content_sha256"], meta_changed["content_sha256"])
        self.assertNotEqual(tf.sha256(base), tf.sha256(changed))


# ---------------------------------------------------------------------------
# M2: legal v2 observation appends (observer inside / outside required)
# ---------------------------------------------------------------------------

class ObservationAppendTests(TerminalObservationTestBase):
    def test_observation_source_binding_and_host_request(self):
        """M2 (observer IN required): append, read/list return same payload."""
        payload = fixture_make_v2_observation(
            observer_uid=2, host_item_keys=["K1", "K2"])
        self.assertIn(2, payload["required_uids"])
        with closing(self._open()) as conn, conn:
            record = tf.append_seal_on(conn, payload, origin="actor")
            read = tf.read_seal_on(
                conn, task_instance_id="instance-1", stage="stage1",
                item_key="K1")
            listed = tf.list_seals_on(
                conn, task_instance_id="instance-1", origin="actor")
        stored_payload = record["payload"]
        self.assertEqual(
            stored_payload["schema"], tf.OBSERVATION_PAYLOAD_SCHEMA)
        self.assertEqual(stored_payload["trigger"], "current_observation")
        self.assertEqual(
            stored_payload["source_request"]["operation"],
            "completion_observe")
        self.assertEqual(
            stored_payload["source_request"]["request_id"], "request-obs-1")
        self.assertEqual(
            stored_payload["source_request"]["request_sha256"], "b" * 64)
        self.assertEqual(
            stored_payload["observation"]["host_item_keys"], ["K1", "K2"])
        self.assertEqual(
            [binding["source_user_id"]
             for binding in stored_payload["source_bindings"]], [2, 3])
        self.assertEqual(
            stored_payload["protocol"]["source_registration_id"],
            "registration-2")
        self.assertEqual(read["payload"], stored_payload)
        self.assertEqual(read["seal_id"], record["seal_id"])
        self.assertEqual([item["payload"] for item in listed],
                         [stored_payload])
        managed = fixture_make_v2_observation(
            observer_uid=3, item_key="K3", host_stage=None)
        with closing(self._open()) as conn, conn:
            record_managed = tf.append_seal_on(
                conn, managed, origin="actor")
        self.assertIsNone(
            record_managed["payload"]["observation"]["host_stage"])

    def test_observer_outside_required(self):
        """M2 (observer OUTSIDE required): collab-originated v2 is legal."""
        payload = fixture_make_v2_observation(observer_uid=1)
        self.assertNotIn(1, payload["required_uids"])
        self.assertEqual(
            [binding["source_user_id"]
             for binding in payload["source_bindings"]], [1, 2, 3])
        with closing(self._open()) as conn, conn:
            record = tf.append_seal_on(conn, payload, origin="collab")
            read = tf.read_seal_on(
                conn, task_instance_id="instance-1", stage="stage1",
                item_key="K1")
            listed = tf.list_seals_on(
                conn, task_instance_id="instance-1", origin="collab")
        self.assertEqual(record["payload"]["schema"],
                         tf.OBSERVATION_PAYLOAD_SCHEMA)
        self.assertEqual(
            record["payload"]["protocol"]["source_registration_id"],
            "registration-1")
        self.assertEqual(read["payload"], record["payload"])
        self.assertEqual([item["payload"] for item in listed],
                         [record["payload"]])


# ---------------------------------------------------------------------------
# M3..M7: v2 payload rejections (all typed PayloadInvalid)
# ---------------------------------------------------------------------------

class ObservationPayloadRejectionTests(TerminalObservationTestBase):
    def assert_rejected(self, payload, *, origin="actor", fragment=None):
        with self.assertRaises(tf.TerminalFactsPayloadInvalid) as caught:
            with closing(self._open()) as conn, conn:
                tf.append_seal_on(conn, payload, origin=origin)
        if fragment is not None:
            self.assertIn(fragment, str(caught.exception))
        return caught.exception

    def test_observation_required_sections_kind_trigger_operation(self):
        """M3: missing sections, wrong kind/trigger/operation, arbitration."""
        good = fixture_make_v2_observation(observer_uid=2)
        missing_observation = copy.deepcopy(good)
        del missing_observation["observation"]
        self.assert_rejected(missing_observation, fragment="fields mismatch")
        missing_bindings = copy.deepcopy(good)
        del missing_bindings["source_bindings"]
        self.assert_rejected(missing_bindings, fragment="fields mismatch")
        bad_kind = copy.deepcopy(good)
        bad_kind["kind"] = "adjudicated"
        self.assert_rejected(bad_kind, fragment="must be unanimous")
        bad_trigger = copy.deepcopy(good)
        bad_trigger["trigger"] = "last_vote"
        self.assert_rejected(
            bad_trigger,
            fragment="v2 payload trigger must be 'current_observation'")
        bad_operation = copy.deepcopy(good)
        bad_operation["source_request"]["operation"] = "vote_save"
        self.assert_rejected(
            bad_operation, fragment="must be 'completion_observe'")
        arbitrated = copy.deepcopy(good)
        arbitrated["arbitration"] = {"arbitrator_user_id": 1}
        self.assert_rejected(
            arbitrated, fragment="must not carry arbitration")
        with closing(self._open()) as conn, conn:
            tf.append_seal_on(conn, good, origin="actor")

    def test_observation_host_item_keys_rejections(self):
        """M4: empty / duplicate / item_key-absent / NUL host keys."""
        good = fixture_make_v2_observation(
            observer_uid=2, host_item_keys=["K1", "K2"])
        empty = copy.deepcopy(good)
        empty["observation"]["host_item_keys"] = []
        self.assert_rejected(
            empty, fragment="observation.host_item_keys must be a non-empty "
                            "list")
        duplicated = copy.deepcopy(good)
        duplicated["observation"]["host_item_keys"] = ["K1", "K1"]
        self.assert_rejected(
            duplicated, fragment="must contain unique canonical keys")
        without_item_key = copy.deepcopy(good)
        without_item_key["observation"]["host_item_keys"] = ["K2"]
        self.assert_rejected(
            without_item_key,
            fragment="observation.host_item_keys must contain payload"
                     ".item_key")
        nul = copy.deepcopy(good)
        nul["observation"]["host_item_keys"] = ["K1", "K\x002"]
        self.assert_rejected(nul, fragment="must not contain NUL")
        with closing(self._open()) as conn, conn:
            tf.append_seal_on(conn, good, origin="actor")

    def test_observation_source_bindings_order_and_coverage(self):
        """M5: unsorted / duplicate uid / missing required / missing
        observer / extra uid are all rejected."""
        good = fixture_make_v2_observation(observer_uid=1)
        unsorted = copy.deepcopy(good)
        unsorted["source_bindings"] = list(
            reversed(unsorted["source_bindings"]))
        self.assert_rejected(
            unsorted,
            fragment="source_bindings must be sorted by source_user_id "
                     "without duplicates")
        duplicate_uid = copy.deepcopy(good)
        duplicate_uid["source_bindings"] = (
            fixture_make_bindings([1, 2]) + fixture_make_bindings([2]))
        self.assert_rejected(
            duplicate_uid,
            fragment="sorted by source_user_id without duplicates")
        missing_required = copy.deepcopy(good)
        missing_required["source_bindings"] = fixture_make_bindings([1, 2])
        self.assert_rejected(
            missing_required,
            fragment="source_bindings must cover required_uids and "
                     "observed_by_user_id")
        missing_observer = copy.deepcopy(good)
        missing_observer["source_bindings"] = fixture_make_bindings([2, 3])
        self.assert_rejected(
            missing_observer,
            fragment="source_bindings must cover required_uids and "
                     "observed_by_user_id")
        extra_uid = copy.deepcopy(good)
        extra_uid["source_bindings"] = fixture_make_bindings([1, 2, 3, 4])
        self.assert_rejected(
            extra_uid,
            fragment="source_bindings must cover required_uids and "
                     "observed_by_user_id")
        with closing(self._open()) as conn, conn:
            tf.append_seal_on(conn, good, origin="actor")

    def test_observation_binding_consistency_rejections(self):
        """M6: opinion library_id and protocol.source_registration_id must
        match the corresponding bindings."""
        good = fixture_make_v2_observation(observer_uid=1)
        opinion_mismatch = copy.deepcopy(good)
        opinion_mismatch["source_bindings"][1]["source_library_id"] = "f" * 32
        self.assert_rejected(
            opinion_mismatch,
            fragment="opinion library_id must match its source binding")
        protocol_mismatch = copy.deepcopy(good)
        protocol_mismatch["protocol"]["source_registration_id"] = (
            "registration-9")
        self.assert_rejected(
            protocol_mismatch,
            fragment="protocol.source_registration_id must match the "
                     "observer binding")
        protocol_none = copy.deepcopy(good)
        protocol_none["protocol"]["source_registration_id"] = None
        self.assert_rejected(
            protocol_none,
            fragment="protocol.source_registration_id must match the "
                     "observer binding")
        with closing(self._open()) as conn, conn:
            tf.append_seal_on(conn, good, origin="actor")

    def test_registration_id_duplicate_nul_and_check_ordering(self):
        """M7: counterexample A (duplicated registration_id across two
        bindings), counterexample B (NUL in registration_id), and
        existing-check-before-new-check ordering."""
        roster = [
            {"uid": 5, "username": "owner5", "role": "reconciler",
             "is_owner": True, "screening_enabled": False,
             "required_screener": False, "source": "owner5-source",
             "config": "ok"},
            {"uid": 6, "username": "cara", "role": "coder",
             "is_owner": False, "screening_enabled": None,
             "required_screener": True, "source": "import5-source",
             "config": "ok"},
            {"uid": 7, "username": "dan", "role": "coder",
             "is_owner": False, "screening_enabled": None,
             "required_screener": True, "source": "grant5-source",
             "config": "ok"},
        ]
        duplicate = fixture_make_v2_observation(
            observer_uid=5, roster=roster)
        duplicate["source_bindings"][1]["registration_id"] = "registration-7"
        exception = self.assert_rejected(
            duplicate, fragment="duplicated across bindings")
        message = str(exception)
        self.assertTrue(
            re.search(r"uid\D*6", message),
            "first binding uid 6 must be identified: %r" % message)
        self.assertTrue(
            re.search(r"uid\D*7", message),
            "current binding uid 7 must be identified: %r" % message)
        nul = fixture_make_v2_observation(observer_uid=1)
        nul["source_bindings"][0]["registration_id"] = "registration-\x000"
        exception = self.assert_rejected(nul, fragment="must not contain NUL")
        self.assertIn("(uid 1)", str(exception))
        order_binding = fixture_make_v2_observation(observer_uid=1)
        order_binding["source_bindings"][0]["registration_id"] = (
            "registration-\x000")
        order_binding["source_bindings"][0]["source_library_id"] = "not-hex"
        exception = self.assert_rejected(
            order_binding, fragment="must be 32 lowercase hex characters")
        self.assertNotIn("must not contain NUL", str(exception))
        order_v1_side = fixture_make_v2_observation(observer_uid=1)
        order_v1_side["opinions"][0]["source_row_sha256"] = "f" * 64
        order_v1_side["source_bindings"] = list(
            reversed(order_v1_side["source_bindings"]))
        exception = self.assert_rejected(
            order_v1_side,
            fragment="source_row_sha256 does not match source_row")
        self.assertNotIn("sorted by source_user_id", str(exception))
        fixed = fixture_make_v2_observation(observer_uid=1)
        with closing(self._open()) as conn, conn:
            tf.append_seal_on(conn, fixed, origin="actor")


# ---------------------------------------------------------------------------
# M8: idempotence, conflict, and rollback zero-persistence
# ---------------------------------------------------------------------------

class AppendIdempotenceConflictTests(TerminalObservationTestBase):
    def test_observation_same_content_new_sealed_at_idempotent(self):
        """M8: same content with a new sealed_at returns the FIRST record."""
        payload = fixture_make_v2_observation(observer_uid=2)
        with closing(self._open()) as conn, conn:
            first = tf.append_seal_on(conn, payload, origin="actor")
        again = copy.deepcopy(payload)
        again["sealed_at"] = STAMP2
        with closing(self._open()) as conn, conn:
            second = tf.append_seal_on(conn, again, origin="actor")
            listed = tf.list_seals_on(conn, task_instance_id="instance-1")
        self.assertEqual(second["seal_id"], first["seal_id"])
        self.assertEqual(second["sealed_at"], STAMP)
        self.assertEqual(second["payload"], first["payload"])
        self.assertEqual(len(listed), 1)

    def test_observation_same_key_different_content_conflict(self):
        """M8: same key with different content is Conflict; a same-content
        append under the other origin is Conflict too (never re-origin)."""
        with closing(self._open()) as conn, conn:
            first = tf.append_seal_on(
                conn, fixture_make_v2_observation(observer_uid=2),
                origin="actor")
        variant = fixture_make_v2_observation(observer_uid=2)
        variant["observation"]["host_operation"] = "completion_observe_2"
        with closing(self._open()) as conn, conn:
            with self.assertRaises(tf.TerminalFactsConflict) as caught:
                tf.append_seal_on(conn, variant, origin="actor")
        self.assertIn("different content", str(caught.exception))
        same = fixture_make_v2_observation(observer_uid=2)
        with closing(self._open()) as conn, conn:
            with self.assertRaises(tf.TerminalFactsConflict) as caught:
                tf.append_seal_on(conn, same, origin="collab")
        self.assertIn("different origin", str(caught.exception))
        with closing(self._open()) as conn, conn:
            listed = tf.list_seals_on(conn, task_instance_id="instance-1")
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["payload"], first["payload"])

    def test_rejected_or_conflicting_append_rolls_back(self):
        """M8: after a rejected or conflicting append, the caller's rollback
        leaves zero persisted state beyond what was already committed."""
        bad = fixture_make_v2_observation(observer_uid=2)
        del bad["observation"]
        conn = self._open()
        with self.assertRaises(tf.TerminalFactsPayloadInvalid):
            tf.append_seal_on(conn, bad, origin="actor")
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM main.team_terminal_seals"
        ).fetchone()["n"]
        self.assertEqual(count, 0)
        conn.rollback()
        conn.execute("BEGIN")
        self.assertIsNone(
            tf.read_seal_on(
                conn, task_instance_id="instance-1", stage="stage1",
                item_key="K1"))
        self.assertEqual(
            tf.list_seals_on(conn, task_instance_id="instance-1"), [])
        conn.rollback()
        with closing(self._open()) as conn, conn:
            first = tf.append_seal_on(
                conn, fixture_make_v2_observation(observer_uid=2),
                origin="actor")
        variant = fixture_make_v2_observation(observer_uid=2)
        variant["observation"]["host_operation"] = "completion_observe_2"
        conn = self._open()
        with self.assertRaises(tf.TerminalFactsConflict):
            tf.append_seal_on(conn, variant, origin="actor")
        conn.rollback()
        conn.execute("BEGIN")
        listed = tf.list_seals_on(conn, task_instance_id="instance-1")
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["payload"], first["payload"])
        conn.rollback()


# ---------------------------------------------------------------------------
# M9: actor v2 archiving (preserve everything, new archived_at)
# ---------------------------------------------------------------------------

class ObservationArchiveTests(TerminalObservationTestBase):
    def test_archive_observation_preserves_original(self):
        """M9: actor v2 archive keeps payload/ID/three digests/original
        sealed_at and records a fresh archived_at; re-archive keeps the
        first archived_at."""
        payload = fixture_make_v2_observation(observer_uid=2)
        with closing(self._open()) as conn, conn:
            source = tf.append_seal_on(conn, payload, origin="actor")
        dst_path = self._prepared_db("archive.db")
        with closing(self._open(dst_path)) as dst, dst:
            archived = tf.archive_seals_on(
                dst, [source], archived_at=ARCHIVE_STAMP)
        self.assertEqual(len(archived), 1)
        archived = archived[0]
        self.assertEqual(archived["origin"], "actor_archive")
        self.assertEqual(archived["archived_at"], ARCHIVE_STAMP)
        self.assertNotEqual(archived["archived_at"], archived["sealed_at"])
        self.assertEqual(archived["sealed_at"], source["sealed_at"])
        self.assertEqual(archived["seal_id"], source["seal_id"])
        self.assertEqual(
            archived["content_sha256"], source["content_sha256"])
        self.assertEqual(
            archived["payload_sha256"], source["payload_sha256"])
        self.assertEqual(archived["payload"], source["payload"])
        self.assertEqual(
            tf.canonical_json(archived["payload"]),
            tf.canonical_json(source["payload"]))
        with closing(self._open(dst_path)) as dst, dst:
            read = tf.read_seal_on(
                dst, task_instance_id="instance-1", stage="stage1",
                item_key="K1")
            listed = tf.list_seals_on(
                dst, task_instance_id="instance-1", origin="actor_archive")
        self.assertEqual(read["payload"], source["payload"])
        self.assertEqual(read["archived_at"], ARCHIVE_STAMP)
        self.assertEqual(
            [row["seal_id"] for row in listed], [source["seal_id"]])
        with closing(self._open(dst_path)) as dst, dst:
            again = tf.archive_seals_on(
                dst, [source], archived_at=ARCHIVE_STAMP_LATER)
        self.assertEqual(again[0]["archived_at"], ARCHIVE_STAMP)

    def test_collab_origin_observation_cannot_archive(self):
        """M9 negative control: collab-originated v2 is not archiveable."""
        payload = fixture_make_v2_observation(observer_uid=1)
        with closing(self._open()) as conn, conn:
            collab = tf.append_seal_on(conn, payload, origin="collab")
        dst_path = self._prepared_db("archive_collab.db")
        with closing(self._open(dst_path)) as dst, dst:
            with self.assertRaises(tf.TerminalFactsPayloadInvalid) as caught:
                tf.archive_seals_on(
                    dst, [collab], archived_at=ARCHIVE_STAMP)
            self.assertIn(
                "only origin 'actor' seals can be archived",
                str(caught.exception))
            self.assertEqual(
                tf.list_seals_on(dst, task_instance_id="instance-1"), [])

    def test_archive_observation_same_key_different_content_conflicts(self):
        """M9: archiving onto an archive target that already holds a
        different seal under the same key is Conflict."""
        with closing(self._open()) as conn, conn:
            v1_source = tf.append_seal_on(
                conn, fixture_make_v1_payload(), origin="actor")
        dst_path = self._prepared_db("archive_conflict.db")
        with closing(self._open(dst_path)) as dst, dst:
            tf.archive_seals_on(
                dst, [v1_source], archived_at="2026-11-17T08:00:00.000Z")
        actor2_path = self._prepared_db("actor2.db")
        with closing(self._open(actor2_path)) as conn, conn:
            obs_source = tf.append_seal_on(
                conn, fixture_make_v2_observation(observer_uid=2),
                origin="actor")
        with closing(self._open(dst_path)) as dst, dst:
            with self.assertRaises(tf.TerminalFactsConflict) as caught:
                tf.archive_seals_on(
                    dst, [obs_source], archived_at="2026-11-17T09:00:00.000Z")
        self.assertIn(
            "archive target already holds different content for this key",
            str(caught.exception))


# ---------------------------------------------------------------------------
# M10: v1 regression manifest (listed, not rewritten)
# ---------------------------------------------------------------------------

class V1RegressionManifestTests(unittest.TestCase):
    def test_v1_regression_manifest_pins_54_tests(self):
        """M10: the 54 untouched v1 regression tests are pinned by name."""
        source_path = (
            Path(__file__).resolve().parent / "test_s2_terminal_facts.py")
        source = source_path.read_text(encoding="utf-8")
        extracted = fixture_extract_v1_regression_names(source)
        self.assertEqual(len(extracted), 54)
        self.assertEqual(extracted, list(V1_REGRESSION_MANIFEST))
        for cls_name in (
            "PrepareTests", "BorrowedContractTests", "PayloadValidationTests",
            "AppendReadTests", "ArchiveTests", "CanonicalTests",
        ):
            self.assertIn("class %s(" % cls_name, source)


if __name__ == "__main__":
    unittest.main()
