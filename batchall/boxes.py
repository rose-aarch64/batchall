"""Box catalog: official boxes and ForcePacks from BattleTechWiki (Sarna), matched to MekBay units.

The wiki is fan-maintained, so every box keeps its source URL, and any miniature that cannot be
matched to exactly one unit goes through a review step instead of being guessed. See
docs/box-catalog-design.md for the matching rules.
"""

import json
import re
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from pathlib import Path

from . import collection, config

SARNA = "https://www.sarna.net"
LIST_PATH = "/wiki/ForcePack"

# Core boxed sets have no machine-readable list on the wiki, so they are named here.
CORE_BOXES = (
    ("BattleTech: A Game of Armored Combat", "/wiki/BattleTech:_A_Game_of_Armored_Combat"),
    ("BattleTech Beginner Box", "/wiki/BattleTech:_Beginner_Box"),
    ("BattleTech: Clan Invasion", "/wiki/BattleTech:_Clan_Invasion"),
    ("BattleTech: Mercenaries", "/wiki/BattleTech:_Mercenaries"),
)

RESOLVED = ("resolved", "approx")
NUMBER_WORDS = {
    w: n
    for n, w in enumerate(
        "zero one two three four five six seven eight nine ten eleven twelve".split()
    )
}
MAX_MINI_TEXT = 70  # longer list entries are prose notes, not miniature names


class BoxError(ValueError):
    pass


def tokens(text):
    """Lowercase alphanumeric tokens, so '(Loki) Hellbringer Prime' equals 'Hellbringer (Loki) Prime'."""
    return frozenset(t for t in re.split(r"[^a-z0-9]+", text.lower()) if t)


# --- fetching ----------------------------------------------------------------------------


class Fetcher:
    """Downloads wiki pages politely and caches the raw HTML so re-parsing needs no network."""

    def __init__(self, cache_dir=None, delay=0.4, refresh=False):
        self.cache_dir = Path(cache_dir or config.BOX_PAGES_DIR)
        self.delay = delay
        self.refresh = refresh

    def get(self, path):
        name = re.sub(r"[^A-Za-z0-9]+", "_", urllib.parse.unquote(path)).strip("_") + ".html"
        target = self.cache_dir / name
        if target.exists() and not self.refresh:
            return target.read_text(encoding="utf-8")
        request = urllib.request.Request(SARNA + path, headers={"User-Agent": config.USER_AGENT})
        with urllib.request.urlopen(request, timeout=40) as response:
            body = response.read().decode("utf-8", errors="replace")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
        time.sleep(self.delay)
        return body


# --- parsing -----------------------------------------------------------------------------


def parse_pack_list(html_text):
    """Return ``[{"section", "path", "name"}]`` from the wiki's list of ForcePacks."""
    start = html_text.find('id="Released_and_Announced_ForcePacks"')
    end = html_text.find('id="References"')
    if start < 0 or end < start:
        raise BoxError("could not find the ForcePack list on the page; the wiki layout may have changed")
    region = html_text[start:end]
    headings = [
        (m.start(), m.group(1))
        for m in re.finditer(r'<h3><span class="mw-headline" id="([^"]+)"', region)
    ]
    packs, seen = [], set()
    for m in re.finditer(
        r'<li><a href="(/wiki/(?!index\.php|Special|Category)[^"#]+)"[^>]*>([^<]+)</a>', region
    ):
        path = unescape(m.group(1))
        if path in seen:
            continue
        seen.add(path)
        section = ""
        for pos, ident in headings:
            if pos < m.start():
                section = ident
        packs.append(
            {
                "section": section.replace("_", " ").replace(" ForcePacks", "").strip(),
                "path": path,
                "name": unescape(m.group(2)).strip(),
            }
        )
    return packs


