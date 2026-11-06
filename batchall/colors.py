"""Company strip colors: the color of the strip on a card, chosen per company.

A company without a color keeps the default crimson, which the page supplies, so nothing is stored for it.
Companies are matched by name ignoring case and outer space, the same way their logos are.
"""

import re

HEX_RE = re.compile(r"#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})")


class ColorError(ValueError):
    pass


def key_of(name):
    return (name or "").strip().lower()


def normalize(value):
    """``#RRGGBB`` in capitals from ``#rgb``, ``#rrggbb`` or the same without the hash. Anything else is refused."""
    match = HEX_RE.fullmatch(value.strip()) if isinstance(value, str) else None
    if match is None:
        raise ColorError("color must be a hex value like #8A1C2B")
    digits = match.group(1).upper()
    if len(digits) == 3:
        digits = "".join(d * 2 for d in digits)
    return "#" + digits


def save(conn, name, color):
    if not isinstance(name, str) or not key_of(name):
        raise ColorError("a company name is required")
    color = normalize(color)
    with conn:
        conn.execute(
            "INSERT INTO company_colors (key, name, color) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET name = excluded.name, color = excluded.color",
            (key_of(name), name.strip(), color),
        )
    return {"key": key_of(name), "name": name.strip(), "color": color}


def list_all(conn):
    rows = conn.execute("SELECT key, name, color FROM company_colors ORDER BY key").fetchall()
    return [{"key": r["key"], "name": r["name"], "color": r["color"]} for r in rows]


def remove(conn, name):
    with conn:
        cursor = conn.execute("DELETE FROM company_colors WHERE key = ?", (key_of(name),))
    return cursor.rowcount > 0
