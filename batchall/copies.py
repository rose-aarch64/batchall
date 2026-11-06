"""Minis: every owned mini is a row of its own, with a paint note, tag, faction, company and material.

The collection entry's ``qty`` and ``painted`` always match its rows. Faction and company say which
force the mini is painted for ("Wolf's Dragoons", "Black Widow Company"). They are separate from the
faction a unit is fielded by in the MekBay data. Material says what the mini is made of ("CGL Plastic",
"Resin", "Metal").

A mini can be marked painted, sit in a box of its own, and be switched to another variant.
"""

from . import collection


class CopyError(ValueError):
    pass


VALUE_FIELDS = ("faction", "company", "material")  # the free-text fields that can be listed for suggestions


def _dict(row):
    return {
        "id": row["id"],
        "collection_id": row["collection_id"],
        "note": row["note"],
        "tag": row["tag"],
        "faction": row["faction"],
        "company": row["company"],
        "material": row["material"],
        "painted": bool(row["painted"]),
        "box": row["box"],
        "created_at": row["created_at"],
    }


def get(conn, copy_id):
    row = conn.execute("SELECT * FROM copies WHERE id = ?", (copy_id,)).fetchone()
    if row is None:
        raise CopyError(f"no copy with id {copy_id}")
    return _dict(row)


def list_tags(conn):
    """Distinct non-empty mini tags with the number of minis under each."""
    rows = conn.execute(
        "SELECT tag, COUNT(*) AS copies FROM copies WHERE tag != '' GROUP BY tag ORDER BY tag"
    )
    return [{"tag": r["tag"], "copies": r["copies"]} for r in rows]


def list_values(conn, field):
    """Distinct non-empty values of ``faction``, ``company`` or ``material`` with the number of minis under each."""
    if field not in VALUE_FIELDS:
        raise CopyError(f"cannot list values of {field!r}")
    rows = conn.execute(
        f"SELECT {field} AS value, COUNT(*) AS copies FROM copies WHERE {field} != '' "
        f"GROUP BY {field} ORDER BY {field}"
    )
    return [{"value": r["value"], "copies": r["copies"]} for r in rows]


def _given(note, tag, faction, company, painted, box, material):
    """The fields to write: text trimmed, painted as 0 or 1, and any field left as None skipped."""
    given = {
        name: value.strip()
        for name, value in (
            ("note", note), ("tag", tag), ("faction", faction), ("company", company), ("box", box),
            ("material", material),
        )
        if value is not None
    }
    if painted is not None:
        given["painted"] = 1 if painted else 0
    return given


BULK_CHUNK = 500  # ids per statement, well under SQLite's limit on parameters


def update_many(conn, ids, note=None, tag=None, faction=None, company=None, painted=None, box=None, material=None):
    """Set the given fields on several minis at once and return how many were changed.

    All or nothing: if any id is unknown, or nothing is to be changed, an error is raised and no mini
    is touched. An id listed twice counts once. Entries' painted totals are kept right.
    """
    ids = list(dict.fromkeys(ids))
    if not ids:
        raise CopyError("no minis were chosen")
    given = _given(note, tag, faction, company, painted, box, material)
    if not given:
        raise CopyError("nothing to change")
    chunks = [ids[i:i + BULK_CHUNK] for i in range(0, len(ids), BULK_CHUNK)]
    with conn:
        entries = set()
        found = 0
        for chunk in chunks:
            marks = ",".join("?" for _ in chunk)
            rows = conn.execute(f"SELECT id, collection_id FROM copies WHERE id IN ({marks})", chunk).fetchall()
            found += len(rows)
            entries.update(r["collection_id"] for r in rows)
        if found != len(ids):
            known = {r["id"] for chunk in chunks for r in conn.execute(
                f"SELECT id FROM copies WHERE id IN ({','.join('?' for _ in chunk)})", chunk)}
            raise CopyError(f"no mini with id {sorted(set(ids) - known)[0]}")  # nothing has been written yet
        assignments = ", ".join(f"{name} = ?" for name in given)
        for chunk in chunks:
            marks = ",".join("?" for _ in chunk)
            conn.execute(f"UPDATE copies SET {assignments} WHERE id IN ({marks})", (*given.values(), *chunk))
        if "painted" in given:
            for entry_id in entries:
                collection.recount(conn, entry_id)
    return len(ids)


def update(conn, copy_id, note=None, tag=None, faction=None, company=None, painted=None, box=None, material=None):
    """Change the fields that are given and keep the rest."""
    current = get(conn, copy_id)
    given = _given(note, tag, faction, company, painted, box, material)
    if given:
        # Write only the fields that were given. Quick edits send several saves at once, and rewriting
        # every field from a read taken earlier would put an old value back over a newer one.
        with conn:
            conn.execute(
                f"UPDATE copies SET {', '.join(f'{name} = ?' for name in given)} WHERE id = ?",
                (*given.values(), copy_id),
            )
            if "painted" in given:
                collection.recount(conn, current["collection_id"])
    return get(conn, copy_id)


def delete_mini(conn, copy_id):
    """Remove one mini from the collection. The entry loses one owned, and is dropped with its last mini."""
    current = get(conn, copy_id)
    entry_id = current["collection_id"]
    with conn:
        conn.execute("DELETE FROM copies WHERE id = ?", (copy_id,))
        left = conn.execute("SELECT COUNT(*) FROM copies WHERE collection_id = ?", (entry_id,)).fetchone()[0]
        if left == 0:
            conn.execute("DELETE FROM collection WHERE id = ?", (entry_id,))
        else:
            collection.recount(conn, entry_id)


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
    with conn:
        if target is None:
            cur = conn.execute(
                "INSERT INTO collection (unit_uuid, qty, painted, box) VALUES (?, 0, 0, ?)",
                (unit_uuid, source["box"]),
            )
            target_id = cur.lastrowid
        else:
            target_id = target["id"]
        conn.execute("UPDATE copies SET collection_id = ? WHERE id = ?", (target_id, copy_id))
        collection.recount(conn, target_id)
        if source["qty"] - 1 <= 0:
            conn.execute("DELETE FROM collection WHERE id = ?", (source_id,))
        else:
            collection.recount(conn, source_id)
    return get(conn, copy_id)
