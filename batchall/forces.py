"""Saved forces: a named list of owned units with a formation and optional BV target."""

from . import collection as coll
from .kinds import KINDS


class ForceError(ValueError):
    pass


def _units_for(conn, force_id):
    force_qty = {
        r["collection_id"]: r["qty"]
        for r in conn.execute(
            "SELECT collection_id, qty FROM force_units WHERE force_id = ?", (force_id,)
        )
    }
    if not force_qty:
        return []
    marks = ",".join("?" for _ in force_qty)
    rows = conn.execute(
        coll.COLLECTION_SELECT + f" WHERE c.id IN ({marks})", list(force_qty)
    ).fetchall()
    units = []
    for row in rows:
        d = coll._row(row)
        d["collection_id"] = d.pop("id")
        d["owned"] = d["qty"]
        d["painted_owned"] = d.pop("painted")
        d["qty"] = force_qty[d["collection_id"]]
        units.append(d)
    units.sort(key=lambda u: (KINDS.index(u["kind"]), -u["bv"], u["name"]))
    return units


def _summarize(conn, row):
    units = _units_for(conn, row["id"])
    total = sum(u["bv"] * u["qty"] for u in units)
    composition = {}
    for u in units:
        composition[u["kind"]] = composition.get(u["kind"], 0) + u["qty"]
    warnings = [
        f"{u['name']}: uses {u['qty']} but only {u['owned']} owned"
        for u in units
        if u["qty"] > u["owned"]
    ]
    return {
        "id": row["id"],
        "name": row["name"],
        "formation": row["formation"],
        "target_bv": row["target_bv"],
        "faction_id": row["faction_id"],
        "era_id": row["era_id"],
        "notes": row["notes"],
        "created_at": row["created_at"],
        "total_bv": total,
        "count": sum(u["qty"] for u in units),
        "composition": composition,
        "units": units,
        "warnings": warnings,
    }


def list_forces(conn):
    rows = conn.execute("SELECT * FROM forces ORDER BY id DESC").fetchall()
    return [_summarize(conn, r) for r in rows]


def get(conn, force_id):
    row = conn.execute("SELECT * FROM forces WHERE id = ?", (force_id,)).fetchone()
    if row is None:
        raise ForceError(f"no force with id {force_id}")
    return _summarize(conn, row)


def save(
    conn,
    name,
    formation,
    units,
    target_bv=None,
    faction_id=None,
    era_id=None,
    notes="",
    force_id=None,
):
    """Create a force, or replace one when ``force_id`` is given.

    ``units`` is a list of ``{"collection_id": int, "qty": int}``.
    """
    name = (name or "").strip()
    if not name:
        raise ForceError("a force needs a name")
    merged = {}
    for u in units or []:
        cid, qty = int(u["collection_id"]), int(u.get("qty", 1))
        if qty < 1:
            continue
        merged[cid] = merged.get(cid, 0) + qty
    for cid in merged:
        coll.get(conn, cid)  # raises if the collection entry does not exist

    with conn:
        if force_id is None:
            cur = conn.execute(
                "INSERT INTO forces (name, formation, target_bv, faction_id, era_id, notes) "
                "VALUES (?,?,?,?,?,?)",
                (name, formation, target_bv, faction_id, era_id, notes),
            )
            force_id = cur.lastrowid
        else:
            get(conn, force_id)
            conn.execute(
                "UPDATE forces SET name=?, formation=?, target_bv=?, faction_id=?, era_id=?, notes=? "
                "WHERE id=?",
                (name, formation, target_bv, faction_id, era_id, notes, force_id),
            )
            conn.execute("DELETE FROM force_units WHERE force_id = ?", (force_id,))
        conn.executemany(
            "INSERT INTO force_units (force_id, collection_id, qty) VALUES (?,?,?)",
            [(force_id, cid, qty) for cid, qty in merged.items()],
        )
    return get(conn, force_id)


def delete(conn, force_id):
    get(conn, force_id)
    with conn:
        conn.execute("DELETE FROM forces WHERE id = ?", (force_id,))
