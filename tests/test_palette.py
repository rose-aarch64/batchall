"""Strip colors taken from faction logos: the PNG reader, the main-color pick and the API list."""

import struct
import tempfile
import unittest
import zlib

from batchall import art, db, palette

RED, BLUE, BLACK, GREY, CLEAR = (200, 30, 30, 255), (30, 60, 200, 255), (0, 0, 0, 255), (128, 128, 128, 255), (0, 0, 0, 0)


def chunk(kind, body):
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))


def make_png(rows, kind=0):
    """An RGBA PNG of ``rows`` (lists of (r, g, b, a)), every row stored with PNG filter ``kind`` (0 to 4)."""
    width, height = len(rows[0]), len(rows)
    bpp, raw, prev = 4, bytearray(), bytes(4 * width)
    for row in rows:
        line = bytes(c for px in row for c in px)
        out = bytearray()
        for i, value in enumerate(line):
            left = line[i - bpp] if i >= bpp else 0
            up, upleft = prev[i], (prev[i - bpp] if i >= bpp else 0)
            if kind == 1:
                pred = left
            elif kind == 2:
                pred = up
            elif kind == 3:
                pred = (left + up) >> 1
            elif kind == 4:
                p = left + up - upleft
                pa, pb, pc = abs(p - left), abs(p - up), abs(p - upleft)
                pred = left if pa <= pb and pa <= pc else up if pb <= pc else upleft
            else:
                pred = 0
            out.append((value - pred) & 255)
        raw += bytes([kind]) + out
        prev = line
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(bytes(raw))) + chunk(b"IEND", b"")


def block(pixel, count):
    return [pixel] * count


class DecodeTests(unittest.TestCase):
    def test_every_png_filter_type_decodes_to_the_same_pixels(self):
        rows = [[(x * 20 % 256, y * 40 % 256, (x + y) * 15 % 256, 255 - x) for x in range(8)] for y in range(6)]
        for kind in range(5):
            width, height, got = palette.decode_png(make_png(rows, kind))
            self.assertEqual((width, height), (8, 6))
            self.assertEqual(got, rows, msg=f"filter {kind}")

    def test_bad_or_unsupported_data_is_refused(self):
        for bad in (b"", b"not a png", make_png([[RED]])[:30]):
            with self.assertRaises(palette.PngError):
                palette.decode_png(bad)


class DominantColorTests(unittest.TestCase):
    def test_a_red_logo_on_a_see_through_background_is_red(self):
        rows = [block(CLEAR, 10)] + [block(CLEAR, 2) + block(RED, 6) + block(CLEAR, 2) for _ in range(6)] + [block(CLEAR, 10)]
        self.assertEqual(palette.dominant_color(make_png(rows)), "#C81E1E")

    def test_the_hue_with_the_most_color_wins_over_a_small_accent(self):
        rows = [block(BLUE, 8) for _ in range(7)] + [block(RED, 8)]
        self.assertEqual(palette.dominant_color(make_png(rows)), "#1E3CC8")

    def test_a_grey_outline_does_not_beat_a_vivid_color(self):
        rows = [block(GREY, 10) for _ in range(6)] + [block(RED, 10) for _ in range(2)]
        self.assertEqual(palette.dominant_color(make_png(rows)), "#C81E1E")

    def test_a_logo_with_no_vivid_color_gives_its_average_grey(self):
        rows = [block(BLACK, 4) + block((200, 200, 200, 255), 4) for _ in range(4)]
        self.assertEqual(palette.dominant_color(make_png(rows)), "#646464")

    def test_an_empty_or_unreadable_image_gives_none(self):
        self.assertIsNone(palette.dominant_color(make_png([block(CLEAR, 4) for _ in range(4)])))
        self.assertIsNone(palette.dominant_color(b"garbage"))


class FactionColorTests(unittest.TestCase):
    def setUp(self):
        palette._cache.clear()
        art.forget_failures()  # a logo that failed in another test must not stay "recently failed" here
        self.conn = db.connect(":memory:")
        with self.conn:
            for i, (name, grp, img) in enumerate([
                ("Alpha", "Inner Sphere", "/images/factions/a.png"),
                ("Beta", "Clan", "/images/factions/b.png"),
                ("Inner Sphere General", "Inner Sphere", "/images/factions/c.png"),
                ("No logo", "Other", None),
            ], 1):
                self.conn.execute("INSERT INTO factions (id, name, grp, img) VALUES (?, ?, ?, ?)", (i, name, grp, img))
        self.red = make_png([block(RED, 6) for _ in range(6)])
        self.blue = make_png([block(BLUE, 6) for _ in range(6)])

    def run_with(self, files):
        with tempfile.TemporaryDirectory() as cache:
            return palette.faction_colors(self.conn, cache=cache, fetch=lambda path: files[path])

    def test_lists_each_factions_color_with_its_group(self):
        found = self.run_with({"/images/factions/a.png": self.red, "/images/factions/b.png": self.blue, "/images/factions/c.png": self.red})
        self.assertEqual([(f["name"], f["group"], f["color"]) for f in found],
                         [("Alpha", "Inner Sphere", "#C81E1E"), ("Beta", "Clan", "#1E3CC8")])

    def test_a_logo_that_cannot_be_fetched_is_skipped(self):
        def fetch(path):
            if path.endswith("b.png"):
                raise OSError("offline")
            return self.red

        with tempfile.TemporaryDirectory() as cache:
            found = palette.faction_colors(self.conn, cache=cache, fetch=fetch)
        self.assertEqual([f["name"] for f in found], ["Alpha"])


if __name__ == "__main__":
    unittest.main()
