from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from contextlib import closing

from coscreen.team_flow_types import normalize_item_keys


class OwnerKeyedDomainUnavailableError(RuntimeError):
    """Owner article domain could not be read or proven complete."""

    reason = "owner_db_unreadable"


def _require_borrowed_owner_read_connection(
    conn: sqlite3.Connection, *, api: str
) -> None:
    """Require a native borrowed connection in the caller's active Row transaction."""
    if not issubclass(type(conn), sqlite3.Connection):
        raise TypeError(
            f"{api} requires a native sqlite3.Connection or native subclass"
        )
    if not conn.in_transaction:
        raise RuntimeError(f"{api} requires an active caller transaction")
    if conn.row_factory is not sqlite3.Row:
        raise RuntimeError(f"{api} requires conn.row_factory is sqlite3.Row")


def canonical_item_rows_on(
    conn: sqlite3.Connection, *, item_keys: Iterable[str]
) -> dict[str, dict]:
    """Read only requested canonical article rows from a borrowed owner connection."""
    keys = normalize_item_keys(item_keys)
    _require_borrowed_owner_read_connection(conn, api="canonical_item_rows_on")

    required_schema = (
        (
            "articles",
            {"zotero_key", "is_duplicate_of", "import_order"},
            ("zotero_key",),
        ),
        (
            "title_duplicate_members",
            {"review_id", "zotero_key", "imported_in_batch"},
            ("review_id", "zotero_key"),
        ),
        (
            "title_duplicate_reviews",
            {"review_id", "status"},
            ("review_id",),
        ),
    )
    table_names = tuple(table for table, _, _ in required_schema)
    table_placeholders = ", ".join("?" for _ in table_names)
    result: dict[str, dict] = {}

    try:
        with closing(
            conn.execute(
                "SELECT name, type FROM main.sqlite_master "
                f"WHERE type = 'table' AND name IN ({table_placeholders})",
                table_names,
            )
        ) as cursor:
            present_tables = {row["name"] for row in cursor.fetchall()}

        missing_tables = set(table_names) - present_tables
        if missing_tables:
            raise OwnerKeyedDomainUnavailableError(
                "owner_db_unreadable: required main tables are missing: "
                + ", ".join(sorted(missing_tables))
            )

        for table, required, expected_primary_key in required_schema:
            with closing(
                conn.execute(f"PRAGMA main.table_info('{table}')")
            ) as cursor:
                table_info = cursor.fetchall()
            present_columns = {row["name"] for row in table_info}
            missing_columns = required - present_columns
            if missing_columns:
                raise OwnerKeyedDomainUnavailableError(
                    f"owner_db_unreadable: main.{table} is missing required "
                    f"columns: {', '.join(sorted(missing_columns))}"
                )
            primary_key_by_position = {
                row["pk"]: row["name"] for row in table_info if row["pk"]
            }
            actual_primary_key = tuple(
                primary_key_by_position[position]
                for position in sorted(primary_key_by_position)
            )
            if actual_primary_key != expected_primary_key:
                raise OwnerKeyedDomainUnavailableError(
                    f"owner_db_unreadable: main.{table} has an unexpected "
                    "primary key layout"
                )

        for start in range(0, len(keys), 200):
            batch = keys[start : start + 200]
            placeholders = ", ".join("?" for _ in batch)
            sql = (
                "SELECT a.* FROM main.articles AS a "
                f"WHERE a.zotero_key IN ({placeholders}) "
                "AND a.is_duplicate_of IS NULL "
                "AND NOT EXISTS ("
                "SELECT 1 FROM main.title_duplicate_members AS m "
                "JOIN main.title_duplicate_reviews AS r "
                "ON r.review_id = m.review_id "
                "WHERE m.zotero_key = a.zotero_key "
                "AND m.imported_in_batch = ? "
                "AND r.status IN (?, ?))"
            )
            with closing(
                conn.execute(sql, (*batch, 1, "pending", "undecided"))
            ) as cursor:
                rows = cursor.fetchall()

            for row in rows:
                key = row["zotero_key"]
                try:
                    normalized_row_key = normalize_item_keys((key,))[0]
                except (TypeError, ValueError) as exc:
                    raise OwnerKeyedDomainUnavailableError(
                        "owner_db_unreadable: main.articles contains an invalid key"
                    ) from exc
                result[normalized_row_key] = dict(row)
    except sqlite3.ProgrammingError:
        raise
    except sqlite3.DatabaseError as exc:
        raise OwnerKeyedDomainUnavailableError(
            "owner_db_unreadable: owner article domain read failed"
        ) from exc

    return result


