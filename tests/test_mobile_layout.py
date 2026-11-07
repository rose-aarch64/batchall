import time
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


@unittest.skipUnless(chrome_path(), "Chrome is not installed")
class PhoneFoundationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base, cls.close_server = fixture_server()

    @classmethod
    def tearDownClass(cls):
        cls.close_server()

    def open(self, width=390, height=844, mobile=True):
        b = Browser(width, height, mobile=mobile)
        b.__enter__()
        self.addCleanup(b.__exit__, None, None, None)
        b.open(self.base + "/")
        return b

    def assert_shows(self, b, selector):
        # Panels fade in over a quarter second, starting at opacity 0, so give them time.
        deadline = time.monotonic() + 3
        while not is_visible(b, selector) and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertTrue(is_visible(b, selector), f"{selector} is not visible")

    def test_tab_bar_is_fixed_to_the_bottom_on_a_phone(self):
        b = self.open()
        self.assertTrue(is_visible(b, "#tabbar"))
        self.assertFalse(is_visible(b, ".tabs"))
        bottom = b.js("document.querySelector('#tabbar').getBoundingClientRect().bottom")
        self.assertEqual(round(bottom), b.js("window.innerHeight"))
        heights = b.js("[...document.querySelectorAll('#tabbar button')].map(e => e.getBoundingClientRect().height)")
        self.assertEqual(len(heights), 3)
        for height in heights:
            self.assertGreaterEqual(height, 44)

    def test_tab_bar_is_hidden_on_desktop(self):
        b = self.open(1280, 800, mobile=False)
        self.assertFalse(is_visible(b, "#tabbar"))
        self.assertTrue(is_visible(b, ".tabs"))

    def test_forces_tab_shows_tray_and_saved_forces_only(self):
        b = self.open()
        b.type_into("#filter-q", "atlas")
        b.click('#tabbar [data-tab="forces"]')
        for selector in ("#tray", "#forces"):
            self.assert_shows(b, selector)
        for selector in ("#roster", "#filter-q", "#view-controls", "#view-import"):
            self.assertFalse(is_visible(b, selector), selector)
        b.click('#tabbar [data-tab="collection"]')
        self.assert_shows(b, "#roster")
        self.assertFalse(is_visible(b, "#tray"))
        self.assertFalse(is_visible(b, "#forces"))
        self.assertEqual(b.js("document.querySelector('#filter-q').value"), "atlas")

    def test_import_tab_still_works_on_a_phone(self):
        b = self.open()
        b.click('#tabbar [data-tab="import"]')
        self.assert_shows(b, "#view-import")
        self.assertFalse(is_visible(b, "#roster"))
        self.assertFalse(is_visible(b, "#forces"))
        self.assertGreaterEqual(b.js("document.querySelector('#import-text').getBoundingClientRect().height"), 120)
        self.assertEqual(overflow(b), 0)

    def test_tab_bar_marks_the_selected_tab(self):
        b = self.open()
        b.click('#tabbar [data-tab="import"]')
        selected = b.js("[...document.querySelectorAll('#tabbar [aria-selected=true]')].map(e => e.dataset.tab)")
        self.assertEqual(selected, ["import"])

    def test_content_is_not_hidden_behind_the_tab_bar(self):
        b = self.open()
        b.click('.seg[data-view="cards"]')
        b.js("window.scrollTo(0, document.documentElement.scrollHeight)")
        last = b.js("(() => { const c = document.querySelectorAll('.mini-card'); return c[c.length - 1].getBoundingClientRect().bottom; })()")
        top = b.js("document.querySelector('#tabbar').getBoundingClientRect().top")
        self.assertLessEqual(last, top)

    def test_catalog_status_sits_in_the_collection_tab_on_a_phone(self):
        b = self.open()
        self.assertTrue(is_visible(b, "#catalog-status"))
        self.assertTrue(b.js("!!document.querySelector('#view-collection > #catalog-status')"))
        self.assertEqual(b.js("document.querySelectorAll('#catalog-status').length"), 1)
        b.click('#tabbar [data-tab="import"]')
        self.assertFalse(is_visible(b, "#catalog-status"))

    def test_catalog_status_goes_back_to_the_top_bar_on_desktop(self):
        b = self.open()
        b.resize(1280, 800)
        self.assertTrue(b.js("!!document.querySelector('.topbar > #catalog-status')"))
        self.assertEqual(b.js("document.querySelectorAll('#catalog-status').length"), 1)

    def test_page_has_viewport_fit_and_theme_color(self):
        b = self.open()
        self.assertEqual(
            b.js("document.querySelector('meta[name=viewport]').content"),
            "width=device-width, initial-scale=1, viewport-fit=cover",
        )
        self.assertEqual(b.js("document.querySelector('meta[name=theme-color]').content"), "#8a1c2b")

    def test_phone_mode_resets_the_page_zoom(self):
        b = self.open()
        self.assertEqual(b.js("getComputedStyle(document.documentElement).zoom"), "1")
        self.assertGreaterEqual(b.js("document.querySelector('#roster td').getBoundingClientRect().height > 0 && "
                                     "parseFloat(getComputedStyle(document.body).fontSize) * "
                                     "parseFloat(getComputedStyle(document.documentElement).zoom)"), 14)
        d = self.open(1280, 800, mobile=False)
        self.assertEqual(d.js("getComputedStyle(document.documentElement).zoom"), "0.8")

    def test_phone_mode_keeps_the_account_box_in_the_top_bar(self):
        b = self.open()
        # showAccount is the app's own function, the one the hosted sign-in path calls.
        b.js("showAccount({display_name: 'Rose', handle: 'rose'})")
        self.assertTrue(is_visible(b, "#sign-out"))
        self.assertGreaterEqual(b.js("document.querySelector('#sign-out').getBoundingClientRect().height"), 44)
        self.assertEqual(overflow(b), 0)

    def test_a_long_display_name_is_truncated_not_overflowing(self):
        b = self.open(360, 800)
        b.js("showAccount({display_name: 'Maximilian ' + 'Wolfgang '.repeat(6), handle: 'max'})")
        self.assertEqual(overflow(b), 0)
        box = b.js("(() => { const r = document.querySelector('#sign-out').getBoundingClientRect(); "
                   "return [r.left, r.right, r.height]; })()")
        self.assertGreaterEqual(box[0], 0)
        self.assertLessEqual(box[1], 360)
        self.assertGreaterEqual(box[2], 44)
        # The name is cut with an ellipsis on one line, so the top bar stays one button tall.
        self.assertLessEqual(b.js("document.querySelector('.topbar').getBoundingClientRect().height"), 64)
        self.assertTrue(b.js("(() => { const n = document.querySelector('#account .account-name'); "
                             "return !!n && n.scrollWidth > n.clientWidth; })()"))

    def test_dialogs_sit_above_the_tab_bar(self):
        b = self.open()
        b.js("(confirmDialog('Remove everything?'), 0)")
        self.assert_shows(b, ".modal-backdrop")
        hit = b.js("(() => { const e = document.elementFromPoint(195, window.innerHeight - 4); "
                   "return !!e && !!e.closest('.modal-backdrop'); })()")
        self.assertTrue(hit, "the tab bar is painted over the dialog backdrop")

    def test_bulk_bar_sits_above_the_tab_bar(self):
        b = self.open()
        b.js("[...document.querySelectorAll('#roster tbody .sel-input')].slice(0, 2).forEach(e => e.click())")
        self.assert_shows(b, "#bulkbar")
        bar = b.js("(() => { const r = document.querySelector('#bulkbar').getBoundingClientRect(); "
                   "return [r.bottom, r.left + r.width / 2, r.top + r.height / 2]; })()")
        tabs_top = b.js("document.querySelector('#tabbar').getBoundingClientRect().top")
        self.assertLessEqual(bar[0], tabs_top + 0.5)
        self.assertTrue(b.js(f"!!document.elementFromPoint({bar[1]}, {bar[2]}).closest('#bulkbar')"))

    def test_plus_tray_adds_to_the_tray_and_the_forces_tab_shows_it(self):
        b = self.open()
        before = b.js("document.querySelectorAll('#tray-list li').length")
        b.click('#roster tbody button[title="Add one to the force tray"]')
        self.assertEqual(b.js("document.querySelectorAll('#tray-list li').length"), before + 1)
        b.click('#tabbar [data-tab="forces"]')
        self.assert_shows(b, "#tray-list li")

    def test_forces_tab_survives_a_resize_to_desktop_and_back(self):
        b = self.open()
        b.click('#tabbar [data-tab="forces"]')
        b.resize(1280, 800)
        for selector in ("#roster", "#tray", "#forces"):
            self.assert_shows(b, selector)
        self.assertEqual(b.js("document.querySelector('.tab.active').dataset.view"), "collection")
        b.resize(390, 844)
        self.assertEqual(b.js("document.body.dataset.tab"), "forces")
        self.assert_shows(b, "#tray")
        self.assert_shows(b, "#forces")
        self.assertFalse(is_visible(b, "#roster"))


if __name__ == "__main__":
    unittest.main()
