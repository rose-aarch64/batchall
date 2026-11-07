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
        self.assertGreaterEqual(b.js("document.querySelectorAll('.mini-card').length"), 12)

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
            self.assertEqual(b.js("document.querySelectorAll('.mini-card').length"), MINIS, f"run {run}")

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
        for selector in ("#card-grid", "#filter-q", "#view-controls", "#view-import"):
            self.assertFalse(is_visible(b, selector), selector)
        b.click('#tabbar [data-tab="collection"]')
        self.assert_shows(b, "#card-grid")
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
        self.assertGreaterEqual(b.js("document.querySelector('.mini-card .card-meta').getBoundingClientRect().height > 0 && "
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
        b.js("[...document.querySelectorAll('.mini-card .sel-input')].slice(0, 2).forEach(e => e.click())")
        self.assert_shows(b, "#bulkbar")
        bar = b.js("(() => { const r = document.querySelector('#bulkbar').getBoundingClientRect(); "
                   "return [r.bottom, r.left + r.width / 2, r.top + r.height / 2]; })()")
        tabs_top = b.js("document.querySelector('#tabbar').getBoundingClientRect().top")
        self.assertLessEqual(bar[0], tabs_top + 0.5)
        self.assertTrue(b.js(f"!!document.elementFromPoint({bar[1]}, {bar[2]}).closest('#bulkbar')"))

    def test_plus_tray_adds_to_the_tray_and_the_forces_tab_shows_it(self):
        b = self.open()
        before = b.js("document.querySelectorAll('#tray-list li').length")
        b.click('.mini-card button[title="Add one to the force tray"]')
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


FILTER_IDS = ("filter-box", "filter-tag", "filter-copy-faction", "filter-copy-company", "filter-material",
              "filter-kind", "filter-era", "filter-faction", "filter-painted")


@unittest.skipUnless(chrome_path(), "Chrome is not installed")
class FilterSheetTests(unittest.TestCase):
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

    def wait_for(self, b, expression, message):
        deadline = time.monotonic() + 3
        while not b.js(expression) and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertTrue(b.js(expression), message)

    def assert_shows(self, b, selector):
        deadline = time.monotonic() + 3
        while not is_visible(b, selector) and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertTrue(is_visible(b, selector), f"{selector} is not visible")

    def assert_hides(self, b, selector):
        deadline = time.monotonic() + 3
        while is_visible(b, selector) and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertFalse(is_visible(b, selector), f"{selector} is still visible")

    def count(self, b):
        return b.js("document.querySelector('#view-count').textContent")

    def choose_faction(self, b, value="2"):
        before = self.count(b)
        b.type_into("#filter-faction", value)
        self.wait_for(b, f"document.querySelector('#view-count').textContent !== {before!r}", "the count did not change")

    def requests(self, b):
        return b.js("performance.getEntriesByType('resource').filter(e => e.name.includes('/api/collection')).length")

    def test_phone_toolbar_shows_only_search_filters_and_add(self):
        b = self.open()
        visible = b.js(
            "[...document.querySelectorAll('.toolbar input, .toolbar select, .toolbar button, .toolbar label')]"
            ".filter(e => e.getClientRects().length > 0 && getComputedStyle(e).visibility !== 'hidden')"
            ".map(e => e.id || e.tagName)"
        )
        self.assertEqual(visible, ["filter-q", "open-filters", "toggle-add"])
        self.assertFalse(is_visible(b, "#filter-faction"))
        self.assertFalse(is_visible(b, "#filter-sheet"))
        self.assertEqual(overflow(b), 0)

    def test_filter_sheet_opens_filters_and_closes(self):
        b = self.open()
        before = self.count(b)
        b.click("#open-filters")
        self.assert_shows(b, "#filter-sheet")
        self.assertEqual(b.js("document.querySelector('#filter-sheet').getAttribute('role')"), "dialog")
        self.assertEqual(b.js("document.querySelector('#filter-sheet').getAttribute('aria-modal')"), "true")
        self.wait_for(b, "Math.round(document.querySelector('#filter-sheet').getBoundingClientRect().bottom) "
                         "=== window.innerHeight", "the sheet does not reach the bottom of the screen")
        # The sheet and its backdrop are painted over the tab bar.
        self.assertTrue(b.js("(() => { const e = document.elementFromPoint(195, window.innerHeight - 4); "
                             "return !!e && !!e.closest('#filter-sheet'); })()"))
        self.assertTrue(b.js("(() => { const e = document.elementFromPoint(195, 4); "
                             "return !!e && e.id === 'sheet-backdrop'; })()"))
        self.choose_faction(b)
        after = self.count(b)
        self.assertNotEqual(after, before)
        self.assertEqual(b.js("document.querySelector('#filter-badge').textContent"), "1")
        b.click("#sheet-done")
        self.assert_hides(b, "#filter-sheet")
        self.assert_hides(b, "#sheet-backdrop")
        self.assertEqual(self.count(b), after)
        self.assertEqual(b.js("document.querySelector('#filter-badge').textContent"), "1")

    def test_clear_all_empties_every_filter_and_hides_the_badge(self):
        b = self.open()
        before = self.count(b)
        b.click("#open-filters")
        self.assert_shows(b, "#filter-sheet")
        self.assertFalse(is_visible(b, "#filter-badge"))
        self.choose_faction(b)
        b.type_into("#filter-box", "nothing")
        b.js("(() => { const e = document.querySelector('#filter-painted'); e.checked = true; "
             "e.dispatchEvent(new Event('change', {bubbles: true})); })()")
        b.type_into("#filter-q", "atlas")
        self.assertEqual(b.js("document.querySelector('#filter-badge').textContent"), "3")
        b.click("#filters-clear")
        for fid in FILTER_IDS:
            self.assertEqual(b.js(f"(() => {{ const e = document.getElementById('{fid}'); "
                                  "return e.type === 'checkbox' ? e.checked : e.value; })()"), False if fid == "filter-painted" else "", fid)
        # The search box is not part of the sheet.
        self.assertEqual(b.js("document.querySelector('#filter-q').value"), "atlas")
        self.assertFalse(is_visible(b, "#filter-badge"))
        b.type_into("#filter-q", "")
        self.wait_for(b, f"document.querySelector('#view-count').textContent === {before!r}", "the full list did not return")

    def test_escape_and_backdrop_close_the_sheet_and_focus_returns(self):
        b = self.open()
        for how in ("escape", "backdrop"):
            with self.subTest(how=how):
                b.click("#open-filters")
                self.assert_shows(b, "#filter-sheet")
                self.assertTrue(b.js("document.querySelector('#filter-sheet').contains(document.activeElement)"),
                                "focus did not move into the sheet")
                if how == "escape":
                    b.js("document.activeElement.dispatchEvent(new KeyboardEvent('keydown', "
                         "{key: 'Escape', bubbles: true, cancelable: true}))")
                else:
                    b.click("#sheet-backdrop")
                self.assert_hides(b, "#filter-sheet")
                self.assertEqual(b.js("document.activeElement.id"), "open-filters")

    def test_filter_controls_are_44px_tall_and_16px_font_in_the_sheet(self):
        b = self.open()
        b.click("#open-filters")
        self.assert_shows(b, "#filter-sheet")
        self.assert_shows(b, "#filter-faction")
        rows = b.js(
            "[...document.querySelectorAll('#filter-sheet input, #filter-sheet select')]"
            ".filter(e => e.getClientRects().length > 0 && e.type !== 'checkbox')"
            ".map(e => [e.id, e.getBoundingClientRect().height, parseFloat(getComputedStyle(e).fontSize)])"
        )
        self.assertGreaterEqual(len(rows), 9)  # 5 text boxes, 3 selects, group-by and sort
        for fid, height, font in rows:
            self.assertGreaterEqual(height, 43.99, fid)  # a select can measure 43.9998 from sub-pixel layout
            self.assertGreaterEqual(font, 16, fid)
        self.assertGreaterEqual(b.js("document.querySelector('#filter-painted').closest('label').getBoundingClientRect().height"), 44)
        self.assertEqual(
            b.js("[...document.querySelectorAll('#filter-sheet button')]"
                 ".filter(e => e.getBoundingClientRect().height < 44).map(e => e.id)"), [])

    def test_sheet_holds_grouping_and_sort_on_a_phone_only(self):
        b = self.open()
        self.assertTrue(b.js("!!document.querySelector('#filter-sheet #group-by-group') && "
                             "!!document.querySelector('#filter-sheet #card-sort')"))
        self.assertEqual(b.js("document.querySelectorAll('#group-by-group, #sort-group, #group-by, #card-sort').length"), 4)
        b.resize(1280, 800)
        self.assertEqual(b.js("[...document.querySelector('#view-controls').children].map(e => e.id).join()"),
                         ",density-group,group-by-group,group-actions,sort-group,view-count")
        b.resize(390, 844)
        self.assertTrue(b.js("!!document.querySelector('#filter-sheet #sort-group')"))

    def test_desktop_filters_stay_inline(self):
        b = self.open(1280, 800, mobile=False)
        for fid in ("filter-q", "filter-box", "filter-tag", "filter-copy-faction", "filter-copy-company",
                    "filter-material", "filter-kind", "filter-era", "filter-faction", "filter-painted", "toggle-add"):
            self.assertTrue(is_visible(b, f"#{fid}"), fid)
        for selector in ("#open-filters", "#filter-badge", "#sheet-backdrop", "#sheet-done", "#filters-clear", ".sheet-head"):
            self.assertFalse(is_visible(b, selector), selector)
        # The wrapper adds no box of its own, so every control is laid out as a child of the toolbar row.
        self.assertEqual(b.js("getComputedStyle(document.querySelector('#filter-sheet')).display"), "contents")
        self.assertTrue(b.js("(() => { const q = document.querySelector('#filter-q').getBoundingClientRect(); "
                             "const f = document.querySelector('#filter-faction').getBoundingClientRect(); "
                             "return Math.abs(q.top - f.top) < 60; })()"))

    def test_resizing_across_640_keeps_filters(self):
        b = self.open()
        self.choose_faction(b)
        shown = self.count(b)
        b.click("#open-filters")
        self.assert_shows(b, "#filter-sheet")
        b.resize(1280, 800)
        self.assert_hides(b, "#sheet-backdrop")
        self.assertEqual(b.js("document.querySelector('#filter-faction').value"), "2")
        self.assertTrue(is_visible(b, "#filter-faction"))
        b.resize(390, 844)
        self.assertFalse(is_visible(b, "#filter-sheet"), "the sheet stayed open across the breakpoint")
        self.assertEqual(b.js("document.querySelector('#filter-faction').value"), "2")
        self.assertEqual(b.js("document.querySelector('#filter-badge').textContent"), "1")
        self.assertEqual(self.count(b), shown)
        # The count text changing means the reload finished, so the request is already recorded.
        before = self.requests(b)
        self.choose_faction(b, "1")
        self.wait_for(b, f"performance.getEntriesByType('resource').filter(e => e.name.includes('/api/collection')).length >= {before + 1}",
                      "no request followed the change")
        self.assertEqual(self.requests(b), before + 1)

    def test_listeners_run_once_after_many_resizes(self):
        b = self.open()
        for _ in range(10):
            b.resize(1280, 800)
            b.resize(390, 844)
        self.wait_for(b, "!!document.querySelector('#filter-sheet #sort-group')", "the View controls did not move into the sheet")
        # Count the DOM effects of each handler: the badge text and the View controls moving.
        b.js("""(() => {
          window.__badge = 0; window.__sheet = 0; window.__bar = 0;
          const watch = (sel, key) => new MutationObserver((records) => { window[key] += records.length; })
            .observe(document.querySelector(sel), {childList: true});
          watch('#filter-badge', '__badge'); watch('#sheet-view', '__sheet'); watch('#view-controls', '__bar');
        })()""")
        # One input event is one badge update, and so is one change event.
        for event in ("input", "change"):
            b.js("(() => { window.__badge = 0; const e = document.querySelector('#filter-kind'); "
                 f"e.value = e.options[1].value; e.dispatchEvent(new Event('{event}', {{bubbles: true}})); }})()")
            self.wait_for(b, "window.__badge >= 1", f"the badge was not updated on {event}")
            self.assertEqual(b.js("window.__badge"), 1, f"badge updates for one {event} event")
        # Two controls move per crossing, so each crossing is two DOM moves.
        b.js("window.__bar = 0; window.__sheet = 0")
        b.resize(1280, 800)
        self.wait_for(b, "!!document.querySelector('#view-controls > #sort-group')", "Sort did not move back")
        self.assertEqual(b.js("window.__bar"), 2)
        b.js("window.__sheet = 0")
        b.resize(390, 844)
        self.wait_for(b, "!!document.querySelector('#filter-sheet #sort-group')", "Sort did not move into the sheet")
        self.assertEqual(b.js("window.__sheet"), 2)

    def test_sheet_is_a_dialog_only_while_it_is_open_on_a_phone(self):
        d = self.open(1280, 800, mobile=False)
        self.assertFalse(d.js("document.querySelector('#filter-sheet').hasAttribute('role')"))
        self.assertFalse(d.js("document.querySelector('#filter-sheet').hasAttribute('aria-modal')"))
        nodes = d.ws.send("Accessibility.getFullAXTree")["nodes"]
        dialogs = [n for n in nodes if not n.get("ignored") and n.get("role", {}).get("value") == "dialog"]
        self.assertEqual(dialogs, [], "a dialog is exposed on desktop")
        b = self.open()
        self.assertFalse(b.js("document.querySelector('#filter-sheet').hasAttribute('role')"))
        b.click("#open-filters")
        self.assert_shows(b, "#filter-sheet")
        self.assertEqual(b.js("document.querySelector('#filter-sheet').getAttribute('role')"), "dialog")
        self.assertEqual(b.js("document.querySelector('#filter-sheet').getAttribute('aria-modal')"), "true")
        b.click("#sheet-done")
        self.assert_hides(b, "#filter-sheet")
        self.assertFalse(b.js("document.querySelector('#filter-sheet').hasAttribute('role')"))
        self.assertFalse(b.js("document.querySelector('#filter-sheet').hasAttribute('aria-modal')"))

    def test_page_behind_the_open_sheet_is_inert(self):
        b = self.open()
        self.assertEqual(b.js("document.querySelectorAll('[inert]').length"), 0)
        b.click("#open-filters")
        self.assert_shows(b, "#filter-sheet")
        for sel in ("#tabbar", ".topbar", "#roster", "#open-filters", "#toggle-add", "#filter-q", "#tray"):
            self.assertTrue(b.js(f"document.querySelector('{sel}').matches(':is([inert], [inert] *)')"), sel)
        for sel in ("#filter-sheet", "#filter-faction", "#sheet-done", "#sheet-backdrop"):
            self.assertFalse(b.js(f"document.querySelector('{sel}').matches(':is([inert], [inert] *)')"), sel)
        b.click("#sheet-done")
        self.assert_hides(b, "#filter-sheet")
        self.assertEqual(b.js("document.querySelectorAll('[inert]').length"), 0)
        self.assertEqual(b.js("document.activeElement.id"), "open-filters")
        # Leaving phone mode with the sheet open frees the page.
        b.click("#open-filters")
        self.assert_shows(b, "#filter-sheet")
        b.resize(1280, 800)
        self.assert_hides(b, "#sheet-backdrop")
        self.assertEqual(b.js("document.querySelectorAll('[inert]').length"), 0)

    def test_focused_input_is_scrolled_into_view_in_the_sheet(self):
        b = self.open()
        b.click("#open-filters")
        self.assert_shows(b, "#filter-sheet")
        b.js("window.__centered = 0; Element.prototype.scrollIntoView = function (o) { "
             "if (o && o.block === 'center') window.__centered++; }")
        # A headless page may not be the focused window, so raise the event focus() would.
        b.js("document.querySelector('#filter-material').dispatchEvent(new FocusEvent('focusin', {bubbles: true}))")
        self.assertEqual(b.js("window.__centered"), 1)
        b.resize(1280, 800)
        b.js("document.querySelector('#filter-material').dispatchEvent(new FocusEvent('focusin', {bubbles: true}))")
        self.assertEqual(b.js("window.__centered"), 1, "scrolling is for the phone sheet only")

    def test_tray_hint_points_at_the_collection_tab(self):
        b = self.open()
        self.assertIn("set on the Collection tab", b.js("document.querySelector('#tray .small').textContent"))


@unittest.skipUnless(chrome_path(), "Chrome is not installed")
class CardsFirstTests(unittest.TestCase):
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

    def wait_for(self, b, expression, message):
        deadline = time.monotonic() + 3
        while not b.js(expression) and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertTrue(b.js(expression), message)

    def set_grouping(self, b, group_by):
        b.click("#open-filters")
        b.click('#group-by-group [data-group="1"]')
        b.js(f"(() => {{ const s = document.querySelector('#group-by'); s.value = {group_by!r}; "
             "s.dispatchEvent(new Event('change', { bubbles: true })); })()")
        b.click("#sheet-done")

    def small_card_targets(self, b, minimum=44):
        return b.js("""(() => {
          const out = [];
          for (const el of document.querySelectorAll('.mini-card button, .mini-card input, .mini-card select')) {
            const r = el.getBoundingClientRect();
            if (!r.width || !r.height) continue;
            // Half a pixel of slack: while the page's slide-in animation runs, a 44px box can measure 43.9999.
            if (r.width >= %d - 0.5 && r.height >= %d - 0.5) continue;
            if (el.type === 'checkbox' && el.closest('label')?.getBoundingClientRect().height >= %d - 0.5) continue;
            out.push(`${el.tagName.toLowerCase()}.${String(el.className).split(' ').join('.')} ${Math.round(r.width)}x${Math.round(r.height)}`);
          }
          return out;
        })()""" % (minimum, minimum, minimum))

    def test_phone_uses_cards_and_hides_view_and_density_controls(self):
        b = self.open()
        self.assertFalse(is_visible(b, "#roster"))
        self.assertEqual(b.js("document.querySelectorAll('.mini-card').length"), MINIS)
        self.assertFalse(is_visible(b, "[data-view=table]"))
        self.assertFalse(is_visible(b, "#density-group"))

    def test_phone_does_not_change_the_saved_desktop_choice(self):
        b = self.open()
        b.js("localStorage.setItem('batchall.view', JSON.stringify({view: 'table'}))")
        b.open(self.base + "/")
        self.set_grouping(b, "company")
        saved = b.js("JSON.parse(localStorage.getItem('batchall.view')).view")
        self.assertEqual(saved, "table")
        b.resize(1280, 800)
        self.wait_for(b, "document.querySelector('#roster') && document.querySelector('#roster').offsetParent !== null",
                      "the table did not come back on a wide screen")

    def test_card_touch_targets_are_44px(self):
        b = self.open()
        self.assertEqual(b.js("document.querySelectorAll('.mini-card').length"), MINIS)
        self.assertEqual(self.small_card_targets(b), [])

    def test_editing_card_spans_the_row_and_its_controls_are_44px(self):
        b = self.open()
        b.click('.mini-card button[title="Edit this mini"]')
        self.wait_for(b, "!!document.querySelector('.mini-card.is-editing .copy-editor')", "the editor did not open")
        card, grid = b.js("""(() => { const c = document.querySelector('.mini-card.is-editing').getBoundingClientRect();
          const g = document.querySelector('#card-grid').getBoundingClientRect(); return [c.width, g.width]; })()""")
        self.assertAlmostEqual(card, grid, delta=1)
        self.assertEqual(self.small_card_targets(b), [])
        self.assertEqual(overflow(b), 0)

    def test_one_column_at_360_and_two_at_390(self):
        left_edges = """(() => [...new Set([...document.querySelectorAll('.mini-card')]
          .filter((c) => c.getBoundingClientRect().top < document.querySelector('.mini-card').getBoundingClientRect().bottom)
          .map((c) => Math.round(c.getBoundingClientRect().left)))].length)()"""
        self.assertEqual(self.open(360, 800).js(left_edges), 1)
        self.assertEqual(self.open(390, 844).js(left_edges), 2)

    def test_grouping_still_works_on_a_phone(self):
        b = self.open()
        self.set_grouping(b, "company")
        self.wait_for(b, "document.querySelectorAll('.card-group-head').length > 0", "no group headers appeared")
        heads = b.js("[...document.querySelectorAll('.card-group-head')].map((h) => Math.round(h.getBoundingClientRect().height))")
        self.assertTrue(all(h >= 44 for h in heads), f"group headers are too short: {heads}")
        before = b.js("document.querySelectorAll('.mini-card').length")
        b.click(".card-group-head button")
        self.wait_for(b, f"document.querySelectorAll('.mini-card').length < {before}", "tapping a group header did not collapse it")

    def test_no_sideways_scroll_with_grouping_on(self):
        b = self.open()
        values = b.js("[...document.querySelectorAll('#group-by option')].map((o) => o.value)")
        for value in values:
            with self.subTest(group_by=value):
                self.set_grouping(b, value)
                self.assertEqual(overflow(b), 0)

    def test_tap_a_name_opens_the_detail_panel_and_close_is_reachable(self):
        b = self.open()
        b.js("window.scrollTo(0, 300)")
        before = b.js("Math.round(window.scrollY)")
        b.click(".mini-card .unit-link")
        self.wait_for(b, "!!document.querySelector('.detail-close')", "the detail panel did not open")
        close = b.js("""(() => { const r = document.querySelector('.detail-close').getBoundingClientRect();
          return [Math.round(r.height), r.top >= 0, r.bottom <= innerHeight, r.left >= 0, r.right <= innerWidth]; })()""")
        self.assertGreaterEqual(close[0], 44)
        self.assertEqual(close[1:], [True, True, True, True])
        b.click(".detail-close")
        self.wait_for(b, "!document.querySelector('.detail-close')", "the detail panel did not close")
        self.assertEqual(b.js("Math.round(window.scrollY)"), before)


if __name__ == "__main__":
    unittest.main()
