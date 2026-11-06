# Accounts, Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let several people sign in to one Batchall server, each with a private catalog, while Rose's existing data becomes the first account and local no-login mode keeps working.

**Architecture:** Shared reference data and the account tables stay in the existing database file, `data/batchall.db`. Each user's collection, minis, photos, forces, logos and colors move to a file of their own, `data/users/<id>.db`. A request opens that user's file as `main` and attaches the shared file, so every existing query in `collection.py`, `copies.py`, `forces.py`, `builder.py` and the rest runs unchanged and can only see one user's rows. Sessions are server-side rows keyed by a hashed random token.

**Tech Stack:** Python 3.12 standard library only (`sqlite3`, `hashlib.scrypt`, `hmac`, `secrets`, `http.server`), plain JavaScript in `web/`.

**Spec:** `handoff/requirements.md` (untracked, local). This plan covers R1, R2, R3, R10, R11, R12, R13, plus invite-only sign-up and "no email", both decided by Rose on 2026-11-06. R4 to R9, R14 and R15 are phase 2.

## Spec deviations

Both change a mechanism, not an outcome. Rose should confirm the first before work starts.

1. **R3 says add `user_id` columns. This plan uses one database file per user.** Isolation then holds by construction: a connection has no way to name another user's row, so the "guessable `/api/minis/12/photo`" anti-goal cannot happen through a missed `WHERE`. It also leaves every domain module and nearly all 422 existing tests untouched. The cost lands in phase 2: the public front page must either open each public user's file or read a summary table kept in the shared file.
2. **R3 says other users' ids answer 404.** Missing ids answer 400 today. In multi-user mode the server checks that the id exists in the caller's database before the handler runs and answers 404. Local mode keeps its current statuses.

## Global Constraints

- Standard library only. No new packages. Python 3.12.
- SQLite stays the database. WAL mode on the shared file and on every user file.
- Passwords: `hashlib.scrypt` with a per-user salt, minimum length 10, never logged, never returned.
- Handles: lowercase letters, digits and hyphens, 3 to 30 characters, unique ignoring case, not one of the reserved paths.
- Sign-in compares in constant time. A wrong handle and a wrong password give the same message.
- Sessions: random 32-byte token stored hashed, cookie `HttpOnly`, `SameSite=Lax`, path `/`, `Secure` over HTTPS. The token changes on sign-in. Sign-out deletes the row.
- Mutating requests keep the `Content-Type: application/json` rule and also check `Origin`, when present, against the allowed hosts.
- The API never accepts a `user_id` from the client.
- The existing tests keep passing. New behavior gets new tests.
- CLI JSON output shapes do not change. `python3 -m batchall ...` works with no new arguments against a single-user database.
- `serve` on loopback with no flags behaves exactly as today.
- Code, comments, commit messages and docs are plain and professional. No `Co-Authored-By` trailer on any commit.
- Work on a branch named `accounts-phase-1`. Run the suite with `python3 -m unittest discover -s tests`. Run one file with `python3 -m unittest discover -s tests -p "test_name.py"`.
- Never run a migration against the real `data/batchall.db` during development. Use a copy in the scratchpad.

## Review Focus

Failure modes the spec implies that no requirement names. Each has a test in the task that owns the code.

1. **Two sign-ups race for one invite code.** Exactly one wins. (Task 2)
2. **Handle `Rose`, `ROSE`, `api`, `../x`.** The first two collide with `rose`, the last two are refused. (Task 2)
3. **A cookie that is garbage, expired, or belongs to a deleted account.** Answers 401, never 500. (Tasks 3, 5, 8)
4. **A 100,000-character or non-ASCII password.** Refused or accepted cleanly, never a crash or a slow hash. (Task 2)
5. **`admin init` run twice, or on a database with no minis.** The second run changes nothing, the empty case works. (Task 6)

---

## File Structure

| File | Responsibility |
|---|---|
| `batchall/db.py` (modify) | Split schema, `connect_shared`, `connect_user`, `is_multiuser`, account tables |
| `batchall/accounts.py` (create) | Handles, passwords, users, invites, password change, deletion |
| `batchall/sessions.py` (create) | Session tokens and lookup |
| `batchall/ratelimit.py` (create) | In-memory failure counter with lockout |
| `batchall/multiuser.py` (create) | `adopt`: move an existing database to multi-user form |
| `batchall/serversettings.py` (create) | `Settings` and reading it from flags and environment |
| `batchall/authapi.py` (create) | Handlers for `/api/auth/*` |
| `batchall/server.py` (modify) | Multi-user dispatch, host and Origin checks, headers, ownership precheck |
| `batchall/cli.py` (modify) | `--user`, `admin` commands, `serve` flags |
| `web/login.html`, `web/login.js` (create), `web/app.js`, `web/index.html`, `web/style.css` (modify) | Sign-in page, sign-out, 401 handling |
| `tests/support.py` (modify) | `seed_catalog(conn)` shared by `make_db` and new fixtures |

---

### Task 1: Split the schema and open per-user connections

**Files:**
- Modify: `batchall/db.py`, `tests/support.py`
- Test: `tests/test_db_split.py`

**Interfaces:**
- Produces:
  - `db.SHARED_SCHEMA`, `db.USER_SCHEMA`, `db.ACCOUNT_SCHEMA`: SQL strings. `db.SCHEMA == SHARED_SCHEMA + USER_SCHEMA`.
  - `db.USER_TABLES: tuple[str, ...]` parents first: `collection, copies, mini_photos, forces, force_units, company_logos, company_colors`.
  - `class db.MultiUserDatabase(ValueError)`.
  - `db.is_multiuser(path=None) -> bool`: true when the file has a `meta` row `multiuser = '1'`. False for a missing file or a file with no `meta` table. Opens read-only.
  - `db.connect_shared(path=None) -> sqlite3.Connection`: shared plus account tables only, WAL on.
  - `db.user_db_path(path, user_id) -> Path`: `<dir of path>/users/<user_id>.db`.
  - `db.connect_user(user_id, path=None) -> sqlite3.Connection`: user tables as `main`, shared file attached as `shared`, WAL on both.
  - `tests.support.seed_catalog(conn)`: the unit, faction, era and availability inserts now inside `make_db`.
- `db.connect(path)` keeps its signature and raises `MultiUserDatabase` when the file is multi-user.

`ACCOUNT_SCHEMA` tables (all `CREATE TABLE IF NOT EXISTS`):
- `users(id INTEGER PRIMARY KEY AUTOINCREMENT, handle TEXT NOT NULL UNIQUE COLLATE NOCASE, display_name TEXT NOT NULL, password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('user','admin')), visibility TEXT NOT NULL DEFAULT 'private' CHECK (visibility IN ('private','unlisted','public')), created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)`
- `invites(id INTEGER PRIMARY KEY AUTOINCREMENT, code_hash TEXT NOT NULL UNIQUE, created_by INTEGER REFERENCES users(id) ON DELETE SET NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, used_by INTEGER REFERENCES users(id) ON DELETE SET NULL, used_at TEXT)`
- `sessions(id INTEGER PRIMARY KEY AUTOINCREMENT, token_hash TEXT NOT NULL UNIQUE, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, created_at TEXT NOT NULL, last_seen TEXT NOT NULL, expires_at TEXT NOT NULL)`

- [ ] **Step 1: Write the failing tests** in `tests/test_db_split.py`, using a temp directory and `support.seed_catalog` on a `connect_shared` connection:
  - `test_user_connections_see_only_their_own_rows`: user 1 adds Atlas through `collection.add`; `collection.list_collection` for user 1 has one entry with the unit's name and BV (the join to the attached `units` works); for user 2 it is empty; `connect_user(2)` can then add Locust without touching user 1.
  - `test_user_file_is_created_under_users_dir`: after `connect_user(7, path)`, `db.user_db_path(path, 7)` exists.
  - `test_user_connection_cannot_see_shared_tables_as_main`: `SELECT name FROM main.sqlite_master` on a user connection lists none of `units`, `users`, `sessions`.
  - `test_connect_refuses_a_multiuser_database`: after `set_meta(shared, "multiuser", 1)`, `db.connect(path)` raises `MultiUserDatabase` and `db.is_multiuser(path)` is true. For a fresh path and for `:memory:` it is false.
  - `test_wal_is_on`: `PRAGMA journal_mode` is `wal` on both.
- [ ] **Step 2: Run** `python3 -m unittest discover -s tests -p "test_db_split.py"`. Expected: FAIL, `connect_shared` is not defined.
- [ ] **Step 3: Split `SCHEMA` in `batchall/db.py`.** `SHARED_SCHEMA` is `meta`, `units` and its indexes, `unit_details`, `factions`, `eras`, `availability`, `boxes`, `box_items` and their indexes. `USER_SCHEMA` is the rest. Split `_migrate` into `_migrate_shared` (box_items, units, factions columns) and `_migrate_user` (collection, copies, force_units, `_add_plain_mini_rows`); `_migrate` calls both so `connect` behaves as before.
- [ ] **Step 4: Implement the new functions.** `connect_user` binds the shared path as a parameter: `ATTACH DATABASE ? AS shared`. SQLite resolves an unqualified name in `main` first and then in attached files, which is what lets existing queries work unchanged. `is_multiuser` must not create a file. Extract `seed_catalog(conn)` in `tests/support.py` and call it from `make_db`.
- [ ] **Step 5: Run the new file, then the whole suite.** Expected: new tests PASS, 422 existing tests PASS.
- [ ] **Step 6: Commit** `git add batchall/db.py tests/support.py tests/test_db_split.py && git commit -m "Split the schema so each user's data can live in its own database file"`

---

### Task 2: Accounts, passwords and invites

**Files:**
- Create: `batchall/accounts.py`
- Test: `tests/test_accounts.py`

