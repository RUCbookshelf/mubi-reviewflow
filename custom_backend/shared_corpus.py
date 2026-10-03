"""Copy a task owner's article catalogue into an authorized member's database."""

from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from pathlib import Path

from coscreen import db as db_mod
from coscreen import tasks
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
    task_dir: str | Path, *, owner_username: str, member_username: str
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

    rows = _read_owner_articles(owner_db)
    names = ", ".join(_ARTICLE_COLUMNS)
    placeholders = ", ".join("?" for _ in _ARTICLE_COLUMNS)
    updates = ", ".join(
        f"{column}=excluded.{column}"
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
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return len(rows)
