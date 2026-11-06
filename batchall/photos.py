"""Mini photos: one photo of the painted figure per mini, with a square crop chosen by the user.

The page shrinks the photo before it is sent, so it arrives as a modest JPEG, PNG or WebP. The whole photo
is kept, with the crop stored beside it as a zoom (1 is "just covers the square") and the point of the photo
(``cx``, ``cy``, each 0 to 1) that sits in the middle of the square. Changing the crop later needs no new upload.
The photo is removed with its mini.
"""

import base64
import binascii
import sqlite3

from . import logos

MAX_BYTES = 8_000_000
MAX_DIMENSION = 20_000
MAX_ZOOM = 8.0
ALLOWED_TYPES = ("image/jpeg", "image/png", "image/webp")


class PhotoError(ValueError):
    pass


def _number(value, label, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
        raise PhotoError(f"{label} must be a number from {low} to {high}")
    return float(value)


def _crop(zoom, cx, cy):
    return (_number(zoom, "zoom", 1, MAX_ZOOM), _number(cx, "cx", 0, 1), _number(cy, "cy", 0, 1))


def _decode(image):
    """The bytes of a base64 string or ``data:`` URL, checked to be a photo format we keep."""
    if not isinstance(image, str) or not image:
        raise PhotoError("image is required")
    if image.startswith("data:"):
        _, _, image = image.partition(",")
    try:
        data = base64.b64decode(image, validate=True)
    except (binascii.Error, ValueError):
        raise PhotoError("image is not valid base64")
    if len(data) > MAX_BYTES:
        raise PhotoError("the photo is too large")
    content_type = logos.sniff(data)
    if content_type not in ALLOWED_TYPES:
        raise PhotoError("the photo must be a JPEG, PNG or WebP image")
    return data, content_type


def _check_copy(conn, copy_id):
    if conn.execute("SELECT 1 FROM copies WHERE id = ?", (copy_id,)).fetchone() is None:
        raise PhotoError(f"no mini with id {copy_id}")


def summary(row):
    return {
        "width": row["width"], "height": row["height"], "zoom": row["zoom"],
        "cx": row["cx"], "cy": row["cy"], "version": row["updated_at"],
    }


def save(conn, copy_id, image, width, height, zoom=1, cx=0.5, cy=0.5):
    """Store a mini's photo and crop, replacing any earlier photo."""
    _check_copy(conn, copy_id)
    data, content_type = _decode(image)
    width = int(_number(width, "width", 1, MAX_DIMENSION))
    height = int(_number(height, "height", 1, MAX_DIMENSION))
    zoom, cx, cy = _crop(zoom, cx, cy)
    with conn:
        conn.execute(
            "INSERT INTO mini_photos (copy_id, content_type, data, width, height, zoom, cx, cy) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(copy_id) DO UPDATE SET content_type = excluded.content_type, data = excluded.data, "
            "width = excluded.width, height = excluded.height, zoom = excluded.zoom, cx = excluded.cx, "
            "cy = excluded.cy, updated_at = CURRENT_TIMESTAMP",
            (copy_id, content_type, data, width, height, zoom, cx, cy),
        )
    return get_summary(conn, copy_id)


def set_crop(conn, copy_id, zoom, cx, cy):
    """Change only the crop of a mini's existing photo."""
    zoom, cx, cy = _crop(zoom, cx, cy)
    with conn:
        cursor = conn.execute("UPDATE mini_photos SET zoom = ?, cx = ?, cy = ? WHERE copy_id = ?", (zoom, cx, cy, copy_id))
    if cursor.rowcount == 0:
        raise LookupError("this mini has no photo")
    return get_summary(conn, copy_id)


def get_summary(conn, copy_id):
    row = conn.execute(
        "SELECT width, height, zoom, cx, cy, updated_at FROM mini_photos WHERE copy_id = ?", (copy_id,)
    ).fetchone()
    return summary(row) if row else None


def summaries(conn):
    """``{copy id: summary}`` for every mini that has a photo, for the collection list."""
    rows = conn.execute("SELECT copy_id, width, height, zoom, cx, cy, updated_at FROM mini_photos").fetchall()
    return {r["copy_id"]: summary(r) for r in rows}


def get(conn, copy_id):
    """``(bytes, content_type)`` of a mini's whole photo, or None."""
    row = conn.execute("SELECT data, content_type FROM mini_photos WHERE copy_id = ?", (copy_id,)).fetchone()
    return (bytes(row["data"]), row["content_type"]) if row else None


def remove(conn, copy_id):
    with conn:
        cursor = conn.execute("DELETE FROM mini_photos WHERE copy_id = ?", (copy_id,))
    return cursor.rowcount > 0