**Interfaces:**
- Consumes: `db.connect_shared`, `db.ACCOUNT_SCHEMA` (Task 1).
- Produces, all in `accounts`:
  - `class AccountError(ValueError)`
  - `RESERVED_HANDLES: frozenset[str]`, at least `api admin login logout signup signin u static assets index`.
  - `MIN_PASSWORD = 10`, `MAX_PASSWORD = 256`
  - `normalize_handle(raw: str) -> str`: strips, lowercases, validates against the handle rules and the reserved set, raises `AccountError`.
  - `hash_password(password: str) -> str` and `verify_password(password: str, stored: str) -> bool`. Stored form `scrypt$<n>$<r>$<p>$<salt hex>$<hash hex>`, n=16384, r=8, p=1, 16-byte salt. Comparison with `hmac.compare_digest`.
  - `create_user(conn, handle, password, display_name=None, role="user") -> dict`: no invite check. `display_name` defaults to the handle, at most 60 characters.
  - `make_invite(conn, secret: bytes, created_by: int | None) -> str`: returns the code once, stores only `hmac_sha256(secret, code)` as hex.
  - `sign_up(conn, secret: bytes, handle, password, invite_code, display_name=None) -> dict`: consumes the invite and creates the user in one `BEGIN IMMEDIATE` transaction.
  - `authenticate(conn, handle, password) -> dict | None`: for an unknown handle it still verifies against a module-level dummy hash.
  - `get_user(conn, user_id) -> dict | None`, `get_user_by_handle(conn, handle) -> dict | None`, `list_users(conn) -> list[dict]`, `first_admin(conn) -> dict | None`.
  - User dicts hold `id, handle, display_name, role, visibility, created_at`. The hash is never in one.

- [ ] **Step 1: Write the failing tests** in `tests/test_accounts.py` against a temp `connect_shared` database:
  - `test_handle_rules`: `Rose` becomes `rose`; `ab`, 31 characters, `has space`, `under_score`, `../x` raise; each of `api`, `admin`, `login`, `u` raises.
  - `test_handles_are_unique_ignoring_case`: creating `rose` then `ROSE` raises `AccountError`.
  - `test_password_rules`: 9 characters raises; 10 passes; 257 characters raises; a 12-character non-ASCII password (`pässwörd-ñandú`) round-trips through `hash_password` and `verify_password`.
  - `test_hash_is_salted_and_not_returned`: two hashes of one password differ; `create_user(...)` has no `password_hash` key; `list_users` rows have none either.
  - `test_authenticate`: right password returns the user dict; wrong password and unknown handle both return `None`.
  - `test_sign_up_requires_a_valid_unused_invite`: no code, wrong code and a reused code each raise `AccountError("invalid invite code")`; a good code creates the user and the invite row has `used_by` set.
  - `test_two_sign_ups_race_for_one_invite`: two threads, each with its own connection to the same file, use one code with different handles; exactly one succeeds, the other raises, and `COUNT(*)` of users is 1.
  - `test_first_admin`: returns the lowest-id user with role `admin`, `None` when there is none.
- [ ] **Step 2: Run** the file. Expected: FAIL, no module `accounts`.
- [ ] **Step 3: Implement the interface above in `batchall/accounts.py`.** The invite check inside `sign_up` is `UPDATE invites SET used_by=?, used_at=? WHERE code_hash=? AND used_by IS NULL` after the user insert, with `rowcount == 1` required or the whole transaction rolls back.
- [ ] **Step 4: Run the file.** Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "Add accounts with scrypt passwords and single-use invite codes"`

---

### Task 3: Sessions

**Files:**
- Create: `batchall/sessions.py`
- Test: `tests/test_sessions.py`

**Interfaces:**
- Consumes: `accounts.get_user` (Task 2), `sessions` table (Task 1).
- Produces, all in `sessions`:
  - `COOKIE_NAME = "batchall_session"`, `SESSION_DAYS = 30`
  - `token_hash(secret: bytes, token: str) -> str`: hex HMAC-SHA256.
  - `create_session(conn, secret, user_id, now=None) -> str`: returns a new `secrets.token_urlsafe(32)` token. `now` is a timezone-aware `datetime`, default current UTC.
  - `lookup(conn, secret, token, now=None) -> dict | None`: the user dict plus `session_id`, or `None` when the token is unknown, expired, or its user is gone. Updates `last_seen`.
  - `end_session(conn, secret, token) -> None`
  - `end_other_sessions(conn, user_id, keep_session_id) -> int`: pass `None` to end all of a user's sessions.
  - `purge_expired(conn, now=None) -> int`

- [ ] **Step 1: Write the failing tests** in `tests/test_sessions.py`:
  - `test_token_round_trip_and_storage`: `lookup` returns the user; the raw token appears nowhere in the `sessions` table (only its hash).
  - `test_two_sessions_have_different_tokens`.
  - `test_garbage_and_empty_tokens_return_none`: `""`, `"x"`, 5,000 characters of `A`.
  - `test_expiry`: a lookup 31 days after creation returns `None`; 29 days returns the user.
  - `test_end_session`, `test_end_other_sessions_keeps_the_named_one`, `test_deleted_user_session_returns_none` (delete the user row, cascade removes the session).
  - `test_wrong_secret_does_not_match`.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.** Store timestamps as UTC ISO-8601 text so string comparison orders correctly.
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "Add server-side sessions keyed by a hashed token"`