class _ListParser(HTMLParser):
    """Builds a tree of nested list items, each with its own text and its children.

    Some pages nest bullets as <ul><li>, others as <dl><dd>, so both count as items.
    """

    ITEM_TAGS = ("li", "dd")
    NAME_TAGS = ("a", "i", "b", "em", "strong")

    def __init__(self):
        super().__init__()
        self.roots, self._stack = [], []

    def handle_starttag(self, tag, attrs):
        if tag in self.ITEM_TAGS:
            self._stack.append({"text": [], "children": []})
        elif tag in self.NAME_TAGS and self._stack:
            # Some pages omit the space before a linked name ("BNC-12S<i><a>Banshee</a></i>").
            text = self._stack[-1]["text"]
            if text and text[-1][-1:].isalnum():
                text.append(" ")

    def handle_endtag(self, tag):
        if tag in self.ITEM_TAGS and self._stack:
            node = self._stack.pop()
            node["text"] = re.sub(r"\s+", " ", unescape("".join(node["text"]))).strip()
            (self._stack[-1]["children"] if self._stack else self.roots).append(node)

    def handle_data(self, data):
        if self._stack:
            self._stack[-1]["text"].append(data)


def _walk(nodes):
    for node in nodes:
        yield node
        yield from _walk(node["children"])


_NUM = r"(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|\d+)"
_POINTS = re.compile(
    rf"^(?P<n>{_NUM})\s+Points?\s*(?:\([^)]*\)\s*)?of\s+(?P<name>[^(]+?)\s*(?:\((?P<note>[^)]*)\))?\s*$",
    re.I,
)
_QTY_PREFIX = re.compile(r"^\((\d+)\)\s*(.+)$")


def _number(word):
    word = word.lower()
    return int(word) if word.isdigit() else NUMBER_WORDS.get(word, 1)


def _clean_mini(text):
    """Split a list entry into (name, quantity, note)."""
    qty = 1
    text = text.strip()
    m = _QTY_PREFIX.match(text)
    if m:
        qty, text = int(m.group(1)), m.group(2)
    else:
        words = re.match(rf"^({_NUM})\s+(.+)$", text, re.I)  # "two Galleon Light Tanks"
        if words:
            qty, text = _number(words.group(1)), words.group(2)
            if text.lower().endswith("s") and not text.lower().endswith("ss"):
                text = text[:-1]
    note = ""
    if "," in text:
        text, note = (part.strip() for part in text.split(",", 1))
    m = re.search(r"\s*\((with [^)]*)\)\s*$", text, re.I)
    if m:
        note = f"{note}; {m.group(1)}" if note else m.group(1)
        text = text[: m.start()]
    return text.strip(), max(1, qty), note


def parse_contents(html_text):
    """Parse a box page's Contents section into minis and Alpha Strike card variants.

    Returns ``{"minis": [{"name", "qty", "note"}], "cards": [str]}`` or None when the page has no
    Contents section.
    """
    m = re.search(r'<span class="mw-headline" id="Contents">', html_text)
    if not m:
        return None
    rest = html_text[m.end():]
    nxt = re.search(r"<h2", rest)
    parser = _ListParser()
    parser.feed(rest[: nxt.start()] if nxt else rest)

    container = next(
        (n for n in _walk(parser.roots) if "miniature" in n["text"].lower() and n["children"]), None
    )
    minis, first_sides, other_sides = [], [], []
    if container:
        for child in container["children"]:
            raw = child["text"]
            if not raw or len(raw) > MAX_MINI_TEXT:
                continue
            name, qty, note = _clean_mini(raw)
            if name:
                minis.append({"name": name, "qty": qty, "note": note})
    for node in _walk(parser.roots):
        if node is container:
            continue
        points = _POINTS.match(node["text"])
        if points:
            minis.append(
                {
                    "name": points.group("name").strip(),
                    "qty": _number(points.group("n")),
                    "note": (points.group("note") or "").strip(),
                }
            )
        elif "/" in node["text"] and not node["children"]:
            sides = [s.strip() for s in node["text"].split("/")]
            first_sides.append(sides[0])
            other_sides.extend(s for s in sides[1:] if s)
    # Cards are double-sided. The first side is the default variant, so it is tried first;
    # the other side only helps a mini that has no first-side card.
    return {"minis": minis, "cards": first_sides + other_sides}


