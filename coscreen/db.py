"""SQLite 持久层 —— meta / articles / decisions / rankings 四表（SPEC §5、§12）。

- 仅用标准库 sqlite3；每个函数自行开闭连接（context-managed）。
- 连接统一启用 WAL 日志模式与外键约束。
- SQL 一律参数化，不拼接任何数据值。
- 重复条目同样入库（is_duplicate_of 非空），筛选/进度默认跳过它们。
- rankings 表（SPEC §12 审计）为后加表：``record_ranking`` 写入前先执行
  ``CREATE TABLE IF NOT EXISTS``，旧库文件无需迁移即可继续使用。
- decisions.tags 列（SPEC §6bis 标签）为后加列：``_connect`` 内做幂等自愈
  迁移（PRAGMA 检查缺列才 ALTER），旧库文件无需手工迁移即可继续使用。
- 复筛三表（SPEC §14：fulltexts / highlights / stage2_decisions）同为后加表：
  ``_connect`` 内幂等执行 ``CREATE TABLE IF NOT EXISTS``，旧库文件无需迁移。
- 编码四表（SPEC §15.1：coding_dimensions / coding_values / coding_notes /
  coding_note_images）同为后加表：``_connect`` 内幂等补建；旧库维度类型约束
  在 ``_ensure_coding_tables`` 中扩展为 choice/text/numeric。
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from coscreen import config
from coscreen.al.ranker import RankingResult
from coscreen.dedup import DuplicatePair
from coscreen.measure_registry import MEASURE_CODES
from coscreen.models import Article

StrPath = str | Path

if TYPE_CHECKING:
    from coscreen.research_trace import TraceContext

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta   (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS articles (
  zotero_key TEXT PRIMARY KEY, item_type TEXT, title TEXT, authors TEXT,
  journal TEXT, year INTEGER, doi TEXT, abstract TEXT, url TEXT,
  source_format TEXT, raw_json TEXT, content_hash TEXT,
  is_duplicate_of TEXT,              -- 指向保留条目的 zotero_key；NULL=非重复
  import_order INTEGER
);
CREATE INDEX IF NOT EXISTS idx_articles_doi ON articles(doi);
CREATE INDEX IF NOT EXISTS idx_articles_hash ON articles(content_hash);
CREATE TABLE IF NOT EXISTS decisions (
  zotero_key TEXT PRIMARY KEY REFERENCES articles(zotero_key),
  decision TEXT NOT NULL,            -- 'include' | 'exclude' | 'maybe'
  exclusion_reason TEXT DEFAULT '',
  notes TEXT DEFAULT '',
  tags TEXT DEFAULT '',              -- 逗号分隔自由文本标签（SPEC §6bis）
  screened_at TEXT NOT NULL,         -- ISO8601 本地时间（首次筛选）
  updated_at TEXT NOT NULL
);
"""

# rankings 审计表（SPEC §12）：每次重排写入一个批次（同批共享 created_at 与
# 批次 id）；单独成常量以便 record_ranking 在旧库上自愈（CREATE IF NOT EXISTS）。
_RANKINGS_SCHEMA = """
CREATE TABLE IF NOT EXISTS rankings (
  id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL,
  strategy TEXT NOT NULL, seed INTEGER NOT NULL, n_labeled INTEGER NOT NULL,
  model_meta TEXT NOT NULL,           -- JSON（特征数/alpha/训练分布等）
  zotero_key TEXT NOT NULL, position INTEGER NOT NULL, score REAL NOT NULL);
CREATE INDEX IF NOT EXISTS idx_rankings_created_at ON rankings(created_at);
"""

_SCHEMA = _SCHEMA + _RANKINGS_SCHEMA

_TITLE_DUPLICATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS title_duplicate_reviews (
  review_id TEXT PRIMARY KEY,
  normalized_title TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('pending','undecided','duplicate','not_duplicate')),
  members_json TEXT NOT NULL,
  keeper_key TEXT NOT NULL DEFAULT '',
  decision_by TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_title_duplicate_status
  ON title_duplicate_reviews(status, created_at);
CREATE TABLE IF NOT EXISTS title_duplicate_members (
  review_id TEXT NOT NULL REFERENCES title_duplicate_reviews(review_id) ON DELETE CASCADE,
  zotero_key TEXT NOT NULL REFERENCES articles(zotero_key) ON DELETE CASCADE,
  imported_in_batch INTEGER NOT NULL CHECK(imported_in_batch IN (0,1)),
  PRIMARY KEY(review_id, zotero_key)
);
CREATE INDEX IF NOT EXISTS idx_title_duplicate_member_key
  ON title_duplicate_members(zotero_key, imported_in_batch);
