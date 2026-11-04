"""Named copies: individual minis in the collection that carry their own paint note and tag.

The collection entry stays the source of truth for how many of a unit are owned. A copy is an
optional label on one of those minis, so an entry can have at most ``qty`` copies.
"""


class CopyError(ValueError):
    pass


def _dict(row):
    return {
        "id": row["id"],
        "collection_id": row["collection_id"],
        "note": row["note"],
        "tag": row["tag"],
        "created_at": row["created_at"],
    }


def get(conn, copy_id):
    row = conn.execute("SELECT * FROM copies WHERE id = ?", (copy_id,)).fetchone()
    if row is None:
        raise CopyError(f"no copy with id {copy_id}")
    return _dict(row)


def list_for(conn, collection_id):
    rows = conn.execute("SELECT * FROM copies WHERE collection_id = ? ORDER BY id", (collection_id,))
    return [_dict(r) for r in rows]


def count_for(conn, collection_id):
    return conn.execute(
        "SELECT COUNT(*) FROM copies WHERE collection_id = ?", (collection_id,)
    ).fetchone()[0]


def list_tags(conn):
    """Distinct non-empty copy tags with the number of copies under each."""
    rows = conn.execute(
        "SELECT tag, COUNT(*) AS copies FROM copies WHERE tag != '' GROUP BY tag ORDER BY tag"
    )
    return [{"tag": r["tag"], "copies": r["copies"]} for r in rows]


def _clean_note(note):
    note = (note or "").strip()
    if not note:
        raise CopyError("a copy needs a note")
    return note


def add(conn, collection_id, note, tag=""):
    entry = conn.execute("SELECT qty FROM collection WHERE id = ?", (collection_id,)).fetchone()
    if entry is None:
        raise CopyError(f"no collection entry with id {collection_id}")
    note = _clean_note(note)
    if count_for(conn, collection_id) >= entry["qty"]:
        raise CopyError(f"all {entry['qty']} owned are already named")
    with conn:
        cur = conn.execute(
            "INSERT INTO copies (collection_id, note, tag) VALUES (?,?,?)",
            (collection_id, note, (tag or "").strip()),
        )
    return get(conn, cur.lastrowid)


def update(conn, copy_id, note=None, tag=None):
    current = get(conn, copy_id)
    with conn:
        conn.execute(
            "UPDATE copies SET note = ?, tag = ? WHERE id = ?",
            (
                current["note"] if note is None else _clean_note(note),
                current["tag"] if tag is None else tag.strip(),
                copy_id,
            ),
        )
    return get(conn, copy_id)


def remove(conn, copy_id):
    get(conn, copy_id)
    with conn:
        conn.execute("DELETE FROM copies WHERE id = ?", (copy_id,))