# --- matching ----------------------------------------------------------------------------


def _public(unit):
    return {k: unit[k] for k in ("uuid", "name", "model", "kind", "tons", "bv", "tech_base")}


class UnitIndex:
    """Lookup tables over the unit catalog, keyed by token sets."""

    def __init__(self, conn):
        self.units = []
        self.by_full = defaultdict(list)
        self.by_chassis = defaultdict(list)
        for row in conn.execute(
            "SELECT uuid, chassis, model, kind, tons, bv, tech_base FROM units"
        ):
            unit = dict(row)
            unit["name"] = f"{unit['chassis']} {unit['model']}".strip()
            unit["_tokens"] = tokens(unit["name"])
            unit["_chassis_tokens"] = tokens(unit["chassis"])
            self.units.append(unit)
            self.by_full[unit["_tokens"]].append(unit)
            self.by_chassis[unit["_chassis_tokens"]].append(unit)


def _is_standard(unit):
    return tokens(unit["model"]) in (frozenset(), frozenset({"standard"}))


def _resolve_card(index, card):
    """Return ``(status, unit)`` for a card variant name, or ``(None, None)``."""
    hits = index.by_full.get(tokens(card), [])
    if len(hits) == 1:
        return "resolved", hits[0]
    if not hits:
        trimmed = re.sub(r"\s*\([^()]*\)\s*$", "", card).strip()
        if trimmed != card:
            hits = index.by_full.get(tokens(trimmed), [])
            if len(hits) == 1:
                return "approx", hits[0]
        standard = [u for u in index.by_chassis.get(tokens(card), []) if _is_standard(u)]
        if len(standard) == 1:
            return "resolved", standard[0]
    return None, None


def card_unit_uuids(index, mini_name, cards):
    """Every unit named by a card variant for this mini, on either side of the card.

    These are the variants the box actually ships a card for, so the variant picker lists them first.
    """
    mini_tokens = tokens(mini_name)
    found = []
    if not mini_tokens:
        return found
    for card in cards:
        if mini_tokens <= tokens(card):
            _, unit = _resolve_card(index, card)
            if unit and unit["uuid"] not in found:
                found.append(unit["uuid"])
    return found


def match_item(index, mini_name, cards):
    """Return ``(status, unit_or_None, card_variant)`` for one miniature.

    Statuses: resolved, approx, chassis_only (the chassis is known but not the variant) and
    no_match. Only resolved and approx carry a unit.
    """
    mini_tokens = tokens(mini_name)
    card = next((c for c in cards if mini_tokens and mini_tokens <= tokens(c)), "")
    if card:
        status, unit = _resolve_card(index, card)
        if unit:
            return status, unit, card
    else:
        same_chassis = index.by_chassis.get(mini_tokens, [])
        if len(same_chassis) == 1:
            return "resolved", same_chassis[0], ""
    status = "chassis_only" if index.by_chassis.get(mini_tokens) else "no_match"
    return status, None, card


def candidates(index, mini_name, card="", limit=12):
    """Units the user could pick for a miniature that did not match on its own."""
    mini_tokens, card_tokens = tokens(mini_name), (tokens(card) if card else frozenset())
    pool = index.by_chassis.get(mini_tokens) or (index.by_chassis.get(card_tokens) if card_tokens else None) or []
    if not pool and mini_tokens:
        pool = [u for u in index.units if mini_tokens <= u["_tokens"]]
    if not pool:
        first = re.split(r"[^a-z0-9]+", mini_name.lower())[0]
        if first:
            pool = [u for u in index.units if first in u["_chassis_tokens"]]
    pool = sorted(pool, key=lambda u: (not (card_tokens and card_tokens <= u["_tokens"]), u["name"]))
    return [_public(u) for u in pool[:limit]]


# --- import ------------------------------------------------------------------------------


