"""Faction and era lookups, and which units a faction fields in an era."""

import json


def _resolve(conn, table, ref, label):
    """Find a row by numeric id, exact name, or unique name substring (case-insensitive)."""
    if ref is None or ref == "":
        return None
    if isinstance(ref, int) or str(ref).strip().isdigit():
        row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (int(ref),)).fetchone()
        if row:
            return dict(row)
        raise ValueError(f"no {label} with id {ref}")
    text = str(ref).strip().lower()
    rows = [dict(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY name")]
    exact = [r for r in rows if r["name"].lower() == text]
    if exact:
        return exact[0]
    partial = [r for r in rows if text in r["name"].lower()]
    if len(partial) == 1:
        return partial[0]
    if not partial:
        raise ValueError(f"no {label} matching {ref!r}")
    names = ", ".join(r["name"] for r in partial[:8])
    raise ValueError(f"{label} {ref!r} is ambiguous: {names}")


def resolve_faction(conn, ref):
    return _resolve(conn, "factions", ref, "faction")


def resolve_era(conn, ref):
    return _resolve(conn, "eras", ref, "era")


def list_factions(conn):
    return [
        {"id": r["id"], "name": r["name"], "group": r["grp"]}
        for r in conn.execute("SELECT * FROM factions ORDER BY grp, name")
    ]


def list_eras(conn):
    return [
        {"id": r["id"], "name": r["name"], "from": r["year_from"], "to": r["year_to"]}
        for r in conn.execute("SELECT * FROM eras ORDER BY year_from")
    ]


def available_mul_ids(conn, faction_id=None, era_id=None):
    """MUL ids fielded by a faction in an era.

    With only a faction, this is the union across all eras. With only an era, the
    union across all factions. With neither, ``None`` means "no restriction".
    """
    if faction_id is None and era_id is None:
        return None
    clauses, params = [], []
    if faction_id is not None:
        clauses.append("faction_id = ?")
        params.append(faction_id)
    if era_id is not None:
        clauses.append("era_id = ?")
        params.append(era_id)
    ids = set()
    for row in conn.execute(
        f"SELECT mul_ids FROM availability WHERE {' AND '.join(clauses)}", params
    ):
        ids.update(json.loads(row["mul_ids"]))
    return ids
