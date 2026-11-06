"""Move a single-user database to multi-user form.

The shared file keeps the catalog and reference tables. The user tables move into
``users/<id>.db`` beside it, with their original ids. Nothing is removed from the shared file until
the copy has been verified, and the removal happens in one transaction together with the
``multiuser`` marker, so a crash leaves either the original or the finished state.
"""

import sqlite3
import time
from pathlib import Path

from . import db

BACKUP_SUFFIX = ".pre-multiuser"


def adopt_data(path, user_id):
    """Give ``user_id`` the collection, minis, photos, forces and company data in ``path``.

    Returns ``{"already": bool, "user_id": int, "counts": {table: rows}, "backup": str | None}``.
    Running it again after a crash is safe: a user file left by an earlier attempt is rebuilt from
    the shared tables, which are still intact until the final step.
    """
    path = Path(path)
    user_id = int(user_id)
    if db.is_multiuser(path):
        return {"already": True, "user_id": user_id, "counts": _existing_counts(path, user_id), "backup": None}

    db.connect(path).close()  # upgrades an old-schema file
    backup = _backup(path)

    target = db.user_db_path(path, user_id)
    _remove_user_file(target)
    try:
        counts = _copy_user_tables(path, user_id)
        _drop_user_tables(path, counts)
    except BaseException:
        _remove_user_file(target)
        raise
    return {"already": False, "user_id": user_id, "counts": counts, "backup": str(backup)}


def _backup(path):
    backup = path.with_name(path.name + BACKUP_SUFFIX)
    if backup.exists():
        backup = path.with_name(f"{path.name}{BACKUP_SUFFIX}-{time.strftime('%Y%m%d-%H%M%S')}")
        n = 1
        while backup.exists():
            n += 1
            backup = path.with_name(f"{path.name}{BACKUP_SUFFIX}-{time.strftime('%Y%m%d-%H%M%S')}-{n}")
    source = sqlite3.connect(path)
    try:
        copy = sqlite3.connect(backup)
        try:
            source.backup(copy)
        finally:
            copy.close()
    except BaseException:
        backup.unlink(missing_ok=True)
        raise
    finally:
        source.close()
    return backup


def _remove_user_file(target):
    for suffix in ("", "-wal", "-shm", "-journal"):
        Path(str(target) + suffix).unlink(missing_ok=True)


def _existing_counts(path, user_id):
    target = db.user_db_path(path, user_id)
    if not target.is_file():
        return {}
    conn = sqlite3.connect(f"{target.resolve().as_uri()}?mode=ro", uri=True)
    try:
        return {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in db.USER_TABLES}
    except sqlite3.Error:
        return {}
    finally:
        conn.close()


def _mismatches(source_counts, copied_counts):
    """The tables whose row counts differ between the original and the copy."""
    return [t for t in db.USER_TABLES if source_counts.get(t) != copied_counts.get(t)]


def _copy_user_tables(path, user_id):
    """Copy every user table into the user's file. Returns the verified row counts."""
    conn = db.connect_user(user_id, path)  # creates the user schema, attaches the shared file
    conn.isolation_level = None
    try:
        conn.execute("BEGIN")
        source = {}
        for table in db.USER_TABLES:
            columns = ", ".join(
                f'"{row["name"]}"' for row in conn.execute(f'PRAGMA main.table_info("{table}")')
            )
            conn.execute(
                f'INSERT INTO main."{table}" ({columns}) SELECT {columns} FROM shared."{table}"'
            )
            source[table] = conn.execute(f'SELECT COUNT(*) FROM shared."{table}"').fetchone()[0]
        _copy_sequences(conn)
        copied = {t: conn.execute(f'SELECT COUNT(*) FROM main."{t}"').fetchone()[0] for t in db.USER_TABLES}
        bad = _mismatches(source, copied)
        if bad:
            raise ValueError("row counts differ after copying: " + ", ".join(bad))
        broken = conn.execute("PRAGMA main.foreign_key_check").fetchall()
        if broken:
            raise ValueError(f"{len(broken)} copied rows break a foreign key")
        conn.execute("COMMIT")
        return copied
    finally:
        conn.close()


def _copy_sequences(conn):
    """Keep AUTOINCREMENT counters so ids of deleted rows are not handed out again."""
    for table in db.USER_TABLES:
        sql = conn.execute(
            "SELECT sql FROM main.sqlite_master WHERE type = 'table' AND name = ?", (table,)
        ).fetchone()[0]
        if "AUTOINCREMENT" not in sql.upper():
            continue
        old = conn.execute("SELECT seq FROM shared.sqlite_sequence WHERE name = ?", (table,)).fetchone()
        top = conn.execute(f'SELECT MAX(id) FROM main."{table}"').fetchone()[0]
        seq = max(old[0] if old else 0, top or 0)
        if seq:
            conn.execute("DELETE FROM main.sqlite_sequence WHERE name = ?", (table,))
            conn.execute("INSERT INTO main.sqlite_sequence (name, seq) VALUES (?, ?)", (table, seq))


def _drop_user_tables(path, expected_counts):
    """Remove the user tables from the shared file and mark it multi-user, in one transaction."""
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("BEGIN IMMEDIATE")
        try:
            now = {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in db.USER_TABLES}
            if now != expected_counts:
                raise ValueError("the database changed while it was being copied; run adopt again")
            for table in reversed(db.USER_TABLES):
                conn.execute(f'DROP TABLE "{table}"')
            db.set_meta(conn, "multiuser", 1)
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
