"""Copy a task owner's article catalogue into an authorized member's database."""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path

from coscreen import db as db_mod
from coscreen import tasks
from coscreen import review_workflow as workflow_store
from coscreen import review_imports as import_store
from coscreen.security import safe_path
from coscreen.task_collaboration import DB_FILENAME as COLLABORATION_DB_FILENAME

_USERNAME_RE = re.compile(r"^[0-9A-Za-z._@\-\u4e00-\u9fff]{3,64}$")
_ARTICLE_COLUMNS = (*db_mod._ARTICLE_COLUMNS, "is_duplicate_of", "import_order")
_ARTICLE_SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
  zotero_key TEXT PRIMARY KEY, item_type TEXT, title TEXT, authors TEXT,
  journal TEXT, year INTEGER, doi TEXT, abstract TEXT, url TEXT,
  source_format TEXT, raw_json TEXT, content_hash TEXT,
  is_duplicate_of TEXT, import_order INTEGER
)
"""


class ReservedScreenerCollisionError(ValueError):
    """用户名 slug 与任务目录保留库名（collaboration.db）相撞（r6 #29）。

    升级前已存在的撞名账号/授权会走到这里：调用方应收敛为 409 撞名改名指引
    （COLLABORATION_RESERVED_NAME_MESSAGE），不得把协作权限库当成员个人库
    初始化或同步目录（修复前为裸 ValueError -> work-queue/articles 500）。
    继承 ValueError：既有按 ValueError 捕获的调用路径语义不变。"""


def _user_db_path(task_dir: str | Path, username: str) -> Path:
    if not isinstance(username, str) or not _USERNAME_RE.fullmatch(username):
        raise ValueError("username is not a valid server account name")
    root = Path(task_dir).resolve()
    if not root.is_dir():
        raise ValueError("task directory does not exist")
    slug = tasks.slugify_screener(username)
    filename = f"{slug}.db"
    if filename.casefold() == COLLABORATION_DB_FILENAME.casefold():
        raise ReservedScreenerCollisionError(
            "username maps to the reserved collaboration database")
    candidate = root / filename
    if candidate.is_symlink():
        raise ValueError("user database must not be a symbolic link")
    return safe_path(root, filename)


def _read_owner_articles(path: Path) -> list[tuple]:
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=15)) as conn:
        table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='articles'"
        ).fetchone()
        if table is None:
            raise ValueError("owner database has no articles table")
        columns = {row[1] for row in conn.execute("PRAGMA table_info(articles)")}
        missing = set(_ARTICLE_COLUMNS) - columns
        if missing:
            raise ValueError("owner articles table is missing required columns")
        # 列清单是本模块固定枚举 _ARTICLE_COLUMNS（上方已校验齐全），语句整体写成字面量。
        return conn.execute(
            "SELECT zotero_key, item_type, title, authors, journal, year, doi, abstract, url, source_format, "
            "raw_json, content_hash, is_duplicate_of, import_order FROM articles ORDER BY import_order, "
            "zotero_key"
        ).fetchall()


def sync_shared_articles(
    task_dir: str | Path, *, owner_username: str, member_username: str,
    catalog_revision: int | None = None,
) -> int:
    """Upsert only the owner's article rows into a member DB; return rows synced.

    The caller must pass server-verified task owner/member identities after its
    collaboration authorization check. Member-only articles and every decision,
    full-text, and coding table remain untouched.
    """
    owner_db = _user_db_path(task_dir, owner_username)
    member_db = _user_db_path(task_dir, member_username)
    if owner_db == member_db:
        raise ValueError("owner and member resolve to the same database")
    if not owner_db.is_file():
        raise FileNotFoundError(owner_db)
    if member_db.exists() and owner_db.samefile(member_db):
        raise ValueError("owner and member resolve to the same database")

    # The caller has verified owner/member identities. Upgrade an existing
    # reviewer through the numbered backup path before any catalogue write;
    # workflow schema belongs only to that migration, including sync markers.
    if not member_db.is_file():
        db_mod.init_db(member_db)
    import_store.migrate(member_db)

    owner_identity = owner_db.name
    if catalog_revision is not None and member_db.is_file():
        with closing(sqlite3.connect(member_db.as_uri() + "?mode=ro", uri=True, timeout=15)) as conn:
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='workflow_catalog_sync'").fetchone():
                synced = conn.execute("SELECT revision FROM workflow_catalog_sync WHERE owner_identity=?", (owner_identity,)).fetchone()
                if synced and int(synced[0]) == catalog_revision:
                    return 0
    rows = _read_owner_articles(owner_db)
    accepted = []
    with closing(workflow_store.connect(owner_db, read_only=True)) as owner_conn:
        if workflow_store.has_table(owner_conn, "workflow_metadata_acceptances"):
            accepted = [dict(row) for row in owner_conn.execute("SELECT * FROM workflow_metadata_acceptances")]

    # 目录 upsert 的列清单同样来自固定枚举 _ARTICLE_COLUMNS；语句直接写成字面量，
    # 行值仍经 executemany 参数绑定（避免在 SQL 文本里拼表名/列名）。

    with closing(sqlite3.connect(member_db, timeout=15)) as conn:
        conn.execute("PRAGMA busy_timeout=15000")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(_ARTICLE_SCHEMA)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_articles_doi ON articles(doi)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_articles_hash ON articles(content_hash)")
            columns = {row[1] for row in conn.execute("PRAGMA table_info(articles)")}
            if set(_ARTICLE_COLUMNS) - columns:
                raise ValueError("member articles table is missing required columns")
            conn.executemany(
                "INSERT INTO articles (zotero_key, item_type, title, authors, journal, year, doi, abstract, url, "
                "source_format, raw_json, content_hash, is_duplicate_of, import_order) VALUES (?, ?, ?, ?, ?, ?, "
                "?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(zotero_key) DO UPDATE SET item_type=CASE WHEN "
                "articles.item_type IS NULL OR articles.item_type='' THEN excluded.item_type ELSE "
                "articles.item_type END, title=CASE WHEN articles.title IS NULL OR articles.title='' THEN "
                "excluded.title ELSE articles.title END, authors=CASE WHEN articles.authors IS NULL OR "
                "articles.authors='' THEN excluded.authors ELSE articles.authors END, journal=CASE WHEN "
                "articles.journal IS NULL OR articles.journal='' THEN excluded.journal ELSE articles.journal END, "
                "year=CASE WHEN articles.year IS NULL OR articles.year='' THEN excluded.year ELSE articles.year "
                "END, doi=CASE WHEN articles.doi IS NULL OR articles.doi='' THEN excluded.doi ELSE articles.doi "
                "END, abstract=CASE WHEN articles.abstract IS NULL OR articles.abstract='' THEN excluded.abstract "
                "ELSE articles.abstract END, url=CASE WHEN articles.url IS NULL OR articles.url='' THEN "
                "excluded.url ELSE articles.url END, source_format=CASE WHEN articles.source_format IS NULL OR "
                "articles.source_format='' THEN excluded.source_format ELSE articles.source_format END, "
                "raw_json=CASE WHEN articles.raw_json IS NULL OR articles.raw_json='' THEN excluded.raw_json ELSE "
                "articles.raw_json END, content_hash=CASE WHEN articles.content_hash IS NULL OR "
                "articles.content_hash='' THEN excluded.content_hash ELSE articles.content_hash END, "
                "is_duplicate_of=excluded.is_duplicate_of, import_order=excluded.import_order",
                rows)
            for approval in accepted:
                applied = conn.execute("SELECT revision FROM workflow_metadata_applied WHERE owner_identity=? AND zotero_key=?",
                                       (owner_identity, approval["zotero_key"])).fetchone()
                if applied and applied[0] >= approval["revision"]:
                    continue
                values = json.loads(approval["metadata_json"])
                # 列清单来自 import_store.METADATA_FIELDS（内部固定常量），语句写成字面量。
                conn.execute(
                    "UPDATE articles SET item_type=?,title=?,authors=?,journal=?,year=?,doi=?,abstract=?,url=? WHERE "
                    "zotero_key=?",
                    [values.get(field) for field in import_store.METADATA_FIELDS] + [approval["zotero_key"]])
                conn.execute("INSERT INTO workflow_metadata_applied VALUES(?,?,?) ON CONFLICT(owner_identity,zotero_key) DO UPDATE SET revision=excluded.revision",
                             (owner_identity, approval["zotero_key"], approval["revision"]))
            if catalog_revision is not None:
                conn.execute("INSERT INTO workflow_catalog_sync VALUES(?,?) ON CONFLICT(owner_identity) DO UPDATE SET revision=excluded.revision",
                             (owner_identity, catalog_revision))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return len(rows)
