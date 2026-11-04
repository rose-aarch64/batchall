"""Unit catalog: download MekBay's public data files and load them into SQLite.

MekBay serves ``units.json``, ``factions.json`` and ``eras.json`` from
``db.mekbay.com``. Faction availability is a list of MUL ids per faction and era,
and those ids match the ``mul1id`` field on units.
"""

import gzip
import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from . import config, details
from .db import get_meta, set_meta
from .kinds import classify


def _fetch(url, timeout=300):
    request = urllib.request.Request(
        url, headers={"Accept-Encoding": "gzip", "User-Agent": config.USER_AGENT}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read()
        if response.headers.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
    return body


def download(dest_dir=None, base_url=None, progress=None):
    """Download the three catalog files into ``dest_dir`` and return the directory."""
    dest = Path(dest_dir or config.CATALOG_DIR)
    dest.mkdir(parents=True, exist_ok=True)
    base = (base_url or config.MEKBAY_DB_URL).rstrip("/")
    for name in config.CATALOG_FILES:
        if progress:
            progress(f"downloading {name}.json")
        body = _fetch(f"{base}/{name}.json")
        json.loads(body)  # fail before overwriting a good cache with a broken download
        (dest / f"{name}.json").write_bytes(body)
    # The lore file is large and only feeds the detail panel, so a failure here must not sink the import.
    fluff = config.FLUFF_FILE
    if progress:
        progress(f"downloading {fluff}.json (large)")
    try:
        body = _fetch(f"{base}/{fluff}.json")
        json.loads(body)
        (dest / f"{fluff}.json").write_bytes(body)
    except (OSError, ValueError) as exc:
        if progress:
            progress(f"skipping lore: {exc}")
    return dest


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _art_path(unit):
    """The unit's artwork path on MekBay's host, or None. Paths that could escape the art folder are dropped."""
    fluff = unit.get("fluff")
    path = fluff.get("img") if isinstance(fluff, dict) else None
    if not isinstance(path, str) or not path.strip():
        return None
    path = path.strip()
    if path.startswith("/") or "\\" in path or ".." in path.split("/"):
        return None
    return path


def load(conn, source_dir=None):
    """Replace the catalog tables with the contents of the downloaded files."""
    src = Path(source_dir or config.CATALOG_DIR)
    units_doc = json.loads((src / "units.json").read_text(encoding="utf-8"))
    factions_doc = json.loads((src / "factions.json").read_text(encoding="utf-8"))
    eras_doc = json.loads((src / "eras.json").read_text(encoding="utf-8"))

    fluff_path = src / f"{config.FLUFF_FILE}.json"
    fluff = {}
    if fluff_path.exists():
        fluff = json.loads(fluff_path.read_text(encoding="utf-8")).get("fluff") or {}

    unit_rows = []
    detail_rows = []
    for u in units_doc["units"]:
        kind = classify(u.get("type"), u.get("subtype"))
        bv = _int_or_none(u.get("bv"))
        if kind is None or not bv or bv <= 0 or not u.get("uuid"):
            continue
        detail_rows.append(
            (
                u["uuid"],
                json.dumps(details.extract_specs(u)),
                json.dumps(details.extract_lore(fluff.get(u.get("name")))),
            )
        )
        unit_rows.append(
            (
                u["uuid"],
                _int_or_none(u.get("mul1id")),
                u.get("chassis") or "",
                u.get("model") or "",
                kind,
                u.get("type"),
                u.get("subtype"),
                u.get("tons"),
                u.get("weightClass"),
                bv,
                _int_or_none(u.get("pv")),
                _int_or_none(u.get("year")),
                u.get("techBase"),
                u.get("role"),
                _int_or_none(u.get("squadSize")),
                _int_or_none(u.get("walk")),
                _int_or_none(u.get("jump")),
                _art_path(u),
            )
        )

    faction_rows = [(f["id"], f["name"], f.get("group")) for f in factions_doc["factions"]]
    availability_rows = []
    for f in factions_doc["factions"]:
        for era_id, mul_ids in (f.get("eras") or {}).items():
            availability_rows.append((f["id"], int(era_id), json.dumps(mul_ids)))
    era_rows = [
        (e["id"], e["name"], (e.get("years") or {}).get("from"), (e.get("years") or {}).get("to"))
        for e in eras_doc["eras"]
    ]

    with conn:
        for table in ("units", "factions", "eras", "availability", "unit_details"):
            conn.execute(f"DELETE FROM {table}")
        conn.executemany("INSERT INTO unit_details (uuid, specs, lore) VALUES (?,?,?)", detail_rows)
        conn.executemany(
            "INSERT OR REPLACE INTO units (uuid, mul_id, chassis, model, kind, type, subtype, tons, "
            "weight_class, bv, pv, year, tech_base, role, squad_size, walk, jump, art) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            unit_rows,
        )
        conn.executemany("INSERT INTO factions (id, name, grp) VALUES (?,?,?)", faction_rows)
        conn.executemany(
            "INSERT INTO eras (id, name, year_from, year_to) VALUES (?,?,?,?)", era_rows
        )
        conn.executemany(
            "INSERT INTO availability (faction_id, era_id, mul_ids) VALUES (?,?,?)",
            availability_rows,
        )
        set_meta(conn, "catalog_units_version", units_doc.get("version", ""))
        set_meta(conn, "catalog_imported_at", datetime.now(timezone.utc).isoformat(timespec="seconds"))
    return {"units": len(unit_rows), "factions": len(faction_rows), "eras": len(era_rows)}


def status(conn):
    return {
        "units": conn.execute("SELECT COUNT(*) FROM units").fetchone()[0],
        "factions": conn.execute("SELECT COUNT(*) FROM factions").fetchone()[0],
        "eras": conn.execute("SELECT COUNT(*) FROM eras").fetchone()[0],
        "version": get_meta(conn, "catalog_units_version"),
        "imported_at": get_meta(conn, "catalog_imported_at"),
    }


def _escape_like(term):
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def search(conn, query="", kind=None, limit=25):
    """Search the catalog. Every whitespace-separated term must appear in chassis + model."""
    clauses, params = [], []
    for term in (query or "").split():
        clauses.append("(u.chassis || ' ' || u.model) LIKE ? ESCAPE '\\'")
        params.append(f"%{_escape_like(term)}%")
    if kind:
        clauses.append("u.kind = ?")
        params.append(kind)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = conn.execute(
        f"SELECT u.* FROM units u {where} ORDER BY u.chassis, u.model LIMIT ?",
        (*params, int(limit)),
    ).fetchall()
    return [unit_dict(r) for r in rows]


def unit_dict(row):
    d = dict(row)
    d["name"] = f"{d['chassis']} {d['model']}".strip()
    if "art" in d:  # callers only need to know whether art exists; the path stays internal
        d["has_art"] = bool(d.pop("art"))
    return d
