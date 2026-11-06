"""SQLite storage: the unit catalog (replaceable) and the user's collection and forces."""

import sqlite3
from pathlib import Path

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS units (
    uuid TEXT PRIMARY KEY,
    mul_id INTEGER,
    chassis TEXT NOT NULL,
    model TEXT NOT NULL,
    kind TEXT NOT NULL,
    type TEXT,
    subtype TEXT,
    tons REAL,
    weight_class TEXT,
    bv INTEGER NOT NULL,
    pv INTEGER,
    year INTEGER,
    tech_base TEXT,
    role TEXT,
    squad_size INTEGER,
    walk INTEGER,
    jump INTEGER,
    art TEXT
);
CREATE INDEX IF NOT EXISTS idx_units_kind ON units(kind);
CREATE INDEX IF NOT EXISTS idx_units_mul ON units(mul_id);

CREATE TABLE IF NOT EXISTS unit_details (
    uuid TEXT PRIMARY KEY,
    specs TEXT NOT NULL,
    lore TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS factions (id INTEGER PRIMARY KEY, name TEXT NOT NULL, grp TEXT);
CREATE TABLE IF NOT EXISTS eras (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, year_from INTEGER, year_to INTEGER
);
CREATE TABLE IF NOT EXISTS availability (
    faction_id INTEGER NOT NULL,
    era_id INTEGER NOT NULL,
    mul_ids TEXT NOT NULL,
    PRIMARY KEY (faction_id, era_id)
);

CREATE TABLE IF NOT EXISTS collection (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    unit_uuid TEXT NOT NULL UNIQUE,
    qty INTEGER NOT NULL DEFAULT 1 CHECK (qty >= 0),
    painted INTEGER NOT NULL DEFAULT 0 CHECK (painted >= 0),
    notes TEXT NOT NULL DEFAULT '',
    box TEXT NOT NULL DEFAULT '',
    added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS copies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    collection_id INTEGER NOT NULL REFERENCES collection(id) ON DELETE CASCADE,
    note TEXT NOT NULL,
    tag TEXT NOT NULL DEFAULT '',
    faction TEXT NOT NULL DEFAULT '',
    company TEXT NOT NULL DEFAULT '',
    material TEXT NOT NULL DEFAULT '',
    painted INTEGER NOT NULL DEFAULT 0 CHECK (painted IN (0, 1)),
    box TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_copies_collection ON copies(collection_id);
CREATE INDEX IF NOT EXISTS idx_copies_tag ON copies(tag);

CREATE TABLE IF NOT EXISTS forces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    formation TEXT NOT NULL,
    target_bv INTEGER,
    faction_id INTEGER,
    era_id INTEGER,
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS boxes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    section TEXT NOT NULL DEFAULT '',
    source_url TEXT NOT NULL DEFAULT '',
    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_boxes_name ON boxes(name);

CREATE TABLE IF NOT EXISTS box_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    box_id INTEGER NOT NULL REFERENCES boxes(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    mini_name TEXT NOT NULL,
    card_variant TEXT NOT NULL DEFAULT '',
    qty INTEGER NOT NULL DEFAULT 1 CHECK (qty > 0),
    unit_uuid TEXT,
    status TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    card_units TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_box_items_box ON box_items(box_id);

CREATE TABLE IF NOT EXISTS force_units (
    force_id INTEGER NOT NULL REFERENCES forces(id) ON DELETE CASCADE,
    collection_id INTEGER NOT NULL REFERENCES collection(id) ON DELETE CASCADE,
    qty INTEGER NOT NULL DEFAULT 1 CHECK (qty > 0),
    PRIMARY KEY (force_id, collection_id)
);
"""


def adopt_legacy_database(target, legacy):
    """Copy the database file from before the rename to its new name, once. Returns True when it copied.

    Does nothing when the new file already exists or there is no old one. The old file is only read, so
    it stays as a backup. The copy uses SQLite's backup API, which gives a consistent snapshot, and goes
    to a temporary file that is renamed into place, so an interrupted copy never leaves a half-written
    database for a later start to trust.
    """
    target, legacy = Path(target), Path(legacy)
    if target.exists() or not legacy.exists():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".copying")
    partial.unlink(missing_ok=True)
    source = sqlite3.connect(f"{legacy.resolve().as_uri()}?mode=ro", uri=True)
    try:
        copy = sqlite3.connect(partial)
        try:
            source.backup(copy)
        finally:
            copy.close()
        partial.replace(target)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    finally:
        source.close()
    return True


def connect(path=None):
    """Open (and create if needed) the database.

    With no path, the default database is used. The first time that happens after the rename to
    Batchall, the old ``battletech.db`` is copied to ``batchall.db``. A path given by the caller or by
    a setting is used as it is.
    """
    if path is None and config.DB_IS_DEFAULT:
        adopt_legacy_database(config.DB_PATH, config.LEGACY_DB_PATH)
    path = str(path or config.DB_PATH)
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn):
    """Bring databases created by earlier versions up to the current schema."""
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(collection)")}
    if "box" not in columns:
        with conn:
            conn.execute("ALTER TABLE collection ADD COLUMN box TEXT NOT NULL DEFAULT ''")
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(box_items)")}
    if "card_units" not in columns:  # filled in by the next `boxes import`
        with conn:
            conn.execute("ALTER TABLE box_items ADD COLUMN card_units TEXT NOT NULL DEFAULT '[]'")
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(copies)")}
    for column in ("faction", "company", "material"):  # empty until a mini is given one
        if column not in columns:
            with conn:
                conn.execute(f"ALTER TABLE copies ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
    if "painted" not in columns:  # a named mini is unpainted until marked; the entry's count is unchanged
        with conn:
            conn.execute("ALTER TABLE copies ADD COLUMN painted INTEGER NOT NULL DEFAULT 0")
    if "box" not in columns:  # empty means the mini sits in its entry's box
        with conn:
            conn.execute("ALTER TABLE copies ADD COLUMN box TEXT NOT NULL DEFAULT ''")
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(units)")}
    if "art" not in columns:  # filled in by the next `catalog import`
        with conn:
            conn.execute("ALTER TABLE units ADD COLUMN art TEXT")
    _add_plain_mini_rows(conn)


def _add_plain_mini_rows(conn):
    """Give each entry one ``copies`` row for every owned mini that has none yet.

    Databases from before minis were rows only had a row per named mini. The missing ones are added as
    plain rows, with the entry's remaining painted count spread over them. Nothing is ever removed.
    """
    entries = conn.execute(
        "SELECT c.id, c.qty, c.painted, "
        "(SELECT COUNT(*) FROM copies WHERE collection_id = c.id) AS minis, "
        "(SELECT COUNT(*) FROM copies WHERE collection_id = c.id AND painted = 1) AS minis_painted "
        "FROM collection c"
    ).fetchall()
    with conn:
        for e in entries:
            missing = e["qty"] - e["minis"]
            if missing <= 0:
                continue
            painted = max(0, min(e["painted"] - e["minis_painted"], missing))
            conn.executemany(
                "INSERT INTO copies (collection_id, note, painted) VALUES (?, '', ?)",
                [(e["id"], 1 if i < painted else 0) for i in range(missing)],
            )
            conn.execute(
                "UPDATE collection SET painted = (SELECT COUNT(*) FROM copies "
                "WHERE collection_id = collection.id AND painted = 1) WHERE id = ?",
                (e["id"],),
            )


def get_meta(conn, key, default=None):
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(conn, key, value):
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )
