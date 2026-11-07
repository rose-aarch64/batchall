import unittest

from browser import Browser, chrome_path, fixture_server, overflow


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
                self.assertTrue(
                    b.js(f"(() => {{ const e = document.querySelector({selector!r}); return !!e && e.offsetParent !== null; }})()"),
                    f"{selector} is not visible",
                )


if __name__ == "__main__":
    unittest.main()
