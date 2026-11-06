"""Unit artwork and faction logos.

MekBay serves one illustration per unit from its public host. The catalog stores each unit's art path.
Faction logos are small PNGs on MekBay's main site, and the catalog stores each faction's logo path.
Images are downloaded the first time they are needed and cached on disk, so each one is fetched once.

The artwork and logos belong to their publishers. They are cached for personal viewing, and the cache
folder is ignored by git so they are not redistributed.
"""

import hashlib
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

from . import config

MAX_BYTES = 5_000_000
RETRY_AFTER_SECONDS = 300
TIMEOUT_SECONDS = 40

# Two downloads at a time keeps us polite to the host when a page asks for many images at once.
_gate = threading.Semaphore(2)
_failures = {}
_failures_lock = threading.Lock()

SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", ".png", "image/png"),
    (b"\xff\xd8\xff", ".jpg", "image/jpeg"),
)


class ArtError(ValueError):
    pass


def cache_dir(override=None):
    return Path(override or config.ART_DIR)


def sniff(data):
    """Return (extension, content type) for PNG or JPEG bytes, or None for anything else."""
    for signature, ext, ctype in SIGNATURES:
        if data.startswith(signature):
            return ext, ctype
    return None


def safe_path(path):
    """Reject art paths that are not plain relative file paths."""
    if not isinstance(path, str) or not path.strip():
        raise ArtError("no art path")
    path = path.strip()
    if path.startswith("/") or "\\" in path or ".." in path.split("/") or "\x00" in path:
        raise ArtError(f"unsafe art path {path!r}")
    return path


