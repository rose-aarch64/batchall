# Shared Collection Link Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A signed-in user can create a private link that lets anyone, without an account, browse that user's collection and saved forces in a read-only copy of the normal UI.

**Architecture:** A `ShareLink` row in the accounts database maps an unguessable token to a user. New routes under `/s/<token>/` validate the token, then serve either the static UI or a read-only subset of the existing `batchall.server.ROUTES` handlers. Those handlers run against the owner's SQLite file opened with `mode=ro`. The web UI detects the `/s/<token>` prefix and switches to a read-only mode.

**Tech Stack:** Python 3.12 standard library for `batchall/`; Django 5.2 for `hosted/`; vanilla JS in `web/app.js`; `unittest` and Django `TestCase`.

**Spec:** `docs/superpowers/specs/2026-11-07-shared-collection-link-design.md`

## Global Constraints

- Sharing exists in hosted mode only. Local mode (`python3 -m batchall serve`) is unchanged.
- `batchall/` and `tests/` stay standard library only. Hosted code lives in `hosted/`.
- Shared routes never write to the owner's database file. They open it with `mode=ro`.
- The server refuses every write on shared routes except `POST /api/build`, which stores nothing.
- Invalid, revoked and replaced links return the same 404.
- The shared page exposes the owner's display name or handle only. No email, account id, file path or admin data.
- One active link per user. Creating a new link replaces the old one. No link exists until the owner creates one.
- Token: 32 random bytes from `secrets`, compared with `secrets.compare_digest`.
- `web/index.html` keeps no inline script and no `style=` attribute (checked by `hosted/tests/test_web.py`).
- Code, comments, commit messages and docs stay plain and professional.
- Existing tests keep passing: `python3 -m unittest discover -s tests` and `.venv/bin/python manage.py test hosted`.
- Work on a new branch `shared-link` cut from `main`.

## Review Focus

Each line below has its test in the task named after it.

1. A malformed token (empty secret, non-numeric id, 5,000 characters, unicode) gets the 404, never a 500. (Task 1)
2. A signed-in visitor, including the owner or a different user, sees only the token owner's data. (Task 4)
3. A token whose owner has no data file yet gets the 404, not a 500 and not a created file. (Task 2)
4. Another user's mini id requested through this token gets 404 and never returns their photo. (Task 4)
5. Deleting the owner's account kills the link. (Task 1)

---

### Task 1: ShareLink model and helpers

**Files:**
- Modify: `hosted/models.py`
- Create: `hosted/migrations/0002_sharelink.py` (generated)
- Create: `hosted/sharing.py`
- Create: `hosted/tests/test_sharing.py`

**Interfaces:**
- Produces: `hosted.models.ShareLink` with `user` (OneToOne to `AUTH_USER_MODEL`, `related_name="share_link"`, `on_delete=CASCADE`), `secret` (`CharField(max_length=43)`), `created_at`, and property `token -> str` equal to `f"{pk}.{secret}"`.
- Produces in `hosted/sharing.py`:
  - `create_link(user) -> ShareLink` replaces any existing link in one transaction.
  - `revoke_link(user) -> bool` returns whether a link existed.
  - `current_link(user) -> ShareLink | None`
  - `resolve(token: str) -> User | None` returns `None` for every invalid form.
  - `link_path(link: ShareLink) -> str` returns `f"/s/{link.token}/"`.
  - `TOKEN_PATTERN = r"[A-Za-z0-9_.\-]{1,200}"` for use in URL routes.

- [ ] **Step 1: Write the failing tests** in `hosted/tests/test_sharing.py` (Django `TestCase`, users from `accounts.create_user("alice", PASSWORD)`).

