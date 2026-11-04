"""The user's collection: which units they own, how many, and how many are painted."""

import difflib
import json
import re

from . import availability
from .catalog import unit_dict

COLLECTION_SELECT = """
SELECT c.id AS id, c.unit_uuid, c.qty, c.painted, c.notes, c.box,
       u.chassis, u.model, u.kind, u.type, u.subtype, u.tons, u.weight_class, u.bv, u.pv,
       u.year, u.tech_base, u.role, u.squad_size, u.walk, u.jump, u.mul_id, u.uuid, u.art
FROM collection c JOIN units u ON u.uuid = c.unit_uuid
"""


class CollectionError(ValueError):
    pass


def _row(row):
    d = unit_dict(row)
    d.pop("uuid", None)
    return d


def get(conn, collection_id):
    row = conn.execute(COLLECTION_SELECT + " WHERE c.id = ?", (collection_id,)).fetchone()
    if row is None:
        raise CollectionError(f"no collection entry with id {collection_id}")
    return _row(row)


def _like(term):
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def list_collection(
    conn, q=None, kind=None, painted_only=False, faction=None, era=None, tech_base=None, box=None
):
    """List owned units, optionally narrowed by text, kind, paint, faction/era, tech base or box."""
    clauses, params = [], []
    for term in (q or "").split():
        clauses.append("(u.chassis || ' ' || u.model) LIKE ? ESCAPE '\\'")
        params.append(_like(term))
    if box and box.strip():
        clauses.append("c.box LIKE ? ESCAPE '\\'")
        params.append(_like(box.strip()))
    if kind:
        clauses.append("u.kind = ?")
        params.append(kind)
    if painted_only:
        clauses.append("c.painted > 0")
    if tech_base:
        clauses.append("u.tech_base = ?")
        params.append(tech_base)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = conn.execute(
        COLLECTION_SELECT + where + " ORDER BY u.kind, u.chassis, u.model", params
    ).fetchall()
    items = [_row(r) for r in rows]

    faction_row = availability.resolve_faction(conn, faction)
    era_row = availability.resolve_era(conn, era)
    allowed = availability.available_mul_ids(
        conn, faction_row["id"] if faction_row else None, era_row["id"] if era_row else None
    )
    if allowed is not None:
        items = [i for i in items if i["mul_id"] in allowed]
    return items


def _clamp(qty, painted):
    qty = max(0, int(qty))
    painted = max(0, min(int(painted), qty))
    return qty, painted


def add(conn, unit_uuid, qty=1, painted=0, notes="", box=""):
    """Add a unit, or increase the quantity if it is already owned.

    A unit already owned keeps its existing box and notes unless new ones are given. Each
    unit has one box label, so adding copies from a second box overwrites the label.
    """
    if conn.execute("SELECT 1 FROM units WHERE uuid = ?", (unit_uuid,)).fetchone() is None:
        raise CollectionError(f"unit {unit_uuid!r} is not in the catalog")
    existing = conn.execute(
        "SELECT id, qty, painted, notes, box FROM collection WHERE unit_uuid = ?", (unit_uuid,)
    ).fetchone()
    box = (box or "").strip()
    with conn:
        if existing:
            new_qty, new_painted = _clamp(existing["qty"] + qty, existing["painted"] + painted)
            conn.execute(
                "UPDATE collection SET qty = ?, painted = ?, notes = ?, box = ? WHERE id = ?",
                (new_qty, new_painted, notes or existing["notes"], box or existing["box"], existing["id"]),
            )
            return get(conn, existing["id"])
        new_qty, new_painted = _clamp(qty, painted)
        cur = conn.execute(
            "INSERT INTO collection (unit_uuid, qty, painted, notes, box) VALUES (?,?,?,?,?)",
            (unit_uuid, new_qty, new_painted, notes, box),
        )
        return get(conn, cur.lastrowid)


def update(conn, collection_id, qty=None, painted=None, notes=None, box=None):
    current = get(conn, collection_id)
    new_qty = current["qty"] if qty is None else qty
    new_painted = current["painted"] if painted is None else painted
    new_qty, new_painted = _clamp(new_qty, new_painted)
    with conn:
        conn.execute(
            "UPDATE collection SET qty = ?, painted = ?, notes = ?, box = ? WHERE id = ?",
            (
                new_qty,
                new_painted,
                current["notes"] if notes is None else notes,
                current["box"] if box is None else box.strip(),
                collection_id,
            ),
        )
    return get(conn, collection_id)


def _card_variant_uuids(conn, entry):
    """Variants the entry's box ships a card for, when the entry's box label is an official box."""
    if not entry["box"]:
        return set()
    found = set()
    for row in conn.execute(
        "SELECT bi.card_units FROM box_items bi JOIN boxes b ON b.id = bi.box_id WHERE b.name = ?",
        (entry["box"],),
    ):
        listed = json.loads(row["card_units"] or "[]")
        if not listed:
            continue
        marks = ",".join("?" for _ in listed)
        chassis = {
            r["chassis"]
            for r in conn.execute(f"SELECT chassis FROM units WHERE uuid IN ({marks})", listed)
        }
        if entry["chassis"] in chassis:
            found.update(listed)
    return found