---

### Task 4: Sign-in rate limiter

**Files:**
- Create: `batchall/ratelimit.py`
- Test: `tests/test_ratelimit.py`

**Interfaces:**
- Produces: `class RateLimiter(max_failures=5, window_seconds=300, lockout_seconds=300, clock=time.monotonic)` with
  - `retry_after(*keys) -> int`: whole seconds left on the longest lockout among the keys, 0 when none applies.
  - `record_failure(*keys) -> None`: counts failures inside the window and starts a lockout at `max_failures`.
  - `reset(*keys) -> None`
  - Keys are hashable, for example `("handle", "rose")` and `("ip", "10.0.0.1")`. Idle entries are dropped and the table is capped at 10,000 keys.

- [ ] **Step 1: Write the failing tests** with a fake clock: four failures leave `retry_after` at 0; the fifth gives 300; 301 seconds later it is 0 again; failures older than the window do not count; `reset` clears a lockout; a lockout on one key does not affect another; `retry_after` with two keys returns the larger; inserting 10,001 distinct keys leaves at most 10,000.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement** with a lock around the table, since the server is threaded.
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "Add a rate limiter for sign-in attempts"`

---

### Task 5: Password change and account deletion

**Files:**
- Modify: `batchall/accounts.py`
- Test: `tests/test_account_lifecycle.py`

**Interfaces:**
- Consumes: `sessions.end_other_sessions`, `db.user_db_path`, `db.connect_user`.
- Produces, in `accounts`:
  - `change_password(conn, user_id, current: str, new: str, keep_session_id: int | None = None) -> None`: wrong `current` raises `AccountError("current password is wrong")`; applies the length rules; ends every other session.
  - `reset_password(conn, user_id, new: str) -> None`: no current-password check, ends all sessions.
  - `delete_account(conn, user_id, password: str, confirm_handle: str, path=None) -> None`: needs the right password and `confirm_handle` equal to the user's handle, then calls `delete_user`.
  - `delete_user(conn, user_id, path=None) -> None`: removes the sessions, the user row and `users/<id>.db` with its `-wal` and `-shm` files. Invites keep their rows with `used_by` null.

- [ ] **Step 1: Write the failing tests** in `tests/test_account_lifecycle.py`:
  - `test_change_password_signs_out_other_sessions`: two sessions, change with `keep_session_id` of the first; the first still looks up, the second returns `None`; the new password authenticates and the old one does not.
  - `test_change_password_rejects_wrong_current_and_short_new`.
  - `test_reset_password_ends_every_session`.
  - `test_delete_account_needs_password_and_typed_handle`: wrong password raises, wrong confirmation raises, and in both cases the user and the file still exist.
  - `test_delete_account_removes_everything`: after deleting a user who had a user database with one mini, the user row, sessions and `users/<id>.db` are gone, and a previously issued token looks up as `None`.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "Add password change, reset and account deletion"`

---

### Task 6: Move an existing database to multi-user form

**Files:**
- Create: `batchall/multiuser.py`
- Test: `tests/test_multiuser_adopt.py`

**Interfaces:**
- Consumes: `db.connect`, `db.connect_shared`, `db.connect_user`, `db.USER_TABLES`, `accounts.create_user`.
- Produces: `multiuser.adopt(path, handle: str, password: str, display_name=None) -> dict` returning `{"already": bool, "user_id": int, "counts": {table: rows}, "backup": str | None}`.

Behavior of `adopt`:
1. If `db.is_multiuser(path)`: return `{"already": True, ...}` and change nothing.
2. Call `db.connect(path).close()` first, which upgrades an old-schema file and, for the default path, copies the pre-rename `battletech.db`.
3. Back up the file with SQLite's backup API to `<path>.pre-multiuser`, adding a timestamp suffix if that name exists.
4. Create the admin user, id 1 on a fresh account table, through `accounts.create_user(role="admin")`.
5. Copy every table in `db.USER_TABLES` into `users/1.db` with explicit column lists and the original ids, so photo, copy, collection and force ids do not change.
6. Compare row counts of every copied table. On any mismatch delete the new user file, roll back the user row, and raise `ValueError`, leaving the original tables in place.
7. Only then drop the user tables from the shared file (children first) and set meta `multiuser = 1`.