```python
def test_create_then_resolve(self):
    link = sharing.create_link(self.alice)
    self.assertEqual(sharing.resolve(link.token), self.alice)

def test_new_link_replaces_old(self):
    old = sharing.create_link(self.alice)
    new = sharing.create_link(self.alice)
    self.assertIsNone(sharing.resolve(old.token))
    self.assertEqual(sharing.resolve(new.token), self.alice)
    self.assertEqual(ShareLink.objects.filter(user=self.alice).count(), 1)

def test_revoke(self):
    link = sharing.create_link(self.alice)
    self.assertTrue(sharing.revoke_link(self.alice))
    self.assertIsNone(sharing.resolve(link.token))
    self.assertFalse(sharing.revoke_link(self.alice))

def test_malformed_tokens_resolve_to_none(self):
    link = sharing.create_link(self.alice)
    pk = link.pk
    for bad in ["", ".", f"{pk}.", f".{link.secret}", f"x.{link.secret}", f"{pk}.wrong",
                f"{pk}.{link.secret}x", "9" * 40 + ".abc", "é.ü", "a" * 5000, f"{pk}.{link.secret}.extra"]:
        self.assertIsNone(sharing.resolve(bad), bad)

def test_deleting_the_user_removes_the_link(self):
    link = sharing.create_link(self.alice)
    self.alice.delete()
    self.assertIsNone(sharing.resolve(link.token))
```

- [ ] **Step 2: Run to verify failure.** Run: `.venv/bin/python manage.py test hosted.tests.test_sharing`. Expected: ImportError for `hosted.sharing`.

- [ ] **Step 3: Implement** `ShareLink` in `hosted/models.py` and generate the migration with `.venv/bin/python manage.py makemigrations hosted -n sharelink`. Implement `hosted/sharing.py` per the Interfaces block. `resolve` splits on the first `.`, rejects a non-digit or over-`2**63-1` id, fetches the row by pk, and compares secrets with `secrets.compare_digest` on UTF-8 bytes. When the row is missing it compares against a fixed dummy secret so both paths do the same work. Use `secrets.token_urlsafe(32)` for the secret.

- [ ] **Step 4: Run to verify pass.** Run the command from Step 2. Expected: all tests pass.

- [ ] **Step 5: Commit** the spec, this plan, and the code.

```bash
git switch -c shared-link
git add docs/superpowers hosted/models.py hosted/migrations/0002_sharelink.py hosted/sharing.py hosted/tests/test_sharing.py
git commit -m "Add share links to the accounts database"
```

### Task 2: Read-only connection to a user's database

**Files:**
- Modify: `batchall/db.py` (next to `connect_user`, around line 255)
- Create: `tests/test_db_readonly.py`

**Interfaces:**
- Produces: `db.connect_user_readonly(user_id: int, path=None) -> sqlite3.Connection`. It has `row_factory = sqlite3.Row`, the user's file as `main` and the shared database attached as `shared`, both opened `mode=ro`. It raises `FileNotFoundError` when the user's file does not exist and creates nothing.

- [ ] **Step 1: Write the failing tests** (copy the temp-dir setup used in `tests/test_db_split.py`).

```python
def test_reads_user_and_shared_tables(self):
    conn = db.connect_user_readonly(self.uid, self.shared)
    self.assertEqual(conn.execute("SELECT COUNT(*) FROM collection").fetchone()[0], 1)
    self.assertGreater(conn.execute("SELECT COUNT(*) FROM units").fetchone()[0], 0)

def test_writes_are_refused(self):
    conn = db.connect_user_readonly(self.uid, self.shared)
    with self.assertRaises(sqlite3.OperationalError):
        conn.execute("DELETE FROM collection")
    with self.assertRaises(sqlite3.OperationalError):
        conn.execute("DELETE FROM shared.units")

def test_missing_user_file_raises_and_creates_nothing(self):
    with self.assertRaises(FileNotFoundError):
        db.connect_user_readonly(999, self.shared)
    self.assertFalse(db.user_db_path(self.shared, 999).exists())
```

- [ ] **Step 2: Run to verify failure.** Run: `python3 -m unittest tests.test_db_readonly -v`. Expected: AttributeError.

- [ ] **Step 3: Implement `connect_user_readonly`.** Open `f"{target.resolve().as_uri()}?mode=ro"` with `uri=True`, then `ATTACH DATABASE` the shared file as a `?mode=ro` URI. Run no PRAGMA that writes and no schema script. Confirm the per-user files use WAL (`PRAGMA journal_mode` on a file made by `connect_user` returns `wal`); if a read-only open cannot read a WAL file whose `-shm` is missing, add a test and handle it. Record what you found in the commit message.