def import_boxes(conn, fetcher=None, progress=None):
    """Rebuild the box catalog from the wiki. Collection rows are never touched."""
    if conn.execute("SELECT COUNT(*) FROM units").fetchone()[0] == 0:
        raise BoxError("load the unit catalog first: python3 -m batchall catalog import")
    fetcher = fetcher or Fetcher()
    say = progress or (lambda message: None)
    index = UnitIndex(conn)

    entries, seen = [], set()
    for name, path in CORE_BOXES:
        entries.append(("core", "Core box", name, path))
        seen.add(path)
    say("reading the ForcePack list")
    for pack in parse_pack_list(fetcher.get(LIST_PATH)):
        if pack["path"] not in seen:
            seen.add(pack["path"])
            entries.append(("forcepack", pack["section"], pack["name"], pack["path"]))

    boxes, skipped, failed = [], [], []
    for number, (kind, section, name, path) in enumerate(entries, 1):
        say(f"[{number}/{len(entries)}] {name}")
        try:
            parsed = parse_contents(fetcher.get(path))
        except (OSError, ValueError) as exc:
            failed.append({"name": name, "error": str(exc)})
            continue
        if not parsed or not parsed["minis"]:
            skipped.append(name)
            continue
        items = []
        for position, mini in enumerate(parsed["minis"]):
            status, unit, card = match_item(index, mini["name"], parsed["cards"])
            on_card = card_unit_uuids(index, mini["name"], parsed["cards"])
            items.append(
                (
                    position,
                    mini["name"],
                    card,
                    mini["qty"],
                    unit["uuid"] if unit else None,
                    status,
                    mini["note"],
                    json.dumps(on_card),
                )
            )
        boxes.append((kind, section, name, SARNA + path, items))

    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with conn:
        conn.execute("DELETE FROM box_items")
        conn.execute("DELETE FROM boxes")
        for kind, section, name, url, items in boxes:
            box_id = conn.execute(
                "INSERT INTO boxes (name, kind, section, source_url, imported_at) VALUES (?,?,?,?,?)",
                (name, kind, section, url, stamp),
            ).lastrowid
            conn.executemany(
                "INSERT INTO box_items (box_id, position, mini_name, card_variant, qty, unit_uuid, status, "
                "note, card_units) VALUES (?,?,?,?,?,?,?,?,?)",
                [(box_id, *item) for item in items],
            )
    counts = Counter(item[5] for _, _, _, _, items in boxes for item in items)  # item[5] is the status
    return {
        "boxes": len(boxes),
        "items": sum(counts.values()),
        "by_status": dict(counts),
        "skipped_no_minis": skipped,
        "failed": failed,
    }


# --- reading -----------------------------------------------------------------------------


def search_boxes(conn, query="", limit=15):
    """Boxes whose name contains every word in ``query``. Core boxes sort first."""
    clauses, params = [], []
    for term in (query or "").split():
        clauses.append("b.name LIKE ? ESCAPE '\\'")
        params.append(collection._like(term))
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = conn.execute(
        f"""
        SELECT b.id, b.name, b.kind, b.section, b.source_url,
               COUNT(i.id) AS items, COALESCE(SUM(i.qty), 0) AS minis,
               COALESCE(SUM(CASE WHEN i.status IN ('resolved', 'approx') THEN 1 ELSE 0 END), 0) AS matched
        FROM boxes b LEFT JOIN box_items i ON i.box_id = b.id
        {where}
        GROUP BY b.id ORDER BY (b.kind != 'core'), b.name LIMIT ?
        """,
        (*params, int(limit)),
    ).fetchall()
    return [dict(r) for r in rows]