- [ ] **Step 1: Write the failing tests** in `tests/test_multiuser_adopt.py`. Build a database in today's schema at a temp path: catalog from `seed_catalog`, two collection entries with named minis, a `mini_photos` row with 2 KB of bytes, a force with two `force_units` rows including a non-empty `as_uuid`, a `company_logos` row, a `company_colors` row.
  - `test_adopt_preserves_every_row`: after `adopt`, `connect_user(1, path)` has the same row count in each of the seven tables, the same ids, the photo bytes, the `as_uuid`, the force name and the color value.
  - `test_adopt_creates_an_admin_and_a_backup`: user 1 has role `admin` and verifies with the given password; the backup file opens with `sqlite3` and holds the original `collection` row count.
  - `test_shared_file_loses_the_user_tables_and_refuses_connect`: `collection` is not in the shared file's `sqlite_master`; `db.connect(path)` raises `MultiUserDatabase`; `units` is still there with its rows.
  - `test_adopt_twice_changes_nothing`: second call returns `already=True`, there is one user, counts are unchanged.
  - `test_adopt_on_an_empty_database`: a database with no minis adopts with all counts 0.
  - `test_failed_verification_leaves_the_original_intact`: monkeypatch the count comparison to report a mismatch; `adopt` raises, `db.is_multiuser(path)` is false, `collection` still has its rows, and `users/1.db` does not exist.
- [ ] **Step 2: Run.** Expected: FAIL, no module `multiuser`.
- [ ] **Step 3: Implement `adopt`** as described. Use one raw `sqlite3` connection with the new user file attached for the copy.
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Rehearse on real data without touching it.** Copy `data/batchall.db` into the scratchpad directory and run `python3 -c "from batchall import multiuser; print(multiuser.adopt('<scratchpad>/batchall.db', 'rose', 'a-long-test-password'))"` from the repo root. Expected: `counts` match what `python3 -m batchall collection list` reports for the real database (acceptance check 7 expects 62 entries and 71 minis, which was not verified when the requirements were written). Report any difference.
- [ ] **Step 6: Commit** `git commit -m "Add adopt to move an existing database to multi-user form"`

---

### Task 7: Server settings, host and Origin checks, headers

**Files:**
- Create: `batchall/serversettings.py`
- Modify: `batchall/server.py`
- Test: `tests/test_server_multiuser_host.py`

**Interfaces:**
- Produces:
  - `serversettings.Settings`: frozen dataclass, fields `multiuser: bool = False`, `secret: bytes = b""`, `allowed_hosts: frozenset[str] = frozenset()`, `trusted_proxy: bool = False`.
  - `serversettings.from_environment(multiuser, allowed_hosts, trusted_proxy, environ=os.environ) -> Settings`: merges the given hosts with `BATCHALL_ALLOWED_HOSTS` (comma separated) and reads `BATCHALL_SECRET`. Raises `ValueError` when `multiuser` and the secret is missing or under 32 characters, or when no allowed host is given.
  - `server.client_ip(peer: str, forwarded_for: str | None, trusted_proxy: bool) -> str`: the last entry of `X-Forwarded-For` when trusted, else the peer address.
  - `server.is_https(forwarded_proto: str | None, trusted_proxy: bool) -> bool`
  - `server.make_server(port, host, db_path, art_dir, settings=None)`: stores `settings`, `shared_path`, `handle_limiter = RateLimiter(5, 300, 300)` and `ip_limiter = RateLimiter(30, 300, 300)` on the server. In multi-user mode `allowed_hosts` is `LOCAL_HOSTS | settings.allowed_hosts`, not the bind address.

Behavior in multi-user mode only:
- Mutating methods with an `Origin` header whose host is not in `allowed_hosts` get 403 before routing. A missing `Origin` passes.
- Every response carries `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `X-Frame-Options: DENY`, and a `Content-Security-Policy` of `default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'` (the image responses from `_send_raw` keep their stricter policy and do not get a second one). `Strict-Transport-Security: max-age=31536000` is added when `is_https` is true.
- Local mode sends none of these additions.

- [ ] **Step 1: Write the failing tests** in `tests/test_server_multiuser_host.py`, using `http.client` against `make_server(port=0, ...)` on a shared database prepared with `adopt`:
  - `test_from_environment_requires_secret_and_hosts`: no secret, 31-character secret and no hosts each raise; flag hosts and environment hosts merge.
  - `test_client_ip`: untrusted ignores the header; trusted with `a, b, c` returns `c`; trusted with no header returns the peer.
  - `test_configured_host_is_accepted_and_bind_address_is_not_required`: `Host: batchall.example` returns something other than 403, `Host: other.example` returns 403, `localhost` is accepted.
  - `test_foreign_origin_on_a_write_is_refused`: `POST /api/nope` with `Origin: https://evil.example` returns 403; with `Origin: https://batchall.example` returns 404; with no `Origin` returns 404.
  - `test_security_headers_on_static_and_api`: `GET /index.html` has the four headers with the exact CSP above; no `Strict-Transport-Security` over plain HTTP; with `X-Forwarded-Proto: https` and `trusted_proxy=True` it is present; with `trusted_proxy=False` it is absent.
  - `test_local_mode_headers_unchanged`: a server with default `Settings` sends no CSP on `GET /index.html`.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.** Add the headers by overriding `Handler.end_headers`, and mark responses that already set a CSP so none is added twice.
