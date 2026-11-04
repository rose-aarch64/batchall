"""Named copies: individual minis in the collection that carry their own paint note, tag, faction and company.

The collection entry stays the source of truth for how many of a unit are owned. A copy is an
optional label on one of those minis, so an entry can have at most ``qty`` copies. Faction and
company say which force the mini is painted for ("Wolf's Dragoons", "Black Widow Company"). They
are separate from the faction a unit is fielded by in the MekBay data.
"""


class CopyError(ValueError):
    pass


VALUE_FIELDS = ("faction", "company")  # the free-text fields that can be listed for suggestions


def _dict(row):
    return {
        "id": row["id"],
        "collection_id": row["collection_id"],
        "note": row["note"],
        "tag": row["tag"],
        "faction": row["faction"],
        "company": row["company"],
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


def list_values(conn, field):
    """Distinct non-empty values of ``faction`` or ``company`` with the number of copies under each."""
    if field not in VALUE_FIELDS:
        raise CopyError(f"cannot list values of {field!r}")
    rows = conn.execute(
        f"SELECT {field} AS value, COUNT(*) AS copies FROM copies WHERE {field} != '' "
        f"GROUP BY {field} ORDER BY {field}"
    )
    return [{"value": r["value"], "copies": r["copies"]} for r in rows]


def _require_a_label(note, faction, company):
    if not (note or faction or company):
        raise CopyError("a copy needs a note, a faction or a company")


def add(conn, collection_id, note, tag="", faction="", company=""):
    entry = conn.execute("SELECT qty FROM collection WHERE id = ?", (collection_id,)).fetchone()
    if entry is None:
        raise CopyError(f"no collection entry with id {collection_id}")
    note, tag = (note or "").strip(), (tag or "").strip()
    faction, company = (faction or "").strip(), (company or "").strip()
    _require_a_label(note, faction, company)
    if count_for(conn, collection_id) >= entry["qty"]:
        raise CopyError(f"all {entry['qty']} owned are already named")
    with conn:
        cur = conn.execute(
            "INSERT INTO copies (collection_id, note, tag, faction, company) VALUES (?,?,?,?,?)",
            (collection_id, note, tag, faction, company),
        )
    return get(conn, cur.lastrowid)


def update(conn, copy_id, note=None, tag=None, faction=None, company=None):
    """Change the fields that are given and keep the rest. A copy always keeps a note, faction or company."""
    current = get(conn, copy_id)
    given = {
        name: value.strip()
        for name, value in (("note", note), ("tag", tag), ("faction", faction), ("company", company))
        if value is not None
    }
    merged = {**current, **given}
    _require_a_label(merged["note"], merged["faction"], merged["company"])
    if given:
        # Write only the fields that were given. Quick edits send several saves at once, and rewriting
        # every field from a read taken earlier would put an old value back over a newer one.
        with conn:
            conn.execute(
                f"UPDATE copies SET {', '.join(f'{name} = ?' for name in given)} WHERE id = ?",
                (*given.values(), copy_id),
            )
    return get(conn, copy_id)


def remove(conn, copy_id):
    get(conn, copy_id)
    with conn:
        conn.execute("DELETE FROM copies WHERE id = ?", (copy_id,))