def for_unit(conn, unit_uuid):
    """Official boxes tied to one unit, for suggesting a box label.

    ``relation`` is ``mini`` when the box ships a miniature of exactly this variant, or ``card`` when it
    only ships an Alpha Strike card for it. Boxes with the mini come first, then boxes by name.
    """
    found = {}
    rows = conn.execute(
        """
        SELECT b.id, b.name, b.kind, i.unit_uuid, i.card_units
        FROM boxes b JOIN box_items i ON i.box_id = b.id
        WHERE i.unit_uuid = ? OR i.card_units LIKE ?
        """,
        (unit_uuid, f"%{unit_uuid}%"),  # a loose prefilter. The JSON check below is exact
    )
    for row in rows:
        if row["unit_uuid"] == unit_uuid:
            relation = "mini"
        elif unit_uuid in json.loads(row["card_units"] or "[]"):
            relation = "card"
        else:
            continue
        entry = found.setdefault(row["id"], {"id": row["id"], "name": row["name"], "kind": row["kind"], "relation": relation})
        if relation == "mini":
            entry["relation"] = "mini"
    return sorted(found.values(), key=lambda b: (b["relation"] != "mini", b["kind"] != "core", b["name"]))


def get_box(conn, box_id):
    box = conn.execute("SELECT * FROM boxes WHERE id = ?", (box_id,)).fetchone()
    if box is None:
        raise BoxError(f"no box with id {box_id}")
    items = []
    for row in conn.execute(
        """
        SELECT i.*, u.chassis, u.model, u.kind AS unit_kind, u.tons, u.bv, u.tech_base
        FROM box_items i LEFT JOIN units u ON u.uuid = i.unit_uuid
        WHERE i.box_id = ? ORDER BY i.position
        """,
        (box_id,),
    ):
        d = dict(row)
        unit = None
        if d["unit_uuid"] and d["chassis"] is not None:
            unit = {
                "uuid": d["unit_uuid"],
                "name": f"{d['chassis']} {d['model']}".strip(),
                "kind": d["unit_kind"],
                "tons": d["tons"],
                "bv": d["bv"],
                "tech_base": d["tech_base"],
            }
        items.append(
            {
                "id": d["id"],
                "mini_name": d["mini_name"],
                "card_variant": d["card_variant"],
                "qty": d["qty"],
                "status": d["status"],
                "note": d["note"],
                "unit": unit,
            }
        )
    result = dict(box)
    result["items"] = items
    return result


# --- adding a box to the collection --------------------------------------------------------


def add_box(conn, box_id, only=None):
    """Add a box's minis to the collection.

    With ``only=None`` every item is processed: matched items are added and the rest come back in
    ``review`` with candidates. With ``only={item_id: unit_uuid | None}`` just those items are
    processed, so a second call after review never adds the matched items again.
    """
    box = get_box(conn, box_id)
    by_id = {item["id"]: item for item in box["items"]}
    index = None
    plan, review, skipped = [], [], []

    if only is not None:
        choices = {}
        for key, value in only.items():
            item_id = int(key)
            if item_id not in by_id:
                raise BoxError(f"item {item_id} is not in box {box['name']!r}")
            choices[item_id] = value
        for item_id, choice in choices.items():
            if choice is None:
                skipped.append(by_id[item_id]["mini_name"])
            else:
                plan.append((by_id[item_id], choice))
    else:
        for item in box["items"]:
            if item["status"] in RESOLVED and item["unit"]:
                plan.append((item, item["unit"]["uuid"]))
            else:
                index = index or UnitIndex(conn)
                review.append(
                    {
                        "item_id": item["id"],
                        "mini_name": item["mini_name"],
                        "card_variant": item["card_variant"],
                        "qty": item["qty"],
                        "status": item["status"],
                        "note": item["note"],
                        "candidates": candidates(index, item["mini_name"], item["card_variant"]),
                    }
                )

    for _, uuid in plan:  # validate everything before writing anything
        if conn.execute("SELECT 1 FROM units WHERE uuid = ?", (uuid,)).fetchone() is None:
            raise BoxError(f"unit {uuid!r} is not in the catalog")

    added = []
    for item, uuid in plan:
        entry = collection.add(conn, uuid, qty=item["qty"], box=box["name"])
        added.append(
            {
                "collection_id": entry["id"],
                "name": entry["name"],
                "qty": item["qty"],
                "status": item["status"],
                "approx": item["status"] == "approx" and only is None,
            }
        )
    return {
        "box": {"id": box["id"], "name": box["name"], "source_url": box["source_url"]},
        "added": added,
        "skipped": skipped,
        "review": review,
    }