- [ ] **Step 4: Run to verify pass.** Run: `python3 -m unittest tests.test_db_readonly -v`. Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add batchall/db.py tests/test_db_readonly.py
git commit -m "Add a read-only connection to a user's database"
```

### Task 3: Extract the API dispatcher

Behavior does not change. The shared API reuses this dispatcher so route matching, body parsing, error mapping and the owned-id check exist once.

**Files:**
- Modify: `hosted/api.py`
- Test: existing `hosted/tests/test_api_isolation.py` (no new test)

**Interfaces:**
- Produces: `dispatch(request, path, *, open_conn, allowed=None) -> HttpResponse` in `hosted/api.py`.
  - `open_conn` is a zero-argument callable returning a connection. It may raise `FileNotFoundError`, which becomes the `{"error": "not found"}` 404.
  - `allowed` is `None` (every route) or a set of `(method, handler)` pairs. A matched route outside the set is answered like an unmatched method: 405 `{"error": "no such endpoint"}`.
  - It does everything `api` does after the authentication and multi-user checks.
- `api(request, path)` keeps its signature and order: route match, then the 401 check, then the 503 check, then `dispatch(request, path, open_conn=lambda: db.connect_user(request.user.id, settings.BATCHALL_SHARED_DB))`.

- [ ] **Step 1: Run the baseline.** Run: `.venv/bin/python manage.py test hosted`. Expected: all pass. Note the count.
- [ ] **Step 2: Extract** `dispatch` from the body of `api` as specified. Do not change messages, statuses or headers.
- [ ] **Step 3: Run `.venv/bin/python manage.py test hosted`.** Expected: same count, all pass.
- [ ] **Step 4: Commit.**

```bash
git add hosted/api.py
git commit -m "Split the API dispatcher out of the hosted adapter"
```

### Task 4: Shared API and page routes

**Files:**
- Create: `hosted/shared.py`
- Modify: `hosted/urls.py`
- Create: `hosted/tests/share_fixture.py` (a `ShareFixture(TestCase)` base: temp shared DB with `seed_catalog` and `mark_multiuser`, users alice and bob, alice with one entry, a mini photo, a saved force, and a link; same setup as `test_api_isolation.py`)
- Create: `hosted/tests/test_shared_api.py`

**Interfaces:**
- Consumes: `sharing.resolve`, `sharing.TOKEN_PATTERN`, `db.connect_user_readonly`, `api.dispatch`.
- Produces in `hosted/shared.py`:
  - `ALLOWED: frozenset[tuple[str, Callable]]` containing a `("GET", handler)` pair for each of these functions in `batchall.server`: `get_meta`, `get_catalog_search`, `get_collection`, `get_boxes`, `get_entry_boxes`, `get_unit_art`, `get_faction_icon`, `get_company_logos`, `get_company_logo`, `get_mini_photo`, `get_faction_colors`, `get_company_colors`, `get_unit_detail`, `get_variants`, `get_copy_tags`, `get_copy_factions`, `get_copy_companies`, `get_copy_materials`, `get_forces`, `get_force_export`. It also contains `("POST", server.post_build)`. It leaves out every other handler, including `get_auth_me`, `get_company_logo_candidates` (it makes outbound requests) and the box catalog.
  - `shared_view(request, token, path) -> HttpResponse`, decorated `csrf_exempt` because it uses no session or cookie.
- URL: `re_path(rf"^s/(?P<token>{TOKEN_PATTERN})/(?P<path>.*)$", shared.shared_view)`, placed before the `^api/` and catch-all routes.
- Behavior of `shared_view`: unresolved token or missing data file gives `JsonResponse({"error": "not found"}, status=404)` for every cause. A path starting `api/` answers `auth/me` with `{"mode": "shared", "owner": <display name or handle>}` and sends everything else to `dispatch(..., allowed=ALLOWED, open_conn=lambda: db.connect_user_readonly(user.id, settings.BATCHALL_SHARED_DB))`. Any other path goes to `pages.page(request, path)`.
- Requests ignore `request.user` completely.

- [ ] **Step 1: Write the failing tests** in `test_shared_api.py`. Define `base = f"/s/{link.token}"`.

```python
def test_page_and_assets_load_under_the_prefix(self):
    for rel in ("", "app.js", "style.css"):
        self.assertEqual(self.client.get(f"{self.base}/{rel}").status_code, 200)

def test_collection_is_the_owners(self):
    data = self.client.get(f"{self.base}/api/collection").json()
    self.assertEqual([e["unit_uuid"] for e in data], ["u-atlas"])