"""

# 复筛（全文阶段，SPEC §14）三表：PDF 全文元数据 / 高亮标记 / 复筛决策。
# stage2_decisions 与初筛 decisions 字段语义完全一致（排除必填理由在调用层
# 校验、screened_at 首筛保留）；高亮 color 取 'jade' | 'amber' | 'terra'。
# 单独成常量以便 _connect 在旧库上自愈（CREATE IF NOT EXISTS，幂等）。
_STAGE2_SCHEMA = """
CREATE TABLE IF NOT EXISTS fulltexts (
  zotero_key TEXT PRIMARY KEY REFERENCES articles(zotero_key),
  filename TEXT NOT NULL,            -- 原始/派生文件名（含扩展名）
  path TEXT NOT NULL,                -- PDF 二进制实际存放路径
  sha256 TEXT NOT NULL,              -- 文件内容摘要（去重与变更检测）
  n_pages INTEGER NOT NULL DEFAULT 0,-- pypdf 页数（解析失败为 0）
  added_at TEXT NOT NULL             -- ISO8601 本地时间（最近一次写入）
);
CREATE TABLE IF NOT EXISTS highlights (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  zotero_key TEXT NOT NULL REFERENCES articles(zotero_key),
  page INTEGER NOT NULL,             -- 1 起始页码
  text TEXT NOT NULL,                -- 高亮的原文选段
  color TEXT NOT NULL DEFAULT 'jade',-- 'jade' | 'amber' | 'terra'
  note TEXT DEFAULT '',              -- 页笔记（选填）
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_highlights_key ON highlights(zotero_key, page);
CREATE TABLE IF NOT EXISTS stage2_decisions (
  zotero_key TEXT PRIMARY KEY REFERENCES articles(zotero_key),
  decision TEXT NOT NULL,            -- 'include' | 'exclude' | 'maybe'
  exclusion_reason TEXT DEFAULT '',
  notes TEXT DEFAULT '',
  tags TEXT DEFAULT '',              -- 逗号分隔自由文本标签（与初筛一致）
  screened_at TEXT NOT NULL,         -- ISO8601 本地时间（首次复筛）
  updated_at TEXT NOT NULL
);
"""

_SCHEMA = _SCHEMA + _STAGE2_SCHEMA

# 编码（数据提取阶段，SPEC §15.1）四表：
# coding_dimensions 维度定义（dtype 'choice'|'text'|'numeric'，options 以 JSON 数组存
# options_json）、coding_values 每（维度 × 文献）一个编码值（多选以 "|" 连接）、
# coding_notes 每（维度 × 文献）一条图文备注、coding_note_images 备注图片
# （磁盘二进制 + sha256 + OCR 文本/状态）。
# 单独成常量以便 _connect 在旧库上自愈（CREATE TABLE IF NOT EXISTS，幂等），
# 字段沿用 SPEC §15.1，并支持 numeric 维度。
_CODING_SCHEMA = """
CREATE TABLE IF NOT EXISTS coding_dimensions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
  dtype TEXT NOT NULL CHECK(dtype IN ('choice','text','numeric')), options_json TEXT NOT NULL DEFAULT '[]',
  multi_select INTEGER NOT NULL DEFAULT 0, unit TEXT DEFAULT '', description TEXT DEFAULT '',
  section TEXT DEFAULT '', position INTEGER NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS coding_values (
  dimension_id INTEGER NOT NULL REFERENCES coding_dimensions(id),
  zotero_key TEXT NOT NULL REFERENCES articles(zotero_key),
  value TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL,
  PRIMARY KEY(dimension_id, zotero_key));
CREATE TABLE IF NOT EXISTS coding_notes (
  id INTEGER PRIMARY KEY AUTOINCREMENT, dimension_id INTEGER NOT NULL, zotero_key TEXT NOT NULL,
  text TEXT DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE(dimension_id, zotero_key));
CREATE TABLE IF NOT EXISTS coding_note_images (
  id INTEGER PRIMARY KEY AUTOINCREMENT, note_id INTEGER NOT NULL REFERENCES coding_notes(id),
  path TEXT NOT NULL, sha256 TEXT NOT NULL, ocr_text TEXT DEFAULT '',
  ocr_status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL);
"""

_SCHEMA = _SCHEMA + _CODING_SCHEMA

# measure CHECK 清单（P9 指标注册表）：由 measure_registry.MEASURE_CODES 生成，
# 保持既有 SQL 字面量风格（',' 连接、无空格）。既有 11 个代码的历史顺序作为
# 前缀不变（旧库模拟测试依赖该前缀子串做字面量替换），注册表新增代码一律
# 追加在尾部。CHECK 扩充后，旧库经 _ensure_effect_schema 的既有整表重建路径
# 自动升级（见下）。
_MEASURE_CHECK_SQL = (
    "measure TEXT NOT NULL CHECK(measure IN ("
    + ",".join(f"'{code}'" for code in MEASURE_CODES) + "))"
)

_ANALYSIS_SCHEMA = """
CREATE TABLE IF NOT EXISTS review_studies (
  id TEXT PRIMARY KEY, label TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS review_reports (
  zotero_key TEXT PRIMARY KEY REFERENCES articles(zotero_key),
  study_id TEXT NOT NULL REFERENCES review_studies(id));
CREATE INDEX IF NOT EXISTS idx_review_reports_study ON review_reports(study_id);
CREATE TABLE IF NOT EXISTS review_effects (
  result_id INTEGER PRIMARY KEY AUTOINCREMENT,
  study_id TEXT NOT NULL REFERENCES review_studies(id),
  comparison TEXT NOT NULL, outcome TEXT NOT NULL, timepoint TEXT NOT NULL,
""" + _MEASURE_CHECK_SQL + """,
  estimate REAL NOT NULL, se REAL NOT NULL CHECK(se > 0),
  source_key TEXT NOT NULL REFERENCES articles(zotero_key),
  input_json TEXT NOT NULL DEFAULT '{}', entry_method TEXT NOT NULL DEFAULT 'manual',
  selected INTEGER NOT NULL CHECK(selected IN (0,1)), source_locator TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS review_rob (
  study_id TEXT NOT NULL REFERENCES review_studies(id),
  comparison TEXT NOT NULL, outcome TEXT NOT NULL, timepoint TEXT NOT NULL, framework TEXT NOT NULL,
  reviewer TEXT NOT NULL, domains_json TEXT NOT NULL, overall TEXT NOT NULL,
  overall_rationale TEXT NOT NULL,
  source_key TEXT NOT NULL REFERENCES articles(zotero_key),
  source_locator TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL,
  PRIMARY KEY(study_id, comparison, outcome, timepoint, framework, reviewer));
CREATE TABLE IF NOT EXISTS review_dta_results (
  study_id TEXT NOT NULL REFERENCES review_studies(id),
  index_test TEXT NOT NULL, target_condition TEXT NOT NULL,
  threshold TEXT NOT NULL, reference_standard TEXT NOT NULL,
  tp INTEGER NOT NULL CHECK(tp>=0), fp INTEGER NOT NULL CHECK(fp>=0),
  fn INTEGER NOT NULL CHECK(fn>=0), tn INTEGER NOT NULL CHECK(tn>=0),
  source_key TEXT NOT NULL REFERENCES articles(zotero_key),
  source_locator TEXT NOT NULL DEFAULT '',
  PRIMARY KEY(study_id,index_test,target_condition,threshold,reference_standard));
CREATE TABLE IF NOT EXISTS review_qual_findings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  study_id TEXT NOT NULL REFERENCES review_studies(id),
  source_key TEXT NOT NULL REFERENCES articles(zotero_key),
  finding TEXT NOT NULL, illustration TEXT NOT NULL, locator TEXT NOT NULL,
  credibility TEXT NOT NULL CHECK(credibility IN ('unequivocal','credible','unsupported')),
  reviewer TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS review_qual_categories (
  id INTEGER PRIMARY KEY AUTOINCREMENT, label TEXT NOT NULL,
  reviewer TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS review_qual_category_findings (
  category_id INTEGER NOT NULL REFERENCES review_qual_categories(id) ON DELETE CASCADE,
  finding_id INTEGER NOT NULL REFERENCES review_qual_findings(id),
  PRIMARY KEY(category_id,finding_id));
CREATE TABLE IF NOT EXISTS review_qual_syntheses (
  id INTEGER PRIMARY KEY AUTOINCREMENT, finding TEXT NOT NULL,
  reviewer TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS review_qual_synthesis_categories (
  synthesis_id INTEGER NOT NULL REFERENCES review_qual_syntheses(id) ON DELETE CASCADE,
  category_id INTEGER NOT NULL REFERENCES review_qual_categories(id),
  PRIMARY KEY(synthesis_id,category_id));
CREATE TABLE IF NOT EXISTS review_dose_curves (
  study_id TEXT NOT NULL REFERENCES review_studies(id), outcome TEXT NOT NULL,
  timepoint TEXT NOT NULL, measure TEXT NOT NULL, reference_dose REAL NOT NULL,
  dose_unit TEXT NOT NULL, contrasts_json TEXT NOT NULL, covariance_json TEXT NOT NULL,
  source_key TEXT NOT NULL REFERENCES articles(zotero_key),
  PRIMARY KEY(study_id,outcome,timepoint,measure));
CREATE TABLE IF NOT EXISTS review_single_group_counts (
  result_id INTEGER PRIMARY KEY AUTOINCREMENT,
  study_id TEXT NOT NULL REFERENCES review_studies(id),
  comparison TEXT NOT NULL CHECK(length(trim(comparison))>0),
  outcome TEXT NOT NULL CHECK(length(trim(outcome))>0),
  timepoint TEXT NOT NULL CHECK(length(trim(timepoint))>0),
  kind TEXT NOT NULL CHECK(kind IN ('proportion','rate')),
  events INTEGER NOT NULL CHECK(typeof(events)='integer' AND events>=0),
  total INTEGER CHECK(total IS NULL OR (typeof(total)='integer' AND total>0)),
  person_time REAL CHECK(person_time IS NULL OR person_time>0),
  time_unit TEXT NOT NULL DEFAULT '',
  source_key TEXT NOT NULL REFERENCES articles(zotero_key),
  source_locator TEXT NOT NULL DEFAULT '',
  selected INTEGER NOT NULL CHECK(selected IN (0,1)),
  CHECK((kind='proportion' AND total IS NOT NULL AND person_time IS NULL AND time_unit='' AND events<=total) OR
        (kind='rate' AND total IS NULL AND person_time IS NOT NULL AND length(trim(time_unit))>0))
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_review_single_group_selected
  ON review_single_group_counts(study_id,comparison,outcome,timepoint,kind) WHERE selected=1;
"""

_SCHEMA += _ANALYSIS_SCHEMA

# GRADE 评估表（衍生产品规格 §0.5 / §3.3）：单独成常量以便 _connect 在旧库上
# 自愈（CREATE TABLE IF NOT EXISTS，幂等）；五降级因子取值
# 'none'|'serious'|'very_serious'|'very_very_serious'，升级因子（观察性研究）
# 由 grade_assessor 校验。追加式新增，不影响既有表。
_GRADE_SCHEMA = """
CREATE TABLE IF NOT EXISTS grade_assessments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT NOT NULL,
  comparison TEXT NOT NULL, outcome TEXT NOT NULL, timepoint TEXT NOT NULL,
  outcome_type TEXT NOT NULL CHECK(outcome_type IN ('critical','important','not_critical')),
  -- GRADE 五降级因子,每项 'none'|'serious'|'very_serious'|'very_very_serious'
  rob_downgrade TEXT NOT NULL CHECK(rob_downgrade IN ('none','serious','very_serious','very_very_serious')),
  inconsistency_downgrade TEXT NOT NULL,
  indirectness_downgrade TEXT NOT NULL,
  imprecision_downgrade TEXT NOT NULL,
  publication_bias_downgrade TEXT NOT NULL,
  -- 升级因子(观察性研究)
  large_effect_upgrade TEXT DEFAULT 'none',
  dose_response_upgrade TEXT DEFAULT 'none',
  plausible_confounding_upgrade TEXT DEFAULT 'none',
  -- 自动预填的信号来源(JSON)
  signal_sources TEXT NOT NULL DEFAULT '{}',
  -- 用户覆盖自动建议的备注
  override_notes TEXT NOT NULL DEFAULT '',
  assessor TEXT NOT NULL, created_at TEXT NOT NULL,
  UNIQUE(task_id, comparison, outcome, timepoint, assessor)
);
"""
_SCHEMA += _GRADE_SCHEMA

# articles 表列（与 Article.to_dict 键一一对应，raw 以 JSON 文本存储）
_ARTICLE_COLUMNS = (
    "zotero_key", "item_type", "title", "authors", "journal", "year",
    "doi", "abstract", "url", "source_format", "raw_json", "content_hash",
)

_INSERT_ARTICLE = (
    f"INSERT INTO articles ({', '.join(_ARTICLE_COLUMNS)}, is_duplicate_of, import_order) "
    f"VALUES ({', '.join(['?'] * (len(_ARTICLE_COLUMNS) + 2))})"
)

_DECISION_COLUMNS = (
    "zotero_key", "decision", "exclusion_reason", "notes", "tags",
    "screened_at", "updated_at",
)

# 决策 upsert（含 tags 列，SPEC §6bis）：冲突更新子句按调用语义拼装——
# save_decision 保留首次 screened_at；restore_decision 覆盖 screened_at（回导
# 无损）；tags 缺省（None）保留原值、显式传值（含空串）覆盖。所有片段均为
# 固定字面量，数据值一律走参数占位符。
# 表名参数化（SPEC §14）：同一 SQL 形状复用于初筛 decisions 与复筛
# stage2_decisions（字段完全同构）；table 仅接受本模块内的固定字面量，
# 数据值一律走参数占位符。
_DECISION_TABLES = ("decisions", "stage2_decisions")


def _upsert_decision_head(table: str) -> str:
    if table not in _DECISION_TABLES:
        raise ValueError(f"未知决策表: {table!r}，必须为 {_DECISION_TABLES} 之一")
    return (
        f"INSERT INTO {table} "
        "(zotero_key, decision, exclusion_reason, notes, tags, screened_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) "
        f"ON CONFLICT(zotero_key) DO UPDATE SET "
        "decision=excluded.decision, "
        "exclusion_reason=excluded.exclusion_reason, "
        "notes=excluded.notes, "
    )


def upsert_decision_sql(
    table: str, *, keep_tags: bool, keep_screened_at: bool
) -> str:
    """按 表名 / tags / screened_at 的保留语义拼装 upsert SQL（仅固定字面量）。

    供初筛（decisions）与复筛（stage2_decisions，SPEC §14）共用；两表字段
    完全同构，语义差异只体现在调用方的时间戳/标签传值上。
    """
    tags_clause = f"tags={table}.tags" if keep_tags else "tags=excluded.tags"
    screened_clause = (
        f"screened_at={table}.screened_at" if keep_screened_at
        else "screened_at=excluded.screened_at"
    )
    return (
        f"{_upsert_decision_head(table)}"
        f"{tags_clause}, {screened_clause}, updated_at=excluded.updated_at"
    )


def _upsert_decision_sql(*, keep_tags: bool, keep_screened_at: bool) -> str:
    """初筛 decisions 表的 upsert SQL（保持原签名，委托给表名参数化版本）。"""
    return upsert_decision_sql(
        "decisions", keep_tags=keep_tags, keep_screened_at=keep_screened_at
    )


@dataclass
class ImportSummary:
    """一次 upsert_articles 的导入摘要。"""

    n_imported: int  # 本次传入的记录总数
    n_duplicates: int  # 本次传入中被判定为重复的条数
    n_new: int  # 新插入条数
    n_existing: int  # 已存在（按 zotero_key 命中、内容刷新）条数


# ---------------------------------------------------------------------------
# 连接与建库
# ---------------------------------------------------------------------------

# 连接初始化与自愈迁移序列的进程内互斥锁。补列/建表本身跨进程由
# BEGIN IMMEDIATE 写事务串行化；进程内的多线程并发则由本锁整体串行：
# journal_mode=WAL 的首次切换需库级独占（并发切换会立即报 database is
# locked），首次打开同一新库时的多线程迁移也不必互相争抢 SQLite 写锁。
# 不可重入：持锁区间内只做普通 SQL（含各函数自身的 BEGIN IMMEDIATE），
# 不得再进入本锁。
_MIGRATE_LOCK = threading.Lock()

#: 进程内已迁移标记：{库文件绝对路径: 迁移完成时的 schema cookie}。
_MIGRATED_SCHEMAS: dict[Path, int] = {}


def _add_column_if_missing(
    conn: sqlite3.Connection, table: str, column: str, ddl_sql: str
) -> None:
    """跨进程安全的补列原语：BEGIN IMMEDIATE → 事务内复查 → ALTER → commit。

    缺列检查与 ALTER 必须同处一个写事务：PRAGMA table_info 不加锁，「先查
    后改」的两个并发连接会同时看到缺列、随后双双 ALTER，后到者抛
    duplicate column name。BEGIN IMMEDIATE 先取写锁再复查，事务内的检查
    结果不会被其他进程已提交的迁移作废。撞上其他进程（旧版本代码）刚提交
    的同名列时按幂等成功处理。table/column 仅接受调用方的固定字面量，不
    接受外部输入。表不存在时不动（建表归各自的 schema 自愈步骤）。
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        if rows and column not in {r[1] for r in rows}:
            conn.execute(ddl_sql)
        conn.commit()
    except sqlite3.OperationalError as exc:
        conn.rollback()
        if "duplicate column name" not in str(exc).lower():
            raise
    except Exception:
        conn.rollback()
        raise


def _ensure_tags_column(conn: sqlite3.Connection) -> None:
    """旧库自愈（SPEC §6bis）：decisions 表缺 tags 列时补齐（幂等、additive）。

    仅在表已存在且缺列时执行 ALTER（PRAGMA table_info 对不存在的表返回空，
    据此区分"新库尚未建表"与"旧库缺列"两种情形）；检查与 ALTER 同处一个
    BEGIN IMMEDIATE 写事务，并发迁移不产生重复加列。
    """
    _add_column_if_missing(
        conn, "decisions", "tags",
        "ALTER TABLE decisions ADD COLUMN tags TEXT DEFAULT ''",
    )


def _ensure_stage2_tables(conn: sqlite3.Connection) -> None:
    """旧库自愈（SPEC §14）：幂等补建复筛三表（fulltexts/highlights/stage2_decisions）。

    与 _RANKINGS_SCHEMA 同一策略：CREATE TABLE IF NOT EXISTS 对新库是重复
    执行（无害）、对未初始化过三表的旧库则自动补齐，旧库文件无需任何手工
    迁移即可使用全文复筛功能。
    """
    conn.executescript(_STAGE2_SCHEMA)


def _ensure_coding_tables(conn: sqlite3.Connection) -> None:
    """旧库自愈：幂等补建编码表并放宽维度类型约束。

    与 _ensure_stage2_tables 完全同策略：CREATE TABLE IF NOT EXISTS 对新库是
    重复执行（无害）、对未初始化过编码表的旧库自动补齐，旧库文件无需手工迁移
    即可使用编码方案工具。旧版 dimensions 表通过 CHECK 仅允许 choice/text；
    重建该表时关闭外键检查，保留行 ID 和内容，原有 coding_values 外键仍指向
    同名表，其他编码表不受影响。
    """
    conn.executescript(_CODING_SCHEMA)
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='coding_dimensions'"
    ).fetchone()
    if row and "'numeric'" not in (row[0] or "").lower():
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                "CREATE TABLE coding_dimensions_numeric_upgrade ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, "
                "dtype TEXT NOT NULL CHECK(dtype IN ('choice','text','numeric')), "
                "options_json TEXT NOT NULL DEFAULT '[]', multi_select INTEGER NOT NULL DEFAULT 0, "
                "unit TEXT DEFAULT '', description TEXT DEFAULT '', section TEXT DEFAULT '', "
                "position INTEGER NOT NULL, created_at TEXT NOT NULL)"
            )
            conn.execute(
                "INSERT INTO coding_dimensions_numeric_upgrade "
                "SELECT id,name,dtype,options_json,multi_select,unit,description,section,position,created_at "
                "FROM coding_dimensions"
            )
            conn.execute("DROP TABLE coding_dimensions")
            conn.execute(
                "ALTER TABLE coding_dimensions_numeric_upgrade RENAME TO coding_dimensions"
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.execute("PRAGMA foreign_keys=ON")


_EFFECT_COLUMNS_SQL = (
    "result_id INTEGER PRIMARY KEY AUTOINCREMENT,"
    "study_id TEXT NOT NULL REFERENCES review_studies(id),"
    "comparison TEXT NOT NULL, outcome TEXT NOT NULL, timepoint TEXT NOT NULL,"
    + _MEASURE_CHECK_SQL + " ,"
    "estimate REAL NOT NULL, se REAL NOT NULL CHECK(se > 0),"
    "source_key TEXT NOT NULL REFERENCES articles(zotero_key),"
    "input_json TEXT NOT NULL DEFAULT '{}', entry_method TEXT NOT NULL DEFAULT 'manual',"
    "selected INTEGER NOT NULL CHECK(selected IN (0,1)), source_locator TEXT NOT NULL DEFAULT ''"
)


def _ensure_effect_schema(conn: sqlite3.Connection) -> None:
    """Migrate any legacy effect layout once to the result-variant schema.

    判据（P9）：除列布局/主键外，CHECK 字面量必须包含 measure_registry 全部
    代码——旧库 CHECK 缺任一注册代码即触发既有的「整表重建平移数据」路径
    （ORDER BY rowid 平移，字段/selected 唯一索引/自增序号均保持），不引入
    新的迁移框架。
    """
    def layout() -> tuple[set[str], bool]:
        rows = conn.execute("PRAGMA table_info(review_effects)").fetchall()
        columns = {row[1] for row in rows}
        table = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='review_effects'"
        ).fetchone()
        schema = table[0] if table else ""
        result_id_is_pk = any(row[1] == "result_id" and row[5] == 1 for row in rows)
        required = {"result_id", "study_id", "comparison", "outcome", "timepoint", "measure",
                    "estimate", "se", "source_key", "input_json", "entry_method", "selected", "source_locator"}
        return columns, bool(rows) and (
            not required.issubset(columns) or not result_id_is_pk
            or not all(f"'{measure}'" in schema for measure in MEASURE_CODES)
        )

    columns, rebuild = layout()
    index_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name='uq_review_effects_selected'"
    ).fetchone()
    if not rebuild and index_exists:
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        columns, rebuild = layout()
        if rebuild:
            expressions = [
                "result_id" if "result_id" in columns else "rowid",
                "study_id", "comparison", "outcome", "timepoint", "measure", "estimate", "se", "source_key",
                "input_json" if "input_json" in columns else "'{}'",
                "entry_method" if "entry_method" in columns else "'manual'",
                "COALESCE(selected,1)" if "selected" in columns else "1",
                "COALESCE(source_locator,'')" if "source_locator" in columns else "''",
            ]
            conn.execute(f"CREATE TABLE review_effects_new ({_EFFECT_COLUMNS_SQL})")
            conn.execute(
                "INSERT INTO review_effects_new (result_id,study_id,comparison,outcome,timepoint,measure,estimate,se,"
                "source_key,input_json,entry_method,selected,source_locator) SELECT "
                + ",".join(expressions) + " FROM review_effects ORDER BY rowid"
            )
            conn.execute("DROP TABLE review_effects")
            conn.execute("ALTER TABLE review_effects_new RENAME TO review_effects")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_review_effects_selected "
            "ON review_effects(study_id,comparison,outcome,timepoint) WHERE selected=1"
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _connect(db_path: StrPath) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    with _MIGRATE_LOCK:  # 连接初始化与自愈迁移序列进程内串行（跨进程由写事务串行）
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        # 稳态快路径：本进程已完成该库的自愈迁移且 schema cookie 未变
        # （PRAGMA schema_version 只随 DDL 变化，数据写入不影响）时跳过全部
        # DDL——读操作不再抢 SQLite 写锁。任何外部 schema 改动（含测试模拟
        # 旧库的 DROP TABLE）都会抬高 cookie，自动回到完整自愈序列。
        path = Path(db_path).resolve()
        try:
            schema_version = int(conn.execute("PRAGMA schema_version").fetchone()[0])
        except (sqlite3.Error, TypeError, IndexError):
            schema_version = None
        if schema_version is not None and _MIGRATED_SCHEMAS.get(path) == schema_version:
            return conn
        _ensure_tags_column(conn)  # 旧库自愈（幂等）：补齐 decisions.tags 列
        conn.executescript(_TITLE_DUPLICATE_SCHEMA)
        _ensure_stage2_tables(conn)  # 旧库自愈（幂等）：补建复筛三表（SPEC §14）
        _ensure_coding_tables(conn)  # 旧库自愈（幂等）：补建编码四表（SPEC §15.1）
        conn.executescript(_ANALYSIS_SCHEMA)
        conn.executescript(_GRADE_SCHEMA)  # 旧库自愈（幂等）：补建 GRADE 评估表（衍生产品规格 §0.5）
        _ensure_effect_schema(conn)
        from coscreen.analysis_storage import ensure_analysis_storage
        ensure_analysis_storage(conn)
        from coscreen.rob_storage import ensure_rob_storage
        ensure_rob_storage(conn)
        _add_column_if_missing(
            conn, "review_dose_curves", "input_json",
            "ALTER TABLE review_dose_curves ADD COLUMN input_json TEXT NOT NULL DEFAULT '{}'",
        )
        # 旧库自愈（幂等）：highlights 补 rects_json 列（SPEC §16 页面级标注）。
        # 函数所有者在 coscreen.fulltext（合同位置）；此处延迟导入以打破
        # db -> fulltext 的循环依赖（fulltext 反向复用本模块的连接工厂）。
        from coscreen.fulltext import _ensure_rects_column  # noqa: PLC0415

        _ensure_rects_column(conn)
        if schema_version is not None:
            try:
                _MIGRATED_SCHEMAS[path] = int(
                    conn.execute("PRAGMA schema_version").fetchone()[0]
                )
            except (sqlite3.Error, TypeError, IndexError):
                pass
    return conn


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def init_db(db_path: StrPath) -> None:
    """建库建表（幂等，可重复调用）。"""
    with closing(_connect(db_path)) as conn, conn:
        conn.executescript(_SCHEMA)


# ---------------------------------------------------------------------------
# meta
# ---------------------------------------------------------------------------

def set_meta(db_path: StrPath, key: str, value: str) -> None:
    with closing(_connect(db_path)) as conn, conn:
        conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def get_meta(db_path: StrPath, key: str) -> str | None:
    with closing(_connect(db_path)) as conn:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


# ---------------------------------------------------------------------------
# articles
# ---------------------------------------------------------------------------

def _row_to_article(row: tuple) -> Article:
    return Article.from_dict(dict(zip(_ARTICLE_COLUMNS, row)))


def upsert_articles(
    db_path: StrPath,
    articles: list[Article],
    duplicate_pairs: list[DuplicatePair],
    title_review_groups: list[dict] | None = None,
) -> ImportSummary:
    """写入一批文献；重复条目也入库（is_duplicate_of 指向保留条目）。

    - 新条目按全局递增 import_order 追加；
    - 已存在条目（zotero_key 命中）刷新内容字段，import_order 保持不变；
    - 重复导入同一文件是幂等的：行数不变、顺序不变，n_existing 计数命中行。
    整个批次在一个事务内完成（原子提交）。
    """
    # dup_key -> kept_key；并解析链式指向（A->B->C 解析为 A->C），兜底外部传入的配对。
    # 自指配对（dup_key == kept_key，即同 zotero_key 的重复行）不产生标记：
    # 两行在主键 upsert 时自然合并为一条，幸存行是"保留条目"而非重复条目。
    raw_map = {p.dup_key: p.kept_key for p in duplicate_pairs}
    resolved: dict[str, str] = {}
    for dup, target in raw_map.items():
        seen = {dup}
        while target in raw_map and target not in seen:
            seen.add(target)
            target = raw_map[target]
        if dup != target:
            resolved[dup] = target

    n_new = 0
    n_existing = 0
    with closing(_connect(db_path)) as conn, conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(import_order), -1) FROM articles"
        ).fetchone()
        next_order = row[0] + 1
        for art in articles:
            dup_of = resolved.get(art.zotero_key)
            d = art.to_dict()
            values = [d[col] for col in _ARTICLE_COLUMNS]
            exists = conn.execute(
                "SELECT is_duplicate_of FROM articles WHERE zotero_key = ?", (art.zotero_key,)
            ).fetchone()
            if exists is None:
                conn.execute(
                    _INSERT_ARTICLE, (*values, dup_of, next_order)
                )
                next_order += 1
                n_new += 1
            else:
                # Refreshing imported content must not undo an earlier exact or
                # manually confirmed title-duplicate decision.
                kept_duplicate_of = dup_of if dup_of is not None else exists[0]
                conn.execute(
                    "UPDATE articles SET item_type=?, title=?, authors=?, journal=?, "
                    "year=?, doi=?, abstract=?, url=?, source_format=?, raw_json=?, "
                    "content_hash=?, is_duplicate_of=? WHERE zotero_key=?",
                    (*values[1:], kept_duplicate_of, art.zotero_key),
                )
                n_existing += 1

        now = _now()
        for group in title_review_groups or []:
            members = group["members"]
            member_keys = {m["zotero_key"] for m in members}
            pending_rows = conn.execute(
                "SELECT review_id,normalized_title,members_json FROM title_duplicate_reviews "
                "WHERE status IN ('pending','undecided') ORDER BY created_at"
            ).fetchall()
            prior_rows = []
            for prior in pending_rows:
                prior_members = json.loads(prior[2])
                prior_keys = {m["zotero_key"] for m in prior_members}
                if prior[1] == group["normalized_title"] or member_keys.intersection(prior_keys):
                    prior_rows.append((prior, prior_members))
            if prior_rows:
                first_prior = prior_rows[0][0]
                review_id = first_prior[0]
                merged: dict[str, dict] = {}
                for _, prior_members in prior_rows:
                    merged.update({m["zotero_key"]: m for m in prior_members})
                for member in members:
                    old = merged.get(member["zotero_key"])
                    if old:
                        old["imported_in_batch"] = bool(
                            old["imported_in_batch"] or member["imported_in_batch"]
                        )
                        if member.get("match_kind") == "fuzzy":
                            old["match_kind"] = "fuzzy"
                    else:
                        merged[member["zotero_key"]] = member
                members = list(merged.values())
                conn.execute(
                    "UPDATE title_duplicate_reviews SET normalized_title=?,members_json=?,updated_at=? "
                    "WHERE review_id=?",
                    (first_prior[1], json.dumps(members, ensure_ascii=False, allow_nan=False),
                     now, review_id),
                )
                for prior, _ in prior_rows[1:]:
                    conn.execute(
                        "DELETE FROM title_duplicate_reviews WHERE review_id=?", (prior[0],)
                    )
            else:
                review_id = uuid.uuid4().hex
                conn.execute(
                    "INSERT INTO title_duplicate_reviews "
                    "(review_id,normalized_title,status,members_json,created_at,updated_at) "
                    "VALUES (?,?, 'pending', ?,?,?)",
                    (review_id, group["normalized_title"],
                     json.dumps(members, ensure_ascii=False, allow_nan=False), now, now),
                )
            conn.executemany(
                "INSERT INTO title_duplicate_members "
                "(review_id,zotero_key,imported_in_batch) VALUES (?,?,?) "
                "ON CONFLICT(review_id,zotero_key) DO UPDATE SET "
                "imported_in_batch=MAX(imported_in_batch,excluded.imported_in_batch)",
                [(review_id, m["zotero_key"], int(bool(m["imported_in_batch"])))
                 for m in members],
            )

    return ImportSummary(
        n_imported=len(articles),
        n_duplicates=sum(1 for a in articles if a.zotero_key in resolved),
        n_new=n_new,
        n_existing=n_existing,
    )


