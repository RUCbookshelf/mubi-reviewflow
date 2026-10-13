"""Protected import orchestration and authenticated batch review routes."""
from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import logging
import shutil
import sqlite3
import tempfile
import uuid
from contextlib import closing
from pathlib import Path
from typing import Literal

from fastapi import Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel

from coscreen import db as db_mod, import_history, import_sources, review_imports as store, review_workflow as workflow
from coscreen import import_review_undo as review_undo
from coscreen.dedup import find_duplicates, find_title_candidate_groups
from coscreen.models import Article

logger = logging.getLogger(__name__)


class VersionBody(BaseModel):
    expected_policy_version: int | None = None


class CandidateBody(VersionBody):
    status: Literal['duplicate', 'not_duplicate', 'undecided']
    keeper_key: str | None = None
    keeper_record_id: str | None = None


class MetadataBody(VersionBody):
    action: Literal['keep_existing', 'accept_incoming']


class ImportRuntime:
    def __init__(self, app, parse_upload, progress, fuzzy_progress, stabilize, title_groups, history_path, touch):
        self.app = app
        self.parse_upload, self.progress, self.fuzzy_progress = parse_upload, progress, fuzzy_progress
        self.stabilize, self.title_groups = stabilize, title_groups
        self.history_path, self.touch = history_path, touch

    @property
    def workflow(self):
        return self.app.state.review_workflow

    def authorize(self, task_id, user, screener):
        with self.workflow.write_guard(task_id, user, 'screen') as ctx:
            if screener and screener != ctx.owner['owner_username']:
                raise HTTPException(403, 'Imports are written only to the verified task owner catalogue.')
            if ctx.info.degraded:
                raise HTTPException(409, 'Task is read-only.')
            return ctx

    async def upload(self, task_id: str, user: dict, files: list, *, batch: bool,
                     source_ids: list[str] | None, job_id: str | None, screener: str | None) -> dict:
        if not files or len(files) > 20:
            raise HTTPException(400, 'Choose between 1 and 20 import files.')
        source_ids = source_ids if source_ids is not None else ['auto'] * len(files)
        if len(source_ids) != len(files) or any(not isinstance(s, str) or not s for s in source_ids):
            raise HTTPException(400, 'source_ids must be a JSON array aligned with uploaded files.')
        # Synchronous worker authorization: never hold a threading.RLock across await.
        await asyncio.to_thread(self.authorize, task_id, user, screener)
        tmpdir = Path(tempfile.mkdtemp(prefix='reviewflow_import_'))
        try:
            self.progress(job_id, task_id, user['user_id'], 'parse', 2)
            parsed = []
            for i, upload in enumerate(files):
                name, articles, path = await self.parse_upload(upload, tmpdir, i)
                originals = [a.to_dict() for a in articles]
                for article in articles:
                    article.raw['source_file'] = name
                parsed.append({'name': name, 'articles': articles, 'originals': originals,
                               'raw_path': path, 'source_id': None if source_ids[i] == 'auto' else source_ids[i],
                               'digest': hashlib.sha256(path.read_bytes()).hexdigest()})
                self.progress(job_id, task_id, user['user_id'], 'parse', min(9, 2 + 7 * (i + 1) // len(files)))
            result = await asyncio.to_thread(self.process, task_id, user, parsed, batch, job_id, screener)
            self.progress(job_id, task_id, user['user_id'], 'complete', 100, staged=result['staged'], batch_id=result['batch_id'])
            return result
        except Exception as exc:
            self.progress(job_id, task_id, user['user_id'], 'error', 100, error=str(exc))
            raise
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def process(self, task_id, user, files, batch, job_id, screener):
        snapshot = sqlite3.connect(':memory:')
        snapshot.row_factory = sqlite3.Row
        try:
            with self.workflow.write_guard(task_id, user, 'screen') as ctx:
                if screener and screener != ctx.owner['owner_username']:
                    raise HTTPException(403, 'Only owner catalogue imports are permitted.')
                if ctx.info.degraded:
                    raise HTTPException(409, 'Task is read-only.')
                store.migrate(ctx.owner_db)
                valid_sources = {s['id'] for s in workflow.sources(ctx.owner_db)} | {workflow.UNASSIGNED}
                if any(f['source_id'] and f['source_id'] not in valid_sources for f in files):
                    raise HTTPException(400, 'Unknown source ID. Create or select a task source before importing.')
                with closing(workflow.connect(ctx.owner_db)) as conn, conn:
                    protected = store.has_research(ctx.info.dir, conn)
                with closing(workflow.connect(ctx.owner_db, read_only=True)) as conn:
                    conn.backup(snapshot)
                version, revision = snapshot.execute('SELECT version,catalog_revision FROM workflow_state WHERE singleton=1').fetchone()
                info, owner_name = ctx.info, ctx.owner['owner_username']
            # All matching and initial legacy duplicate preparation uses this
            # consistent detached snapshot, with no task lock or live write lock.
            existing = [Article.from_dict(r) for r in store.active_rows(snapshot)]
            incoming = [article for f in files for article in f['articles']]
            self.stabilize(existing, incoming)
            bid = uuid.uuid4().hex
            import_sources.prepare_files(snapshot, files)
            # The original bytes commit first. Failed/stale jobs remain explicitly
            # failed/prepared in history; no active rows exist before this succeeds.
            hid = import_history.save_import(self.history_path(info), task_id=task_id, screener=owner_name,
                import_type='batch' if batch else 'single', stats={'batch_id': bid, 'staged': True, 'status': 'preparing', 'screening_ready': False},
                files=[(f['name'], f['raw_path']) for f in files])
            committed = False
            try:
                self.progress(job_id, task_id, user['user_id'], 'fuzzy', 10)
                plan = store.prepare(snapshot, files, batch_id=bid, history_id=hid, owner_id=user['user_id'],
                    import_type='batch' if batch else 'single', protected=protected, title_groups=self.title_groups,
                    progress_callback=lambda stage, done, total: self.fuzzy_progress(job_id, task_id, user['user_id'], stage, done, total))
                legacy = self.legacy_plan(snapshot, bid, existing, incoming, batch, plan['new_record_ids']) if not protected else None
                self.progress(job_id, task_id, user['user_id'], 'save', 97)
                with self.workflow.write_guard(task_id, user, 'screen', expected_version=version) as fresh:
                    current = workflow.settings(fresh.owner_db)
                    if current['catalog_revision'] != revision:
                        raise HTTPException(409, {'code': 'import_catalogue_changed', 'version': current['version'],
                            'workflow_settings': self.workflow.settings(fresh), 'message': 'Catalogue changed during matching; retry the import.'})
                    with closing(workflow.connect(fresh.owner_db)) as conn, conn:
                        conn.execute('BEGIN IMMEDIATE')
                        protected = store.has_research(fresh.info.dir, conn) or protected
                        self.copy_prepared(snapshot, conn, bid, plan['new_record_ids'])
                        conn.execute('UPDATE workflow_import_batches SET protected=? WHERE id=?', (int(protected), bid))
                        activation = {'activated_new': 0, 'activated_source_records': 0}
                        if not protected:
                            activation = self.initial_save(conn, fresh.owner_db, bid, legacy)
                        store.refresh_statuses(conn)
                        conn.execute('UPDATE workflow_state SET version=version+1 WHERE singleton=1')
                        result = self.compat_result(conn, bid, files, batch, legacy if not protected else None, plan, activation)
                        result['import_id'] = hid
                        result['policy_version'] = conn.execute('SELECT version FROM workflow_state WHERE singleton=1').fetchone()[0]
                    committed = True
                    self.workflow.notify(fresh, 'import-staged' if result['staged'] else 'import-activated')
                result['progress'] = db_mod.get_progress(fresh.owner_db)
                # A history refresh is a derived summary. Readers also hydrate
                # authoritative status by batch_id, covering a crash here.
                try:
                    import_history.update_import_stats(self.history_path(info), hid, result)
                except Exception:
                    logger.exception('Unable to refresh import history summary for %s', hid)
                try:
                    self.touch(info)
                except Exception:
                    logger.exception('Unable to update task timestamp after committed import %s', bid)
                return result
            except Exception as exc:
                if not committed:
                    try:
                        import_history.update_import_stats(self.history_path(info), hid,
                            {'batch_id': bid, 'staged': True, 'status': 'failed', 'screening_ready': False, 'error': str(exc)})
                    except Exception:
                        logger.exception('Unable to record failed import %s', hid)
                raise
        finally:
            snapshot.close()

    @staticmethod
    def copy_prepared(snapshot, conn, bid, new_ids):
        def copy(table, where, params):
            rows = snapshot.execute(f'SELECT * FROM {table} WHERE {where}', params).fetchall()
            for row in rows:
                conn.execute(f'INSERT OR IGNORE INTO {table} VALUES({",".join("?" for _ in row)})', tuple(row))
        copy('workflow_sources', '1=1', ())
        copy('workflow_source_details', '1=1', ())
        copy('workflow_import_batches', 'id=?', (bid,))
        copy('workflow_import_files', 'batch_id=?', (bid,))
        # New rows appear in parse order; target records always precede links.
        for rid in new_ids:
            copy('workflow_source_records', 'id=?', (rid,))
            copy('workflow_import_metadata', 'record_id=?', (rid,))
        copy('workflow_import_members', 'batch_id=?', (bid,))
        for row in snapshot.execute('''SELECT DISTINCT c.id FROM workflow_import_candidates c
                JOIN workflow_import_candidate_members cm ON cm.candidate_id=c.id
                JOIN workflow_import_members m ON m.record_id=cm.ref_id AND cm.ref_type='record' WHERE m.batch_id=?''', (bid,)):
            copy('workflow_import_candidates', 'id=?', (row[0],))
            copy('workflow_import_candidate_members', 'candidate_id=?', (row[0],))

    def legacy_plan(self, conn, bid, existing, incoming, batch, new_record_ids):
        new_ids = set(new_record_ids)
        batch_rows = conn.execute('''SELECT r.* FROM workflow_import_members m
            JOIN workflow_source_records r ON r.id=m.record_id
            WHERE m.batch_id=? ORDER BY m.file_index,r.row_index''', (bid,)).fetchall()
        if len(batch_rows) != len(incoming):
            raise ValueError('Import source membership no longer matches the parsed records.')
        # Newly created, self-contained title/fuzzy candidates retain the legacy
        # initial review flow. Older staged candidates and identity conflicts
        # must keep their records outside the active catalogue.
        transferable = {}
        for candidate in store.candidate_rows(conn, bid, include_members=False):
            if candidate['kind'] == 'identity_conflict' or candidate['status'] != 'pending':
                continue
            members = {r[0] for r in conn.execute('''SELECT ref_id FROM workflow_import_candidate_members
                WHERE candidate_id=? AND ref_type='record' ''', (candidate['id'],))}
            if members and members <= new_ids:
                transferable[candidate['id']] = members
        # A title group touching an identity-blocked member must stay staged as
        # a whole. Removing it from the transferable set can block another group,
        # so propagate until all remaining legacy groups are self-contained.
        while True:
            ignored = set(transferable)
            excluded = {cid for cid, members in transferable.items()
                        if any(store.dependency_blocked(conn, rid, ignored_candidates=ignored)
                               for rid in members)}
            if not excluded:
                break
            for cid in excluded:
                del transferable[cid]
        eligible_ids = set()
        for row in batch_rows:
            if row['id'] not in new_ids:
                continue
            if store.dependency_blocked(conn, row['id'], ignored_candidates=set(transferable)):
                continue
            key, root = store.record_target(conn, row['id'])
            # A prior batch's reviewed but still unactivated root retains its
            # identity and explicit activation boundary; don't create an alias.
            if key is None and root not in new_ids:
                continue
            eligible_ids.add(row['id'])
        existing_keys = {a.zotero_key for a in existing}
        incoming = [article for row, article in zip(batch_rows, incoming)
                    if row['id'] in eligible_ids or
                    (row['state'] == 'active' and article.zotero_key in existing_keys)]
        kept, within = find_duplicates(incoming, include_fuzzy=False)
        selected = kept if batch else incoming
        exact, report = find_duplicates(existing + selected, include_fuzzy=False)
        _, fuzzy = find_duplicates(existing + selected)
        existing_keys = {a.zotero_key for a in existing}
        groups = self.title_groups(exact, {a.zotero_key for a in selected} - existing_keys, fuzzy.fuzzy_pairs)
        _, previous = find_duplicates(existing, include_fuzzy=False)
        incoming_keys = {item.zotero_key for item in incoming}
        all_title_groups = [g for g in find_title_candidate_groups(existing + incoming)
                            if len(g) > 1 and any(a.zotero_key in incoming_keys for a in g)]
        return {'articles': selected, 'pairs': report.exact_pairs, 'groups': groups, 'all_title_groups': all_title_groups,
                'eligible_record_ids': eligible_ids, 'transfer_candidate_ids': set(transferable),
                'within': within, 'report': report, 'previous': previous,
                'fuzzy': fuzzy, 'existing_keys': existing_keys, 'incoming': incoming}

    @staticmethod
    def initial_save(conn, db, bid, legacy):
        # The owner transaction repeats the full dependency check before any
        # active insert. Only this batch's eligible legacy title groups may be
        # transferred; unresolved staged dependencies are never bypassed.
        ignored = legacy['transfer_candidate_ids']
        for rid in legacy['eligible_record_ids']:
            if store.dependency_blocked(conn, rid, ignored_candidates=ignored):
                raise ValueError('A staged import dependency still requires review; retry after reviewing it.')
        canonical_before = conn.execute('SELECT COUNT(*) FROM articles WHERE is_duplicate_of IS NULL').fetchone()[0]
        summary = db_mod.upsert_articles(db, legacy['articles'], legacy['pairs'], legacy['groups'], conn=conn, preserve_existing=True)
        linked = 0
        for row in conn.execute('''SELECT DISTINCT r.* FROM workflow_source_records r JOIN workflow_import_members m ON m.record_id=r.id
                WHERE m.batch_id=? AND r.state='staged' ORDER BY r.created_at,r.row_index''', (bid,)).fetchall():
            if row['id'] not in legacy['eligible_record_ids']:
                continue
            # Resolve the reviewed root before considering any physical key.
            # An incoming alias must never replace its staged canonical identity.
            key, root = store.record_target(conn, row['id'])
            if key is None:
                root_row = conn.execute('SELECT incoming_key FROM workflow_source_records WHERE id=?', (root,)).fetchone()
                if root_row and conn.execute('SELECT 1 FROM articles WHERE zotero_key=?', (root_row[0],)).fetchone():
                    key = store.canonical(conn, root_row[0])
            if key is None:
                continue
            key = store.canonical(conn, key)
            conn.execute('UPDATE workflow_source_records SET canonical_key=? WHERE id=?', (key, row['id']))
            store.proposal(conn, row['id'], key)
            proposal = conn.execute('SELECT action FROM workflow_import_metadata WHERE record_id=?', (row['id'],)).fetchone()
            if proposal and proposal[0] is None:
                continue
            workflow.associate_sources(db, [key], [row['source_id']], conn=conn)
            conn.execute("UPDATE workflow_source_records SET state='active' WHERE id=?", (row['id'],))
            linked += 1
        # Delete only the self-contained new candidates transferred to legacy
        # title review. Older/blocked groups keep every member and decision.
        for cid in legacy['transfer_candidate_ids']:
            conn.execute('DELETE FROM workflow_import_candidate_members WHERE candidate_id=?', (cid,))
            conn.execute('DELETE FROM workflow_import_candidates WHERE id=?', (cid,))
        workflow.bump_catalog_revision(db, conn=conn)
        canonical_after = conn.execute('SELECT COUNT(*) FROM articles WHERE is_duplicate_of IS NULL').fetchone()[0]
        return {'activated_new': canonical_after - canonical_before, 'activated_source_records': linked,
                'n_imported': summary.n_imported, 'n_duplicates': summary.n_duplicates,
                'n_new': summary.n_new, 'n_existing': summary.n_existing}

    @staticmethod
    def compat_result(conn, bid, files, batch, legacy, plan, activation):
        summary = store.batch_summary(conn, bid)
        summary['source_files'] = summary.pop('files')
        parsed = sum(len(f['articles']) for f in files)
        rows = conn.execute('''SELECT DISTINCT r.* FROM workflow_source_records r JOIN workflow_import_members m ON m.record_id=r.id WHERE m.batch_id=?''', (bid,)).fetchall()
        exact_rows = [r for r in rows if r['outcome'] in ('exact_doi','exact_key','exact_staged','metadata')]
        n_new = activation.get('n_new', activation['activated_new'])
        existing_count = activation.get('n_existing', sum(r['canonical_key'] is not None for r in rows))
        title_pending = conn.execute("SELECT COUNT(*) FROM title_duplicate_reviews WHERE status IN ('pending','undecided')").fetchone()[0]
        title_pending_records = conn.execute("SELECT COUNT(DISTINCT m.zotero_key) FROM title_duplicate_members m JOIN title_duplicate_reviews r ON r.review_id=m.review_id WHERE r.status IN ('pending','undecided') AND m.imported_in_batch=1").fetchone()[0]
        groups = legacy['groups'] if legacy else []
        result = {**summary, **activation,
            'exact_match_hits': len(exact_rows), 'exact_duplicate_groups': len({r['canonical_key'] or r['target_record_id'] for r in exact_rows}),
            'exact_duplicate_excess': max(0, parsed - summary['canonical_unique']),
            'fuzzy_candidate_excess': plan['fuzzy_candidate_excess'],
            'duplicates_against_db': sum(r['canonical_key'] is not None and r['outcome'] != 'new' for r in rows),
            'title_candidate_groups_including_exact': len(groups) if legacy else summary['candidate_groups'],
            'title_candidate_excess_including_exact': sum(len(g['members']) - 1 for g in groups),
            'title_review_groups': len(groups) if legacy else summary['candidate_groups'],
            'title_review_pending': title_pending,
            'title_review_pending_records': title_pending_records,
            'title_review_excess': sum(len(g['members']) - 1 for g in groups),
            'note': plan['note'], 'screening_ready': not summary['staged'] and title_pending == 0
            and summary['pending_candidate_groups'] == 0}
        if legacy:
            metric_report = legacy['within'] if batch else legacy['report']
            incoming_keys = {a.zotero_key for a in legacy['incoming']}
            hits = [pair for pair in metric_report.exact_pairs if batch or pair.dup_key in incoming_keys]
            previous_excess = 0 if batch else legacy['previous'].total - legacy['previous'].unique
            result.update(exact_match_hits=len(hits), exact_duplicate_groups=len({pair.kept_key for pair in hits}),
                          exact_duplicate_excess=max(0, metric_report.total-metric_report.unique-previous_excess),
                          fuzzy_candidate_excess=sum(pair.dup_key in incoming_keys for pair in legacy['fuzzy'].fuzzy_pairs),
                          title_candidate_groups_including_exact=len(legacy['all_title_groups']),
                          title_candidate_excess_including_exact=sum(len(group)-1 for group in legacy['all_title_groups']))
        if batch:
            within = legacy['within'] if legacy else None
            cross = []
            within_counts = {i: 0 for i in range(len(files))}
            for row in exact_rows:
                if row['target_record_id']:
                    target = conn.execute('SELECT filename FROM workflow_source_records WHERE id=?', (row['target_record_id'],)).fetchone()
                    if target and target[0] != row['filename']:
                        cross.append({'kept_from': target[0], 'dup_from': row['filename'], 'method': 'exact',
                                      'title': json.loads(row['article_json']).get('title','')})
                    elif target:
                        index = next((i for i,f in enumerate(files) if f['name']==row['filename']), 0)
                        within_counts[index] += 1
            result.update(files=[{'name': f['name'], 'parsed': len(f['articles']), 'duplicates_within': within_counts[i],
                                  'source_id': f['source_id'], 'sha256': f['digest']} for i,f in enumerate(files)],
                          total_parsed=parsed, cross_file_duplicates=cross,
                          exact_duplicates=within.total-within.unique if within else max(0,parsed-summary['canonical_unique']),
                          unique_after_dedup=within.unique if within else summary['canonical_unique'],
                          imported=activation.get('n_imported',0), new=n_new, existing=existing_count)
        else:
            result.update(n_parsed=parsed, n_imported=activation.get('n_imported',0),
                          n_duplicates=activation.get('n_duplicates',0), n_new=n_new, n_existing=existing_count,
                          unique_after_exact_dedup=max(0, parsed-result['exact_duplicate_excess']) if legacy else summary['canonical_unique'])
        return result


def register_review_import_routes(app, require_user, **dependencies):
    runtime = ImportRuntime(app, **dependencies)
    app.state.review_imports = runtime

    def read_context(task_id, user):
        ctx = runtime.workflow.context(task_id, user)
        if ctx.owner is None:
            raise HTTPException(403, 'Import provenance requires verified task ownership.')
        with runtime.workflow.lock(task_id):
            store.migrate(ctx.owner_db)
            review_undo.migrate(ctx.owner_db)
        return ctx

    def translate(exc):
        if isinstance(exc, KeyError):
            raise HTTPException(404, str(exc)) from exc
        raise HTTPException(409, str(exc)) from exc

    @app.get('/api/tasks/{task_id}/import-review-summary')
    def review_summary(task_id: str, user: dict = Depends(require_user)):
        """Distinct task-wide checklist counts; reimports do not double count."""
        ctx = read_context(task_id,user)
        with closing(workflow.connect(ctx.owner_db,read_only=True)) as conn:
            source = conn.execute('SELECT COUNT(*) FROM workflow_source_records').fetchone()[0]
            staged = conn.execute("SELECT COUNT(*) FROM workflow_source_records WHERE state='staged'").fetchone()[0]
            metadata = conn.execute('SELECT COUNT(*) FROM workflow_import_metadata WHERE action IS NULL').fetchone()[0]
            candidates = conn.execute("SELECT COUNT(*) FROM workflow_import_candidates WHERE status IN ('pending','undecided')").fetchone()[0]
            return {'source_records':source, 'pending_records':staged,
                    'metadata_review_count':metadata, 'pending_candidate_groups':candidates,
                    'can_manage':ctx.can_manage}

    @app.get('/api/tasks/{task_id}/import-batches')
    def batches(task_id: str, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0), user: dict = Depends(require_user)):
        ctx = read_context(task_id, user)
        with closing(workflow.connect(ctx.owner_db, read_only=True)) as conn:
            ids = conn.execute('SELECT id FROM workflow_import_batches ORDER BY created_at DESC,id LIMIT ? OFFSET ?', (limit,offset)).fetchall()
            total = conn.execute('SELECT COUNT(*) FROM workflow_import_batches').fetchone()[0]
            return {'batches': [store.batch_summary(conn,r[0]) for r in ids], 'total': total,
                    'next_offset': offset+limit if offset+limit<total else None, 'can_manage':ctx.can_manage}

    @app.get('/api/tasks/{task_id}/import-batches/{batch_id}')
    def detail(task_id: str, batch_id: str, limit: int = Query(50, ge=1, le=200), offset: int = Query(0,ge=0),
               view: Literal['all','attention','completed'] = Query('all'), user: dict = Depends(require_user)):
        from custom_backend.import_review_presentation import review_projection, present_record
        ctx = read_context(task_id,user)
        with closing(workflow.connect(ctx.owner_db,read_only=True)) as conn:
            try:
                result = store.batch_summary(conn,batch_id)
            except KeyError as exc:
                translate(exc)
            projection = review_projection(conn, batch_id)
            rows = projection[view]
            result['records'] = [present_record(conn,r,projection['classification'][r['id']]) for r in rows[offset:offset+limit]]
            result['review_counts'] = projection['review_counts']
            result['view'] = view
            result['view_total'] = len(rows)
            result['next_offset'] = offset+limit if offset+limit<len(rows) else None
            result['can_manage'] = ctx.can_manage
            result['undo'] = review_undo.available(conn, batch_id, str(user['user_id'])) if ctx.can_manage else {'available': False}
            last_review = conn.execute('SELECT after_json FROM workflow_import_undo ORDER BY id DESC LIMIT 1').fetchone()
            legacy_seq = conn.execute('SELECT COALESCE(MAX(seq),0) FROM title_duplicate_undo').fetchone()[0]
            result['legacy_undo_allowed'] = not last_review or legacy_seq > json.loads(last_review[0]).get('legacy_seq',0)
            return result

    @app.get('/api/tasks/{task_id}/import-batches/{batch_id}/candidates')
    def candidates(task_id: str, batch_id: str, limit: int = Query(50,ge=1,le=200), offset: int = Query(0,ge=0),
                   view: Literal['all','attention','completed'] = Query('all'), user: dict = Depends(require_user)):
        ctx=read_context(task_id,user)
        with closing(workflow.connect(ctx.owner_db,read_only=True)) as conn:
            try:
                store.batch_summary(conn,batch_id)
            except KeyError as exc:
                translate(exc)
            selected=store.candidate_rows(conn,batch_id,include_members=False)
            if view!='all':
                selected=[c for c in selected if (c['status'] in ('pending','undecided'))==(view=='attention')]
            total=len(selected)
            from custom_backend.import_review_presentation import present_candidates
            rows=present_candidates(conn,selected[offset:offset+limit])
            return {'candidates':rows, 'total':total, 'next_offset':offset+limit if offset+limit<total else None}

    def mutate(task_id, batch_id, user, body, operation, reason, undo_kind=None, subject=None):
        with runtime.workflow.write_guard(task_id,user,'screen',expected_version=body.expected_policy_version) as ctx:
            if ctx.info.degraded:
                raise HTTPException(409,'Task is read-only.')
            store.migrate(ctx.owner_db)
            review_undo.migrate(ctx.owner_db)
            try:
                with closing(workflow.connect(ctx.owner_db)) as conn, conn:
                    conn.execute('BEGIN IMMEDIATE')
                    store.batch_summary(conn,batch_id)
                    snapshot = review_undo.snapshot(conn, undo_kind, subject) if undo_kind else None
                    before_changes = conn.total_changes
                    extra=operation(conn,ctx) or {}
                    changed = conn.total_changes != before_changes
                    if changed:
                        conn.execute('UPDATE workflow_state SET version=version+1 WHERE singleton=1')
                    result={**store.batch_summary(conn,batch_id),**extra}
                    result['policy_version']=conn.execute('SELECT version FROM workflow_state WHERE singleton=1').fetchone()[0]
                    if changed and undo_kind:
                        review_undo.remember(conn,batch_id,str(user['user_id']),undo_kind,subject,snapshot,result['policy_version'])
                    if reason == 'import-activated':
                        conn.execute("UPDATE workflow_import_undo SET state='activated' WHERE state='available'")
            except (ValueError,KeyError) as exc:
                translate(exc)
            if changed:
                runtime.workflow.notify(ctx,reason)
            return result

    @app.post('/api/tasks/{task_id}/import-batches/{batch_id}/candidates/{review_id}')
    def decide(task_id: str, batch_id: str, review_id: str, body: CandidateBody, user: dict=Depends(require_user)):
        return mutate(task_id,batch_id,user,body,lambda conn,ctx:store.decide_candidate(conn,batch_id,review_id,body.status,
            str(user['user_id']),body.keeper_key,body.keeper_record_id),'import-candidate-reviewed','candidate',review_id)

    @app.post('/api/tasks/{task_id}/import-batches/{batch_id}/metadata/{record_id}')
    def metadata(task_id: str,batch_id: str,record_id: str,body: MetadataBody,user: dict=Depends(require_user)):
        return mutate(task_id,batch_id,user,body,lambda conn,ctx:store.decide_metadata(conn,batch_id,record_id,body.action,
            str(user['user_id'])),'import-metadata-reviewed','metadata',record_id)

    @app.post('/api/tasks/{task_id}/import-batches/{batch_id}/undo')
    def undo(task_id: str,batch_id: str,body: VersionBody,user: dict=Depends(require_user)):
        return mutate(task_id,batch_id,user,body,lambda conn,ctx:review_undo.restore(conn,batch_id,str(user['user_id'])),'import-review-undone')

    @app.post('/api/tasks/{task_id}/import-batches/{batch_id}/activate')
    def activate(task_id: str,batch_id: str,body: VersionBody,user: dict=Depends(require_user)):
        return mutate(task_id,batch_id,user,body,lambda conn,ctx:store.activate(conn,ctx.owner_db,batch_id),'import-activated')

    @app.get('/api/tasks/{task_id}/import-batches/{batch_id}/export')
    def export(task_id: str,batch_id: str,user: dict=Depends(require_user)):
        ctx=read_context(task_id,user)
        with closing(workflow.connect(ctx.owner_db,read_only=True)) as conn:
            try:
                store.batch_summary(conn,batch_id)
            except KeyError as exc:
                translate(exc)
            rows=conn.execute('''SELECT m.file_index,r.* FROM workflow_import_members m JOIN workflow_source_records r ON r.id=m.record_id
                WHERE m.batch_id=? ORDER BY m.file_index,r.row_index''',(batch_id,)).fetchall()
            output=io.StringIO(newline='')
            fields=['batch_id','file_index','record_id','source_id','filename','digest','row_index','incoming_key','staged_key',
                    'canonical_key','target_record_id','outcome','state','candidate_statuses','metadata_action','original_json']
            writer=csv.DictWriter(output,fieldnames=fields)
            writer.writeheader()
            for row in rows:
                public=store.public_record(conn,dict(row))
                data={field:row[field] for field in fields if field in row.keys()}
                data.update(batch_id=batch_id,record_id=row['id'],candidate_statuses=store.dumps(public['candidate_statuses']),
                            metadata_action=public['metadata']['action'] if public['metadata'] else '')
                # Prevent spreadsheet formula execution while preserving exact
                # originals in the JSON column and archived source bytes.
                writer.writerow({k:("'"+str(v) if isinstance(v,str) and v.startswith(('=','+','-','@','\t','\r')) else v) for k,v in data.items()})
        return Response('\ufeff'+output.getvalue(),media_type='text/csv',headers={'Content-Disposition':f'attachment; filename="import-{batch_id}.csv"'})
