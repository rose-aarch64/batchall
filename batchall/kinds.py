"""Unit kinds used for combined-arms mixes.

MekBay reports a broad ``type`` and a finer ``subtype``. The builder only cares
about the handful of kinds that actually go on a tabletop force.
"""

KINDS = ("mech", "vehicle", "ba", "infantry", "proto", "aero")

KIND_LABELS = {
    "mech": "BattleMech",
    "vehicle": "Vehicle",
    "ba": "Battle Armor",
    "infantry": "Infantry",
    "proto": "ProtoMech",
    "aero": "Aerospace Fighter",
}

KIND_ALIASES = {
    "mechs": "mech",
    "battlemech": "mech",
    "battlemechs": "mech",
    "vehicles": "vehicle",
    "tank": "vehicle",
    "tanks": "vehicle",
    "vtol": "vehicle",
    "vtols": "vehicle",
    "battle-armor": "ba",
    "battlearmor": "ba",
    "armor": "ba",
    "foot": "infantry",
    "protomech": "proto",
    "protomechs": "proto",
    "protomek": "proto",
    "fighter": "aero",
    "fighters": "aero",
    "aerospace": "aero",
}


def normalize_kind(text):
    """Return a canonical kind for a user-supplied word, or raise ValueError."""
    key = str(text).strip().lower()
    key = KIND_ALIASES.get(key, key)
    if key not in KINDS:
        raise ValueError(f"unknown unit kind {text!r}; expected one of {', '.join(KINDS)}")
    return key


def classify(unit_type, subtype):
    """Map a MekBay type/subtype pair to a kind, or None if it is not a playable force unit."""
    unit_type = unit_type or ""
    subtype = subtype or ""
    if unit_type == "Mek":
        return "mech"
    if unit_type in ("Tank", "VTOL"):
        return "vehicle"
    if unit_type == "Infantry":
        return "ba" if subtype == "Battle Armor" else "infantry"
    if unit_type == "ProtoMek":
        return "proto"
    if unit_type == "Aero" and "Fighter" in subtype:
        return "aero"
    return None
