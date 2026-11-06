"""Saved forces: a named list of owned units with a formation and optional BV target."""

from . import collection as coll
from .catalog import unit_dict
from .kinds import KINDS


class ForceError(ValueError):
    pass


def _units_for(conn, force_id):
    """The force's units, one row per (owned mini, variant fielded).

    A mini fielded as another variant (a proxy) takes that variant's stats, its ``unit_uuid`` is
    the variant's, and ``owned_as`` names the variant actually owned.
    """
    rows = conn.execute(
        "SELECT collection_id, as_uuid, qty FROM force_units WHERE force_id = ?", (force_id,)
    ).fetchall()
    if not rows:
        return []
    ids = sorted({r["collection_id"] for r in rows})
    marks = ",".join("?" for _ in ids)
    owned = {}
    for row in conn.execute(coll.COLLECTION_SELECT + f" WHERE c.id IN ({marks})", ids):
        entry = coll._row(row)
        owned[entry["id"]] = entry
    units = []
    for r in rows:
        base = owned[r["collection_id"]]
        d = dict(base)
        d["collection_id"] = d.pop("id")
        d["owned"] = d["qty"]
        d["painted_owned"] = d.pop("painted")
        d["qty"] = r["qty"]
        if r["as_uuid"]:
            variant = conn.execute("SELECT * FROM units WHERE uuid = ?", (r["as_uuid"],)).fetchone()
            if variant is None:  # a catalog refresh dropped it; fall back to the variant owned
                d["variant_missing"] = True
            else:
                v = unit_dict(variant)
                variant_uuid = v.pop("uuid")
                d.update(v)
                d["unit_uuid"] = variant_uuid
                d["owned_as"] = base["name"]
        units.append(d)
    units.sort(key=lambda u: (KINDS.index(u["kind"]), -u["bv"], u["name"]))
    return units


def _summarize(conn, row):
    units = _units_for(conn, row["id"])
    total = sum(u["bv"] * u["qty"] for u in units)
    composition = {}
    for u in units:
        composition[u["kind"]] = composition.get(u["kind"], 0) + u["qty"]
    used = {}
    for u in units:
        used[u["collection_id"]] = used.get(u["collection_id"], 0) + u["qty"]
    warnings = []
    for u in units:
        cid = u["collection_id"]
        if used.get(cid, 0) > u["owned"]:
            warnings.append(f"{u.get('owned_as') or u['name']}: uses {used.pop(cid)} but only {u['owned']} owned")
        if u.get("variant_missing"):
            warnings.append(f"{u['name']}: the variant it was fielded as is gone from the catalog")
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

    ``units`` is a list of ``{"collection_id": int, "qty": int, "unit_uuid": str}``. The
    ``unit_uuid`` is optional: it names the variant of the mini's chassis that the force fields it
    as (a proxy). Leave it out, or give the variant owned, to field the mini as owned.
    """
    name = (name or "").strip()
    if not name:
        raise ForceError("a force needs a name")
    merged = {}
    entries = {}
    for u in units or []:
        cid, qty = int(u["collection_id"]), int(u.get("qty", 1))
        if qty < 1:
            continue
        if cid not in entries:
            entries[cid] = coll.get(conn, cid)  # raises if the collection entry does not exist
        as_uuid = (u.get("unit_uuid") or "").strip()
        if as_uuid == entries[cid]["unit_uuid"]:
            as_uuid = ""
        elif as_uuid and as_uuid not in {v["uuid"] for v in coll.variants(conn, cid)}:
            raise ForceError(f"{as_uuid} is not a variant of {entries[cid]['name']}")
        merged[(cid, as_uuid)] = merged.get((cid, as_uuid), 0) + qty

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
            "INSERT INTO force_units (force_id, collection_id, as_uuid, qty) VALUES (?,?,?,?)",
            [(force_id, cid, as_uuid, qty) for (cid, as_uuid), qty in merged.items()],
        )
    return get(conn, force_id)


def delete(conn, force_id):
    get(conn, force_id)
    with conn:
        conn.execute("DELETE FROM forces WHERE id = ?", (force_id,))
