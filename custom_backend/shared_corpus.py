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


def _user_db_path(task_dir: str | Path, username: str) -> Path:
    if not isinstance(username, str) or not _USERNAME_RE.fullmatch(username):
        raise ValueError("username is not a valid server account name")
    root = Path(task_dir).resolve()
    if not root.is_dir():
        raise ValueError("task directory does not exist")
    slug = tasks.slugify_screener(username)
    filename = f"{slug}.db"
    if filename.casefold() == COLLABORATION_DB_FILENAME.casefold():
        raise ValueError("username maps to the reserved collaboration database")
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
        names = ", ".join(_ARTICLE_COLUMNS)
        return conn.execute(
            f"SELECT {names} FROM articles ORDER BY import_order, zotero_key"
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

    names = ", ".join(_ARTICLE_COLUMNS)
    placeholders = ", ".join("?" for _ in _ARTICLE_COLUMNS)
    updates = ", ".join(
        (f"{column}=excluded.{column}" if column in {"is_duplicate_of", "import_order"} else
         f"{column}=CASE WHEN articles.{column} IS NULL OR articles.{column}='' "
         f"THEN excluded.{column} ELSE articles.{column} END")
        for column in _ARTICLE_COLUMNS
        if column != "zotero_key"
    )
    upsert_sql = (
        f"INSERT INTO articles ({names}) VALUES ({placeholders}) "
        f"ON CONFLICT(zotero_key) DO UPDATE SET {updates}"
    )

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
            conn.executemany(upsert_sql, rows)
            for approval in accepted:
                applied = conn.execute("SELECT revision FROM workflow_metadata_applied WHERE owner_identity=? AND zotero_key=?",
                                       (owner_identity, approval["zotero_key"])).fetchone()
                if applied and applied[0] >= approval["revision"]:
                    continue
                values = json.loads(approval["metadata_json"])
                conn.execute("UPDATE articles SET " + ",".join(f"{field}=?" for field in import_store.METADATA_FIELDS) + " WHERE zotero_key=?",
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