def test_photo_and_forces_are_served(self):
    self.assertEqual(self.client.get(f"{self.base}/api/minis/{self.mini}/photo").status_code, 200)
    self.assertEqual([f["name"] for f in self.client.get(f"{self.base}/api/forces").json()], ["Alice lance"])

def test_me_reports_shared_mode_without_private_fields(self):
    me = self.client.get(f"{self.base}/api/auth/me").json()
    self.assertEqual(set(me), {"mode", "owner"})
    self.assertEqual(me["mode"], "shared")

def test_bad_revoked_and_replaced_tokens_give_the_same_404(self):
    old = self.link.token
    sharing.create_link(self.alice)           # replaces
    answers = {self.client.get(f"/s/{t}/api/collection").content
               for t in (old, "nope", "1.x", "9" * 5000, "é")}
    self.assertEqual(len(answers), 1)
    self.assertEqual(self.client.get(f"/s/{old}/").status_code, 404)

def test_signed_in_visitor_still_sees_only_the_token_owner(self):
    self.client.force_login(self.bob)
    data = self.client.get(f"{self.base}/api/collection").json()
    self.assertEqual([e["unit_uuid"] for e in data], ["u-atlas"])

def test_other_users_mini_id_is_404(self):
    self.assertEqual(self.client.get(f"{self.base}/api/minis/{self.bob_mini}/photo").status_code, 404)

def test_every_other_route_is_refused_and_nothing_changes(self):
    before = self.snapshot_alice()            # row counts for every user table
    for method, pattern, handler in server.ROUTES:
        if (method, handler) in shared.ALLOWED:
            continue
        path = pattern.pattern.strip("^$").replace(r"(\d+)", str(self.mini)).replace(r"([^/]+)", "u-atlas")
        response = self.client.generic(method, f"{self.base}{path}", b"{}", content_type="application/json")
        self.assertIn(response.status_code, (404, 405), f"{method} {path}")
    self.assertEqual(self.snapshot_alice(), before)

def test_build_works_without_a_session_and_stores_nothing(self):
    before = self.snapshot_alice()
    response = self.client.post(f"{self.base}/api/build", {"target_bv": 2000}, content_type="application/json")
    self.assertEqual(response.status_code, 200)
    self.assertEqual(self.snapshot_alice(), before)

def test_owner_without_a_data_file_gets_404_and_no_file(self):
    carol = accounts.create_user("carol", PASSWORD)
    token = sharing.create_link(carol).token
    self.assertEqual(self.client.get(f"/s/{token}/api/collection").status_code, 404)
    self.assertFalse(db.user_db_path(self.shared, carol.id).exists())

def test_malformed_token_in_a_page_url_is_404_not_500(self):
    for t in ("", "%00", "a" * 5000):
        self.assertEqual(self.client.get(f"/s/{t}/").status_code, 404)
```

- [ ] **Step 2: Run to verify failure.** Run: `.venv/bin/python manage.py test hosted.tests.test_shared_api`. Expected: failures (404 on every route, or ImportError).
- [ ] **Step 3: Implement** `hosted/shared.py` and the URL entry as specified. `ALLOWED` is built from `server.` names, so a renamed handler fails at import.
- [ ] **Step 4: Run to verify pass.** Run the Step 2 command, then `.venv/bin/python manage.py test hosted`. Expected: all pass.
- [ ] **Step 5: Commit.**

```bash
git add hosted/shared.py hosted/urls.py hosted/tests/share_fixture.py hosted/tests/test_shared_api.py
git commit -m "Serve a read-only copy of a collection under a share link"
```

### Task 5: Owner endpoints to create and revoke the link

**Files:**
- Modify: `hosted/authviews.py`, `hosted/urls.py`
- Create: `hosted/tests/test_share_endpoints.py`

**Interfaces:**
- Produces `/api/auth/share`, following the decorator and error style already in `hosted/authviews.py`:
  - `GET` returns `{"url": "/s/<token>/"}` or `{"url": null}`.
  - `POST` creates or replaces the link and returns `{"url": "/s/<token>/"}`.
  - `DELETE` revokes it and returns `{"url": null}`.
  - All three require sign-in (401) and, for writes, the CSRF token.
- The URL is a path. The page builds the absolute link from `location.origin`.

- [ ] **Step 1: Write the failing tests** (client with `enforce_csrf_checks=True`, sign in with the pattern in `test_authviews.py`).

```python
def test_signed_out_is_401(self):
    for method in ("get", "post", "delete"):
        self.assertEqual(getattr(self.anon, method)("/api/auth/share").status_code, 401)

