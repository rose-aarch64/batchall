"""Named copies: individual minis in the collection that carry their own paint note, tag, faction and company.

The collection entry stays the source of truth for how many of a unit are owned. A copy is an
optional label on one of those minis, so an entry can have at most ``qty`` copies. Faction and
company say which force the mini is painted for ("Wolf's Dragoons", "Black Widow Company"). They
are separate from the faction a unit is fielded by in the MekBay data.

A copy can also be marked painted, sit in a box of its own, and be switched to another variant.
The entry's ``painted`` stays the total of painted minis, named or not. The unnamed stack's painted
count is that total minus the painted copies.
"""

from . import collection


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
        "painted": bool(row["painted"]),
        "box": row["box"],
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


def _stack_painted(conn, entry_id):
    """How many of the entry's unnamed minis are painted. Read this before changing copies."""
    entry = conn.execute("SELECT qty, painted FROM collection WHERE id = ?", (entry_id,)).fetchone()
    named, named_painted = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(painted), 0) FROM copies WHERE collection_id = ?", (entry_id,)
    ).fetchone()
    return max(0, min(entry["painted"] - named_painted, entry["qty"] - named))


def _restack(conn, entry_id, stack_painted):
    """Set the entry's painted total from a stack count and the copies as they now stand."""
    entry = conn.execute("SELECT qty FROM collection WHERE id = ?", (entry_id,)).fetchone()
    named, named_painted = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(painted), 0) FROM copies WHERE collection_id = ?", (entry_id,)
    ).fetchone()
    stack = max(0, min(stack_painted, entry["qty"] - named))
    conn.execute("UPDATE collection SET painted = ? WHERE id = ?", (stack + named_painted, entry_id))


def add(conn, collection_id, note, tag="", faction="", company="", painted=False, box=""):
    entry = conn.execute("SELECT qty FROM collection WHERE id = ?", (collection_id,)).fetchone()
    if entry is None:
        raise CopyError(f"no collection entry with id {collection_id}")
    note, tag = (note or "").strip(), (tag or "").strip()
    faction, company = (faction or "").strip(), (company or "").strip()
    box = (box or "").strip()
    _require_a_label(note, faction, company)
    if count_for(conn, collection_id) >= entry["qty"]:
        raise CopyError(f"all {entry['qty']} owned are already named")
    stack = _stack_painted(conn, collection_id)
    with conn:
        cur = conn.execute(
            "INSERT INTO copies (collection_id, note, tag, faction, company, painted, box) VALUES (?,?,?,?,?,?,?)",
            (collection_id, note, tag, faction, company, 1 if painted else 0, box),
        )
        _restack(conn, collection_id, stack)
    return get(conn, cur.lastrowid)


def update(conn, copy_id, note=None, tag=None, faction=None, company=None, painted=None, box=None):
    """Change the fields that are given and keep the rest. A copy always keeps a note, faction or company."""
    current = get(conn, copy_id)
    given = {
        name: value.strip()
        for name, value in (
            ("note", note), ("tag", tag), ("faction", faction), ("company", company), ("box", box),
        )
        if value is not None
    }
    if painted is not None:
        given["painted"] = 1 if painted else 0
    merged = {**current, **given}
    _require_a_label(merged["note"], merged["faction"], merged["company"])
    if given:
        stack = _stack_painted(conn, current["collection_id"])
        # Write only the fields that were given. Quick edits send several saves at once, and rewriting
        # every field from a read taken earlier would put an old value back over a newer one.
        with conn:
            conn.execute(
                f"UPDATE copies SET {', '.join(f'{name} = ?' for name in given)} WHERE id = ?",
                (*given.values(), copy_id),
            )
            if "painted" in given:
                _restack(conn, current["collection_id"], stack)
    return get(conn, copy_id)


def remove(conn, copy_id):
    """Drop a copy's name. The mini goes back into the unnamed stack, painted if it was."""
    current = get(conn, copy_id)
    entry_id = current["collection_id"]
    stack = _stack_painted(conn, entry_id) + (1 if current["painted"] else 0)
    with conn:
        conn.execute("DELETE FROM copies WHERE id = ?", (copy_id,))
        _restack(conn, entry_id, stack)


def swap(conn, copy_id, unit_uuid):
    """Switch one named mini to another variant of its chassis, leaving the other minis where they are.

    The copy moves to the entry for the new variant, which is created if the variant is not owned yet.
    The old entry loses one mini, and is dropped when none are left.
    """
    current = get(conn, copy_id)
    source_id = current["collection_id"]
    source = collection.get(conn, source_id)
    if unit_uuid == source["unit_uuid"]:
        return current
    if unit_uuid not in {v["uuid"] for v in collection.variants(conn, source_id)}:
        raise CopyError("that unit is not a variant of the same chassis")
    target = conn.execute("SELECT id FROM collection WHERE unit_uuid = ?", (unit_uuid,)).fetchone()
    source_stack = _stack_painted(conn, source_id)
    with conn:
        if target is None:
            cur = conn.execute(
                "INSERT INTO collection (unit_uuid, qty, painted, box) VALUES (?, 0, 0, ?)",
                (unit_uuid, source["box"]),
            )
            target_id, target_stack = cur.lastrowid, 0
        else:
            target_id, target_stack = target["id"], _stack_painted(conn, target["id"])
        conn.execute("UPDATE collection SET qty = qty + 1 WHERE id = ?", (target_id,))
        conn.execute("UPDATE collection SET qty = qty - 1 WHERE id = ?", (source_id,))
        conn.execute("UPDATE copies SET collection_id = ? WHERE id = ?", (target_id, copy_id))
        _restack(conn, target_id, target_stack)
        if source["qty"] - 1 <= 0:
            conn.execute("DELETE FROM collection WHERE id = ?", (source_id,))
        else:
            _restack(conn, source_id, source_stack)
    return get(conn, copy_id)
