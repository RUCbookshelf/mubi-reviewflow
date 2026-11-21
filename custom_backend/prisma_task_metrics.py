"""Optional task metrics adapter; the manual PRISMA generator stays independent.

P22（C11）：team 模式的 metrics/records 追加「当前团队投影」分支——经
``custom_backend.team_current_results.current_team_projection``（受保护按键
只读 scope + 统一 keyed views：每键 F1/可信当前意见/按键 attestation；
2026-11-15 裁定锚——完成条目保留完成时结论与名单，新名单只作用于未完成）。
旧路径零改动：reviewer 模式、personal/legacy 任务、按人 raw 导出、独立 CSV、
generic latest（``latest_decision_arbitration``）与 ``outcomes()`` 本体均按
基线保留，不全局过滤任何 loader。
"""
from __future__ import annotations

import json
import csv
import io
import sqlite3
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
    # review67 L-47（对齐 screening_arbitration/appraisal_agreement/
    # arbitration_session 对同一批 {slug}.db 的读口径）：busy_timeout 15s
    # 后的锁忙 OperationalError 与 IO 故障是「暂时不可读」→ 503 稍后重试，
    # 不再让 GET /prisma-metrics 整页 500；确无决策/永久损坏（无法解析的
    # 库）降级为空——与「该成员没有决策」同口径跳过该成员，不阻塞其余成员。
    try:
        with closing(workflow.connect(path, read_only=True)) as conn:
            if not workflow.has_table(conn, table):
                return {}
            # table 只来自本模块固定枚举（outcomes() 传入 decisions / stage2_decisions），
            # 两个分支各写一条字面量 SQL，不再把表名拼进语句。
            if table == 'stage2_decisions':
                rows = conn.execute('SELECT * FROM stage2_decisions')
            else:
                rows = conn.execute('SELECT * FROM decisions')
            return {r['zotero_key']: dict(r) for r in rows}
    except sqlite3.OperationalError as exc:
        if 'locked' in str(exc).lower() or 'busy' in str(exc).lower():
            raise HTTPException(
                503, '筛选员决策库暂时无法读取（可能正被其他操作占用），请稍后重试。') from exc
        # review67 round2（deepseek 复审 L-47）：现存文件打不开/disk I/O
        # error 同样是「暂时不可读」→503（对齐 screening_arbitration 的
        # M-08 口径）——此前静默 return {} 会把该成员当「无决策」少算。
        if 'unable to open' in str(exc).lower() or 'disk i/o' in str(exc).lower():
            raise HTTPException(
                503, '筛选员决策库暂时无法读取（存储暂时不可用），请稍后重试。') from exc
        return {}
    except OSError as exc:
        # IO 故障（磁盘/权限/网络盘抖动）同样是暂时不可读（同仲裁先例口径）。
        raise HTTPException(
            503, '筛选员决策库暂时无法读取（可能正被其他操作占用），请稍后重试。') from exc
    except sqlite3.DatabaseError:
        # 永久损坏/无法解析：跳过该成员（不阻塞其余筛选员）。
        return {}


def outcomes(ctx, stage: str, mode: str) -> tuple[dict, int]:
    table = 'decisions' if stage == 'stage1' else 'stage2_decisions'
    if mode == 'reviewer' or ctx.task_kind != 'team':
        return decisions(ctx.actor_db, table), 0
    # fix-r1 R3：team PRISMA 读按**当前有效单一角色**验证（coding:read_all→
    # reconciler；choice 生效后另一旧 grant 不再放行），不再消费 raw grant；
    # ctx.can_manage 的属主管理读例外保留。拒绝沿用既有 403 形状（读路径不抛
    # team.role.*）；下方 coder 名单/仲裁快照的终局派生不属本批（留后续）。
    if not ctx.can_manage and not task_collaboration.has_team_action_role(
            ctx.collaboration_db, user_id=ctx.user['user_id'],
            action='coding:read_all', task_id=ctx.info.task_id):
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


def _storage_unavailable(exc: Exception) -> HTTPException:
    """S4（store 裸调收口）：sqlite 族故障 → 结构化 503（storage unavailable）。

    站点 128/129/162/170/191/301/303 此前不在任何 sqlite 感知 try 内，
    ``sqlite3.DatabaseError`` 逃逸全局兜底成通用 500。负载形状沿用
    ``_team_flow_http`` 的 error_code/message_key 家族并补 ``retryable``
    （锁忙/IO 抖动可稍后重试）。只读连接/查询不产生 IntegrityError，
    不存在与既有 4xx 语义交叠的路径。
    """
    return HTTPException(503, {
        "error_code": "prisma_metrics_storage_unavailable",
        "message_key": "prisma_metrics.storage_unavailable",
        "params": {},
        "retryable": True,
        "details": f"{type(exc).__name__}: {exc}",
        "message": "任务数据库暂时不可用（可能正被其他操作占用，或磁盘只读/已满），请稍后重试。"})