def variants(conn, collection_id):
    """Units of the same chassis and kind as an owned unit, so a mini can be switched variant.

    Variants the mini's official box has a card for come first, flagged ``on_card``.
    """
    current = get(conn, collection_id)
    on_card = _card_variant_uuids(conn, current)
    rows = conn.execute(
        "SELECT * FROM units WHERE chassis = ? AND kind = ? ORDER BY model",
        (current["chassis"], current["kind"]),
    ).fetchall()
    result = []
    for row in rows:
        d = unit_dict(row)
        d["current"] = d["uuid"] == current["unit_uuid"]
        d["on_card"] = d["uuid"] in on_card
        result.append(d)
    result.sort(key=lambda v: not v["on_card"])  # stable: model order is kept inside each group
    return result


def swap(conn, collection_id, unit_uuid):
    """Change an owned mini to another variant of the same chassis.

    If the target variant is already owned, the two entries merge (quantities and painted counts
    add up) and saved forces that used the old entry move to the target.
    """
    current = get(conn, collection_id)
    allowed = {v["uuid"] for v in variants(conn, collection_id)}
    if unit_uuid not in allowed:
        raise CollectionError("that unit is not a variant of the same chassis")
    if unit_uuid == current["unit_uuid"]:
        return current
    target = conn.execute(
        "SELECT id, qty, painted, notes, box FROM collection WHERE unit_uuid = ?", (unit_uuid,)
    ).fetchone()
    with conn:
        if target is None:
            conn.execute("UPDATE collection SET unit_uuid = ? WHERE id = ?", (unit_uuid, collection_id))
            return get(conn, collection_id)
        qty, painted = _clamp(target["qty"] + current["qty"], target["painted"] + current["painted"])
        notes = "; ".join(n for n in (target["notes"], current["notes"]) if n)
        conn.execute(
            "UPDATE collection SET qty = ?, painted = ?, notes = ?, box = ? WHERE id = ?",
            (qty, painted, notes, target["box"] or current["box"], target["id"]),
        )
        for force in conn.execute(
            "SELECT force_id, qty FROM force_units WHERE collection_id = ?", (collection_id,)
        ).fetchall():
            existing = conn.execute(
                "SELECT qty FROM force_units WHERE force_id = ? AND collection_id = ?",
                (force["force_id"], target["id"]),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE force_units SET qty = ? WHERE force_id = ? AND collection_id = ?",
                    (existing["qty"] + force["qty"], force["force_id"], target["id"]),
                )
                conn.execute(
                    "DELETE FROM force_units WHERE force_id = ? AND collection_id = ?",
                    (force["force_id"], collection_id),
                )
            else:
                conn.execute(
                    "UPDATE force_units SET collection_id = ? WHERE force_id = ? AND collection_id = ?",
                    (target["id"], force["force_id"], collection_id),
                )
        conn.execute("DELETE FROM collection WHERE id = ?", (collection_id,))
    return get(conn, target["id"])


def list_boxes(conn):
    """Distinct non-empty box labels with the number of collection entries in each."""
    rows = conn.execute(
        "SELECT box, COUNT(*) AS entries FROM collection WHERE box != '' GROUP BY box ORDER BY box"
    )
    return [{"box": r["box"], "entries": r["entries"]} for r in rows]


def remove(conn, collection_id):
    get(conn, collection_id)
    with conn:
        conn.execute("DELETE FROM collection WHERE id = ?", (collection_id,))


# --- text import -------------------------------------------------------------------------

_QTY_PREFIX = re.compile(r"^\s*(\d+)\s*[xX]\s+(.*)$")
_QTY_SUFFIX = re.compile(r"^(.*?)\s+[xX]\s*(\d+)\s*$")


def _norm(text):
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _parse_line(line):
    line = line.strip().strip("-*•").strip()
    if not line or line.startswith("#"):
        return None
    qty = 1
    m = _QTY_PREFIX.match(line)
    if m:
        qty, line = int(m.group(1)), m.group(2)
    else:
        m = _QTY_SUFFIX.match(line)
        if m:
            line, qty = m.group(1), int(m.group(2))
    return line.strip(), max(1, qty)


def import_text(conn, text, painted=False, box=""):
    """Add units from a plain list, one per line: ``Atlas AS7-D``, ``2x Locust LCT-1V``.

    Names are matched against the catalog. Exact matches are added; near matches
    and ties are reported back instead of guessed. ``box`` labels every added unit.
    """
    catalog = [
        (r["uuid"], f"{r['chassis']} {r['model']}".strip())
        for r in conn.execute("SELECT uuid, chassis, model FROM units")
    ]
    by_norm = {}
    for uuid, name in catalog:
        by_norm.setdefault(_norm(name), []).append((uuid, name))
    norm_names = list(by_norm)

    added, unmatched, ambiguous = [], [], []
    for raw in text.splitlines():
        parsed = _parse_line(raw)
        if parsed is None:
            continue
        name, qty = parsed
        key = _norm(name)
        hits = by_norm.get(key, [])
        if len(hits) == 1:
            entry = add(conn, hits[0][0], qty=qty, painted=qty if painted else 0, box=box)
            added.append({"line": raw.strip(), "id": entry["id"], "name": entry["name"], "qty": qty})
            continue
        if len(hits) > 1:
            ambiguous.append({"line": raw.strip(), "candidates": [n for _, n in hits[:6]]})
            continue
        close = difflib.get_close_matches(key, norm_names, n=4, cutoff=0.8)
        suggestions = [by_norm[c][0][1] for c in close]
        if suggestions:
            ambiguous.append({"line": raw.strip(), "candidates": suggestions})
        else:
            unmatched.append(raw.strip())
    return {"added": added, "ambiguous": ambiguous, "unmatched": unmatched}