- [ ] **Step 4: Run the new file and `tests/test_server_host.py`.** Expected: PASS, local-mode host tests unchanged.
- [ ] **Step 5: Commit** `git commit -m "Add multi-user server settings, host and Origin checks, and security headers"`

---

### Task 8: Auth endpoints and the session cookie

**Files:**
- Create: `batchall/authapi.py`
- Modify: `batchall/server.py`
- Test: `tests/test_server_auth.py`

**Interfaces:**
- Consumes: `accounts`, `sessions`, `RateLimiter`, `client_ip`, `is_https` (Tasks 2 to 7).
- Produces, in `authapi`: `@dataclass AuthResult(data: dict, session_token: str | None = None, clear_session: bool = False)` and handlers `signup`, `login`, `logout`, `me`, `change_password`, `delete_account`, each `(ctx, body) -> AuthResult` where `ctx` carries `shared` (a `connect_shared` connection), `settings`, `user` (dict or `None`), `session_token`, `session_id`, `ip`, `handle_limiter`, `ip_limiter`.
- Routes, handled before `ROUTES` and open to signed-out callers except where noted:
  - `POST /api/auth/signup` `{handle, password, invite_code, display_name?}`: creates the user, signs in, returns the user dict.
  - `POST /api/auth/login` `{handle, password}`: returns the user dict. A failure returns 401 `{"error": "invalid handle or password"}`. A locked handle or IP returns 429 with a `Retry-After` header. Failures count against the handle and IP limiters, success resets the handle key. Any incoming session cookie is ignored and a new token is always issued.
  - `POST /api/auth/logout`: ends the session, clears the cookie, always 200.
  - `GET /api/auth/me`: the signed-in user, or 401. In local mode, always `{"mode": "local"}` with 200.
  - `POST /api/auth/password` `{current, new}` (signed in): keeps the caller's session, ends the rest.
  - `DELETE /api/auth/account` `{password, confirm}` (signed in): deletes the account and clears the cookie.
- Cookie: `batchall_session=<token>; Path=/; HttpOnly; SameSite=Lax; Max-Age=2592000`, plus `; Secure` when `is_https`. Clearing sets `Max-Age=0`.

- [ ] **Step 1: Write the failing tests** in `tests/test_server_auth.py` with a helper that starts a multi-user server (settings secret of 32 `x` characters, allowed host `localhost`) and a client that keeps the cookie:
  - `test_signup_login_logout_round_trip`: sign up with an invite from `make_invite` returns 200 and a `Set-Cookie` containing `HttpOnly`, `SameSite=Lax`, `Path=/`; `GET /api/auth/me` returns the handle and no `password_hash`; logout; `me` returns 401.
  - `test_signup_with_a_used_invite_is_refused`.
  - `test_login_failures_are_indistinguishable`: unknown handle and wrong password return the same status and body.
  - `test_sixth_failure_is_rate_limited`: five wrong passwords then a right one returns 429 with a `Retry-After` header.
  - `test_login_issues_a_new_token_each_time`: two logins give different cookies; a cookie sent with the login request is not reused.
  - `test_secure_flag_only_behind_a_trusted_https_proxy`.
  - `test_garbage_and_expired_cookies_give_401`: `me` with `batchall_session=xxx` returns 401, not 500.
  - `test_change_password_keeps_this_session_and_ends_others`.
  - `test_delete_account_needs_password_and_confirmation_then_clears_cookie`; the old cookie then gets 401.
  - `test_local_mode_me`: a default-settings server returns `{"mode": "local"}`.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement `authapi.py` and wire the routes into `Handler._api`.** Parse the `Cookie` header with `http.cookies.SimpleCookie`. Open the shared connection with `db.connect_shared(server.shared_path)` per request.
