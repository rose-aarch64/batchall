"""The user's collection: which units they own, how many, and how many are painted."""

import difflib
import json
import re

from . import availability
from .catalog import unit_dict

COLLECTION_SELECT = """
SELECT c.id AS id, c.unit_uuid, c.qty, c.painted, c.notes, c.box,
       u.chassis, u.model, u.kind, u.type, u.subtype, u.tons, u.weight_class, u.bv, u.pv,
       u.year, u.tech_base, u.role, u.squad_size, u.walk, u.jump, u.mul_id, u.uuid, u.art,
       (SELECT COUNT(*) FROM copies WHERE collection_id = c.id) AS copy_count
FROM collection c JOIN units u ON u.uuid = c.unit_uuid
"""


class CollectionError(ValueError):
    pass


def _row(row):
    d = unit_dict(row)
    d.pop("uuid", None)
    return d


def _attach_copies(conn, items):
    """Give each row the named copies it carries, oldest first."""
    by_entry = {}
    for c in conn.execute("SELECT id, collection_id, note, tag FROM copies ORDER BY id"):
        by_entry.setdefault(c["collection_id"], []).append(
            {"id": c["id"], "note": c["note"], "tag": c["tag"]}
        )
    for item in items:
        item["copies"] = by_entry.get(item["id"], [])
    return items


CLAN_GROUPS = ("IS Clan", "HW Clan")  # MekBay's names for the invading and the homeworld Clans


def _attach_faction_groups(conn, items):
    """Give each row the faction groups (Inner Sphere, Clan, ...) that field its unit when it debuts, sorted.

    Only the first era the unit appears in counts. Availability lists every faction that ever fields
    a unit, so later captures and purchases would otherwise put a Clan mech in Inner Sphere.
    A Clan-tech unit also joins every Clan group that fields it in any era, since one Clan group
    often adopts a design an era after another. A unit can belong to several groups. One with no
    MUL id, or that no faction fields, gets none.
    """
    # mul id -> (year the first era starts, groups fielding it in that era)
    first = {}
    clan_ever = {}  # mul id -> Clan groups fielding it in any era
    for r in conn.execute(
        "SELECT f.grp AS grp, a.mul_ids AS mul_ids, e.year_from AS year FROM availability a "
        "JOIN factions f ON f.id = a.faction_id JOIN eras e ON e.id = a.era_id WHERE f.grp IS NOT NULL"
    ):
        for mul_id in json.loads(r["mul_ids"]):
            year, groups = first.get(mul_id, (None, set()))
            if year is None or r["year"] < year:
                first[mul_id] = (r["year"], {r["grp"]})
            elif r["year"] == year:
                groups.add(r["grp"])
            if r["grp"] in CLAN_GROUPS:
                clan_ever.setdefault(mul_id, set()).add(r["grp"])
    for item in items:
        groups = set(first.get(item["mul_id"], (None, ()))[1])
        if item["tech_base"] == "Clan":
            groups |= clan_ever.get(item["mul_id"], set())
        item["faction_groups"] = sorted(groups)
    return items


def _attach_available_eras(conn, items):
    """Give each row the ids of every era its unit is available in, earliest era first.

    Any faction fielding the unit in an era counts. A unit with no MUL id, or that no faction fields,
    gets an empty list.
    """
    years = {r["id"]: r["year_from"] for r in conn.execute("SELECT id, year_from FROM eras")}
    by_mul = {}
    for r in conn.execute("SELECT era_id, mul_ids FROM availability"):
        for mul_id in json.loads(r["mul_ids"]):
            by_mul.setdefault(mul_id, set()).add(r["era_id"])
    for item in items:
        eras = by_mul.get(item["mul_id"], ())
        item["available_eras"] = sorted(eras, key=lambda e: (years.get(e, 0), e))
    return items


