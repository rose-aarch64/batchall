import unittest

from browser import Browser, chrome_path, fixture_server, is_visible, overflow, small_targets

MINIS = 14  # owned minis in the fixture database


@unittest.skipUnless(chrome_path(), "Chrome is not installed")
class MobileLayoutBaselineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base, cls.close_server = fixture_server()

    @classmethod
    def tearDownClass(cls):
        cls.close_server()

    def open(self, width, height, mobile=True):
        b = Browser(width, height, mobile=mobile)
        b.__enter__()
        self.addCleanup(b.__exit__, None, None, None)
        b.open(self.base + "/")
        return b

    def test_harness_loads_the_page(self):
        b = self.open(390, 844)
        self.assertNotEqual(b.js("document.querySelector('#catalog-status').textContent.trim()"), "")
        self.assertEqual(b.console_errors(), [])
        self.assertGreaterEqual(b.js("document.querySelectorAll('#roster tbody tr').length"), 12)

    def assert_no_sideways_scroll(self, b, label):
        for tab in ("collection", "import"):
            b.click(f".tab[data-view={tab}]")
            self.assertEqual(overflow(b), 0, f"{label}: sideways scroll on the {tab} tab")

    def test_no_sideways_scroll_on_any_phone_size_collection_view(self):
        for width in (360, 390, 430):
            with self.subTest(width=width):
                b = self.open(width, 800)
                self.assert_no_sideways_scroll(b, f"{width}x800")

    def test_landscape_phone_has_no_sideways_scroll(self):
        b = self.open(844, 390)
        self.assert_no_sideways_scroll(b, "844x390")

    def test_desktop_layout_is_unchanged_at_1280(self):
        b = self.open(1280, 800, mobile=False)
        for selector in ("#view-controls", "#roster", ".toolbar input#filter-q", "#filter-faction", "#forces"):
            with self.subTest(selector=selector):
                self.assertTrue(is_visible(b, selector), f"{selector} is not visible")

    def test_open_returns_only_after_the_roster_has_rendered(self):
        b = self.open(390, 844)
        for run in range(5):
            b.open(self.base + "/")
            self.assertEqual(b.js("document.querySelectorAll('#roster tbody tr').length"), MINIS, f"run {run}")

    def add_button(self, b, size, position="fixed"):
        b.js(
            "(() => { const e = document.createElement('button'); e.id = 'probe'; e.textContent = 'probe'; "
            f"e.style.cssText = 'position:{position};left:0;bottom:0;width:{size}px;height:{size}px;padding:0'; "
            "document.body.appendChild(e); })()"
        )
        self.addCleanup(b.js, "(() => { const e = document.getElementById('probe'); if (e) e.remove(); })()")

    def test_small_targets_reports_a_small_fixed_button(self):
        b = self.open(390, 844)
        self.add_button(b, 30)
        self.assertTrue([t for t in small_targets(b) if t.startswith("button#probe")])

    def test_small_targets_ignores_a_big_enough_fixed_button(self):
        b = self.open(390, 844)
        # The page runs at zoom 0.8 (web/style.css), so 60 CSS pixels measure 48 on screen.
        self.add_button(b, 60)
        self.assertEqual([t for t in small_targets(b) if t.startswith("button#probe")], [])

    def test_is_visible_tells_hidden_from_shown(self):
        b = self.open(390, 844)
        b.js(
            "(() => { const styles = {'probe-ok': 'width:50px;height:50px', "
            "'probe-vis': 'width:50px;height:50px;visibility:hidden', "
            "'probe-none': 'width:50px;height:50px;display:none', "
            "'probe-zero': 'width:0;height:0', "
            "'probe-clear': 'width:50px;height:50px;opacity:0'}; "
            "for (const [id, css] of Object.entries(styles)) { const e = document.createElement('div'); "
            "e.id = id; e.style.cssText = css; document.body.appendChild(e); } })()"
        )
        self.assertTrue(is_visible(b, "#probe-ok"))
        for name in ("vis", "none", "zero", "clear", "missing"):
            self.assertFalse(is_visible(b, f"#probe-{name}"), name)

    def test_console_errors_only_cover_the_latest_page(self):
        b = self.open(390, 844)
        b.js("console.error('first page error')")
        self.assertEqual(b.console_errors(), ["first page error"])
        b.open(self.base + "/")
        self.assertEqual(b.console_errors(), [])


if __name__ == "__main__":
    unittest.main()
