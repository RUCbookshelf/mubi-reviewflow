"""Optional task metrics adapter; the manual PRISMA generator stays independent."""
from __future__ import annotations

import json
import csv
import io
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import Depends, HTTPException
from fastapi.responses import Response
from coscreen import review_workflow as workflow, tasks, task_collaboration


def decisions(path: Path, table: str) -> dict:
    if not path.is_file():
        return {}
    with closing(workflow.connect(path, read_only=True)) as conn:
        if not workflow.has_table(conn, table):
            return {}
        return {r['zotero_key']: dict(r) for r in conn.execute(f'SELECT * FROM {table}')}


def outcomes(ctx, stage: str, mode: str) -> tuple[dict, int]:
    table = 'decisions' if stage == 'stage1' else 'stage2_decisions'
    if mode == 'reviewer' or ctx.task_kind != 'team':
        return decisions(ctx.actor_db, table), 0
    if not ctx.can_manage and not task_collaboration.authorize_action(ctx.collaboration_db, ctx.user['user_id'], 'coding:read_all', task_id=ctx.info.task_id):
        raise HTTPException(403, 'Team PRISMA metrics require the task owner or Adjudicator.')
    from custom_backend import auth
    grants = task_collaboration.list_role_grants(ctx.collaboration_db, actor_user_id=ctx.owner['owner_user_id'], task_id=ctx.info.task_id)
    names = {account['username'] for g in grants if g['role'] == 'coder'
             if (account := auth.get_user_by_id(g['user_id']))}
    owner_votes = decisions(ctx.owner_db, table)
    if owner_votes:
        names.add(ctx.owner['owner_username'])
    votes = {name: decisions(ctx.info.dir / (tasks.slugify_screener(name) + '.db'), table) for name in sorted(names)}
    from coscreen.screening_arbitration import latest_decision_arbitration
    saved = latest_decision_arbitration(ctx.collaboration_db, stage)
    saved_rows = {r['zotero_key']: r for r in saved['records']} if saved else {}
    result, unresolved = {}, 0
    for key in set().union(*(set(v) for v in votes.values())) if votes else set():
        current = [v[key] for v in votes.values() if key in v]
        values = [r['decision'] for r in current]
        if len(votes) >= 2 and len(current) == len(votes) and len(set(values)) == 1:
            reasons = {r.get('exclusion_reason', '').strip() for r in current}
            result[key] = {'decision': values[0], 'exclusion_reason': next(iter(reasons)) if len(reasons) == 1 else ''}
            continue
        old = saved_rows.get(key)
        # An adjudication snapshot is usable only for the unchanged live votes.
        old_names = {k[len('decision_'):] for k in (old or {}) if k.startswith('decision_')}
        def unchanged_vote(name):
            live = votes[name].get(key)
            if not live:
                return False
            if any(str(old.get(f'{field}_{name}', '') or '') != str(live.get(field, '') or '')
                   for field in ('decision', 'exclusion_reason', 'notes')):
                return False
            try:
                # Legacy decision times are local, whereas adjudication times are UTC.
                current_time = datetime.fromisoformat(live.get('updated_at') or live['screened_at']).astimezone(timezone.utc)
                snapshot_time = datetime.fromisoformat(saved['created_at'].replace('Z', '+00:00'))
                return current_time <= snapshot_time
            except (KeyError, TypeError, ValueError):
                return False
        # Export labels must identify exactly the current reviewers. Unverifiable
        # offline labels and stale snapshots require manual confirmation.
        unchanged = old and old_names == set(votes) and all(unchanged_vote(name) for name in votes)
        if unchanged and old.get('arbitration_status') == 'adjudicated' and old.get('final_decision'):
            result[key] = {'decision': old['final_decision'], 'exclusion_reason': ''}
        else:
            unresolved += 1
    return result, unresolved