def _owner_active_keys(ctx) -> set | None:
    """P22：owner 域 canonical 活跃键（articles 表缺＝域无法确定 → None，
    不把未知当零——known_total 侧用 null 呈现）。"""
    try:
        with closing(workflow.connect(ctx.owner_db, read_only=True)) as conn:
            if not workflow.has_table(conn, 'articles'):
                return None
            return {r['zotero_key'] for r in conn.execute(
                'SELECT zotero_key FROM articles WHERE is_duplicate_of IS NULL')}
    except sqlite3.DatabaseError as exc:
        # S4：metric_records:284 的 try 只捕 TeamFlowUnavailableError——此处
        # 收敛 sqlite 族，不让坏库/锁忙逃逸成通用 500。
        raise _storage_unavailable(exc) from exc


def _team_current_projection_or_403(runtime, ctx, item_keys):
    """P22：team 模式当前团队投影（owner/Adjudicator 门与旧 outcomes 同形状
    403；受保护按键只读 scope，零持久化）。"""
    if not ctx.can_manage and not task_collaboration.has_team_action_role(
            ctx.collaboration_db, user_id=ctx.user['user_id'],
            action='coding:read_all', task_id=ctx.info.task_id):
        raise HTTPException(403, 'Team PRISMA metrics require the task owner or Adjudicator.')
    from custom_backend.team_current_results import current_team_projection
    return current_team_projection(
        runtime, ctx.info, ctx.user, item_keys=item_keys)


def _team_flow_http(exc):
    """N4（P22 复审）：TeamFlowUnavailableError → typed 503 HTTPException。

    负载形状照 review_workflow_api 既有映射（error_code/message_key/params/
    details/message）；未捕获时该 typed 错误会逃逸全局兜底成通用 500（legacy
    F2 未登记的团队任务 mode=team 即触发 completion_source_unresolved）。
    """
    return HTTPException(getattr(exc, "status", 503), {
        "error_code": getattr(exc, "code", "team.flow.unavailable"),
        "message_key": getattr(exc, "message_key", "team.flow.unavailable"),
        "params": getattr(exc, "params", {}),
        "details": str(exc), "message": str(exc)})