def test_lifecycle(self):
    self.assertIsNone(self.get().json()["url"])
    first = self.post().json()["url"]
    self.assertEqual(self.get().json()["url"], first)
    second = self.post().json()["url"]
    self.assertNotEqual(first, second)
    self.assertEqual(self.anon.get(first).status_code, 404)    # replaced
    self.assertEqual(self.anon.get(second).status_code, 200)
    self.assertEqual(self.delete().json()["url"], None)
    self.assertEqual(self.anon.get(second).status_code, 404)

def test_write_without_csrf_token_is_403(self):
    self.assertEqual(self.client_no_token.post("/api/auth/share").status_code, 403)

def test_links_are_per_user(self):
    self.post()
    self.assertIsNone(self.bob_get().json()["url"])
```

- [ ] **Step 2: Run to verify failure.** Run: `.venv/bin/python manage.py test hosted.tests.test_share_endpoints`. Expected: 404 responses.
- [ ] **Step 3: Implement** the view and the `path("api/auth/share", ...)` entry above the `^api/` catch-all. Use `sharing.create_link`, `revoke_link`, `current_link`, `link_path`.
- [ ] **Step 4: Run to verify pass.** Run the Step 2 command. Expected: PASS.
- [ ] **Step 5: Commit.**

```bash
git add hosted/authviews.py hosted/urls.py hosted/tests/test_share_endpoints.py
git commit -m "Add endpoints to create, read and revoke the share link"
```

### Task 6: Read-only mode in the web UI

**Files:**
- Modify: `web/app.js`, `web/style.css`, `web/index.html` (only if an element needs an id)
- Create: `hosted/tests/test_share_browser.py`

**Interfaces:**
- Produces in `web/app.js`:
  - `const SHARE_BASE = (location.pathname.match(/^\/s\/[A-Za-z0-9_.\-]+/) || [""])[0]` and `const SHARED = SHARE_BASE !== ""`.
  - `apiUrl(path: string) -> string` returns `SHARE_BASE + path`. `api()` calls it for every request.
  - In shared mode `api()` rejects any request that is not `GET` or `POST /api/build` with `Error("read-only")` before sending it.
  - `document.body.classList.add("shared")` and `owner` display set during `init()` from `GET /api/auth/me` when `mode === "shared"`.

- [ ] **Step 1: Write the failing browser test** in `hosted/tests/test_share_browser.py`. Use `LiveServerTestCase`, the `Browser` class from `tests/browser.py` (add `tests/` to `sys.path` as `test_api_isolation.py` does) and `@unittest.skipUnless(chrome_path(), "Chrome is not installed")`. Seed alice with the fixture from `ShareFixture`, then:

```python
def test_shared_page_is_read_only(self):
    b = self.open(f"{self.live_server_url}{self.link_path}", 1280, 900)
    self.assertGreaterEqual(b.js("document.querySelectorAll('#roster tbody tr').length"), 1)
    self.assertEqual(b.console_errors(), [])
    for selector in ("#toggle-add", ".tab[data-view=import]", "#tray-save", "#bulk-remove", "#sign-out"):
        self.assertFalse(is_visible(b, selector), selector)
    self.assertTrue(b.js("document.body.classList.contains('shared')"))

def test_no_write_request_is_sent_while_browsing(self):
    b = self.open(...)
    b.js("window.__writes = []; const f = window.fetch; "
         "window.fetch = (u, o) => { if (o && o.method && o.method !== 'GET' && !/\\/api\\/build$/.test(u)) window.__writes.push(o.method + ' ' + u); return f(u, o); }")
    # open the first row's detail panel, switch to cards view, open a force
    self.assertEqual(b.js("window.__writes"), [])

def test_build_for_me_works(self):
    # add one row to the tray, press #tray-build, expect a toast and tray content