def list_articles(db_path: StrPath, include_duplicates: bool = False) -> list[Article]:
    """按 import_order 列出文献；默认过滤掉重复条目。"""
    sql = f"SELECT {', '.join(_ARTICLE_COLUMNS)} FROM articles"
    if not include_duplicates:
        sql += (
            " WHERE is_duplicate_of IS NULL AND NOT EXISTS ("
            "SELECT 1 FROM title_duplicate_members m "
            "JOIN title_duplicate_reviews r ON r.review_id=m.review_id "
            "WHERE m.zotero_key=articles.zotero_key "
            "AND m.imported_in_batch=1 AND r.status IN ('pending','undecided'))"
        )
    sql += " ORDER BY import_order, zotero_key"
    with closing(_connect(db_path)) as conn:
        rows = conn.execute(sql).fetchall()
    return [_row_to_article(r) for r in rows]


def list_title_duplicate_reviews(
    db_path: StrPath, *, status: str | None = "pending",
    limit: int = 25, offset: int = 0,
) -> tuple[list[dict], int]:
    """Return paginated human-review groups and the total matching group count."""
    with closing(_connect(db_path)) as conn:
        if status is None:
            total = conn.execute("SELECT COUNT(*) FROM title_duplicate_reviews").fetchone()[0]
            rows = conn.execute(
            "SELECT review_id,normalized_title,status,members_json,keeper_key,decision_by,updated_at "
                "FROM title_duplicate_reviews ORDER BY created_at,review_id LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        else:
            if status in {"pending", "undecided"}:
                where = "status IN ('pending','undecided')"
                count_params = ()
                params = (limit, offset)
            else:
                where = "status=?"
                count_params = (status,)
                params = (status, limit, offset)
            total = conn.execute(
                f"SELECT COUNT(*) FROM title_duplicate_reviews WHERE {where}", count_params
            ).fetchone()[0]
            rows = conn.execute(
                "SELECT review_id,normalized_title,status,members_json,keeper_key,decision_by,updated_at "
                f"FROM title_duplicate_reviews WHERE {where} "
                "ORDER BY created_at,review_id LIMIT ? OFFSET ?",
                params,
            ).fetchall()
    return ([{"review_id": r[0], "normalized_title": r[1], "status": r[2],
              "members": json.loads(r[3]), "keeper_key": r[4],
              "decision_by": r[5], "updated_at": r[6]} for r in rows], total)


def title_duplicate_review_progress(db_path: StrPath) -> dict:
    with closing(_connect(db_path)) as conn:
        pending, total = conn.execute(
            "SELECT SUM(status IN ('pending','undecided')),COUNT(*) FROM title_duplicate_reviews"
        ).fetchone()
        pending_records = conn.execute(
            "SELECT COUNT(DISTINCT m.zotero_key) FROM title_duplicate_members m "
            "JOIN title_duplicate_reviews r ON r.review_id=m.review_id "
            "WHERE r.status IN ('pending','undecided')"
        ).fetchone()[0]
        confirmed = conn.execute(
            "SELECT COUNT(*) FROM title_duplicate_reviews WHERE status='duplicate'"
        ).fetchone()[0]
        rejected = conn.execute(
            "SELECT COUNT(*) FROM title_duplicate_reviews WHERE status='not_duplicate'"
        ).fetchone()[0]
        excluded_records = conn.execute(
            "SELECT COUNT(DISTINCT m.zotero_key) FROM title_duplicate_members m "
            "JOIN title_duplicate_reviews r ON r.review_id=m.review_id "
            "WHERE r.status='duplicate' "
            "AND m.zotero_key<>r.keeper_key"
        ).fetchone()[0]
    return {"pending_groups": int(pending or 0), "candidate_groups": int(total or 0),
            "pending_records": int(pending_records or 0),
            "confirmed_duplicate_records_excluded": int(excluded_records or 0),
            "confirmed_duplicate_groups": int(confirmed or 0),
            "not_duplicate_groups": int(rejected or 0)}


def decide_title_duplicate_review(
    db_path: StrPath, review_id: str, decision: str, keeper_key: str = "",
    decided_by: str = "",
) -> bool:
    """Resolve a candidate group; confirmed duplicates are excluded from screening."""
    if decision not in {"duplicate", "not_duplicate", "undecided"}:
        raise ValueError("decision must be duplicate, not_duplicate, or undecided")
    now = _now()
    with closing(_connect(db_path)) as conn, conn:
        row = conn.execute(
            "SELECT status,members_json FROM title_duplicate_reviews WHERE review_id=?",
            (review_id,),
        ).fetchone()
        if row is None:
            return False
        members = json.loads(row[1])
        keys = {m["zotero_key"] for m in members}
        if decision == "duplicate":
            if keeper_key not in keys:
                raise ValueError("keeper_key must identify a member of the candidate group")
            duplicate_keys = [m["zotero_key"] for m in members
                               if m["zotero_key"] != keeper_key]
            conn.executemany(
                "UPDATE articles SET is_duplicate_of=? WHERE zotero_key=?",
                [(keeper_key, key) for key in duplicate_keys],
            )
            status = "duplicate"
        elif decision == "not_duplicate":
            status = "not_duplicate"
        else:
            status = "undecided"
        conn.execute(
            "UPDATE title_duplicate_reviews SET status=?,keeper_key=?,decision_by=?,updated_at=? WHERE review_id=?",
            (status, keeper_key if decision == "duplicate" else "", decided_by, now, review_id),
        )
    return True


def has_pending_title_duplicate_review(db_path: StrPath, zotero_key: str) -> bool:
    with closing(_connect(db_path)) as conn:
        return conn.execute(
            "SELECT 1 FROM title_duplicate_members m "
            "JOIN title_duplicate_reviews r ON r.review_id=m.review_id "
            "WHERE m.zotero_key=? AND m.imported_in_batch=1 "
            "AND r.status IN ('pending','undecided') LIMIT 1",
            (zotero_key,),
        ).fetchone() is not None


def get_article(db_path: StrPath, zotero_key: str) -> Article | None:
    with closing(_connect(db_path)) as conn:
        row = conn.execute(
            f"SELECT {', '.join(_ARTICLE_COLUMNS)} FROM articles WHERE zotero_key = ?",
            (zotero_key,),
        ).fetchone()
    return _row_to_article(row) if row else None


# ---------------------------------------------------------------------------
# decisions
# ---------------------------------------------------------------------------

def save_decision(
    db_path: StrPath,
    zotero_key: str,
    decision: str,
    exclusion_reason: str = "",
    notes: str = "",
    tags: str | None = None,
    *,
    trace_context: TraceContext | None = None,
) -> None:
    """保存/更新一条筛选决策（upsert）。decision 必须是 config.DECISIONS 之一。

    screened_at 记录首次筛选时间，更新时保留；updated_at 每次写入均刷新。
    tags（SPEC §6bis，逗号分隔自由文本）：传 None（缺省）时更新**保留原值**、
    首次插入存空串；传字符串（含空串，用于显式清空）时整体覆盖。
    zotero_key 不存在时因外键约束抛 sqlite3.IntegrityError。
    """
    if decision not in config.DECISIONS:
        raise ValueError(
            f"非法 decision: {decision!r}，必须为 {config.DECISIONS} 之一"
        )
    now = _now()
    sql = _upsert_decision_sql(
        keep_tags=tags is None, keep_screened_at=True
    )
    with closing(_connect(db_path)) as conn, conn:
        request_result = None
        if trace_context is not None:
            from coscreen.research_trace import begin_capture_transaction, begin_write_request

            begin_capture_transaction(conn, trace_context, "screen")
            replay, request_result = begin_write_request(
                conn, trace_context, stage="screen", operation="decision_save",
                subject_id=zotero_key,
                payload={"decision": decision, "exclusion_reason": exclusion_reason,
                         "notes": notes, "tags": {"keep": True} if tags is None else tags},
            )
            if replay:
                return None
        old = conn.execute(
            "SELECT decision,exclusion_reason,notes,tags FROM decisions WHERE zotero_key=?",
            (zotero_key,),
        ).fetchone()
        before = ({"decision": old[0], "exclusion_reason": old[1] or "", "notes": old[2] or "",
                   "tags": old[3] or ""} if old else None)
        after = {
            "decision": decision, "exclusion_reason": exclusion_reason or "", "notes": notes or "",
            "tags": (tags if tags is not None else (old[3] if old else "")) or "",
        }
        conn.execute(
            sql,
            (zotero_key, decision, exclusion_reason, notes, tags or "", now, now),
        )
        if trace_context is not None:
            from coscreen.research_trace import capture_event

            capture_event(
                conn, trace_context, stage="screen",
                action="decision_created" if old is None else "decision_changed",
                subject_id=zotero_key, before=before, after=after,
            )
            from coscreen.research_trace import finish_write_request

            finish_write_request(conn, trace_context)


def restore_decision(
    db_path: StrPath,
    zotero_key: str,
    decision: str,
    exclusion_reason: str = "",
    notes: str = "",
    screened_at: str | None = None,
    tags: str | None = None,
    *,
    trace_context: TraceContext | None = None,
) -> None:
    """回导专用：按导出 CSV 中的原始时间戳无损还原一条决策。

    与 save_decision 的区别：screened_at 以传入值为准（保持首次筛选时间），
    缺省时才取当前时间；updated_at 始终取当前时间（回导也是一次显式写入）。
    tags 语义与 save_decision 一致：None（缺省）保留原值、首次插入存空串；
    传字符串（含空串）整体覆盖（回导路径始终显式传值：7 列新文件原样还原、
    6 列旧文件按 "" 处理，见 SPEC §6bis）。
    """
    if decision not in config.DECISIONS:
        raise ValueError(
            f"非法 decision: {decision!r}，必须为 {config.DECISIONS} 之一"
        )
    stamp = screened_at if screened_at else _now()
    sql = _upsert_decision_sql(
        keep_tags=tags is None, keep_screened_at=False
    )
    with closing(_connect(db_path)) as conn, conn:
        if trace_context is not None:
            from coscreen.research_trace import begin_capture_transaction, begin_write_request

            begin_capture_transaction(conn, trace_context, "screen")
            replay, _ = begin_write_request(
                conn, trace_context, stage="screen", operation="decision_restore",
                subject_id=zotero_key,
                payload={"decision": decision, "exclusion_reason": exclusion_reason,
                         "notes": notes, "tags": {"keep": True} if tags is None else tags,
                         "screened_at": stamp},
            )
            if replay:
                return None
        old = conn.execute(
            "SELECT decision,exclusion_reason,notes,tags FROM decisions WHERE zotero_key=?",
            (zotero_key,),
        ).fetchone()
        before = ({"decision": old[0], "exclusion_reason": old[1] or "", "notes": old[2] or "",
                   "tags": old[3] or ""} if old else None)
        after = {
            "decision": decision, "exclusion_reason": exclusion_reason or "", "notes": notes or "",
            "tags": (tags if tags is not None else (old[3] if old else "")) or "",
        }
        conn.execute(
            sql,
            (zotero_key, decision, exclusion_reason, notes, tags or "", stamp, _now()),
        )
        if trace_context is not None:
            from coscreen.research_trace import capture_event

            capture_event(
                conn, trace_context, stage="screen",
                action="decision_imported" if trace_context.source == "import" else (
                    "decision_created" if old is None else "decision_changed"
                ), subject_id=zotero_key, before=before, after=after,
            )
            from coscreen.research_trace import finish_write_request

            finish_write_request(conn, trace_context)


def restore_decisions(
    db_path: StrPath,
    rows: list[dict],
    *,
    trace_context: TraceContext | None = None,
) -> int:
    """CSV 回导的原子批量版：整批校验后单连接单事务 upsert。

    - decision 值与 zotero_key 在库性在写入前对整批校验，任一行非法即
      整批 ValueError——不留部分导入的非原子状态；
    - 单条连接 + 单事务（此前逐行各开一条连接，N 条决策 O(N) 次连接与提交）；
    - 行字段语义同 restore_decision（tags/screened_at 显式覆盖），空
      zotero_key 行跳过；返回写入条数。
    """
    prepared: list[tuple[str, str, str, str, str, str | None]] = []
    for row in rows:
        key = (row.get("zotero_key") or "").strip()
        decision = row.get("decision") or ""
        if not key:
            continue
        if decision not in config.DECISIONS:
            raise ValueError(
                f"非法 decision: {decision!r}，必须为 {config.DECISIONS} 之一"
            )
        prepared.append((
            key, decision,
            row.get("exclusion_reason") or "",
            row.get("notes") or "",
            row.get("tags") or "",
            row.get("screened_at") or None,
        ))
    if not prepared:
        return 0
    now = _now()
    sql = _upsert_decision_sql(keep_tags=False, keep_screened_at=False)
    with closing(_connect(db_path)) as conn, conn:
        if trace_context is not None:
            from coscreen.research_trace import begin_capture_transaction, begin_write_request

            begin_capture_transaction(conn, trace_context, "screen")
            replay, result = begin_write_request(
                conn, trace_context, stage="screen", operation="decision_restore_batch",
                subject_id="batch",
                payload=[{"zotero_key": key, "decision": decision,
                          "exclusion_reason": reason, "notes": notes, "tags": tags,
                          "screened_at": screened_at}
                         for key, decision, reason, notes, tags, screened_at in prepared],
            )
            if replay:
                return int(result)
        known = {r[0] for r in conn.execute("SELECT zotero_key FROM articles")}
        unknown = sorted({key for key, *_ in prepared if key not in known})
        if unknown:
            raise ValueError(
                f"以下 zotero_key 不在当前数据库中（请确认导入了同一批文献）: {unknown}"
            )
        if trace_context is None:
            conn.execute("BEGIN IMMEDIATE")
        recording = False
        if trace_context is not None:
            from coscreen.research_trace import is_recording

            recording = is_recording(conn, trace_context, "screen")
        if not recording:
            conn.executemany(
                sql,
                [(key, decision, reason, notes, tags,
                  screened_at if screened_at else now, now)
                 for key, decision, reason, notes, tags, screened_at in prepared],
            )
        else:
            from coscreen.research_trace import capture_event

            # Read each old row immediately before its upsert so duplicate keys in
            # one imported batch form the same before/after chain as the writes.
            for key, decision, reason, notes, tags, screened_at in prepared:
                old = conn.execute(
                    "SELECT decision,exclusion_reason,notes,tags FROM decisions WHERE zotero_key=?",
                    (key,),
                ).fetchone()
                before = ({"decision": old[0], "exclusion_reason": old[1] or "", "notes": old[2] or "",
                           "tags": old[3] or ""} if old else None)
                after = {"decision": decision, "exclusion_reason": reason, "notes": notes, "tags": tags}
                conn.execute(
                    sql,
                    (key, decision, reason, notes, tags,
                     screened_at if screened_at else now, now),
                )
                capture_event(
                    conn, trace_context, stage="screen",
                    action="decision_imported" if trace_context.source == "import" else (
                        "decision_created" if old is None else "decision_changed"
                    ), subject_id=key, before=before, after=after,
                )
        if trace_context is not None:
            from coscreen.research_trace import finish_write_request

            finish_write_request(conn, trace_context, len(prepared))
    return len(prepared)


def get_decision(db_path: StrPath, zotero_key: str) -> dict | None:
    with closing(_connect(db_path)) as conn:
        row = conn.execute(
            f"SELECT {', '.join(_DECISION_COLUMNS)} FROM decisions WHERE zotero_key = ?",
            (zotero_key,),
        ).fetchone()
    if row is None:
        return None
    return dict(zip(_DECISION_COLUMNS, row))


def list_decisions(db_path: StrPath) -> list[dict]:
    """列出全部决策（按文献导入顺序）；仅含导出所需的 6 个字段（含 tags）。"""
    cols = (
        "zotero_key", "decision", "exclusion_reason", "notes", "tags",
        "screened_at",
    )
    with closing(_connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT d.zotero_key, d.decision, d.exclusion_reason, d.notes, "
            "d.tags, d.screened_at "
            "FROM decisions d "
            "LEFT JOIN articles a ON a.zotero_key = d.zotero_key "
            "ORDER BY a.import_order, d.zotero_key"
        ).fetchall()
    return [dict(zip(cols, r)) for r in rows]


def list_decisions_chrono(db_path: StrPath) -> list[dict]:
    """列出全部决策，按 updated_at 升序（最近筛选的排在最后）。

    与 :func:`list_decisions` 的导入顺序不同，这里反映的是**筛选操作的时间
    先后**，供"连续排除"（stop rule，SPEC §12）统计使用；同一秒内的写入按
    zotero_key 稳定排序，保证确定性。返回行含 updated_at 字段。
    """
    cols = ("zotero_key", "decision", "exclusion_reason", "notes",
            "screened_at", "updated_at")
    with closing(_connect(db_path)) as conn:
        rows = conn.execute(
            f"SELECT {', '.join(cols)} FROM decisions "
            "ORDER BY updated_at, zotero_key"
        ).fetchall()
    return [dict(zip(cols, r)) for r in rows]


# ---------------------------------------------------------------------------
# 进度
# ---------------------------------------------------------------------------

def get_progress(db_path: StrPath) -> dict:
    """筛选进度：total 只统计非重复条目；重复条目上的决策不计入。"""
    counts = {"include": 0, "exclude": 0, "maybe": 0}
    with closing(_connect(db_path)) as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM articles a WHERE is_duplicate_of IS NULL "
            "AND NOT EXISTS (SELECT 1 FROM title_duplicate_members m "
            "JOIN title_duplicate_reviews r ON r.review_id=m.review_id "
            "WHERE m.zotero_key=a.zotero_key AND m.imported_in_batch=1 "
            "AND r.status IN ('pending','undecided'))"
        ).fetchone()[0]
        for decision, cnt in conn.execute(
            "SELECT dec.decision, COUNT(*) FROM decisions dec "
            "JOIN articles a ON a.zotero_key = dec.zotero_key "
            "WHERE a.is_duplicate_of IS NULL AND NOT EXISTS ("
            "SELECT 1 FROM title_duplicate_members m "
            "JOIN title_duplicate_reviews r ON r.review_id=m.review_id "
            "WHERE m.zotero_key=a.zotero_key AND m.imported_in_batch=1 "
            "AND r.status IN ('pending','undecided')) "
            "GROUP BY dec.decision"
        ):
            if decision in counts:
                counts[decision] = cnt
    screened = sum(counts.values())
    return {
        "total": total,
        "screened": screened,
        "remaining": total - screened,
        "include": counts["include"],
        "exclude": counts["exclude"],
        "maybe": counts["maybe"],
    }


