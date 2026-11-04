"""Per-unit detail for the click-through panel: specs from units.json and lore from MekBay's fluff file.

Lore arrives as HTML. It is reduced to plain-text paragraphs here so the web UI never has to
render markup from a download.
"""

import json
from html.parser import HTMLParser

LORE_SECTIONS = ("overview", "capabilities", "deployment", "history", "notes")
LORE_CREDITS = ("manufacturer", "primaryFactory")

_BREAKS = {"p", "br", "li", "ul", "ol", "div", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "table"}
_SKIPPED = {"script", "style"}


class _Paragraphs(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.paragraphs = []
        self._words = []
        self._skip = 0

    def _flush(self):
        text = " ".join("".join(self._words).split())
        if text:
            self.paragraphs.append(text)
        self._words = []

    def handle_starttag(self, tag, attrs):
        if tag in _SKIPPED:
            self._skip += 1
        elif tag in _BREAKS:
            self._flush()

    def handle_endtag(self, tag):
        if tag in _SKIPPED:
            self._skip = max(0, self._skip - 1)
        elif tag in _BREAKS:
            self._flush()

    def handle_data(self, data):
        if not self._skip:
            self._words.append(data)


def html_to_paragraphs(html):
    """Plain-text paragraphs from an HTML fragment. Anything that is not a string gives no paragraphs."""
    if not isinstance(html, str) or not html.strip():
        return []
    parser = _Paragraphs()
    parser.feed(html)
    parser.close()
    parser._flush()
    return parser.paragraphs


def _int(value):
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _weapons(comp):
    """Entries that have a range are weapons. Identical weapons in one location collapse into one row."""
    rows = {}
    for item in comp if isinstance(comp, list) else []:
        if not isinstance(item, dict) or not item.get("r"):
            continue
        key = (item.get("n"), item.get("l"), item.get("d"), item.get("r"))
        qty = _int(item.get("q")) or 1
        if key in rows:
            rows[key]["qty"] += qty
        else:
            rows[key] = {"name": key[0], "location": key[1], "qty": qty, "damage": key[2], "range": key[3]}
    return list(rows.values())


def extract_specs(unit):
    """The fields the detail panel shows, taken from one record of MekBay's units.json."""
    quirks = unit.get("quirks")
    return {
        "movement": {
            "walk": _int(unit.get("walk")),
            "run": _int(unit.get("run")),
            "jump": _int(unit.get("jump")),
            "type": unit.get("moveType"),
        },
        "armor": {"points": _int(unit.get("armor")), "type": unit.get("armorType")},
        "structure": {"type": unit.get("structureType"), "internal": _int(unit.get("internal"))},
        "engine": {"type": unit.get("engine"), "rating": _int(unit.get("engineRating"))},
        "heat": {"generated": _int(unit.get("heat")), "dissipation": _int(unit.get("dissipation"))},
        "weapons": _weapons(unit.get("comp")),
        "quirks": [q for q in quirks if isinstance(q, str)] if isinstance(quirks, list) else [],
        "tech_rating": unit.get("techRating"),
        "level": unit.get("level"),
        "cost": _int(unit.get("cost")),
        "crew": _int(unit.get("crewSize")),
    }


def extract_lore(entry):
    """Lore sections as paragraph lists, plus the manufacturer credits. Empty sections are left out."""
    if not isinstance(entry, dict):
        return {}
    lore = {}
    for key in LORE_SECTIONS:
        paragraphs = html_to_paragraphs(entry.get(key))
        if paragraphs:
            lore[key] = paragraphs
    for key in LORE_CREDITS:
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            lore[key] = value.strip()
    return lore


def get(conn, uuid):
    """One unit's basics, specs and lore, or None when the unit is not in the catalog."""
    from .catalog import unit_dict  # imported here because catalog imports this module

    row = conn.execute("SELECT * FROM units WHERE uuid = ?", (uuid,)).fetchone()
    if row is None:
        return None
    unit = unit_dict(row)
    stored = conn.execute("SELECT specs, lore FROM unit_details WHERE uuid = ?", (uuid,)).fetchone()
    unit["has_details"] = stored is not None
    unit["specs"] = json.loads(stored["specs"]) if stored else {}
    unit["lore"] = json.loads(stored["lore"]) if stored else {}
    return unit