```

Check `tests/browser.py` for `open`, `click`, `js`, `console_errors` and `is_visible` signatures before writing this.

- [ ] **Step 2: Run to verify failure.** Run: `.venv/bin/python manage.py test hosted.tests.test_share_browser`. Expected: FAIL (the add button is visible, requests lack the prefix) or skip if Chrome is missing. If it skips, say so in the commit message and run the checks manually in Step 5.
- [ ] **Step 3: Prefix every request.** Route `api()` through `apiUrl`. Replace the string-built image URLs that bypass `api()` with `apiUrl(...)`. Find them with `grep -n '"/api/\|`/api/' web/app.js`; known sites are the faction icon (line ~653), company logo image (~696) and unit art (~1756), plus the mini photo URL. Leave `/api/auth/*` calls in place; the shared server answers `auth/me` and nothing else is called in shared mode.
- [ ] **Step 4: Remove the edit controls.** CSS in `web/style.css`: `body.shared` hides `#toggle-add`, `#add-panel`, the Import tab in both `.tab[data-view=import]` and `#tabbar [data-tab=import]`, `#view-import`, the bulk bar and its select-all and checkbox column, `#tray-save`, and `#account`. In JS, skip rendering the control for every mutating call site found by `grep -n 'api("\(POST\|PUT\|PATCH\|DELETE\)"' web/app.js`. Today these are: company color and logo editing (lines ~815-896), inline copy field edits and bulk changes (~947, 977, 1168, 1225), add and remove mini (~992, 1004), photo upload, crop and removal (~1700-1711), variant swap (~1917), box add (~1933, 1981), quick add (~1990), force save and delete (~2105, 2196), import (~2208). Show copy fields as plain text. Keep `POST /api/build` and the tray.
- [ ] **Step 5: Run to verify pass.** Run `.venv/bin/python manage.py test hosted`, then `python3 -m unittest discover -s tests` (the layout tests prove local mode is unchanged). Also open a share link in a browser and look at table and cards views on a phone width.
- [ ] **Step 6: Commit.**

```bash
git add web/app.js web/style.css web/index.html hosted/tests/test_share_browser.py
git commit -m "Add a read-only mode to the web UI for share links"
```

### Task 7: Owner's Share control

**Files:**
- Modify: `web/app.js` (near `showAccount`, line ~2466), `web/style.css`
- Modify: `hosted/tests/test_web.py` (assertions only)

**Interfaces:**
- Consumes: `/api/auth/share` from Task 5.
- Produces: a **Share** button in the `#account` box beside Sign out, shown only in hosted mode. It opens the existing modal helper (see the photo editor near line 1700 for usage) with: the full link (`location.origin + url`) in a read-only text input, **Copy**, **New link** (POST), and **Turn off** (DELETE). When no link exists the dialog shows only **Create link**.

- [ ] **Step 1: Write the failing test.** Extend `test_share_browser.py` (owner signs in with `force_login` cookie set via the live server login endpoint):

```python
def test_owner_can_create_and_revoke(self):
    # click #share-open, click [data-share=create]; the input value starts with origin + "/s/"
    # open that URL in a second page: rows load. click [data-share=off]; reload the second page: not found.
```

- [ ] **Step 2: Run to verify failure.** Run: `.venv/bin/python manage.py test hosted.tests.test_share_browser`. Expected: FAIL, no `#share-open`.
- [ ] **Step 3: Implement** the button and dialog. Build elements with the existing `h()` helper. No inline handlers, no `style=` attributes.
- [ ] **Step 4: Run to verify pass.** Run the Step 2 command, `.venv/bin/python manage.py test hosted.tests.test_web`, and the full suites from Task 6 Step 5.
- [ ] **Step 5: Commit.**

```bash
git add web/app.js web/style.css hosted/tests/test_web.py hosted/tests/test_share_browser.py
git commit -m "Let signed-in users create, copy and turn off their share link"
```

### Task 8: Docs and full check

**Files:**
- Modify: `README.md` (hosted-mode section: how to share a collection and what visitors can and cannot do)
- Modify: `HANDOFF.md` (gitignored; refresh the stale "branch not merged" status and move shared links out of Phase 2)

- [ ] **Step 1: Update both files.** State plainly that links are unguessable but not secret from anyone who has them, and that local mode has no sharing.
- [ ] **Step 2: Run everything.** Run: `python3 -m unittest discover -s tests` and `.venv/bin/python manage.py test hosted`. Expected: all pass. Report the counts.
- [ ] **Step 3: Commit.**

```bash
git add README.md
git commit -m "Document share links"
```