def iter_canonical_item_keys_on(
    conn: sqlite3.Connection,
    *,
    after: tuple[int | None, str] | None = None,
    limit: int = 200,
) -> tuple[tuple[str, ...], tuple[int | None, str] | None]:
    """Read one NULL-first canonical-key page using the caller's owner transaction."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
        raise ValueError("limit must be a non-bool integer from 1 through 200")

    if after is not None:
        if not isinstance(after, tuple) or len(after) != 2:
            raise ValueError("after must be None or a two-item tuple")
        order_value, after_key = after
        if order_value is not None and (
            isinstance(order_value, bool)
            or not isinstance(order_value, int)
            or order_value < -9223372036854775808
            or order_value > 9223372036854775807
        ):
            raise ValueError(
                "after order must be None or a signed 64-bit non-bool integer"
            )
        after_key = normalize_item_keys((after_key,))[0]

    _require_borrowed_owner_read_connection(
        conn, api="iter_canonical_item_keys_on"
    )

    required_schema = (
        (
            "articles",
            {"zotero_key", "is_duplicate_of", "import_order"},
            ("zotero_key",),
        ),
        (
            "title_duplicate_members",
            {"review_id", "zotero_key", "imported_in_batch"},
            ("review_id", "zotero_key"),
        ),
        (
            "title_duplicate_reviews",
            {"review_id", "status"},
            ("review_id",),
        ),
    )
    table_names = tuple(table for table, _, _ in required_schema)
    table_placeholders = ", ".join("?" for _ in table_names)

    try:
        with closing(
            conn.execute(
                "SELECT name, type FROM main.sqlite_master "
                f"WHERE type = 'table' AND name IN ({table_placeholders})",
                table_names,
            )
        ) as cursor:
            present_tables = {row["name"] for row in cursor.fetchall()}

        missing_tables = set(table_names) - present_tables
        if missing_tables:
            raise OwnerKeyedDomainUnavailableError(
                "owner_db_unreadable: required main tables are missing: "
                + ", ".join(sorted(missing_tables))
            )

        for table, required, expected_primary_key in required_schema:
            with closing(
                conn.execute(f"PRAGMA main.table_info('{table}')")
            ) as cursor:
                table_info = cursor.fetchall()
            present_columns = {row["name"] for row in table_info}
            missing_columns = required - present_columns
            if missing_columns:
                raise OwnerKeyedDomainUnavailableError(
                    f"owner_db_unreadable: main.{table} is missing required "
                    f"columns: {', '.join(sorted(missing_columns))}"
                )
            primary_key_by_position = {
                row["pk"]: row["name"] for row in table_info if row["pk"]
            }
            actual_primary_key = tuple(
                primary_key_by_position[position]
                for position in sorted(primary_key_by_position)
            )
            if actual_primary_key != expected_primary_key:
                raise OwnerKeyedDomainUnavailableError(
                    f"owner_db_unreadable: main.{table} has an unexpected "
                    "primary key layout"
                )

        parameters: tuple[object, ...] = (1, "pending", "undecided")
        continuation = ""
        if after is not None:
            if order_value is None:
                continuation = (
                    "AND (a.import_order IS NOT NULL OR "
                    "(a.import_order IS NULL AND a.zotero_key > ?)) "
                )
                parameters = (*parameters, after_key)
            else:
                continuation = (
                    "AND (a.import_order > ? OR "
                    "(a.import_order = ? AND a.zotero_key > ?)) "
                )
                parameters = (*parameters, order_value, order_value, after_key)

        sql = (
            "SELECT a.zotero_key, a.import_order FROM main.articles AS a "
            "WHERE a.is_duplicate_of IS NULL "
            "AND NOT EXISTS ("
            "SELECT 1 FROM main.title_duplicate_members AS m "
            "JOIN main.title_duplicate_reviews AS r "
            "ON r.review_id = m.review_id "
            "WHERE m.zotero_key = a.zotero_key "
            "AND m.imported_in_batch = ? "
            "AND r.status IN (?, ?)) "
            f"{continuation}"
            "ORDER BY a.import_order ASC, a.zotero_key ASC "
            "LIMIT ?"
        )
        with closing(
            conn.execute(sql, (*parameters, limit + 1))
        ) as cursor:
            rows = cursor.fetchall()

        for row in rows:
            order_value = row["import_order"]
            if order_value is not None and type(order_value) is not int:
                raise OwnerKeyedDomainUnavailableError(
                    "owner_db_unreadable: main.articles.import_order must be "
                    "NULL or an SQLite integer"
                )
            key = row["zotero_key"]
            try:
                normalize_item_keys((key,))
            except (TypeError, ValueError) as exc:
                raise OwnerKeyedDomainUnavailableError(
                    "owner_db_unreadable: main.articles contains an invalid key"
                ) from exc

        has_more = len(rows) > limit
        page_rows = rows[:limit]
        page_keys = tuple(row["zotero_key"] for row in page_rows)
        if not has_more:
            return page_keys, None

        last_row = page_rows[-1]
        next_after = (last_row["import_order"], last_row["zotero_key"])
        return page_keys, next_after
    except sqlite3.ProgrammingError:
        raise
    except sqlite3.DatabaseError as exc:
        raise OwnerKeyedDomainUnavailableError(
            "owner_db_unreadable: owner article domain page read failed"
        ) from exc