def get(conn, collection_id):
    row = conn.execute(COLLECTION_SELECT + " WHERE c.id = ?", (collection_id,)).fetchone()
    if row is None:
        raise CollectionError(f"no collection entry with id {collection_id}")
    return _attach_available_eras(conn, _attach_faction_groups(conn, _attach_copies(conn, [_row(row)])))[0]


def _like(term):
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def list_collection(
    conn, q=None, kind=None, painted_only=False, faction=None, era=None, tech_base=None, box=None,
    tag=None,
):
    """List owned units, optionally narrowed by text, kind, paint, faction/era, tech base, box or copy tag."""
    clauses, params = [], []
    for term in (q or "").split():
        clauses.append("(u.chassis || ' ' || u.model) LIKE ? ESCAPE '\\'")
        params.append(_like(term))
    if box and box.strip():
        clauses.append("c.box LIKE ? ESCAPE '\\'")
        params.append(_like(box.strip()))
    if tag and tag.strip():
        clauses.append(
            "EXISTS (SELECT 1 FROM copies cp WHERE cp.collection_id = c.id AND cp.tag LIKE ? ESCAPE '\\')"
        )
        params.append(_like(tag.strip()))
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
    items = _attach_available_eras(
        conn, _attach_faction_groups(conn, _attach_copies(conn, [_row(r) for r in rows]))
    )

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
    named = conn.execute(
        "SELECT COUNT(*) FROM copies WHERE collection_id = ?", (collection_id,)
    ).fetchone()[0]
    if new_qty < named:
        raise CollectionError(f"{named} copies are named, so remove a named copy before lowering the quantity")
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
        conn.execute("UPDATE copies SET collection_id = ? WHERE collection_id = ?", (target["id"], collection_id))
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


_MUL_URL = re.compile(r"/Unit/Details/(\d+)", re.I)
_MUL_ID = re.compile(r"^[\"']?(?:mul\s*)?#?\s*(\d+)\b", re.I)


def _mul_id_from(text):
    """Pull a MUL id out of ``1234``, ``MUL 1234``, ``#1234`` or a masterunitlist.info link."""
    m = _MUL_URL.search(text) or _MUL_ID.match(text.strip())
    return int(m.group(1)) if m else None


def import_text(conn, text, painted=False, box="", mode="name"):
    """Add units from a plain list, one per line: ``Atlas AS7-D``, ``2x Locust LCT-1V``.

    Names are matched against the catalog. Exact matches are added; near matches
    and ties are reported back instead of guessed. ``box`` labels every added unit.

    With ``mode="mul"`` each line is a Master Unit List id or unit link instead
    (``1234``, ``2x MUL 1234``, ``https://masterunitlist.info/Unit/Details/1234/...``).
    Ids that several catalog entries share are reported back as ambiguous.
    """
    if mode not in ("name", "mul"):
        raise CollectionError(f"unknown import mode: {mode}")
    catalog = [
        (r["uuid"], f"{r['chassis']} {r['model']}".strip(), r["mul_id"])
        for r in conn.execute("SELECT uuid, chassis, model, mul_id FROM units")
    ]
    by_norm, by_mul = {}, {}
    for uuid, name, mul_id in catalog:
        by_norm.setdefault(_norm(name), []).append((uuid, name))
        if mul_id is not None:
            by_mul.setdefault(int(mul_id), []).append((uuid, name))
    norm_names = list(by_norm)

    added, unmatched, ambiguous = [], [], []
    for raw in text.splitlines():
        parsed = _parse_line(raw)
        if parsed is None:
            continue
        name, qty = parsed
        if mode == "mul":
            mul_id = _mul_id_from(name)
            hits = by_mul.get(mul_id, []) if mul_id is not None else []
            if len(hits) == 1:
                entry = add(conn, hits[0][0], qty=qty, painted=qty if painted else 0, box=box)
                added.append({"line": raw.strip(), "id": entry["id"], "name": entry["name"], "qty": qty})
            elif hits:
                ambiguous.append({"line": raw.strip(), "candidates": [n for _, n in hits[:6]]})
            else:
                unmatched.append(raw.strip())
            continue
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
