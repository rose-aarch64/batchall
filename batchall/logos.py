"""Company logos: found on Sarna (the BattleTech wiki), confirmed by the user, then stored.

A company is free text on a mini, so MekBay has no logo for most of them. Sarna has a page for many
units and a lead image on that page. ``candidates`` looks the name up and returns the pages it found
with their lead image, and nothing is saved until the user picks one and ``save`` is called. The image
is kept in the database so it is downloaded once.

The images belong to their publishers. They are kept for personal viewing, and the database file is
ignored by git so they are not redistributed.
"""

import json
import re
import urllib.parse
import urllib.request
from urllib.parse import urlparse

from . import config

SARNA_API = "https://www.sarna.net/wiki/api.php"
SARNA_PAGE = "https://www.sarna.net/wiki/"
THUMB_SIZE = 256  # bitmaps come back scaled to this width; SVG logos come back as the SVG itself
MAX_CANDIDATES = 4
MAX_BYTES = 2_000_000
TIMEOUT_SECONDS = 30

SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


class LogoError(ValueError):
    pass


def key_of(name):
    """The key a company is stored under: its name without surrounding space, ignoring case."""
    return (name or "").strip().lower()


UNSAFE_SVG = re.compile(rb"<\s*(script|foreignObject|iframe|object|embed)|\bon\w+\s*=|javascript:|data:text/html|<!ENTITY", re.I)


def sniff(data):
    """The content type of PNG, JPEG, GIF or plain SVG bytes, or None for anything else.

    An SVG that holds script, event handlers or embedded documents is not accepted.
    """
    for signature, ctype in SIGNATURES:
        if data.startswith(signature):
            return ctype
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    head = data[:1000].lstrip(b"\xef\xbb\xbf \t\r\n")
    if (head.startswith(b"<?xml") or head.startswith(b"<svg") or head.startswith(b"<!--") or head.startswith(b"<!DOCTYPE svg")) \
            and b"<svg" in data[:5000] and not UNSAFE_SVG.search(data):
        return "image/svg+xml"
    return None


def safe_image_url(url):
    """Accept only an https image under Sarna's /wiki/images/ folder."""
    parsed = urlparse(url) if isinstance(url, str) else None
    if (
        parsed is None or parsed.scheme != "https" or not parsed.hostname
        or not (parsed.hostname == "sarna.net" or parsed.hostname.endswith(".sarna.net"))
        or not parsed.path.startswith("/wiki/images/") or ".." in parsed.path.split("/")
    ):
        raise LogoError(f"not a Sarna image address: {url!r}")
    return url


def _get(url, limit):
    request = urllib.request.Request(url, headers={"User-Agent": config.USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise LogoError("the image is unexpectedly large")
    return data


def fetch_json(url):
    return json.loads(_get(url, 1_000_000))


def fetch_image(url):
    return _get(safe_image_url(url), MAX_BYTES)


def _api_url(**params):
    return SARNA_API + "?" + urllib.parse.urlencode({"action": "query", "format": "json", "redirects": 1, **params})


def _pages(data):
    return list((data.get("query") or {}).get("pages", {}).values())


def candidates(name, fetch=None):
    """Sarna pages that look like ``name`` and have a lead image, best match first.

    Each is ``{"title", "page_url", "image_url"}``. The page with exactly that title comes first, then
    the top search hits. An empty list means Sarna has nothing with an image for that name.
    """
    fetch = fetch or fetch_json  # looked up at call time, so a test can swap the module's function
    name = (name or "").strip()
    if not name:
        raise LogoError("a company name is required")
    image = {"prop": "pageimages", "piprop": "thumbnail", "pithumbsize": THUMB_SIZE}
    exact = _pages(fetch(_api_url(titles=name, **image)))
    hits = _pages(fetch(_api_url(generator="search", gsrsearch=name, gsrnamespace=0, gsrlimit=MAX_CANDIDATES, **image)))
    hits.sort(key=lambda p: p.get("index", 0))  # the search generator numbers its hits best first
    found, seen = [], set()
    for page in exact + hits:
        thumb = (page.get("thumbnail") or {}).get("source")
        title = page.get("title")
        if not thumb or not title or title in seen:
            continue
        try:
            safe_image_url(thumb)
        except LogoError:
            continue
        seen.add(title)
        found.append({
            "title": title,
            "page_url": SARNA_PAGE + urllib.parse.quote(title.replace(" ", "_")),
            "image_url": thumb,
        })
    return found[:MAX_CANDIDATES]


def save(conn, name, image_url, source_title="", fetch=None):
    """Download the picked image and store it as the company's logo, replacing any earlier one."""
    fetch = fetch or fetch_image
    if not isinstance(name, str):
        raise LogoError("a company name is required")
    key = key_of(name)
    if not key:
        raise LogoError("a company name is required")
    data = fetch(safe_image_url(image_url))
    ctype = sniff(data)
    if ctype is None:
        raise LogoError("that file is not a plain PNG, JPEG, GIF or SVG image")
    with conn:
        conn.execute(
            "INSERT INTO company_logos (key, name, content_type, data, source_title, source_url, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(key) DO UPDATE SET name = excluded.name, content_type = excluded.content_type, "
            "data = excluded.data, source_title = excluded.source_title, source_url = excluded.source_url, "
            "updated_at = CURRENT_TIMESTAMP",
            (key, name.strip(), ctype, data, source_title or "", image_url),
        )
    return summarize(conn, key)


def summarize(conn, key):
    row = conn.execute(
        "SELECT key, name, content_type, source_title, updated_at, substr(data, 1, 26) AS head "
        "FROM company_logos WHERE key = ?", (key,)
    ).fetchone()
    return _summary(row) if row else None


def _is_opaque(content_type, head):
    """True for a logo with no see-through pixels: a JPEG, or a PNG whose color type has no alpha.

    The PNG color type is the byte at offset 25 of the file: 0 is gray and 2 is RGB, so neither can be
    transparent. Palette PNGs (3) can be, so they are left alone.
    """
    if content_type == "image/jpeg":
        return True
    head = bytes(head or b"")
    return content_type == "image/png" and len(head) >= 26 and head[25] in (0, 2)


def _summary(row):
    # ``opaque`` marks logos with a solid background, so the page can blend them into the card.
    return {
        "key": row["key"], "name": row["name"], "source_title": row["source_title"],
        "opaque": _is_opaque(row["content_type"], row["head"]), "version": row["updated_at"],
    }


def list_all(conn):
    rows = conn.execute(
        "SELECT key, name, content_type, source_title, updated_at, substr(data, 1, 26) AS head "
        "FROM company_logos ORDER BY key"
    ).fetchall()
    return [_summary(r) for r in rows]


def get(conn, name):
    """``(bytes, content_type)`` for a company's logo, or None."""
    row = conn.execute("SELECT data, content_type FROM company_logos WHERE key = ?", (key_of(name),)).fetchone()
    return (bytes(row["data"]), row["content_type"]) if row else None


def remove(conn, name):
    with conn:
        cursor = conn.execute("DELETE FROM company_logos WHERE key = ?", (key_of(name),))
    return cursor.rowcount > 0