# ---------------------------------------------------------------------------
# rankings（主动学习排序审计，SPEC §12）
# ---------------------------------------------------------------------------

_INSERT_RANKING = (
    "INSERT INTO rankings "
    "(created_at, strategy, seed, n_labeled, model_meta, zotero_key, position, score) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
)


def record_ranking(db_path: StrPath, result: RankingResult) -> int:
    """把一次重排结果写入 rankings 审计表；返回本批次 id。

    - 整个批次（每个键一行）共享同一 created_at（ISO8601）并落在同一事务内，
      批次 id = 本批插入的最大行 id（AUTOINCREMENT 单调递增，最新批次最大）；
    - 同一批次的各行以 created_at 归组（id 为逐行自增，不能作批次键）。
      为保证"最近批次"可被 :func:`latest_ranking` 唯一识别，本函数保证
      created_at **严格大于**既有最大值：同一秒内连续重排时，时间戳按秒
      顺延 +1（确定性、可复现）；
    - ``order`` 为空时无行可写，返回 0；
    - 兼容旧库：写入前先执行 ``CREATE TABLE IF NOT EXISTS``（幂等、additive），
      未经新版 init_db 建过 rankings 表的数据库文件也能直接记录。
    """
    created_at = _now()
    meta_json = json.dumps(result.model_meta, ensure_ascii=False, sort_keys=True)
    with closing(_connect(db_path)) as conn, conn:
        conn.executescript(_RANKINGS_SCHEMA)  # 旧库自愈（幂等）
        row = conn.execute("SELECT MAX(created_at) FROM rankings").fetchone()
        prev_created_at = row[0] if row else None
        if prev_created_at is not None and created_at <= prev_created_at:
            created_at = (
                datetime.fromisoformat(prev_created_at) + timedelta(seconds=1)
            ).isoformat(timespec="seconds")
        cur = conn.cursor()
        for position, key in enumerate(result.order):
            cur.execute(
                _INSERT_RANKING,
                (
                    created_at,
                    result.strategy,
                    int(result.seed),
                    int(result.n_labeled),
                    meta_json,
                    key,
                    position,
                    float(result.scores.get(key, 0.0)),
                ),
            )
        batch_id = cur.lastrowid if result.order else 0
    return int(batch_id)