def download(path):
    """Fetch one image from MekBay's public host and return its bytes."""
    path = safe_path(path)
    url = config.MEKBAY_ART_URL + urllib.parse.quote(path)
    request = urllib.request.Request(url, headers={"User-Agent": config.USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        data = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ArtError("art file is unexpectedly large")
    if sniff(data) is None:
        raise ArtError("art file is not a PNG or JPEG")
    return data


def _file_stem(art_path):
    # Cached by art path, not by unit: variants of a chassis share one picture. Hashing keeps the
    # file name safe whatever the path looks like.
    return hashlib.sha256(art_path.encode("utf-8")).hexdigest()[:40]


def _cached(art_path, folder):
    stem = _file_stem(art_path)
    for _, ext, ctype in SIGNATURES:
        candidate = folder / f"{stem}{ext}"
        if candidate.exists():
            return candidate, ctype
    return None, None


def _get_or_fetch(art_path, folder, fetch):
    """Return ``(bytes, content_type)`` for an image from the cache, downloading it if needed, or None.

    A failed download is remembered for a few minutes so a broken link is not retried on every request.
    """
    path, ctype = _cached(art_path, folder)
    if path:
        return path.read_bytes(), ctype

    with _failures_lock:
        failed_at = _failures.get(art_path)
    if failed_at is not None and time.monotonic() - failed_at < RETRY_AFTER_SECONDS:
        return None

    with _gate:
        path, ctype = _cached(art_path, folder)  # another request may have finished it while we waited
        if path:
            return path.read_bytes(), ctype
        try:
            data = fetch(art_path)
            kind = sniff(data)
            if kind is None:
                raise ArtError("not an image")
        except (OSError, ValueError):
            with _failures_lock:
                _failures[art_path] = time.monotonic()
            return None
        ext, ctype = kind
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"{_file_stem(art_path)}{ext}"
        temp = target.with_suffix(target.suffix + ".part")
        temp.write_bytes(data)
        os.replace(temp, target)  # a half-written file never appears under the real name
        return data, ctype


def get_art(conn, unit_uuid, cache=None, fetch=download):
    """Return ``(bytes, content_type)`` for a unit's art, or None if it has none or cannot be fetched."""
    row = conn.execute("SELECT art FROM units WHERE uuid = ?", (unit_uuid,)).fetchone()
    if row is None or not row["art"]:
        return None
    return _get_or_fetch(row["art"], cache_dir(cache), fetch)


# --- faction logos -----------------------------------------------------------------------------

FACTION_ICON_PATH = re.compile(r"/images/factions/[A-Za-z0-9][A-Za-z0-9._-]*\.png")
FACTION_ICON_MAX_BYTES = 500_000


def safe_faction_icon_path(path):
    """Accept only a plain ``/images/factions/<name>.png`` path, the one place MekBay keeps logos."""
    if not isinstance(path, str) or not FACTION_ICON_PATH.fullmatch(path):
        raise ArtError(f"not a faction logo path: {path!r}")
    return path


def download_faction_icon(path):
    """Fetch one faction logo from MekBay's main site and return its PNG bytes."""
    path = safe_faction_icon_path(path)
    request = urllib.request.Request(config.MEKBAY_SITE_URL + path, headers={"User-Agent": config.USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        data = response.read(FACTION_ICON_MAX_BYTES + 1)
    if len(data) > FACTION_ICON_MAX_BYTES:
        raise ArtError("faction logo is unexpectedly large")
    kind = sniff(data)
    if kind is None or kind[0] != ".png":
        raise ArtError("faction logo is not a PNG")
    return data


def _icon_folder(cache=None):
    return cache_dir(cache) / "factions"  # apart from unit art, so the unit art counts stay as they were


def _icon_path(conn, faction_id):
    """The faction's stored logo path if it has a safe one, else None."""
    row = conn.execute("SELECT img FROM factions WHERE id = ?", (faction_id,)).fetchone()
    if row is None or not row["img"]:
        return None
    try:
        return safe_faction_icon_path(row["img"])
    except ArtError:
        return None


def get_faction_icon(conn, faction_id, cache=None, fetch=download_faction_icon):
    """Return ``(bytes, content_type)`` for a faction's logo, or None if it has none or cannot be fetched."""
    img = _icon_path(conn, faction_id)
    if img is None:
        return None
    return _get_or_fetch(img, _icon_folder(cache), fetch)


def faction_icon_status(conn, cache=None):
    """Counts for the logos: factions with one, distinct images (factions can share a file), and how many are downloaded."""
    folder = _icon_folder(cache)
    rows = conn.execute("SELECT id FROM factions").fetchall()
    paths = [p for p in (_icon_path(conn, r["id"]) for r in rows) if p]
    images = set(paths)
    cached = sum(1 for p in images if _cached(p, folder)[0])
    return {
        "factions": len(rows), "with_icon": len(paths), "images": len(images),
        "cached": cached, "missing": len(images) - cached,
    }


def prefetch_faction_icons(conn, cache=None, fetch=download_faction_icon, progress=None):
    """Download the logo of every faction that has one and is not cached yet."""
    folder = _icon_folder(cache)
    todo, seen = [], set()
    for row in conn.execute("SELECT id, name FROM factions ORDER BY name").fetchall():
        img = _icon_path(conn, row["id"])
        if img and img not in seen and not _cached(img, folder)[0]:
            seen.add(img)
            todo.append((row["id"], row["name"]))
    fetched, failed = 0, []
    for number, (faction_id, name) in enumerate(todo, 1):
        if progress:
            progress(f"[{number}/{len(todo)}] {name}")
        if get_faction_icon(conn, faction_id, cache=cache, fetch=fetch):
            fetched += 1
        else:
            failed.append(name)
    return {"requested": len(todo), "fetched": fetched, "failed": failed}


def forget_failures():
    with _failures_lock:
        _failures.clear()


def status(conn, cache=None):
    """Counts for the art cache: how many owned units have art, and how many are already downloaded."""
    folder = cache_dir(cache)
    rows = conn.execute(
        "SELECT u.uuid, u.art FROM collection c JOIN units u ON u.uuid = c.unit_uuid"
    ).fetchall()
    images = {r["art"] for r in rows if r["art"]}  # variants of a chassis share one image
    cached = sum(1 for art_path in images if _cached(art_path, folder)[0])
    size = sum(f.stat().st_size for f in folder.glob("*") if f.is_file()) if folder.exists() else 0
    return {
        "owned_units": len(rows),
        "units_with_art": sum(1 for r in rows if r["art"]),
        "images": len(images),
        "cached": cached,
        "missing": len(images) - cached,
        "cache_bytes": size,
    }


def prefetch(conn, cache=None, fetch=download, progress=None):
    """Download art for every owned unit that has some and is not cached yet."""
    folder = cache_dir(cache)
    rows = conn.execute(
        "SELECT u.uuid, u.chassis, u.art FROM collection c JOIN units u ON u.uuid = c.unit_uuid ORDER BY u.chassis"
    ).fetchall()
    todo, seen = [], set()
    for row in rows:  # one request per distinct image
        if row["art"] and row["art"] not in seen and not _cached(row["art"], folder)[0]:
            seen.add(row["art"])
            todo.append(row)
    fetched, failed = 0, []
    for number, row in enumerate(todo, 1):
        if progress:
            progress(f"[{number}/{len(todo)}] {row['chassis']}")
        if get_art(conn, row["uuid"], cache=cache, fetch=fetch):
            fetched += 1
        else:
            failed.append(row["chassis"])
    return {"requested": len(todo), "fetched": fetched, "failed": failed}
