"""Move a single-user database to multi-user form.

The shared file keeps the catalog and reference tables. The user tables move into
``users/<id>.db`` beside it, with their original ids. Nothing is removed from the shared file until
the copy has been verified, and the removal happens in one transaction together with the
``multiuser`` marker, so a crash leaves either the original or the finished state.
"""

import sqlite3
import struct
import time
from pathlib import Path

from . import config, db

BACKUP_SUFFIX = ".pre-multiuser"
LOCK_WAIT = 5.0  # seconds to wait on a locked shared file when checking its state

# adopt stamps the user file it creates with this value in the SQLite header (PRAGMA user_version),
# written before anything else goes in. Only a file carrying it, or an empty one, may be deleted
# by adopt, so a file that a live account has been using is never removed. Nothing else reads it.
ADOPT_MARKER = 0x41444F50  # "ADOP"


class UserFileInTheWay(ValueError):
    """A user file that adopt did not create is already at the target path. Nothing was changed."""


def adopt_data(path, user_id):
    """Give ``user_id`` the collection, minis, photos, forces and company data in ``path``.

    Returns ``{"already": bool, "user_id": int, "counts": {table: rows}, "backup": str | None}``.
    Running it again after a crash is safe: a user file left by an earlier attempt is rebuilt from
    the shared tables, which are still intact until the final step.
    """
    path = Path(path)
    user_id = int(user_id)
    if db.is_multiuser(path):
        return _already(path, user_id)

    target = db.user_db_path(path, user_id)
    if target.exists() and not _is_adopt_file(target):
        raise UserFileInTheWay(
            f"{target} already exists and was not created by adopt; it may hold a live account's data. "
            "Nothing was changed."
        )

    if config.DB_IS_DEFAULT and path == Path(config.DB_PATH):
        db.connect().close()  # also copies the pre-rename database when only that exists
    else:
        db.connect(path).close()  # upgrades an old-schema file

    # Hold the shared file's write lock from before the copy until the tables are dropped, so no
    # edit can land between what was copied and what is removed. Readers are not blocked.
    lock = sqlite3.connect(path, isolation_level=None, timeout=30)
    lock.row_factory = sqlite3.Row
    try:
        lock.execute("PRAGMA foreign_keys = OFF")
        lock.execute("BEGIN IMMEDIATE")
        try:
            if db.get_meta(lock, "multiuser") == "1":  # another run finished while this one waited
                lock.execute("ROLLBACK")
                return _already(path, user_id)
            backup = _backup(path)
            _remove_user_file(target)
            counts = _copy_user_tables(path, user_id)
            _drop_user_tables(lock, counts)
        except BaseException:
            # Roll back first (a no-op after a commit), then judge by the committed state: once the
            # drop has committed, the user file is the only copy of the data and must stay.
            if lock.in_transaction:
                lock.execute("ROLLBACK")
            if _unadopted(path):
                _remove_user_file(target)
            raise
    finally:
        lock.close()
    return {"already": False, "user_id": user_id, "counts": counts, "backup": str(backup)}


def _already(path, user_id):
    counts = _existing_counts(path, user_id)
    result = {"already": True, "user_id": user_id, "counts": counts, "backup": None}
    if not db.user_db_path(path, user_id).is_file():
        result["user_file_missing"] = True
    return result


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
    """Delete a user file, but only one that adopt created."""
    if Path(target).exists() and not _is_adopt_file(target):
        return
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
    target = db.user_db_path(path, user_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(f"PRAGMA user_version = {ADOPT_MARKER}")  # first write: the file is ours from the start
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(db.USER_SCHEMA)
        conn.execute("ATTACH DATABASE ? AS shared", (str(Path(path).resolve()),))
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


def _drop_user_tables(lock, expected_counts):
    """Remove the user tables and mark the file multi-user, then commit the lock's transaction.

    ``lock`` is the connection that has held ``BEGIN IMMEDIATE`` on the shared file since before the
    copy, so the drops and the marker are one transaction. The row-count check is a backstop.
    """
    now = {t: lock.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in db.USER_TABLES}
    if now != expected_counts:
        raise ValueError("the database changed while it was being copied; run adopt again")
    for table in reversed(db.USER_TABLES):
        lock.execute(f'DROP TABLE "{table}"')
    db.set_meta(lock, "multiuser", 1)
    lock.execute("COMMIT")


def _unadopted(path, timeout=None):
    """True while the committed shared file still has the user tables and no multi-user marker.

    Callers use it to decide whether data may be deleted, so anything unclear (a locked or
    unreadable file) answers False.
    """
    try:
        conn = sqlite3.connect(
            f"{Path(path).resolve().as_uri()}?mode=ro", uri=True, timeout=LOCK_WAIT if timeout is None else timeout
        )
    except sqlite3.Error:
        return False
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if "collection" not in tables:
            return False
        if "meta" in tables:
            row = conn.execute("SELECT value FROM meta WHERE key = 'multiuser'").fetchone()
            if row is not None and row[0] == "1":
                return False
        return True
    except sqlite3.Error:
        return False
    finally:
        conn.close()


def _read_marker(target):
    """The file's ``user_version`` read from the raw header, or None if it is not a SQLite file.

    No connection is opened: a read-only connection cannot roll back a hot journal left by a killed
    run, and the marker was committed before the copy began, so the header bytes are valid anyway.
    """
    try:
        with open(target, "rb") as handle:
            header = handle.read(100)
    except OSError:
        return None
    if len(header) < 100 or not header.startswith(b"SQLite format 3\x00"):
        return None
    return struct.unpack(">I", header[60:64])[0]


def _is_adopt_file(target):
    """True when ``target`` was created by adopt: it carries the marker, or is still empty."""
    try:
        if Path(target).stat().st_size == 0:
            return True
    except OSError:
        return False
    return _read_marker(target) == ADOPT_MARKER
