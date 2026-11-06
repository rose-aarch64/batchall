"""Colors taken from faction logos, offered as strip colors.

Each faction's logo is read and its main color found: the hue that covers the most of the logo, weighted
toward vivid pixels so a grey outline does not win. Logos with no vivid color (black, white or grey) give
their average grey. A small PNG reader does the decoding so no imaging library is needed.
"""

import colorsys
import struct
import zlib

from . import art

MAX_SIDE = 1000
HUE_BINS = 24
CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}  # PNG color type -> samples per pixel (8-bit, not interlaced)


class PngError(ValueError):
    pass


def decode_png(data):
    """``(width, height, rows)`` of an 8-bit, non-interlaced PNG, each row a list of ``(r, g, b, a)``."""
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise PngError("not a PNG")
    pos, idat, palette, trns, header = 8, [], None, None, None
    while pos + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif kind == b"PLTE":
            palette = [tuple(body[i:i + 3]) for i in range(0, len(body) - 2, 3)]
        elif kind == b"tRNS":
            trns = body
        elif kind == b"IDAT":
            idat.append(body)
        elif kind == b"IEND":
            break
    if header is None or not idat:
        raise PngError("missing PNG data")
    width, height, depth, ctype, _, _, interlace = header
    if depth != 8 or interlace != 0 or ctype not in CHANNELS or not 0 < width <= MAX_SIDE or not 0 < height <= MAX_SIDE:
        raise PngError("unsupported PNG")
    if ctype == 3 and palette is None:
        raise PngError("palette missing")
    bpp = CHANNELS[ctype]
    stride = width * bpp
    try:
        raw = zlib.decompressobj().decompress(b"".join(idat), height * (stride + 1))
    except zlib.error:
        raise PngError("bad PNG data")
    if len(raw) < height * (stride + 1):
        raise PngError("short PNG data")

    rows, prev = [], bytearray(stride)
    for y in range(height):
        start = y * (stride + 1)
        kind, line = raw[start], bytearray(raw[start + 1:start + 1 + stride])
        for i in range(stride):
            left = line[i - bpp] if i >= bpp else 0
            up, upleft = prev[i], (prev[i - bpp] if i >= bpp else 0)
            if kind == 1:
                line[i] = (line[i] + left) & 255
            elif kind == 2:
                line[i] = (line[i] + up) & 255
            elif kind == 3:
                line[i] = (line[i] + ((left + up) >> 1)) & 255
            elif kind == 4:
                p = left + up - upleft
                pa, pb, pc = abs(p - left), abs(p - up), abs(p - upleft)
                pred = left if pa <= pb and pa <= pc else up if pb <= pc else upleft
                line[i] = (line[i] + pred) & 255
            elif kind != 0:
                raise PngError("bad PNG filter")
        prev = line
        row = []
        for x in range(width):
            s = line[x * bpp:(x + 1) * bpp]
            if ctype == 6:
                row.append((s[0], s[1], s[2], s[3]))
            elif ctype == 2:
                row.append((s[0], s[1], s[2], 255))
            elif ctype == 0:
                row.append((s[0], s[0], s[0], 255))
            elif ctype == 4:
                row.append((s[0], s[0], s[0], s[1]))
            else:
                r, g, b = palette[s[0]] if s[0] < len(palette) else (0, 0, 0)
                row.append((r, g, b, trns[s[0]] if trns and s[0] < len(trns) else 255))
        rows.append(row)
    return width, height, rows


def dominant_color(data):
    """The logo's main color as ``#RRGGBB``, or None when the image cannot be read or has no visible pixels."""
    try:
        _, _, rows = decode_png(data)
    except PngError:
        return None
    bins = [[0.0, 0, 0, 0, 0] for _ in range(HUE_BINS)]  # weight, red, green and blue totals, pixel count
    grey = [0, 0, 0, 0]
    for row in rows:
        for r, g, b, a in row:
            if a < 128:
                continue
            grey[0] += r; grey[1] += g; grey[2] += b; grey[3] += 1
            h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
            if s < 0.3 or v < 0.2:  # grey, near black or washed out: not a color of the logo
                continue
            slot = bins[int(h * HUE_BINS) % HUE_BINS]
            slot[0] += s * v
            slot[1] += r; slot[2] += g; slot[3] += b; slot[4] += 1
    if not grey[3]:
        return None
    best = max(bins, key=lambda slot: slot[0])
    if best[4]:  # the average of the pixels in the winning hue
        total, n = best[1:4], best[4]
    else:  # nothing vivid: the logo's average grey
        total, n = grey[:3], grey[3]
    return "#{:02X}{:02X}{:02X}".format(*(round(c / n) for c in total))


_cache = {}


def faction_colors(conn, cache=None, fetch=art.download_faction_icon):
    """``[{id, name, group, color}]`` for every faction whose logo is available and has a readable color."""
    found = []
    for row in conn.execute("SELECT id, name, grp, img FROM factions ORDER BY name").fetchall():
        if row["name"].endswith(" General"):  # catch-all entries with a plain grey logo, not a real color
            continue
        got = art.get_faction_icon(conn, row["id"], cache=cache, fetch=fetch)
        if got is None:
            continue
        data = got[0]
        key = (row["img"], len(data))
        if key not in _cache:
            _cache[key] = dominant_color(data)
        if _cache[key]:
            found.append({"id": row["id"], "name": row["name"], "group": row["grp"] or "Other", "color": _cache[key]})
    return found