def summary(ctx, mode: str) -> dict:
    with closing(workflow.connect(ctx.owner_db, read_only=True)) as conn:
        articles = {r['zotero_key']: dict(r) for r in conn.execute('SELECT zotero_key,is_duplicate_of FROM articles')}
        def root(key):
            seen = set()
            while key in articles and articles[key]['is_duplicate_of'] and key not in seen:
                seen.add(key); key = articles[key]['is_duplicate_of']
            return key
        active_keys = {k for k,a in articles.items() if a['is_duplicate_of'] is None}
        originals = [dict(r) for r in conn.execute('SELECT * FROM workflow_source_records')] if workflow.has_table(conn, 'workflow_source_records') else []
        active = [r for r in originals if r['state'] == 'active' and r['canonical_key']]
        represented = {root(r['canonical_key']) for r in active}
        complete_provenance = active_keys <= represented and represented <= active_keys
        counts = Counter(r['source_id'] for r in active)
        catalog = {s['id']: s for s in workflow.sources(ctx.owner_db)}
        unidentified = any(not catalog.get(sid, {}).get('source_type') for sid in counts)
        db_rows, other_rows = [], []
        for sid,n in counts.items():
            source = catalog.get(sid, {})
            kind = source.get('source_type')
            if kind:
                (db_rows if kind in {'database','register'} else other_rows).append({'source_type': kind, 'name': source['name'], 'n': n, 'source_id':sid})
        record_types = {}
        for r in active:
            record_types.setdefault(root(r['canonical_key']), set()).add(catalog.get(r['source_id'],{}).get('source_type'))
        # A report found in both pathways is counted in the database pathway once.
        other_keys = {k for k,types in record_types.items() if types and None not in types and not types & {'database','register'}}
        db_keys = active_keys - other_keys
        database_records = [r for r in active if catalog.get(r['source_id'],{}).get('source_type') in {'database','register'}]
        duplicate_count = len(database_records) - len({root(r['canonical_key']) for r in database_records}) if complete_provenance and not unidentified else None
        group_links = {r[0]: r[1] for r in conn.execute('SELECT zotero_key,study_id FROM review_reports')} if workflow.has_table(conn,'review_reports') else {}
    first, first_unresolved = outcomes(ctx, 'stage1', mode)
    second, second_unresolved = outcomes(ctx, 'stage2', mode)
    first = {k:v for k,v in first.items() if k in active_keys}
    sought = {k for k,v in first.items() if v['decision'] in {'include','maybe'}}
    second = {k:v for k,v in second.items() if k in sought}
    included = {k for k,v in second.items() if v['decision']=='include'}
    full_pending = sum(v['decision']=='maybe' for v in second.values())
    ready = len(first)==len(active_keys) and not first_unresolved and not second_unresolved and not full_pending and sought <= set(second)
    warnings = []
    if not complete_provenance: warnings.append('Original import counts need confirmation for legacy records.')
    if unidentified: warnings.append('Confirm source types before applying identification counts.')
    if not ready: warnings.append('Screening is incomplete. Task metrics are a draft, not a final flow.')
    warnings.append('Retrieval failures cannot be inferred from reports awaiting assessment.')
    fields = {'identified_rows_db': db_rows, 'identified_rows_other': other_rows,
              'sources_scope': 'db_reg_other' if other_rows else 'db_reg',
              'duplicates_removed': duplicate_count,
              'records_screened': len(db_keys & set(first)),
              'records_excluded_screening': sum(v['decision']=='exclude' for k,v in first.items() if k in db_keys),
              'reports_sought_db': len(sought & db_keys), 'reports_sought_other': len(sought & other_keys),
              'reports_assessed_db': len(set(second) & db_keys), 'reports_assessed_other': len(set(second) & other_keys),
              'reports_of_included_studies': len(included) if ready else None,
              'studies_included': len({group_links[k] for k in included}) if ready and included <= set(group_links) else None,
              'reports_not_retrieved_db': None, 'reports_not_retrieved_other': None}
    for field,rows,scope in [('screening_exclusion_reasons',first,db_keys),('exclusion_reasons_db',second,db_keys),('exclusion_reasons_other',second,other_keys)]:
        tally = Counter(v.get('exclusion_reason','').strip() for k,v in rows.items() if k in scope and v['decision']=='exclude')
        fields[field] = [{'reason':reason,'n':n} for reason,n in tally.items() if reason]
        if tally.get(''): warnings.append('Some exclusion reasons need confirmation.')
    if fields['studies_included'] is None: warnings.append('Enter a study count or confirm study grouping.')
    return {'fields': fields, 'warnings': list(dict.fromkeys(warnings)), 'task_kind':ctx.task_kind,
            'mode':mode, 'version':workflow.settings(ctx.owner_db)['version'], 'complete':ready,
            'pending':{'unscreened':len(active_keys-set(first)), 'team_unresolved':first_unresolved+second_unresolved,
                       'fulltext_unresolved':full_pending, 'staged_records':sum(r['state']=='staged' for r in originals)},
            'sources':list(catalog.values()), 'coverage_complete':complete_provenance and not unidentified}


def register_prisma_task_metrics(app, require_user):
    @app.get('/api/tasks/{task_id}/prisma-metrics/records')
    def metric_records(task_id: str, mode: Literal['reviewer','team']='reviewer', user: dict=Depends(require_user)):
        runtime=app.state.review_workflow
        with runtime.lock(task_id):
            ctx=runtime.context(task_id,user)
            first,_=outcomes(ctx,'stage1',mode)
            second,_=outcomes(ctx,'stage2',mode)
            catalog={s['id']:s for s in workflow.sources(ctx.owner_db)}
            output=io.StringIO(newline='');writer=csv.writer(output)
            writer.writerow(['source_record_id','source_id','source_name','source_type','filename','row_index','state',
                             'canonical_key','original_title','initial_outcome','fulltext_outcome','metric_mode'])
            with closing(workflow.connect(ctx.owner_db,read_only=True)) as conn:
                articles={r[0]:r[1] for r in conn.execute('SELECT zotero_key,is_duplicate_of FROM articles')}
                rows=conn.execute('SELECT * FROM workflow_source_records ORDER BY filename,row_index,id') if workflow.has_table(conn,'workflow_source_records') else []
                for row in rows:
                    key=row['canonical_key'];seen=set()
                    while key in articles and articles[key] and key not in seen:
                        seen.add(key);key=articles[key]
                    original=json.loads(row['original_json']);source=catalog.get(row['source_id'],{})
                    values=[row['id'],row['source_id'],source.get('name',''),source.get('source_type',''),row['filename'],row['row_index'],row['state'],key or '',
                            original.get('title',''),first.get(key,{}).get('decision',''),second.get(key,{}).get('decision',''),mode]
                    # Treat source-supplied strings as text in spreadsheet applications.
                    writer.writerow(["'"+value if isinstance(value,str) and value.startswith(('=','+','-','@','\t','\r')) else value for value in values])
            return Response('\ufeff'+output.getvalue(),media_type='text/csv',headers={'Content-Disposition':'attachment; filename="prisma-task-records.csv"','Cache-Control':'no-store'})

    @app.get('/api/tasks/{task_id}/prisma-metrics')
    def task_metrics(task_id: str, mode: Literal['reviewer','team']='reviewer', user: dict=Depends(require_user)):
        runtime=app.state.review_workflow
        with runtime.lock(task_id):
            ctx=runtime.context(task_id,user)
            return summary(ctx,mode)
