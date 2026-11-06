"""Shared fixtures: a small synthetic catalog in an in-memory database."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from batchall import db  # noqa: E402

# uuid, mul_id, chassis, model, kind, tons, bv, role, tech_base
UNITS = [
    ("u-atlas", 1, "Atlas", "AS7-D", "mech", 100, 1897, "Juggernaut", "Inner Sphere"),
    ("u-locust", 2, "Locust", "LCT-1V", "mech", 20, 432, "Scout", "Inner Sphere"),
    ("u-wolfhound", 3, "Wolfhound", "WLF-2", "mech", 35, 1148, "Striker", "Inner Sphere"),
    ("u-shadow", 4, "Shadow Hawk", "SHD-2H", "mech", 55, 1063, "Skirmisher", "Inner Sphere"),
    ("u-timber", 5, "Timber Wolf", "Prime", "mech", 75, 2737, "Brawler", "Clan"),
    ("u-condor", 6, "Condor", "Heavy Tank", "vehicle", 50, 700, "Brawler", "Inner Sphere"),
    ("u-scorpion", 7, "Scorpion", "Light Tank", "vehicle", 25, 420, "Scout", "Inner Sphere"),
    ("u-elemental", 8, "Elemental", "Laser", "ba", 1, 230, "Ambusher", "Clan"),
    ("u-marauder", 9, "Marauder", "MAD-3R", "mech", 75, 1500, "Sniper", "Inner Sphere"),
    ("u-nomul", None, "Mystery", "Prototype", "mech", 40, 900, "Striker", "Inner Sphere"),
]


def make_db():
    conn = db.connect(":memory:")
    seed_catalog(conn)
    return conn


def seed_catalog(conn):
    """Insert the synthetic units, factions, eras and availability into a connection."""
    with conn:
        for uuid, mul, chassis, model, kind, tons, bv, role, tech in UNITS:
            conn.execute(
                "INSERT INTO units (uuid, mul_id, chassis, model, kind, type, subtype, tons, "
                "weight_class, bv, pv, year, tech_base, role) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (uuid, mul, chassis, model, kind, "t", "s", tons, "w", bv, 10, 3025, tech, role),
            )
        conn.execute("INSERT INTO factions (id, name, grp) VALUES (1, 'Federated Suns', 'IS')")
        conn.execute("INSERT INTO factions (id, name, grp) VALUES (2, 'Clan Wolf', 'Clan')")
        conn.execute("INSERT INTO eras (id, name, year_from, year_to) VALUES (10, 'Star League', 2571, 2780)")
        conn.execute("INSERT INTO eras (id, name, year_from, year_to) VALUES (13, 'Clan Invasion', 3050, 3061)")
        conn.execute(
            "INSERT INTO availability VALUES (1, 13, ?)", (json.dumps([1, 2, 3, 4, 6, 7, 9]),)
        )
        conn.execute("INSERT INTO availability VALUES (2, 13, ?)", (json.dumps([5, 8]),))
        conn.execute("INSERT INTO availability VALUES (1, 10, ?)", (json.dumps([1]),))


def own(conn, uuid, qty=1, painted=0):
    from batchall import collection

    return collection.add(conn, uuid, qty=qty, painted=painted)


def name_mini(conn, entry_id, note="", tag="", faction="", company="", painted=False, box="", material=""):
    """Give one of an entry's minis the details given and return it.

    Picks a mini that has no details yet, an unpainted one first, so paint already on the other minis
    stays where it is. Fails if every mini already has details.
    """
    from batchall import collection, copies

    def bare(m):
        return not (m["note"] or m["tag"] or m["faction"] or m["company"] or m["material"] or m["box"])

    free = [m for m in collection.get(conn, entry_id)["minis"] if bare(m)]
    assert free, f"every mini of entry {entry_id} already has details"
    mini = min(free, key=lambda m: (m["painted"], m["id"]))
    return copies.update(
        conn, mini["id"], note=note, tag=tag, faction=faction, company=company, painted=painted, box=box,
        material=material,
    )
