"""Task workflow policies and queues using identities verified by the host."""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field

from coscreen import coding, review_workflow as store, task_collaboration, tasks
from coscreen.models import Article
from custom_backend.events import BUS

Stage = Literal["screen", "fulltext", "coding"]


@dataclass
class WorkflowContext:
    info: tasks.TaskInfo
    user: dict
    owner: dict | None
    owner_db: Path
    actor_db: Path
    can_manage: bool
    task_kind: str
    collaboration_db: Path


class WorkflowRuntime:
    def __init__(self, task_lookup, screener_db, data_dir, visible) -> None:
        self.task_lookup, self.screener_db = task_lookup, screener_db
        self.data_dir, self.visible = Path(data_dir), visible
        self._locks: dict[str, threading.RLock] = {}
        self._locks_guard = threading.Lock()

    def lock(self, task_id: str) -> threading.RLock:
        with self._locks_guard:
            return self._locks.setdefault(task_id, threading.RLock())

    def context(self, task_id: str, user: dict) -> WorkflowContext:
        info = self.task_lookup(task_id)
        if not self.visible(info, user):
            raise HTTPException(403, "Task access denied.")
        collab = task_collaboration.shared_store_path(info.dir)
        owner = task_collaboration.get_owner(collab, task_id=task_id)
        # Legacy reads must not initialize databases or create backup files.
        actor_db = info.dir / (tasks.slugify_screener(user["username"]) + ".db")
        if owner is None:
            return WorkflowContext(info, user, None, actor_db, actor_db, False, "legacy", collab)
        with self.lock(task_id):
            owner_db = self.screener_db(info, owner["owner_username"])
            store.migrate(owner_db)
            grants = task_collaboration.list_role_grants(collab, actor_user_id=owner["owner_user_id"], task_id=task_id)
            if any(grant["user_id"] != owner["owner_user_id"] for grant in grants):
                self.latch_team_db(owner_db)
            kind = "team" if store.settings(owner_db)["team_latched"] else "personal"
            actor_db = self.screener_db(info, user["username"])
            store.migrate(actor_db)
            return WorkflowContext(info, user, owner, owner_db, actor_db,
                                   owner["owner_user_id"] == user["user_id"], kind, collab)

    @staticmethod
    def latch_team_db(db: Path) -> None:
        with closing(store.connect(db)) as conn, conn:
            conn.execute("UPDATE workflow_state SET team_latched=1 WHERE singleton=1 AND team_latched=0")

    def latch_team(self, task_id: str, user: dict) -> None:
        with self.lock(task_id):
            # context discovers only non-owner grants; a self-grant must not
            # change the task kind. Call before revoking an existing grant.
            self.context(task_id, user)

    def settings(self, ctx: WorkflowContext) -> dict:
        data = store.settings(ctx.owner_db)
        return {key: data[key] for key in ("version", "stages", "evidence_required_field_ids")} | {
            "task_kind": ctx.task_kind, "can_manage": ctx.can_manage, "sources": store.sources(ctx.owner_db)}

    def notify(self, ctx: WorkflowContext, reason: str) -> None:
        if ctx.owner is None:
            return
        # Recheck participant IDs after commit; events contain no personal work.
        try:
            ids = task_collaboration.list_participant_ids(ctx.collaboration_db,
                actor_user_id=ctx.user["user_id"], task_id=ctx.info.task_id)
        except PermissionError:
            return
        event = {"type": "task-workflow-changed", "task_id": ctx.info.task_id,
                 "version": store.settings(ctx.owner_db)["version"], "reason": reason}
        for actor in ids:
            BUS.publish(actor, event)

    def sync_catalog(self, ctx: WorkflowContext) -> None:
        if ctx.owner is not None and not ctx.can_manage:
            from custom_backend.shared_corpus import sync_shared_articles
            sync_shared_articles(ctx.info.dir, owner_username=ctx.owner["owner_username"],
                                 member_username=ctx.user["username"],
                                 catalog_revision=store.catalog_revision(ctx.owner_db))

    @staticmethod
    def article_rows(db: Path, *, compact: bool = False) -> list[dict]:
        if not db.is_file():
            return []
        with closing(store.connect(db, read_only=True)) as conn:
            if not store.has_table(conn, "articles"):
                return []
            columns = "zotero_key,title,authors,journal,year,doi,item_type,is_duplicate_of,import_order" if compact else "*"
            return [dict(row) for row in conn.execute(f"SELECT {columns} FROM articles ORDER BY import_order,zotero_key")]

    @staticmethod
    def article_row(db: Path, key: str) -> dict | None:
        if not db.is_file():
            return None
        with closing(store.connect(db, read_only=True)) as conn:
            if not store.has_table(conn, "articles"):
                return None
            row = conn.execute("SELECT * FROM articles WHERE zotero_key=?", (key,)).fetchone()
            return dict(row) if row else None

    @staticmethod
    def saved_rows(db: Path, stage: str) -> dict:
        table = {"screen": "decisions", "fulltext": "stage2_decisions", "coding": "coding_values"}[stage]
        if not db.is_file():
            return {}
        with closing(store.connect(db, read_only=True)) as conn:
            if not store.has_table(conn, table):
                return {}
            if stage == "coding":
                result: dict = {}
                for row in conn.execute("SELECT zotero_key,dimension_id,value FROM coding_values"):
                    result.setdefault(row[0], {})[row[1]] = row[2]
                return result
            return {row["zotero_key"]: dict(row) for row in conn.execute(f"SELECT * FROM {table}")}

    @staticmethod
    def saved_item(db: Path, stage: str, key: str) -> dict | None:
        table = {"screen": "decisions", "fulltext": "stage2_decisions", "coding": "coding_values"}[stage]
        if not db.is_file():
            return None
        with closing(store.connect(db, read_only=True)) as conn:
            if not store.has_table(conn, table):
                return None
            if stage == "coding":
                return {row[0]: row[1] for row in conn.execute(
                    "SELECT dimension_id,value FROM coding_values WHERE zotero_key=?", (key,))}
            row = conn.execute(f"SELECT * FROM {table} WHERE zotero_key=?", (key,)).fetchone()
            return dict(row) if row else None

    def eligible(self, ctx: WorkflowContext, stage: str) -> tuple[list[dict], dict]:
        rows = [row for row in self.article_rows(ctx.owner_db, compact=True) if row.get("is_duplicate_of") is None]
        # Owner's per-key title review status is authoritative for every reviewer.
        if ctx.owner_db.is_file():
            with closing(store.connect(ctx.owner_db, read_only=True)) as conn:
                blocked = set()
                if store.has_table(conn, "title_duplicate_members"):
                    blocked = {row[0] for row in conn.execute("SELECT m.zotero_key FROM title_duplicate_members m JOIN title_duplicate_reviews r ON r.review_id=m.review_id WHERE m.imported_in_batch=1 AND r.status IN ('pending','undecided')")}
            rows = [row for row in rows if row["zotero_key"] not in blocked]
        saved = self.saved_rows(ctx.actor_db, stage)
        if stage == "fulltext":
            initial = self.saved_rows(ctx.actor_db, "screen")
            rows = [row for row in rows if initial.get(row["zotero_key"], {}).get("decision") in {"include", "maybe"}]
        elif stage == "coding":
            initial = self.saved_rows(ctx.actor_db, "screen")
            second = self.saved_rows(ctx.actor_db, "fulltext")
            included = {key for key, decision in second.items() if decision["decision"] == "include"}
            if not included:
                included = {key for key, decision in initial.items() if decision["decision"] == "include"
                            and second.get(key, {}).get("decision") != "maybe"}
            rows = [row for row in rows if row["zotero_key"] in included]
        return rows, saved

    def allowed_sources(self, ctx: WorkflowContext, stage: str) -> list[str]:
        policy = store.settings(ctx.owner_db)["stages"][stage]
        active = {source["id"] for source in store.sources(ctx.owner_db) if source["active"]}
        return sorted(active & set(policy["source_ids"]) if policy["mode"] == "selected" else active)

    def scoped(self, ctx: WorkflowContext, stage: str, rows: list[dict], source_id: str | None = None) -> list[dict]:
        allowed = set(self.allowed_sources(ctx, stage))
        if source_id is not None:
            if source_id not in {source["id"] for source in store.sources(ctx.owner_db)}:
                raise HTTPException(400, "Unknown source ID.")
            allowed &= {source_id}
        links = store.source_links(ctx.owner_db)
        return [row for row in rows if links.get(row["zotero_key"], {store.UNASSIGNED}) & allowed]

    def writable_reason(self, ctx: WorkflowContext, stage: str, key: str, *, canonical_only: bool = False) -> str | None:
        if ctx.info.degraded:
            return "task_read_only"
        if store.settings(ctx.owner_db)["stages"][stage]["mode"] == "paused":
            return "stage_paused"
        row = self.article_row(ctx.owner_db, key)
        if row is None or row.get("is_duplicate_of") is not None:
            return "article_not_eligible"
        with closing(store.connect(ctx.owner_db, read_only=True)) as conn:
            if store.has_table(conn, "title_duplicate_members") and conn.execute(
                    "SELECT 1 FROM title_duplicate_members m JOIN title_duplicate_reviews r ON r.review_id=m.review_id "
                    "WHERE m.zotero_key=? AND m.imported_in_batch=1 AND r.status IN ('pending','undecided') LIMIT 1", (key,)).fetchone():
                return "article_not_eligible"
            links = {entry[0] for entry in conn.execute("SELECT source_id FROM workflow_article_sources WHERE zotero_key=?", (key,))} if store.has_table(conn, "workflow_article_sources") else set()
        if not canonical_only and stage != "screen":
            initial = self.saved_item(ctx.actor_db, "screen", key) or {}
            if stage == "fulltext" and initial.get("decision") not in {"include", "maybe"}:
                return "article_not_eligible"
            if stage == "coding":
                second = self.saved_item(ctx.actor_db, "fulltext", key) or {}
                any_second_include = False
                if ctx.actor_db.is_file():
                    with closing(store.connect(ctx.actor_db, read_only=True)) as conn:
                        if store.has_table(conn, "stage2_decisions"):
                            any_second_include = bool(conn.execute("SELECT 1 FROM stage2_decisions WHERE decision='include' LIMIT 1").fetchone())
                if (any_second_include and second.get("decision") != "include") or (
                        not any_second_include and (initial.get("decision") != "include" or second.get("decision") == "maybe")):
                    return "article_not_eligible"
        if not (links or {store.UNASSIGNED}) & set(self.allowed_sources(ctx, stage)):
            return "article_outside_scope"
        return None

    @contextmanager
    def write_guard(self, task_id: str, user: dict, stage: str, key: str | None = None,
                    expected_version: int | None = None, *, canonical_only: bool = False):
        if stage not in store.STAGES:
            raise HTTPException(400, "Unknown workflow stage.")
        # Same-process RLock is held through the caller's actual save/commit. It
        # complements SQLite transactions and the preexisting task file lock.
        with self.lock(task_id):
            ctx = self.context(task_id, user)
            state = store.settings(ctx.owner_db)
            if expected_version is not None and state["version"] != expected_version:
                raise HTTPException(409, {"code": "workflow_version_conflict", "version": state["version"],
                                          "workflow_settings": self.settings(ctx)})
            if key is None:
                if not ctx.can_manage:
                    raise HTTPException(403, "Only a verified task owner can manage workflow settings.")
            else:
                if canonical_only and (stage != "coding" or ctx.owner is None or not task_collaboration.authorize_action(
                        ctx.collaboration_db, user["user_id"], "coding:submit", task_id=task_id)):
                    raise HTTPException(403, "Task coding submission access denied.")
                self.sync_catalog(ctx)
                reason = self.writable_reason(ctx, stage, key, canonical_only=canonical_only)
                if reason:
                    raise HTTPException(409, {"code": reason, "version": state["version"],
                                              "workflow_settings": self.settings(ctx)})
            yield ctx

    def check_maybe(self, ctx: WorkflowContext, stage: str, decision: str) -> None:
        if decision == "maybe" and not store.settings(ctx.owner_db)["stages"][stage].get("show_maybe", True):
            raise HTTPException(409, {"code": "maybe_disabled", "version": store.settings(ctx.owner_db)["version"],
                                      "workflow_settings": self.settings(ctx)})

    def coding_complete(self, ctx: WorkflowContext, key: str, values: dict, dimension_ids: set[int]) -> bool:
        """Completion hook for Task 3's required-evidence check.

        Foundation semantics match existing coding_progress: all configured
        dimensions contain nonempty values, and an empty scheme is incomplete.
        """
        return bool(dimension_ids) and all(str(values.get(dim, "")).strip() for dim in dimension_ids)

    def queue(self, task_id: str, user: dict, stage: str, source_id: str | None = None,
              order: str = "import", *, shuffle: bool = False) -> dict:
        with self.lock(task_id):
            ctx = self.context(task_id, user)
            self.sync_catalog(ctx)
            rows, saved = self.eligible(ctx, stage)
            scoped = self.scoped(ctx, stage, rows, source_id)
            dimensions = set()
            if stage == "coding" and ctx.actor_db.is_file():
                with closing(store.connect(ctx.actor_db, read_only=True)) as conn:
                    if store.has_table(conn, "coding_dimensions"):
                        dimensions = {row[0] for row in conn.execute("SELECT id FROM coding_dimensions")}
            def complete(key: str) -> bool:
                return bool(saved.get(key)) if stage != "coding" else self.coding_complete(
                    ctx, key, saved.get(key, {}), dimensions)
            def progress(items: list[dict]) -> dict:
                done = sum(complete(row["zotero_key"]) for row in items)
                return {"total": len(items), "completed": done, "pending": len(items) - done}
            keys = [row["zotero_key"] for row in scoped]
            if order == "random" and ctx.owner is not None:
                # UI selection identifies the journal; the current policy only
                # filters keys. Source/policy changes must not reset positions.
                scope = json.dumps({"stage": stage, "source_id": source_id}, sort_keys=True)
                keys = store.random_order(ctx.actor_db, str(user["user_id"]), task_id, scope, keys,
                                          {key for key in keys if not complete(key)}, shuffle=shuffle)
            by_key = {row["zotero_key"]: row for row in scoped}
            queue = [{"key": key, "zotero_key": key, **{name: by_key[key].get(name) for name in
                      ("title", "authors", "journal", "year", "doi", "item_type")}} for key in keys]
            state = store.settings(ctx.owner_db)
            return {"queue": queue, "total": len(queue), "pending": progress(scoped)["pending"],
                    "scope_progress": progress(scoped), "overall_progress": progress(rows),
                    "policy_version": state["version"], "allowed_sources": self.allowed_sources(ctx, stage),
                    "paused": state["stages"][stage]["mode"] == "paused"}

    def work_item(self, task_id: str, user: dict, stage: str, key: str) -> dict:
        with self.lock(task_id):
            ctx = self.context(task_id, user)
            self.sync_catalog(ctx)
            row = self.article_row(ctx.owner_db, key)
            if row is None:
                # An actor's own historical active record remains readable.
                row = self.article_row(ctx.actor_db, key)
            if row is None:
                raise HTTPException(404, "Article not found.")
            reason = self.writable_reason(ctx, stage, key)
            try:
                raw = json.loads(row.get("raw_json") or "{}")
            except (ValueError, TypeError):
                raw = {}
            article = Article.from_dict({**row, "raw": raw}).to_dict()
            article["raw"] = json.loads(article.pop("raw_json") or "{}")
            return {"article": article, "saved": self.saved_item(ctx.actor_db, stage, key),
                    "writable": reason is None, "reason": reason,
                    "policy_version": store.settings(ctx.owner_db)["version"]}

    @staticmethod
    def bump_version(conn: sqlite3.Connection) -> None:
        conn.execute("UPDATE workflow_state SET version=version+1 WHERE singleton=1")

    def reason_db(self, ctx: WorkflowContext) -> Path:
        if ctx.task_kind == "personal":
            return self.data_dir / "workflow_accounts" / f"{int(ctx.owner['owner_user_id'])}.sqlite3"
        return ctx.owner_db


