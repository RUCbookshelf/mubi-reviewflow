"""Import-derived provenance. Never change article metadata or research decisions."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path

from coscreen import review_workflow as workflow

VERSION = 4
#: v5（文件名溯源重构）版本号；context() 热路径判齐用
FILENAME_VERSION = 5
SOURCE_TYPES = {'database', 'register', 'website', 'organisation', 'citation_searching', 'other'}
# MM3-03：v4 原设想的「属主命名来源别名表」已随其唯一消费分支一并删除——
# v5 起 detect() 刻意只认上传文件名（见其注释），source_type 恒为 None，
# 该分支从未生效；如需恢复来源类型识别，须基于 RIS DB/DP 元数据另立设计
# 并配套迁移，而非沿用这里的文件名口径。


def first(raw: dict, *keys: str) -> str:
    """取第一个非空字符串元数据值（**不截断**，r9 夜扫 b16·F4-87）。

    原实现 ``value.strip()[:200]`` 会把 legacy 的 ``source_file`` 截到 200
    字符，而 :func:`detect` 用完整文件名派生 identity——同一文件在迁移路径
    与 v5 上传路径得到两个来源行。文件名长度上限由写入口负责
    （``security._MAX_NAME_LENGTH=180``）；读侧截断只会制造身份分裂。
    """
    for key in keys:
        value = raw.get(key)
        if isinstance(value, list):
            value = next((v for v in value if str(v).strip()), '')
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ''


def detect(original: dict, filename: str, digest: str) -> dict:
    # Source identity is the complete uploaded filename, never RIS DB/DP metadata.
    name = Path(filename.replace('\\', '/')).name or 'Source not recorded'
    # b06·F4-84：孤立代理字符无法编码进 workflow_sources（TEXT 存储本身
    # 接受，但后续 ensure 的 json.dumps/UNIQUE 比较与日志会炸）。用
    # surrogatepass 容错后按 errors="replace" 归一——展示名可替换，来源
    # identity 保持确定性；正常文件名逐字节不变。
    name = name.encode('utf-8', 'surrogatepass').decode('utf-8', 'replace')
    return {'name': name, 'identity': 'filename:' + workflow.normalized(name),
            'identification': 'file', 'source_type': None, 'provider': '',
            'suggestion': '', 'evidence': {'filename': name}}


def ensure(conn: sqlite3.Connection, detected: dict) -> str:
    """解析/创建来源行，返回 source_id。

    r9 夜扫 b16·F4-88：``named`` 回退按 ``normalized_name`` 查 ``workflow_sources``
    时不排除 UNASSIGNED 保留行——文件名规范化后恰为 ``unassigned`` 时会取到
    全零 UUID，随即改写保留行显示名并把 detail 挂到保留行上。保留行的身份是
    产品不变量（``workflow.sources`` 以 ``reserved`` 标记、改名端点直接 400），
    因此 named 回退显式排除它；命中保留行时按 identity 新建独立来源。
    正常文件名的行为完全不变（它们不可能命中保留行的规范化名）。
    """
    known = conn.execute('SELECT source_id FROM workflow_source_details WHERE identity=? ORDER BY source_id LIMIT 1', (detected['identity'],)).fetchone()
    normalized_name = workflow.normalized(detected['name'])
    # 规范化名与保留行同名（文件名恰为 ``unassigned``）：workflow_sources 的
    # normalized_name 是 UNIQUE，无法再建第二个同名来源；而该文件字面含义
    # 就是「未归属」。直接返回保留 id 且**不写任何行**——既不改写保留行的
    # 显示名、不挂 detail（修前的污染），也不因 UNIQUE 冲突落 FK 失败。
    if normalized_name == workflow.normalized('Unassigned'):
        return workflow.UNASSIGNED
    named = conn.execute(
        'SELECT id FROM workflow_sources WHERE normalized_name=? AND id<>?',
        (normalized_name, workflow.UNASSIGNED)).fetchone()
    sid = known[0] if known else named[0] if named else str(uuid.uuid5(uuid.NAMESPACE_URL, 'reviewflow:source:' + detected['identity']))
    # 兜底：identity 已登记在保留行名下时同样按「未归属」处理（不写行）。
    if sid == workflow.UNASSIGNED:
        return workflow.UNASSIGNED
    conn.execute("INSERT OR IGNORE INTO workflow_sources VALUES(?,?,?,1,datetime('now'))",(sid,detected['name'],normalized_name))
    conn.execute('UPDATE workflow_sources SET name=?,normalized_name=? WHERE id=?',(detected['name'],normalized_name,sid))
    conn.execute("""INSERT INTO workflow_source_details VALUES(?,?,?,?,?,?,?)
                 ON CONFLICT(source_id) DO UPDATE SET identification=CASE WHEN identity=excluded.identity AND identification='confirmed' THEN 'confirmed' ELSE 'file' END,identity=excluded.identity,
                 evidence_json=excluded.evidence_json,provider='',suggestion='' """,
                 (sid, detected['identification'], detected['source_type'], detected['provider'],
                  detected['suggestion'], json.dumps(detected['evidence'], ensure_ascii=False), detected['identity']))
    return sid


def prepare_files(conn: sqlite3.Connection, files: list[dict]) -> None:
    """为上传文件补齐来源归属，**尊重调用方已校验的显式 source_id**。

    r9 夜扫 b16·F4-86：此前无条件用文件名派生 sid 覆写 ``file['source_id']``，
    API 层刚校验通过的客户端来源选择被静默丢弃（文档 docs/WORKFLOW_CONTROLS.md
    宣称 ``POST /import(-batch)`` 接受 ``source_id``/``source_ids``）。现在：
    - 已给显式 id 的文件原样保留（API 已校验属于当前任务来源集合）；
    - ``None``/``''``/``'auto'`` 仍是「按文件名派生」语义（现行前端恒发
      ``'auto'``，行为不变）。
    """
    for file in files:
        explicit = file.get('source_id')
        if isinstance(explicit, str) and explicit.strip() and explicit != 'auto':
            sid = explicit
        else:
            sid = ensure(conn, detect({}, file['name'], file['digest']))
        file['source_ids'] = [sid] * len(file['articles'])
        file['source_id'] = sid


def _safe_original_json(row: sqlite3.Row) -> dict:
    """解码 source record 的 original_json；坏行降级为空 dict。

    r9 夜扫 b06·F4-84：``json.loads(row['original_json'])`` 无防护，库外改写/
    部分写留下的坏行令迁移在记录版本前抛出；``context()`` 每请求检测版本
    缺失就重跑整条迁移链 → 该库**每个请求持续 500**、无产品内恢复。原始
    载荷解不出来时退回 ``article_json``（另一份同源副本），再不行按空 dict
    处理——来源归属只按文件名派生（``detect`` 不读 original 内容），空 dict
    与完整载荷得到同一 identity，因而降级不改变归属结果。
    """
    for column in ('original_json', 'article_json'):
        try:
            value = json.loads(row[column] or '{}')
        except (TypeError, ValueError, KeyError, IndexError):
            continue
        if isinstance(value, dict):
            return value
    return {}


def _backup_usable(path: Path) -> bool:
    """备份是否可打开、非空且通过 quick_check（损坏/截断/0 字节一律 False）。"""
    try:
        if path.stat().st_size <= 0:
            return False
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            tables = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
            integrity = conn.execute("PRAGMA quick_check").fetchone()
        finally:
            conn.close()
    except (sqlite3.Error, OSError, ValueError):
        return False
    return tables > 0 and bool(integrity) and integrity[0] == 'ok'


def _verified_backup(source_conn: sqlite3.Connection, backup: Path) -> None:
    """对该库写一份**可验证**的升级前备份（r9 夜扫 b16·F4-85）。

    原先 ``if not backup.exists()`` 就把任何残留 .bak 当作有效保护网：上次
    迁移中途被杀/掉电留下的截断文件会被永久信任，下次真升级时「以为有
    回滚点」而实际打不开。现在：
    - 已有备份先校验（能打开 + 含表 + quick_check ok）；可用则复用（不覆盖
      用户已有的完好备份）；
    - 不可用/缺失时先写 .tmp，复制完成、校验通过才 rename 成正式名——
      进程在拷贝中被杀只会留下 .tmp，不会污染正式备份名。
    """
    backup = Path(backup)
    if backup.is_file() and _backup_usable(backup):
        return
    # 每个进程用独立临时名（b06·F4-83 并发首升级：两个 worker 会同时走这里，
    # 共用固定 .tmp 名会让后到者撞 WinError 32/文件占用）。写完校验通过才
    # rename 到正式名；两者内容都是同一库的一致快照，谁最后落名都有效。
    tmp = backup.with_name(f"{backup.name}.{uuid.uuid4().hex}.tmp")
    try:
        with closing(sqlite3.connect(tmp)) as target:
            source_conn.backup(target)
        if not _backup_usable(tmp):
            raise ValueError(f"升级前备份不可用（复制结果校验失败）：{backup}")
        try:
            tmp.replace(backup)
        except OSError:
            # 另一 worker 已先落名：它写的是同一库的一致快照，可接受。
            if not _backup_usable(backup):
                raise
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def _migrate_v4(db: str | Path) -> None:
    workflow.migrate(db)
    with closing(workflow.connect(db, read_only=True)) as conn:
        if conn.execute('SELECT 1 FROM workflow_migrations WHERE version=?', (VERSION,)).fetchone():
            return
        # 备份只保护既有库；本进程新建、尚在首建链中的库没有可备份的数据。
        if not workflow.is_fresh_database(db):
            _verified_backup(conn, Path(db).with_name(Path(db).name + '.pre-source-v4.bak'))
    with closing(workflow.connect_for_initial_migration(db)) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        # b06·F4-83：版本检查此前只在写事务外的只读连接里做，事务内不复检。
        # 文档化部署是 uvicorn --workers 2：两个 worker 同时首升级时双双通过
        # 检查，后到者 BEGIN IMMEDIATE 排队后重跑整个迁移体，在
        # INSERT INTO workflow_migrations 上撞 UNIQUE 主键 → 首波请求 500。
        # 现在事务内（已持写锁）再查一次：另一进程已完成则回滚为无操作
        # ——不引入全局锁，正常单进程路径只是多一次 SELECT。
        if conn.execute('SELECT 1 FROM workflow_migrations WHERE version=?', (VERSION,)).fetchone():
            conn.rollback()
            return
        conn.execute('''CREATE TABLE IF NOT EXISTS workflow_source_details(
            source_id TEXT PRIMARY KEY REFERENCES workflow_sources(id), identification TEXT NOT NULL,
            source_type TEXT, provider TEXT NOT NULL DEFAULT '', suggestion TEXT NOT NULL DEFAULT '',
            evidence_json TEXT NOT NULL, identity TEXT NOT NULL)''')
        recovered = set()
        # Original rows are immutable; only their source association is recovered.
        if workflow.has_table(conn, 'workflow_source_records'):
            for row in conn.execute('SELECT * FROM workflow_source_records WHERE source_id=?', (workflow.UNASSIGNED,)).fetchall():
                # b06·F4-84：坏 original_json 不再让整条迁移链失败（见
                # _safe_original_json）；归属只按 filename 派生。
                sid = ensure(conn, detect(_safe_original_json(row), row['filename'], row['digest']))
                collision = conn.execute('SELECT 1 FROM workflow_source_records WHERE source_id=? AND digest=? AND row_index=?',
                                         (sid, row['digest'], row['row_index'])).fetchone()
                if collision:
                    continue  # Never collapse retained originals or change candidate IDs.
                conn.execute('UPDATE workflow_source_records SET source_id=? WHERE id=?', (sid, row['id']))
                if row['state'] == 'active' and row['canonical_key']:
                    key, seen = row['canonical_key'], set()
                    while key not in seen:
                        seen.add(key)
                        article = conn.execute('SELECT is_duplicate_of FROM articles WHERE zotero_key=?', (key,)).fetchone()
                        if article is None:
                            break
                        if article[0] is None:
                            workflow.associate_sources(db, [key], [sid], conn=conn)
                            break
                        key = article[0]
                recovered.add(sid)
            # File-level identity is supplementary; records remain authoritative for mixed files.
            conn.execute('''UPDATE workflow_import_files SET source_id=COALESCE((
                SELECT r.source_id FROM workflow_import_members m JOIN workflow_source_records r ON r.id=m.record_id
                WHERE m.batch_id=workflow_import_files.batch_id AND m.file_index=workflow_import_files.file_index
                ORDER BY r.row_index LIMIT 1),source_id) WHERE source_id=?''', (workflow.UNASSIGNED,))
        if workflow.has_table(conn, 'articles'):
            for row in conn.execute('''SELECT a.zotero_key,a.raw_json FROM articles a
                JOIN workflow_article_sources s ON s.zotero_key=a.zotero_key WHERE s.source_id=?''', (workflow.UNASSIGNED,)).fetchall():
                try:
                    raw = json.loads(row['raw_json'] or '{}')
                except (TypeError, ValueError):
                    continue
                filename = first(raw, 'source_file')
                if not filename:
                    continue
                # b06·F4-84（第二处）：filename 可含孤立代理字符（库外改写/
                # 旧版遗留），'legacy-file:' + filename 的 utf-8 编码会抛
                # UnicodeEncodeError（是 ValueError 子类，但此处不在任何
                # try 内）。保留原字符做 identity，编码时按 surrogatepass
                # 容错——digest 只用于文件级身份，不参与展示。
                digest = hashlib.sha256(
                    ('legacy-file:' + filename).encode('utf-8', 'surrogatepass')
                ).hexdigest()
                sid = ensure(conn, detect({'raw': raw}, filename or 'Legacy import', digest))
                # Legacy duplicate rows retain provenance but are not new work.
                conn.execute('INSERT OR IGNORE INTO workflow_article_sources VALUES(?,?)', (row['zotero_key'], sid))
                conn.execute('DELETE FROM workflow_article_sources WHERE zotero_key=? AND source_id=?', (row['zotero_key'], workflow.UNASSIGNED))
                key, seen = row['zotero_key'], set()
                while key not in seen:
                    seen.add(key)
                    article = conn.execute('SELECT is_duplicate_of FROM articles WHERE zotero_key=?', (key,)).fetchone()
                    if article is None:
                        break
                    if article[0] is None:
                        workflow.associate_sources(db, [key], [sid], conn=conn)
                        break
                    key = article[0]
                recovered.add(sid)
        # MM3-03：v4 原设想的「识别属主命名来源（database）」分支已删除。
        # 判定条件是双重死路：detect() 恒 source_type=None（v5 起来源身份
        # 刻意只认上传文件名，不读 RIS DB/DP 元数据），且 e['name'] 是文件名
        # 而比较对象 label 是来源名（'pubmed-export.ris' ≠ 'PubMed'）——
        # 分支与其别名表从未生效，属主命名来源保持 legacy 呈现。取舍＝删除
        # 而非实现：实现识别需推翻文件名身份设计并改变迁移产出（违反保守
        # 铁律 R3「能不改行为就不改」），删除则零行为变化。
        policy = json.loads(conn.execute('SELECT stages_json FROM workflow_state WHERE singleton=1').fetchone()[0])
        placeholder_used = conn.execute('SELECT 1 FROM workflow_article_sources WHERE source_id=? LIMIT 1', (workflow.UNASSIGNED,)).fetchone()
        if workflow.has_table(conn, 'workflow_source_records'):
            placeholder_used = placeholder_used or conn.execute('SELECT 1 FROM workflow_source_records WHERE source_id=? LIMIT 1', (workflow.UNASSIGNED,)).fetchone()
        for stage in policy.values():
            if workflow.UNASSIGNED in stage['source_ids']:
                stage['source_ids'] = sorted(set(stage['source_ids']) | recovered)
                if not placeholder_used:
                    stage['source_ids'].remove(workflow.UNASSIGNED)
        conn.execute('UPDATE workflow_state SET stages_json=?,version=version+1 WHERE singleton=1', (json.dumps(policy),))
        conn.execute("INSERT OR IGNORE INTO workflow_migrations VALUES(?,datetime('now'))", (VERSION,))


def decorate(conn: sqlite3.Connection, sources: list[dict]) -> list[dict]:
    result = []
    policies = json.loads(conn.execute('SELECT stages_json FROM workflow_state WHERE singleton=1').fetchone()[0])
    referenced = {sid for stage in policies.values() for sid in stage['source_ids']}
    for source in sources:
        sid = source['id']
        links = conn.execute('SELECT COUNT(*) FROM workflow_article_sources s JOIN articles a ON a.zotero_key=s.zotero_key WHERE s.source_id=? AND a.is_duplicate_of IS NULL', (sid,)).fetchone()[0] if workflow.has_table(conn,'articles') else conn.execute('SELECT COUNT(*) FROM workflow_article_sources WHERE source_id=?',(sid,)).fetchone()[0]
        originals, filenames = 0, set()
        if workflow.has_table(conn, 'workflow_source_records'):
            rows = conn.execute('SELECT filename FROM workflow_source_records WHERE source_id=?', (sid,)).fetchall()
            originals, filenames = len(rows), {r[0] for r in rows}
        if source['reserved'] and not links and not originals:
            continue
        if not source['reserved'] and not links and not originals and sid not in referenced:
            continue
        detail = conn.execute('SELECT * FROM workflow_source_details WHERE source_id=?', (sid,)).fetchone() if workflow.has_table(conn, 'workflow_source_details') else None
        active_links = conn.execute('SELECT COUNT(*) FROM workflow_article_sources s JOIN articles a ON a.zotero_key=s.zotero_key WHERE s.source_id=? AND a.is_duplicate_of IS NULL', (sid,)).fetchone()[0] if workflow.has_table(conn,'articles') else links
        result.append({**source, **(dict(detail) if detail else {}), 'name': 'Source not recorded' if source['reserved'] else source['name'],
                       'record_count': originals, 'linked_count': active_links, 'files': sorted(filenames),
                       'identification': detail['identification'] if detail else 'legacy'})
    return result


def migrate(db: str | Path) -> None:
    """Upgrade filename associations without rewriting original records or research."""
    _migrate_v4(db)
    with closing(workflow.connect(db, read_only=True)) as conn:
        if conn.execute('SELECT 1 FROM workflow_migrations WHERE version=?', (FILENAME_VERSION,)).fetchone():
            return
        # 同 v4：首建链中的新库跳过空库备份，真实旧库照常备份。
        if not workflow.is_fresh_database(db):
            _verified_backup(conn, Path(db).with_name(Path(db).name + '.pre-filename-v5.bak'))
    with closing(workflow.connect_for_initial_migration(db)) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        # b06·F4-83（v5 同构）：写事务内复检版本，另一进程已完成则无操作。
        if conn.execute('SELECT 1 FROM workflow_migrations WHERE version=?', (FILENAME_VERSION,)).fetchone():
            conn.rollback()
            return
        mapping, source_status = {}, {}
        if workflow.has_table(conn, 'workflow_source_records'):
            for row in conn.execute('SELECT * FROM workflow_source_records').fetchall():
                if not row['filename']:
                    continue
                old = row['source_id']
                sid = ensure(conn, detect({}, row['filename'], row['digest']))
                mapping.setdefault(old, set()).add(sid)
                status=conn.execute('SELECT active FROM workflow_sources WHERE id=?',(old,)).fetchone()
                source_status[sid]=max(source_status.get(sid,0),status[0] if status else 1)
                collision = conn.execute('SELECT id FROM workflow_source_records WHERE source_id=? AND digest=? AND row_index=? AND id<>?',
                                         (sid, row['digest'], row['row_index'], row['id'])).fetchone()
                # Keep immutable originals and IDs if a legacy collision cannot be reassociated.
                if collision:
                    mapping[old].add(old)
                    continue
                conn.execute('UPDATE workflow_source_records SET source_id=? WHERE id=?', (sid, row['id']))
                if row['state'] == 'active' and row['canonical_key']:
                    key, seen = row['canonical_key'], set()
                    while key and key not in seen:
                        seen.add(key)
                        article = conn.execute('SELECT is_duplicate_of FROM articles WHERE zotero_key=?', (key,)).fetchone()
                        if article is None:
                            break
                        if article[0] is None:
                            workflow.associate_sources(db, [key], [sid], conn=conn)
                            # Remove only this proven obsolete source association.
                            if old != sid:
                                remaining = conn.execute('SELECT 1 FROM workflow_source_records WHERE source_id=? AND canonical_key=? AND state=? LIMIT 1', (old,key,'active')).fetchone()
                                if not remaining:
                                    conn.execute('DELETE FROM workflow_article_sources WHERE zotero_key=? AND source_id=?',(key,old))
                            break
                        key = article[0]
            conn.execute("""UPDATE workflow_import_files SET source_id=COALESCE((
                SELECT r.source_id FROM workflow_import_members m JOIN workflow_source_records r ON r.id=m.record_id
                WHERE m.batch_id=workflow_import_files.batch_id AND m.file_index=workflow_import_files.file_index
                ORDER BY r.row_index LIMIT 1),source_id)""")
        # Older catalogs may retain only source_file in article raw metadata.
        if workflow.has_table(conn,'articles'):
            for article in conn.execute('SELECT zotero_key,raw_json FROM articles WHERE is_duplicate_of IS NULL').fetchall():
                try:
                    filename=first(json.loads(article['raw_json'] or '{}'),'source_file')
                except (TypeError,ValueError):
                    continue
                if not filename:
                    continue
                if conn.execute('SELECT 1 FROM workflow_source_records WHERE canonical_key=? LIMIT 1',(article['zotero_key'],)).fetchone() if workflow.has_table(conn,'workflow_source_records') else False:
                    continue
                old_ids=[r[0] for r in conn.execute('SELECT source_id FROM workflow_article_sources WHERE zotero_key=?',(article['zotero_key'],))]
                sid=ensure(conn,detect({},filename,''))
                workflow.associate_sources(db,[article['zotero_key']],[sid],conn=conn)
                for old in old_ids:
                    mapping.setdefault(old,set()).add(sid)
                    status=conn.execute('SELECT active FROM workflow_sources WHERE id=?',(old,)).fetchone()
                    source_status[sid]=max(source_status.get(sid,0),status[0] if status else 1)
                    if old!=sid:
                        conn.execute('DELETE FROM workflow_article_sources WHERE zotero_key=? AND source_id=?',(article['zotero_key'],old))
        for sid, active in source_status.items():
            conn.execute('UPDATE workflow_sources SET active=? WHERE id=?',(active,sid))
        policy = json.loads(conn.execute('SELECT stages_json FROM workflow_state WHERE singleton=1').fetchone()[0])
        for stage in policy.values():
            ids = set()
            for old in stage['source_ids']:
                ids.update(mapping.get(old, {old}))
                if conn.execute('SELECT 1 FROM workflow_article_sources WHERE source_id=? LIMIT 1',(old,)).fetchone():
                    ids.add(old)
            stage['source_ids'] = sorted(ids)
        conn.execute('UPDATE workflow_state SET stages_json=?,version=version+1,catalog_revision=catalog_revision+1 WHERE singleton=1',(json.dumps(policy),))
        conn.execute("INSERT OR IGNORE INTO workflow_migrations VALUES(5,datetime('now'))")