- [ ] **Step 4: Run the new file and the existing server tests.** Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "Add sign-up, sign-in, sign-out and password endpoints with a session cookie"`

---

### Task 9: Scope every API route to the signed-in user

**Files:**
- Modify: `batchall/server.py`
- Test: `tests/test_server_isolation.py`

**Interfaces:**
- Produces in `server`:
  - `OWNED_ID_ROUTES: dict[callable, str]` mapping each id-taking handler to its table: `get_entry_boxes`, `get_variants`, `post_swap`, `patch_collection`, `delete_collection` to `collection`; `patch_copy`, `post_copy_swap`, `delete_mini`, `get_mini_photo`, `put_mini_photo`, `patch_mini_photo`, `delete_mini_photo` to `copies`; `get_force_export`, `put_force`, `delete_force` to `forces`.
  - `SHARED_ID_ROUTES: frozenset` of `get_box_detail`, `post_box_add`, `get_faction_icon`.

Behavior in multi-user mode: every `/api/` path other than `/api/auth/*` needs a valid session (else 401 `{"error": "sign in required"}`). The handler gets `db.connect_user(user["id"], shared_path)`. Before an id-taking handler runs, `SELECT 1 FROM <table> WHERE id = ?` on that connection must find the row, else 404 `{"error": "not found"}`. Static files stay public. Local mode is untouched.

- [ ] **Step 1: Write the failing tests** in `tests/test_server_isolation.py`. Create users A and B with `create_user`, give A (through `connect_user`) an entry with two minis, a photo, a saved force, a company logo and a company color, and sign B in over HTTP:
  - `test_every_id_route_in_routes_is_classified`: iterate `server.ROUTES`; every handler whose pattern's first group is `(\d+)` is in `OWNED_ID_ROUTES` or `SHARED_ID_ROUTES`. This fails the build when a later route forgets the check.
  - `test_other_users_ids_answer_404_on_every_owned_route`: for each owned-id route and its method, using A's real ids with B's cookie, the status is 404. A request body is sent for write methods.
  - `test_other_users_data_is_not_listed`: B's `GET /api/collection`, `/api/forces`, `/api/company-logos`, `/api/company-colors`, `/api/copy-tags` are empty.
  - `test_bulk_routes_with_foreign_ids_change_nothing`: B's `PATCH /api/minis` and `DELETE /api/minis` with A's mini ids return 400 and A's minis are unchanged.
  - `test_same_id_in_two_accounts_is_two_rows`: B adds an entry (which also gets id 1); B's `PATCH /api/collection/1` changes B's entry and A's entry is unchanged.
  - `test_signed_out_requests_get_401`: `GET /api/collection`, `/api/meta` and `POST /api/build` with no cookie, a garbage cookie, and a logged-out cookie.
  - `test_static_files_stay_public`: `GET /index.html` without a cookie returns 200.
  - `test_shared_reference_routes_work_when_signed_in`: `GET /api/meta` and `/api/catalog/search?q=atlas` return the seeded catalog for B.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement the guard in `Handler._api`.** Resolve the user with `sessions.lookup` before choosing a connection, and close both connections in `finally`.
- [ ] **Step 4: Run the new file, then the whole suite.** Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "Scope the API to the signed-in user and answer 404 for other users' ids"`

---

### Task 10: CLI, `serve` flags and operator docs

**Files:**
- Modify: `batchall/cli.py`, `batchall/server.py` (`serve` start-up), `README.md`, `~/.claude/skills/battletech/SKILL.md`
- Test: `tests/test_cli_multiuser.py`

**Interfaces:**
- Global option `--user HANDLE`. On a multi-user database every non-`admin` command runs against that user's connection, defaulting to `accounts.first_admin`. On a single-user database `--user` is an error: `database is not in multi-user mode`.
- `admin` subcommands, run against the shared file:
  - `admin init --handle H [--display-name N] [--password-env VAR]` calls `multiuser.adopt`; the password comes from the named environment variable or two `getpass` prompts.
  - `admin invite` prints `{"invite": "<code>"}`.
  - `admin users` prints the user list as JSON.
  - `admin set-password --handle H [--password-env VAR]` calls `accounts.reset_password`.
  - `admin delete-user --handle H --yes` calls `accounts.delete_user`, and refuses without `--yes`.
- `serve --multiuser [--allowed-host H ...] [--trusted-proxy]`. It builds `Settings` with `from_environment`, refuses when the database is not multi-user (message names `admin init`), and in `server.serve` uses `connect_shared` for the start-up icon fill. `serve` without `--multiuser` on a multi-user database refuses with a message naming `--multiuser`.
- The secret comes from `BATCHALL_SECRET` only, never from a flag.

- [ ] **Step 1: Write the failing tests** in `tests/test_cli_multiuser.py`, following the invocation and output-capture pattern in `tests/test_cli_import.py`:
  - `test_collection_list_output_is_identical_after_adopt`: capture `collection list` JSON on a seeded single-user database, run `admin init`, capture it again with no `--user`; the two JSON documents are equal.
  - `test_build_works_against_a_multiuser_database`: `build --target 3000 --formation lance` returns the same units as before adopt for a fixed `--seed`.
  - `test_user_flag_selects_the_account`: with two users, `--user bob` lists Bob's collection and the default lists the admin's.
  - `test_user_flag_on_a_single_user_database_errors`.
  - `test_admin_invite_users_set_password_delete`: invite prints a code that `sign_up` accepts; `users` lists both; `set-password` makes the new password authenticate; `delete-user` without `--yes` exits non-zero and deletes nothing.
  - `test_catalog_import_from_dir_works_on_a_multiuser_database`: `catalog import --from-dir` against the fixture directory used in the existing catalog tests fills the shared `units` table.
  - `test_serve_refusals`: `serve --multiuser` on a single-user database, `serve` on a multi-user one, and `--multiuser` with no `BATCHALL_SECRET` each exit non-zero with the messages above.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.** In `main`, handle `admin` before opening a connection, and pick `connect_user` when `db.is_multiuser(args.db)`.
- [ ] **Step 4: Run the new file, then the whole suite.** Expected: PASS.
- [ ] **Step 5: Document.** Add a "Hosting for several people" section to `README.md` covering `admin init`, `admin invite`, `serve --multiuser`, the three environment settings, a Caddy reverse-proxy snippet that forwards `Host` and `X-Forwarded-Proto`, and backup of the shared file and `data/users/` with `sqlite3 <file> ".backup <dest>"`. Add one paragraph to the `battletech` skill doc: the CLI takes `--user HANDLE` on a multi-user database and defaults to the first admin.
- [ ] **Step 6: Commit** `git add batchall/cli.py batchall/server.py README.md tests/test_cli_multiuser.py && git commit -m "Add admin commands, --user and serve --multiuser"` (the skill doc lives outside the repo and is not committed).

---

### Task 11: Sign-in page and sign-out in the web UI

**Files:**
- Create: `web/login.html`, `web/login.js`
- Modify: `web/app.js` (`api()` near line 216 and `init()` near line 2284), `web/index.html`, `web/style.css`
- Test: `tests/test_web_login.py`

**Interfaces:**
- Consumes: the `/api/auth/*` routes (Task 8).
- `login.html` has a sign-in form (handle, password) and a create-account form (handle, password, invite code), switchable. `login.js` posts to `/api/auth/login` or `/api/auth/signup` and goes to `/` on success. No inline script or `style=` attributes, because the Task 7 CSP forbids inline script.
- `app.js`: `api()` redirects to `/login.html` on a 401 response. `init()` calls `GET /api/auth/me`; when `mode` is not `local`, it fills a new `<span id="account" class="muted" hidden>` in `index.html`'s topbar with the display name and a "Sign out" button that posts to `/api/auth/logout` and goes to `/login.html`.

- [ ] **Step 1: Write the failing tests** in `tests/test_web_login.py` against a multi-user test server:
  - `test_login_page_is_public_and_has_no_inline_script`: `GET /login.html` with no cookie returns 200, its body has no `<script>` tag without a `src`, and no ` style="`.
  - `test_index_page_has_no_inline_script_or_style_attribute`.
  - `test_login_page_references_existing_files`: every `src` and `href` in `login.html` resolves to a served 200.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Build the page and wire the app.** Reuse `style.css` colors and fonts. Do not change panel text sizes.
- [ ] **Step 4: Run the new file and the whole suite.** Expected: PASS.
- [ ] **Step 5: Click through in a browser** using the `run` skill on a scratchpad copy of the database: `admin init`, `admin invite`, start `serve --multiuser --allowed-host localhost` with a 32-character `BATCHALL_SECRET`, sign in as the admin and confirm the collection loads; sign out, create a second account with the invite and confirm it sees an empty collection; confirm the browser console shows no Content-Security-Policy violation. Report anything the CSP blocks (inline styles set by `app.js`, fonts, photo blobs).
- [ ] **Step 6: Update `HANDOFF.md`** (gitignored) to say phase 1 is done, what remains for phase 2, and the per-user-database decision with its phase 2 consequence.
- [ ] **Step 7: Commit** `git add web tests/test_web_login.py && git commit -m "Add a sign-in page and sign-out to the web UI"`

---

## Self-review

**Spec coverage (phase 1 scope):**
- R1 accounts: Tasks 2, 4, 5. Handle and password rules, constant-time compare, same message, rate limiting, password change signs out others, deletion with password and typed confirmation.
- R2 sessions: Tasks 3, 7, 8. Hashed token, cookie flags, new token per sign-in, sign-out deletes the row, Origin check.
- R3 ownership: Tasks 1, 9, with the two deviations at the top. Reference data is writable only through the CLI because no HTTP route writes it.
- R10 host and proxy: Task 7. R11 local mode: Tasks 7 and 10, plus the unchanged existing tests. R12 CLI: Task 10. R13 migration: Task 6.
- Decisions of 2026-11-06 (invite-only, no email, admin resets by CLI): Tasks 2, 5, 10.
- Not covered here on purpose: R4 to R9 (visibility, front page, public view, public API, suspend, uploads and quotas), R14 deployment, the JSON export in R15.

**Step scan:** each step names a file, a signature or a test name with its assertion. No step defers a decision.

**Type consistency:** `connect_user(user_id, path=None)` is called that way in Tasks 5, 6, 9 and 10. `create_user(conn, handle, password, display_name=None, role="user")` is used with those names in Tasks 6 and 9. The `secret: bytes` parameter appears on `make_invite`, `sign_up`, `create_session`, `lookup` and `end_session`, and `Settings.secret` supplies it in Task 8. `RateLimiter.retry_after`, `record_failure` and `reset` match between Tasks 4 and 8.

**Proportion:** the plan is a few times the length of the phase-1 requirements it implements. Code bodies are left to the implementer, and only signatures, schema text and test assertions are fixed.