class SettingsIn(BaseModel):
    expected_version: int = Field(ge=1)
    stages: dict
    evidence_required_field_ids: list[int] = Field(default_factory=list, max_length=1000)


class SourceIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    active: bool = True
    expected_version: int | None = Field(default=None, ge=1)


class ReasonIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    stage: Literal["screen", "fulltext"] | None = None


def register_review_workflow_routes(app, require_user, task_lookup, screener_db, data_dir, visible) -> WorkflowRuntime:
    runtime = WorkflowRuntime(task_lookup, screener_db, data_dir, visible)
    app.state.review_workflow = runtime

    @app.get("/api/tasks/{task_id}/workflow-settings")
    def settings(task_id: str, user: dict = Depends(require_user)) -> dict:
        return runtime.settings(runtime.context(task_id, user))

    @app.put("/api/tasks/{task_id}/workflow-settings")
    def set_settings(task_id: str, body: SettingsIn, user: dict = Depends(require_user)) -> dict:
        with runtime.write_guard(task_id, user, "screen", expected_version=body.expected_version) as ctx:
            valid = {source["id"] for source in store.sources(ctx.owner_db)}
            stages = {}
            if set(body.stages) != set(store.STAGES):
                raise HTTPException(400, "Supply all three workflow stages.")
            for stage in store.STAGES:
                raw = body.stages[stage]
                if not isinstance(raw, dict) or raw.get("mode") not in {"all", "selected", "paused"}:
                    raise HTTPException(400, "Invalid workflow mode.")
                ids = raw.get("source_ids", [])
                if not isinstance(ids, list) or any(not isinstance(sid, str) for sid in ids) or not set(ids) <= valid:
                    raise HTTPException(400, "Invalid workflow source IDs.")
                if raw["mode"] == "selected" and not ids:
                    raise HTTPException(400, "Selected mode requires at least one source.")
                stages[stage] = {"mode": raw["mode"], "source_ids": sorted(set(ids))}
                if stage != "coding":
                    if type(raw.get("show_maybe", True)) is not bool:
                        raise HTTPException(400, "show_maybe must be a boolean.")
                    stages[stage]["show_maybe"] = raw.get("show_maybe", True)
            field_ids = body.evidence_required_field_ids
            dimensions = {dim["id"] for dim in coding.list_dimensions(ctx.owner_db)}
            if any(type(fid) is not int or fid not in dimensions for fid in field_ids):
                raise HTTPException(400, "Required evidence fields must exist in the task coding scheme.")
            with closing(store.connect(ctx.owner_db)) as conn, conn:
                conn.execute("UPDATE workflow_state SET stages_json=?,evidence_json=? WHERE singleton=1",
                             (json.dumps(stages), json.dumps(sorted(set(field_ids)))))
                runtime.bump_version(conn)
            runtime.notify(ctx, "settings")
            return runtime.settings(ctx)

    @app.get("/api/tasks/{task_id}/sources")
    def sources(task_id: str, user: dict = Depends(require_user)) -> dict:
        ctx = runtime.context(task_id, user)
        return {"sources": store.sources(ctx.owner_db), "can_manage": ctx.can_manage,
                "version": store.settings(ctx.owner_db)["version"]}

    def save_source(task_id: str, user: dict, body: SourceIn, source_id: str | None) -> dict:
        with runtime.write_guard(task_id, user, "screen", expected_version=body.expected_version) as ctx:
            text, norm = body.name.strip(), store.normalized(body.name)
            if not norm:
                raise HTTPException(400, "Source name is required.")
            if source_id == store.UNASSIGNED:
                raise HTTPException(400, "Unassigned is a reserved source.")
            sid = source_id or str(uuid.uuid4())
            try:
                with closing(store.connect(ctx.owner_db)) as conn, conn:
                    if source_id:
                        if not conn.execute("SELECT 1 FROM workflow_sources WHERE id=?", (sid,)).fetchone():
                            raise HTTPException(404, "Source not found.")
                        conn.execute("UPDATE workflow_sources SET name=?,normalized_name=?,active=? WHERE id=?", (text, norm, body.active, sid))
                    else:
                        conn.execute("INSERT INTO workflow_sources VALUES(?,?,?,?,datetime('now'))", (sid, text, norm, body.active))
                    runtime.bump_version(conn)
            except sqlite3.IntegrityError as exc:
                raise HTTPException(409, "A source with this name already exists.") from exc
            runtime.notify(ctx, "source")
            return {"source": next(source for source in store.sources(ctx.owner_db) if source["id"] == sid), "version": store.settings(ctx.owner_db)["version"]}

    @app.post("/api/tasks/{task_id}/sources", status_code=201)
    def create_source(task_id: str, body: SourceIn, user: dict = Depends(require_user)) -> dict:
        return save_source(task_id, user, body, None)

    @app.put("/api/tasks/{task_id}/sources/{source_id}")
    def update_source(task_id: str, source_id: str, body: SourceIn, user: dict = Depends(require_user)) -> dict:
        return save_source(task_id, user, body, source_id)

    @app.get("/api/tasks/{task_id}/reason-library")
    def reasons(task_id: str, stage: Literal["screen", "fulltext"], user: dict = Depends(require_user)) -> dict:
        ctx = runtime.context(task_id, user)
        path = runtime.reason_db(ctx)
        entries = []
        if path.is_file():
            with closing(store.connect(path, read_only=True)) as conn:
                if store.has_table(conn, "workflow_reasons"):
                    entries = [dict(row) for row in conn.execute("SELECT id,text,stage FROM workflow_reasons WHERE stage=? ORDER BY rowid", (stage,))]
        return {"entries": entries, "can_manage": ctx.can_manage, "task_kind": ctx.task_kind}

    def edit_reason(task_id: str, user: dict, body: ReasonIn | None, reason_id: str | None, stage: str | None) -> dict:
        with runtime.write_guard(task_id, user, "screen") as ctx:
            path = runtime.reason_db(ctx)
            store.migrate(path)
            sid = reason_id or str(uuid.uuid4())
            try:
                with closing(store.connect(path)) as conn, conn:
                    old = conn.execute("SELECT stage FROM workflow_reasons WHERE id=?", (sid,)).fetchone() if reason_id else None
                    if reason_id and old is None:
                        raise HTTPException(404, "Reason not found.")
                    if body is None:
                        conn.execute("DELETE FROM workflow_reasons WHERE id=?", (sid,))
                    else:
                        text, norm = body.text.strip(), store.normalized(body.text)
                        if not norm:
                            raise HTTPException(400, "Reason text is required.")
                        selected_stage = old[0] if old else stage or body.stage
                        if selected_stage not in {"screen", "fulltext"}:
                            raise HTTPException(400, "Select a reason stage.")
                        conn.execute("INSERT INTO workflow_reasons VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET text=excluded.text,normalized_text=excluded.normalized_text",
                                     (sid, selected_stage, text, norm))
            except sqlite3.IntegrityError as exc:
                raise HTTPException(409, "This reason already exists in the stage library.") from exc
            if ctx.task_kind == "team":
                runtime.notify(ctx, "reason-library")
            return {"id": sid, "deleted": body is None}

    @app.post("/api/tasks/{task_id}/reason-library", status_code=201)
    def create_reason(task_id: str, body: ReasonIn, stage: Literal["screen", "fulltext"] | None = None, user: dict = Depends(require_user)) -> dict:
        return edit_reason(task_id, user, body, None, stage)

    @app.put("/api/tasks/{task_id}/reason-library/{reason_id}")
    def update_reason(task_id: str, reason_id: str, body: ReasonIn, user: dict = Depends(require_user)) -> dict:
        return edit_reason(task_id, user, body, reason_id, None)

    @app.delete("/api/tasks/{task_id}/reason-library/{reason_id}")
    def delete_reason(task_id: str, reason_id: str, user: dict = Depends(require_user)) -> dict:
        return edit_reason(task_id, user, None, reason_id, None)

    @app.get("/api/tasks/{task_id}/work-queue/{stage}")
    def queue(task_id: str, stage: Stage, source_id: str | None = None, order: Literal["import", "random"] = "import", user: dict = Depends(require_user)) -> dict:
        return runtime.queue(task_id, user, stage, source_id, order)

    @app.post("/api/tasks/{task_id}/work-queue/screen/shuffle")
    def shuffle(task_id: str, source_id: str | None = None, user: dict = Depends(require_user)) -> dict:
        return runtime.queue(task_id, user, "screen", source_id, "random", shuffle=True)

    @app.get("/api/tasks/{task_id}/work-items/{stage}/{key}")
    def work_item(task_id: str, stage: Stage, key: str, user: dict = Depends(require_user)) -> dict:
        return runtime.work_item(task_id, user, stage, key)

    return runtime