def latest_ranking(db_path: StrPath) -> dict | None:
    """取回最近一次重排批次；从未重排过返回 ``None``。

    最近批次 = created_at 最大的行组（record_ranking 保证其严格递增且同批
    共享）。返回 ``{"id", "created_at", "strategy", "seed", "n_labeled",
    "model_meta"(已解析 dict), "order": [按 position 升序的 zotero_key]}``，
    其中 id 为该批次最大行 id。旧库没有 rankings 表时同样返回 ``None``
    （只读判定，不产生建表副作用）。
    """
    with closing(_connect(db_path)) as conn:
        try:
            row = conn.execute("SELECT MAX(id) FROM rankings").fetchone()
        except sqlite3.OperationalError:  # 旧库无该表
            return None
        if row is None or row[0] is None:
            return None
        batch_id = int(row[0])
        header = conn.execute(
            "SELECT created_at, strategy, seed, n_labeled, model_meta "
            "FROM rankings WHERE id = ?",
            (batch_id,),
        ).fetchone()
        keys = [
            r[0]
            for r in conn.execute(
                "SELECT zotero_key FROM rankings WHERE created_at = ? "
                "ORDER BY position",
                (header[0],),
            ).fetchall()
        ]
    created_at, strategy, seed, n_labeled, meta_json = header
    return {
        "id": batch_id,
        "created_at": created_at,
        "strategy": strategy,
        "seed": int(seed),
        "n_labeled": int(n_labeled),
        "model_meta": json.loads(meta_json),
        "order": keys,
    }