def summary(ctx, mode: str, runtime=None) -> dict:
    try:
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
    except sqlite3.DatabaseError as exc:
        # S4：task_metrics 端点无局部兜底——connect/has_table/只读查询的
        # sqlite 族故障收敛为 typed 503，不再逃逸全局通用 500。
        raise _storage_unavailable(exc) from exc
    # P22（C11）：team 模式改走当前团队投影（每键 F1/可信当前意见/按键
    # attestation；完成条目保留完成时结论，新名单只作用于未完成——
    # 2026-11-15 裁定锚）。runtime 缺席（旧直调方）回落原 outcomes 路径。
    team_projection = None
    if mode == 'team' and ctx.task_kind == 'team' and runtime is not None:
        from custom_backend.team_current_results import (
            outcomes_from_current_team)
        from custom_backend.team_screening_runtime import (
            TeamFlowUnavailableError)
        try:
            team_projection = _team_current_projection_or_403(
                runtime, ctx, tuple(sorted(active_keys)))
        except TeamFlowUnavailableError as exc:
            # N4：typed 503（如 legacy F2 未登记 → completion_source_
            # unresolved），不逃逸成通用 500。
            raise _team_flow_http(exc) from exc
        first, first_unresolved, second, second_unresolved = \
            outcomes_from_current_team(team_projection, domain=active_keys)
    else:
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
    if team_projection is not None:
        # P22：投影侧诊断如实上抛（来源坏/意见不全不伪空、不排除）。
        if team_projection['counts']['unavailable']:
            warnings.append('Some team completion sources are unreadable; affected records are reported, not excluded.')
        if team_projection['counts']['partial']:
            warnings.append('Some records are awaiting member votes (partial team completion, not a team conclusion).')
    fields = {'identified_rows_db': db_rows, 'identified_rows_other': other_rows,
              'sources_scope': 'db_reg_other' if other_rows else 'db_reg',
              'duplicates_removed': duplicate_count,
              # R6-F9（r8 复审回退 db-only）：records_screened/初筛排除按
              # **数据库/注册库列键域**（db_keys）计——该字段的第二消费方
              # 「任务指标导入表单」把这两个数填进 PRISMA 数据库列字段，
              # R2/R3 按数据库列勾稽；并入 other-only 键曾使导入表单直接
              # R2/R3 error（导出被阻断，实测 records_screened=2 vs
              # identified_rows_db=1）。任务页与 CLI 的跨口径对齐（CLI 计
              # 全部决策行）属产品仲裁项，见 r7 指示文档 F9 条目。
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
    payload = {'fields': fields, 'warnings': list(dict.fromkeys(warnings)), 'task_kind':ctx.task_kind,
            'mode':mode, 'version':workflow.settings(ctx.owner_db)['version'], 'complete':ready,
            'pending':{'unscreened':len(active_keys-set(first)), 'team_unresolved':first_unresolved+second_unresolved,
                       'fulltext_unresolved':full_pending, 'staged_records':sum(r['state']=='staged' for r in originals)},
            'sources':list(catalog.values()), 'coverage_complete':complete_provenance and not unidentified}
    if team_projection is not None:
        # P22：当前团队投影计数（eligible/blocked 含 partial/unavailable/
        # known_total 锚；owner 域无法确定总数用 null——_owner_active_keys
        # 侧 None 时这里不会到达（summary 的 active_keys 恒可定））。
        payload['current_team'] = {
            'counts': team_projection['counts'],
            'roster_required': list(team_projection['roster']['required']),
            'completion_required_uids': list(
                team_projection['roster']['completion_required_uids'])}
    return payload


def register_prisma_task_metrics(app, require_user):
    @app.get('/api/tasks/{task_id}/prisma-metrics/records')
    def metric_records(task_id: str, mode: Literal['reviewer','team']='reviewer', user: dict=Depends(require_user)):
        runtime=app.state.review_workflow
        # R6-C3（复审扫尾）：锁键用规范 task_id（尾点别名不得分键）
        task_id=runtime.task_lookup(task_id).task_id
        with runtime.lock(task_id):
            ctx=runtime.context(task_id,user)
            if mode == 'team' and ctx.task_kind == 'team':
                # P22：records 的 team 投影（初始/全文两列=当前团队结论；
                # 来源坏键空结论＋unavailable 计数，不伪空排除）。N4：
                # typed 错误（含 F2 未登记）→503，不逃逸通用 500。
                from custom_backend.team_screening_runtime import (
                    TeamFlowUnavailableError)
                try:
                    domain = _owner_active_keys(ctx)
                    projection = _team_current_projection_or_403(
                        runtime, ctx,
                        tuple(sorted(domain)) if domain is not None else ())
                except TeamFlowUnavailableError as exc:
                    raise _team_flow_http(exc) from exc
                from custom_backend.team_current_results import (
                    outcomes_from_current_team)
                first, _, second, _ = outcomes_from_current_team(
                    projection, domain=domain)
            else:
                first,_=outcomes(ctx,'stage1',mode)
                second,_=outcomes(ctx,'stage2',mode)
            catalog={s['id']:s for s in workflow.sources(ctx.owner_db)}
            output=io.StringIO(newline='');writer=csv.writer(output)
            writer.writerow(['source_record_id','source_id','source_name','source_type','filename','row_index','state',
                             'canonical_key','original_title','initial_outcome','fulltext_outcome','metric_mode'])
            try:
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
            except sqlite3.DatabaseError as exc:
                # S4：records 端点 connect/has_table/只读查询 sqlite 族 → 503。
                raise _storage_unavailable(exc) from exc
            return Response('\ufeff'+output.getvalue(),media_type='text/csv',headers={'Content-Disposition':'attachment; filename="prisma-task-records.csv"','Cache-Control':'no-store'})

    @app.get('/api/tasks/{task_id}/prisma-metrics')
    def task_metrics(task_id: str, mode: Literal['reviewer','team']='reviewer', user: dict=Depends(require_user)):
        runtime=app.state.review_workflow
        # R6-C3（复审扫尾）：锁键用规范 task_id（尾点别名不得分键）
        task_id=runtime.task_lookup(task_id).task_id
        with runtime.lock(task_id):
            ctx=runtime.context(task_id,user)
            return summary(ctx,mode,runtime=runtime)
