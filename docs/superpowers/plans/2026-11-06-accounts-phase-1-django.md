# Accounts, Phase 1 on Django Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let several people sign in to one hosted Batchall server, each with a private catalog, while Rose's existing data becomes the first account and the stdlib-only local server keeps working.

**Architecture:** A Django 5.2 LTS project, `hosted/`, owns accounts: users, sessions, CSRF, password hashing, the admin site and invite codes, all in a small SQLite file, `data/accounts.db`. It does not use the ORM for catalog data. A single catch-all API view looks up the request in the existing `batchall.server.ROUTES` table, opens the signed-in user's database with `db.connect_user` (their own file, with the shared catalog attached) and calls the existing handler unchanged. The domain modules, the CLI and the local `python3 -m batchall serve` stay standard library only.

**Tech Stack:** Python 3.12, Django 5.2 LTS (hosted mode only), gunicorn for production, SQLite, plain JavaScript in `web/`.

**Spec:** `handoff/requirements.md` (untracked, local). This plan supersedes `2026-11-06-accounts-phase-1.md`, whose Task 1 (the database split, commits `b12fb83` and `ebc53b9`) is already done and kept. Covers R1, R2, R3, R10, R11, R12, R13, invite-only sign-up and "no email". R4 to R9, R14 and R15 are phase 2.

## Spec deviations

Rose approved the move to Django on 2026-11-06 and delegated the choices below.

1. **Django for the hosted layer only.** The spec's "standard library only" constraint now applies to the `batchall` package, the CLI and local mode. Hosted mode needs `requirements-hosted.txt` (Django, gunicorn). The user-visible local workflow is unchanged.
2. **Ownership by per-user database file, not `user_id` columns** (R3). Django's ORM is used only for accounts. Isolation holds by construction: a request's connection holds one user's rows. Phase 2's front page must open each public user's file or read a summary table.
3. **Other users' ids answer 404** (R3). Missing ids answer 400 in the handlers today, so the hosted API checks that the id exists in the caller's database first. Local mode keeps its statuses.
4. **Django stores the session key itself in the sessions table**, not a hash of it (R2 asks for hashed). Anyone who can read `accounts.db` can already read everything, so the gain from hashing is small. Accepted.
5. **Hosted admin tasks use Django's admin site and `manage.py` commands** (`adopt`, `createinvite`, `changepassword`, `createsuperuser`), not `batchall admin ...` CLI subcommands (R8, R12).
6. **The hosted server is Django** (`manage.py runserver` or `gunicorn hosted.wsgi`), so there is no `serve --multiuser`. `python3 -m batchall serve` refuses a multi-user database.
7. **No session "last seen" column.** Django sessions carry an expiry only.
8. **Sign-in rate limiting is in process memory.** Limits are per worker and reset on restart. The same limiters cover the API sign-in and the admin login.

## Global Constraints

- `batchall/` modules, `tests/`, the CLI and `python3 -m batchall serve` need nothing beyond the standard library. `python3 -m unittest discover -s tests` must pass on a machine without Django.
- Django is pinned `Django>=5.2,<5.3`. Hosted tests run with `.venv/bin/python manage.py test`. Create the venv with `python3 -m venv .venv && .venv/bin/pip install -r requirements-hosted.txt`. `.venv/` is git-ignored.
- SQLite stays the database. WAL mode on the shared file, every user file and `accounts.db`.
- Passwords: Django scrypt hasher first in `PASSWORD_HASHERS`, minimum length 10, maximum 256 characters, never logged, never returned.
- Handles: lowercase letters, digits and hyphens, 3 to 30 characters, unique ignoring case, not one of the reserved paths. The handle is Django's `username`; the display name is `first_name`, at most 60 characters.
- Sign-in failures give one message: `invalid handle or password`.
- Cookie: name `batchall_session`, `HttpOnly`, `SameSite=Lax`, path `/`, `Secure` when `BATCHALL_HTTPS=1`.
- Every mutating request is CSRF-protected by Django and keeps the `Content-Type: application/json` rule.
- The API never accepts a `user_id` from the client.
- Existing stdlib tests keep passing (432 at the start of this plan). New behavior gets new tests.
- CLI JSON output shapes do not change. `python3 -m batchall ...` works with no new arguments against a single-user database.
- Code, comments, commit messages and docs are plain and professional. No `Co-Authored-By` trailer on any commit.
- Work on branch `accounts-phase-1`. Never run a migration against the real `data/batchall.db`; use a copy in the scratchpad.

## Review Focus

1. **A POST without a CSRF token** answers a JSON 403, not Django's HTML page. (Task 4)
2. **A lone-surrogate or 100,000-character password** on sign-up or sign-in gives 400 or 401, never 500 or a slow hash. (Tasks 3, 4)
3. **Two sign-ups race for one invite code.** Exactly one wins. (Task 3)
4. **A user created in the Django admin or by `adopt` with `Rose` or `api` as the handle.** Stored lowercased or refused. (Tasks 3, 6)
5. **A deleted account.** Its `users/<id>.db` is gone and its old session cookie answers 401. (Task 4)

---

## File Structure

| File | Responsibility |
|---|---|
| `requirements-hosted.txt`, `manage.py` (create) | Hosted dependencies and Django entry point |
| `hosted/settings.py`, `test_settings.py`, `wsgi.py`, `urls.py`, `middleware.py` (create) | Django configuration, security headers, routing |
| `hosted/models.py`, `accounts.py`, `admin.py` (create) | Invites, handle and password rules, sign-up, admin forms |
| `hosted/authviews.py`, `signals.py` (create) | `/api/auth/*` views, data cleanup on user delete |
| `hosted/api.py`, `pages.py` (create) | Catch-all API adapter over `server.ROUTES`, static page view |
| `hosted/management/commands/adopt.py`, `createinvite.py` (create) | Operator commands |
| `batchall/ratelimit.py`, `multiuser.py`, `accountsdb.py` (create) | Stdlib helpers: limiter, data adoption, read-only user lookup for the CLI |
| `batchall/config.py`, `db.py`, `server.py`, `cli.py` (modify) | Accounts path, drop unused schema, route classification, `--user` |
| `web/login.html`, `web/login.js`, `web/app.js`, `web/index.html`, `web/style.css` | Sign-in page, CSRF header, sign-out |
| `hosted/tests/` and `tests/` | Django tests and stdlib tests |

---

### Task 1: Django scaffold, settings and security headers

**Files:**
- Create: `requirements-hosted.txt`, `manage.py`, `hosted/__init__.py`, `hosted/settings.py`, `hosted/test_settings.py`, `hosted/urls.py`, `hosted/wsgi.py`, `hosted/middleware.py`, `hosted/tests/__init__.py`
- Modify: `.gitignore` (add `.venv/`), `batchall/config.py`, `batchall/db.py`
- Test: `hosted/tests/test_settings.py`

**Interfaces:**
- Produces:
  - `config.ACCOUNTS_DB_PATH`: `Path`, from `BATCHALL_ACCOUNTS_DB` else `DATA_DIR / "accounts.db"`.
  - Django settings: `SECRET_KEY` from `BATCHALL_SECRET`; `BATCHALL_SHARED_DB = config.DB_PATH`; `BATCHALL_TRUSTED_PROXY: bool` from `BATCHALL_TRUSTED_PROXY == "1"`; `BATCHALL_HTTPS: bool` from `BATCHALL_HTTPS == "1"`; `ALLOWED_HOSTS` = `localhost`, `127.0.0.1`, `[::1]` plus the comma list in `BATCHALL_ALLOWED_HOSTS`; `CSRF_TRUSTED_ORIGINS` from the comma list in `BATCHALL_TRUSTED_ORIGINS`.
  - `hosted.middleware.SecurityHeadersMiddleware`.
- `manage.py` uses `hosted.test_settings` when the first argument is `test`, else `hosted.settings`. `test_settings` sets `BATCHALL_SECRET` to 50 `x` characters with `os.environ.setdefault` before importing `hosted.settings`, and points `DATABASES["default"]["TEST"]["NAME"]` at a temp file so thread tests share one database.

Settings values (exact):
- `SECRET_KEY`: settings import raises `ImproperlyConfigured` when `BATCHALL_SECRET` is missing or under 32 characters.
- `DEBUG`: true only when `BATCHALL_DEBUG == "1"`.
- `DATABASES["default"]`: sqlite at `config.ACCOUNTS_DB_PATH`, `OPTIONS = {"init_command": "PRAGMA journal_mode=WAL;", "transaction_mode": "IMMEDIATE"}`.
- `INSTALLED_APPS`: `django.contrib.admin`, `auth`, `contenttypes`, `sessions`, `messages`, `staticfiles`, and `hosted`.
- `MIDDLEWARE`: `SecurityMiddleware`, `SessionMiddleware`, `CsrfViewMiddleware`, `AuthenticationMiddleware`, `MessageMiddleware`, `XFrameOptionsMiddleware`, then `hosted.middleware.SecurityHeadersMiddleware`.
- `SESSION_COOKIE_NAME = "batchall_session"`, `SESSION_COOKIE_HTTPONLY = True`, `SESSION_COOKIE_SAMESITE = "Lax"`, `SESSION_COOKIE_AGE = 30 * 24 * 3600`. `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE` follow `BATCHALL_HTTPS`; `SECURE_HSTS_SECONDS = 31536000` only when `BATCHALL_HTTPS`. `SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")` only when `BATCHALL_TRUSTED_PROXY`.
- `PASSWORD_HASHERS`: `ScryptPasswordHasher` then `PBKDF2PasswordHasher`. `AUTH_PASSWORD_VALIDATORS`: `MinimumLengthValidator` with `min_length=10`.
- `SECURE_REFERRER_POLICY = "no-referrer"`, `X_FRAME_OPTIONS = "DENY"`, `DATA_UPLOAD_MAX_MEMORY_SIZE = 14_000_000`, `USE_TZ = True`, `DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"`, `STATIC_URL = "/static/"`, `STATIC_ROOT = config.DATA_DIR / "static"`, `CSRF_FAILURE_VIEW = "hosted.authviews.csrf_failure"` (added in Task 4; until then leave the default).
- `SecurityHeadersMiddleware` adds `Content-Security-Policy` to every response that lacks one: `default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'`, and for paths starting `/admin/` the same without `blob:`. `X-Content-Type-Options: nosniff` comes from Django's default.

- [ ] **Step 1: Create the venv and `requirements-hosted.txt`** (`Django>=5.2,<5.3`, `gunicorn>=22`), add `.venv/` to `.gitignore`, install with `.venv/bin/pip install -r requirements-hosted.txt`.
- [ ] **Step 2: Write the failing tests** in `hosted/tests/test_settings.py` (Django `SimpleTestCase`; use `subprocess` with a cleaned environment for the import-time cases):
  - `test_missing_or_short_secret_refuses_to_load`: importing `hosted.settings` with no `BATCHALL_SECRET`, and with 31 characters, exits non-zero with "ImproperlyConfigured" in stderr.
  - `test_allowed_hosts_and_trusted_origins_merge_environment`.
  - `test_session_cookie_settings`: name, HttpOnly, SameSite `Lax`; `SESSION_COOKIE_SECURE` false by default and true with `BATCHALL_HTTPS=1`.
  - `test_password_policy`: first hasher is scrypt; password `short` fails `validate_password`; ten characters pass.
  - `test_security_headers_on_a_response`: `Client().get("/admin/login/")` has `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `X-Frame-Options: DENY`, and a CSP that contains `frame-ancestors 'none'` and, for `/admin/` paths, no `blob:`.
  - `test_accounts_db_path_setting`: `config.ACCOUNTS_DB_PATH` honors `BATCHALL_ACCOUNTS_DB` (import in a subprocess).
- [ ] **Step 3: Run** `.venv/bin/python manage.py test hosted`. Expected: FAIL, no `hosted` package.
- [ ] **Step 4: Implement the scaffold and settings** as specified. `urls.py` routes only `admin/` for now. `wsgi.py` exposes `application`.
- [ ] **Step 5: Retire the unused account schema.** Delete `ACCOUNT_SCHEMA` from `batchall/db.py` and its use in `connect_shared`, and remove or adjust tests in `tests/test_db_split.py` that depend on it. Accounts now live in Django's database.
- [ ] **Step 6: Run** `.venv/bin/python manage.py test hosted`, then `python3 -m unittest discover -s tests` with the system Python. Expected: both PASS. Then run `BATCHALL_SECRET=<50 x> BATCHALL_HTTPS=1 BATCHALL_ALLOWED_HOSTS=batchall.example .venv/bin/python manage.py check --deploy` and report any warning it prints.
- [ ] **Step 7: Commit** `git add -A requirements-hosted.txt manage.py hosted .gitignore batchall tests && git commit -m "Add a Django project for hosted mode with security settings"`

---

### Task 2: Sign-in rate limiter

**Files:**
- Create: `batchall/ratelimit.py`
- Test: `tests/test_ratelimit.py`

**Interfaces:**
- Produces: `class RateLimiter(max_failures=5, window_seconds=300, lockout_seconds=300, clock=time.monotonic)` with
  - `retry_after(*keys) -> int`: whole seconds left on the longest lockout among the keys, 0 when none applies.
  - `record_failure(*keys) -> None`: counts failures inside the window and starts a lockout at `max_failures`.
  - `reset(*keys) -> None`
  - Keys are hashable, for example `("handle", "rose")` and `("ip", "10.0.0.1")`. Idle entries are dropped and the table is capped at 10,000 keys. A lock guards the table, since the server is threaded.

- [ ] **Step 1: Write the failing tests** with a fake clock: four failures leave `retry_after` at 0; the fifth gives 300; 301 seconds later it is 0; failures older than the window do not count; `reset` clears a lockout; a lockout on one key does not affect another; `retry_after` with two keys returns the larger; inserting 10,001 distinct keys leaves at most 10,000; two threads recording failures concurrently do not lose counts.
- [ ] **Step 2: Run** `python3 -m unittest discover -s tests -p "test_ratelimit.py"`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "Add a rate limiter for sign-in attempts"`

---

### Task 3: Handles, passwords, invites and the admin forms

**Files:**
- Create: `hosted/models.py`, `hosted/migrations/__init__.py`, `hosted/migrations/0001_initial.py` (generated by `makemigrations`), `hosted/accounts.py`, `hosted/admin.py`
- Test: `hosted/tests/test_accounts.py`

**Interfaces:**
- Consumes: `hosted.settings` (Task 1).
- Produces:
  - `models.Invite`: `code_hash` (CharField 64, unique), `created_by` (FK to `AUTH_USER_MODEL`, null, `SET_NULL`), `created_at` (auto), `used_by` (FK, null, `SET_NULL`), `used_at` (DateTime, null).
  - In `hosted.accounts`:
    - `class AccountError(ValueError)`
    - `RESERVED_HANDLES: frozenset[str]`, at least `api admin login logout signup signin u static assets index`.
    - `MIN_PASSWORD = 10`, `MAX_PASSWORD = 256`
    - `normalize_handle(raw: str) -> str`: strips, lowercases, validates with `re.fullmatch(r"[a-z0-9-]{3,30}", ...)` and the reserved set, raises `AccountError`.
    - `check_password(password: str) -> str`: returns the NFC-normalized password; raises `AccountError` when under 10 or over 256 characters, or when the text cannot be encoded as UTF-8 (lone surrogates). The length check runs before any hashing.
    - `create_user(handle, password, display_name=None, superuser=False) -> User`: no invite check. Uses `normalize_handle`, `check_password`, `User.objects.create_user`/`create_superuser`; a taken handle (case-insensitive) raises `AccountError("that handle is already taken")`. `display_name` defaults to the handle, at most 60 characters.
    - `make_invite(created_by=None) -> str`: returns a `secrets.token_urlsafe(16)` code once; stores `salted_hmac("batchall-invite", code).hexdigest()`.
    - `sign_up(handle, password, invite_code, display_name=None) -> User`: validates everything first (so scrypt and rules run before the transaction), then inside `transaction.atomic()` creates the user and claims the invite with `Invite.objects.filter(code_hash=h, used_by__isnull=True).update(used_by=user, used_at=now)`, requiring one row updated or rolling back and raising `AccountError("invalid invite code")`. An unusable or unencodable invite code gives the same error.
  - In `hosted.admin`: `HandleUserCreationForm` (subclass of Django's `BaseUserCreationForm`) whose `clean_username` returns `normalize_handle(...)`; a `UserAdmin` subclass using it, replacing the default registration; a read-only `Invite` admin (no add, no change, delete allowed) showing `created_at`, `used_by`, `used_at` and never the hash.

- [ ] **Step 1: Write the failing tests** in `hosted/tests/test_accounts.py`:
  - `test_handle_rules`: `Rose` becomes `rose`; `ab`, 31 characters, `has space`, `under_score`, `../x`, `rose\n` handled (`rose\n` strips to `rose`), and each of `api`, `admin`, `login`, `u` raises.
  - `test_handles_are_unique_ignoring_case`: `create_user("rose", ...)` then `create_user("ROSE", ...)` raises `AccountError`.
  - `test_password_rules`: 9 characters raises; 10 passes; 257 raises; a 100,000-character password raises quickly; `"\ud800" * 12` raises `AccountError`; a 12-character non-ASCII password round-trips and composed and decomposed forms of one password both authenticate.
  - `test_users_get_scrypt_hashes_and_no_password_in_output`: `user.password.startswith("scrypt$")`.
  - `test_sign_up_requires_a_valid_unused_invite`: no code, wrong code, reused code, and a `"\ud800"` code each raise `AccountError("invalid invite code")`; a good code creates the user and the invite row has `used_by`.
  - `test_failed_sign_up_leaves_the_invite_unused`: a taken handle with a valid invite raises and the invite is still unused.
  - `test_two_sign_ups_race_for_one_invite` (`TransactionTestCase`): two threads, same code, different handles; exactly one succeeds and exactly one user exists.
  - `test_admin_create_form_normalizes_and_refuses_reserved`: the form with username `Rose` saves `rose`; with `admin` is invalid.
  - `test_invite_admin_never_shows_the_hash`.
- [ ] **Step 2: Run** `.venv/bin/python manage.py test hosted.tests.test_accounts`. Expected: FAIL.
- [ ] **Step 3: Implement the models, run `manage.py makemigrations hosted`, and implement `accounts.py` and `admin.py`.**
- [ ] **Step 4: Run the file, repeating the race test five times.** Expected: PASS every time.
- [ ] **Step 5: Commit** `git commit -m "Add handle and password rules, invite codes and admin forms"`

---

### Task 4: Authentication endpoints

**Files:**
- Create: `hosted/authviews.py`, `hosted/signals.py`
- Modify: `hosted/urls.py`, `hosted/apps.py` (create it; `ready()` imports the signals), `hosted/settings.py` (`CSRF_FAILURE_VIEW`)
- Test: `hosted/tests/test_authviews.py`

**Interfaces:**
- Consumes: `hosted.accounts`, `batchall.ratelimit.RateLimiter`, `db.user_db_path`.
- Produces in `hosted.authviews`:
  - `client_ip(request) -> str`: the last `X-Forwarded-For` entry when `settings.BATCHALL_TRUSTED_PROXY`, else `REMOTE_ADDR`.
  - Module-level `handle_limiter = RateLimiter(5, 300, 300)` and `ip_limiter = RateLimiter(30, 300, 300)`.
  - `csrf_failure(request, reason="") -> JsonResponse`: status 403, body `{"error": "CSRF check failed"}`.
- Routes (JSON in and out, all under `/api/auth/`):
  - `POST signup` `{handle, password, invite_code, display_name?}`: creates the user, logs in, returns the user dict. Failures count against `ip_limiter`. Errors give 400 with the `AccountError` message.
  - `POST login` `{handle, password}`: returns the user dict. A wrong handle, wrong password, inactive user, over-long or unencodable password all give 401 `{"error": "invalid handle or password"}`. A locked handle or IP gives 429 with `Retry-After`. Failures count against both limiters; success resets the handle key. Calls `django.contrib.auth.login`, which issues a new session key.
  - `POST logout`: ends the session, always 200.
  - `GET me` (decorated `ensure_csrf_cookie`): the user dict `{id, handle, display_name, is_admin}` or 401 `{"error": "sign in required"}`.
  - `POST password` `{current, new}` (signed in): `check_password(new)`, verifies `current`, `set_password`, `update_session_auth_hash` so this session survives and Django invalidates the others.
  - `DELETE account` `{password, confirm}` (signed in): needs the right password and `confirm` equal to the handle, then deletes the user and logs out.
- `signals.py`: a `post_delete` receiver on `User` removes `users/<id>.db`, `-wal` and `-shm` under the directory of `settings.BATCHALL_SHARED_DB`, ignoring missing files. This covers deletion from the admin site as well.

- [ ] **Step 1: Write the failing tests** in `hosted/tests/test_authviews.py`, using `Client(enforce_csrf_checks=True)` and a helper that GETs `/api/auth/me` first to obtain the `csrftoken` cookie and sends it as `X-CSRFToken`. Reset both limiters in `setUp`. Point `BATCHALL_SHARED_DB` at a temp directory with `override_settings`.
  - `test_signup_login_logout_round_trip`: sign up with a `make_invite` code returns 200 and a `batchall_session` cookie with `HttpOnly` and `SameSite=Lax`; `me` returns the handle and no password field; logout; `me` returns 401.
  - `test_post_without_csrf_token_gets_a_json_403`: `POST /api/auth/login` with no token returns 403 with `{"error": "CSRF check failed"}` and a JSON content type.
  - `test_login_failures_are_indistinguishable`: unknown handle and wrong password return identical status and body.
  - `test_surrogate_and_oversized_passwords_never_500`: sign-up with `"\ud800"*12` returns 400; login with it and with 100,000 characters returns 401; no 500.
  - `test_sixth_failure_is_rate_limited`: five wrong passwords then the right one returns 429 with `Retry-After`.
  - `test_login_issues_a_new_session_key`: two logins on one client give different `batchall_session` values; a session key set before login is not reused.
  - `test_garbage_cookie_gives_401`.
  - `test_change_password_keeps_this_session_and_ends_others`: two clients signed in as one user; client A changes the password; A's `me` is 200, B's is 401; the new password logs in and the old does not.
  - `test_delete_account_needs_password_and_confirmation`: wrong password and wrong confirmation both leave the user in place; the right pair deletes the user, removes `users/<id>.db` (create it with `connect_user` first), and the old cookie then gets 401.
  - `test_admin_deletion_also_removes_the_data_file`: `user.delete()` removes `users/<id>.db`.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement the views, routes, signal and apps config.**
- [ ] **Step 4: Run the file and the whole Django suite.** Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "Add sign-up, sign-in, sign-out and password endpoints"`

---

### Task 5: Scope the API to the signed-in user

**Files:**
- Create: `hosted/api.py`, `hosted/pages.py`
- Modify: `batchall/server.py` (add two constants, no behavior change), `hosted/urls.py`
- Test: `hosted/tests/test_api_isolation.py`

**Interfaces:**
- Consumes: `server.ROUTES`, `server.RawResponse`, `server.ApiError`, `server.MAX_BODY_BYTES`, `db.connect_user`, `db.connect_shared`.
- Produces:
  - In `batchall/server.py`: `OWNED_ID_ROUTES: dict` mapping each id-taking handler to its table: `get_entry_boxes`, `get_variants`, `post_swap`, `patch_collection`, `delete_collection` to `collection`; `patch_copy`, `post_copy_swap`, `delete_mini`, `get_mini_photo`, `put_mini_photo`, `patch_mini_photo`, `delete_mini_photo` to `copies`; `get_force_export`, `put_force`, `delete_force` to `forces`. And `SHARED_ID_ROUTES: frozenset` of `get_box_detail`, `post_box_add`, `get_faction_icon`.
  - `hosted.api.api(request, path)`: URL `re_path(r"^api/(?P<path>.*)$", ...)`, placed after the `/api/auth/` routes and `admin/`. Behavior, in order:
    1. Match `"/api/" + path` against `server.ROUTES`. No pattern matches: 404 `{"error": "no such endpoint"}`. Pattern matches but not the method: 405 with the same body.
    2. Not signed in: 401 `{"error": "sign in required"}`.
    3. Body for POST, PUT, PATCH, DELETE: empty is `{}`; otherwise `Content-Type` must contain `application/json` (else 415), valid JSON, an object (else 400, same messages as `Handler._read_body`).
    4. Open `db.connect_user(request.user.id, settings.BATCHALL_SHARED_DB)`. If the handler is in `OWNED_ID_ROUTES`, `SELECT 1 FROM <table> WHERE id = ?` on that connection must find the row, else 404 `{"error": "not found"}`.
    5. Call `handler(conn, query, body, *groups)` with `query = urllib.parse.parse_qs(request.META["QUERY_STRING"])`. A dict or list result goes out as JSON with `Cache-Control: no-store`; a `RawResponse` goes out with its content type, `X-Content-Type-Options: nosniff`, `Content-Security-Policy: default-src 'none'; style-src 'unsafe-inline'; sandbox`, and `Cache-Control: private, max-age=<cache_seconds>` (or `no-store` when 0). `ApiError` gives its status; `ValueError`, `KeyError`, `TypeError` give 400 with the message. Close the connection in `finally`.
  - `hosted.pages.page(request, path)`: serves files under `config.WEB_DIR` for GET only (empty path serves `index.html`), same path-escape check and content types as `Handler._static`, `Cache-Control: no-cache`, 404 otherwise. It is the last URL pattern.

- [ ] **Step 1: Write the failing tests** in `hosted/tests/test_api_isolation.py`. Create users A and B with `create_user`; build the shared file with `connect_shared` plus `tests/support.py`'s `seed_catalog` (add `tests` to the test's `sys.path`); give A, through `connect_user`, an entry with two minis, a photo, a saved force, a company logo and a company color. Sign B in with a `Client()` using `client.force_login`; send `HTTP_X_CSRFTOKEN`-free requests with `Client(enforce_csrf_checks=False)` for these tests.
  - `test_every_id_route_in_routes_is_classified`: for every entry in `server.ROUTES` whose pattern's first group is `(\d+)`, the handler is in `OWNED_ID_ROUTES` or `SHARED_ID_ROUTES`. Fails when a later route forgets the check.
  - `test_other_users_ids_answer_404_on_every_owned_route`: each owned-id route with A's real ids and B's session returns 404; write methods send a JSON body.
  - `test_other_users_data_is_not_listed`: B's `GET /api/collection`, `/api/forces`, `/api/company-logos`, `/api/company-colors`, `/api/copy-tags` are empty.
  - `test_bulk_routes_with_foreign_ids_change_nothing`: B's `PATCH /api/minis` and `DELETE /api/minis` with A's mini ids return 400 and A's minis are unchanged.
  - `test_same_id_in_two_accounts_is_two_rows`: B adds an entry (id 1 in B's file); `PATCH /api/collection/1` as B changes B's entry only.
  - `test_unknown_path_is_404_before_the_sign_in_check`: signed-out `GET /api/nope` is 404, signed-out `GET /api/collection` is 401, signed-out `POST /api/build` is 401.
  - `test_shared_reference_routes_work_when_signed_in`: `/api/meta` and `/api/catalog/search?q=atlas` return the seeded catalog for B.
  - `test_raw_responses_carry_image_headers`: A's mini photo request as A has `nosniff`, the sandbox CSP and `private, max-age=86400`.
  - `test_body_rules`: a non-JSON content type with a body is 415; a JSON array body is 400; a body over `MAX_BODY_BYTES` is 4xx and never 500.
  - `test_pages_are_public_and_safe`: signed-out `GET /index.html` and `/login.html` (once it exists, skip until Task 8) return 200; `GET /../manage.py` and `GET /%2e%2e/manage.py` return 404.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the new file, the whole Django suite, and `python3 -m unittest discover -s tests`.** Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "Scope the hosted API to the signed-in user and answer 404 for other users' ids"`

---

### Task 6: Move an existing database to multi-user form

**Files:**
- Create: `batchall/multiuser.py`, `hosted/management/__init__.py`, `hosted/management/commands/__init__.py`, `hosted/management/commands/adopt.py`, `hosted/management/commands/createinvite.py`
- Test: `tests/test_multiuser_adopt.py`, `hosted/tests/test_commands.py`

**Interfaces:**
- Consumes: `db.connect`, `db.connect_user`, `db.USER_TABLES`, `accounts.create_user`, `accounts.make_invite`.
- Produces:
  - `multiuser.adopt_data(path, user_id: int) -> dict` returning `{"already": bool, "user_id": int, "counts": {table: rows}, "backup": str | None}`. Stdlib only. Behavior:
    1. If `db.is_multiuser(path)`: return `{"already": True, ...}` and change nothing.
    2. `db.connect(path).close()` first, which upgrades an old-schema file and, for the default path, copies the pre-rename `battletech.db`.
    3. Back up the file with SQLite's backup API to `<path>.pre-multiuser` (timestamp suffix if that name exists).
    4. Copy every table in `db.USER_TABLES` into `users/<user_id>.db` with explicit column lists and the original ids.
    5. Compare row counts of every copied table. On any mismatch delete the new user file and raise `ValueError`, leaving the original tables in place.
    6. Only then drop the user tables from the shared file (children first) and set meta `multiuser = 1`.
  - `manage.py adopt --handle H [--display-name N] [--password-env VAR]`: if the shared database is already multi-user, print `already adopted` and create nothing. Otherwise create the superuser with `accounts.create_user(superuser=True)` (password from the named environment variable or two `getpass` prompts), then `adopt_data(path, user.id)`; if that raises, delete the user (the signal removes any file) and re-raise. Prints the result as JSON.
  - `manage.py createinvite` prints `{"invite": "<code>"}`.

- [ ] **Step 1: Write the failing tests.** In `tests/test_multiuser_adopt.py` (stdlib), build a database in today's schema at a temp path: catalog from `seed_catalog`, two collection entries with named minis, a `mini_photos` row with 2 KB of bytes, a force with two `force_units` rows including a non-empty `as_uuid`, a `company_logos` row, a `company_colors` row.
  - `test_adopt_preserves_every_row`: after `adopt_data(path, 5)`, `connect_user(5, path)` has the same row count in each of the seven tables, the same ids, the photo bytes, the `as_uuid`, the force name and the color value.
  - `test_adopt_makes_a_backup`: the backup opens with `sqlite3` and holds the original `collection` row count.
  - `test_shared_file_loses_the_user_tables_and_refuses_connect`: `collection` is not in the shared file's `sqlite_master`; `db.connect(path)` raises `MultiUserDatabase`; `units` still has its rows.
  - `test_adopt_twice_changes_nothing`: second call returns `already=True`; counts unchanged.
  - `test_adopt_on_an_empty_database`: all counts 0.
  - `test_failed_verification_leaves_the_original_intact`: monkeypatch the count comparison to report a mismatch; `adopt_data` raises, `db.is_multiuser(path)` is false, `collection` keeps its rows, `users/5.db` does not exist.
  In `hosted/tests/test_commands.py`:
  - `test_adopt_creates_a_superuser_and_moves_the_data`: with a legacy database at a temp path set as `BATCHALL_SHARED_DB`, `call_command("adopt", handle="Rose", password_env=...)` creates a superuser `rose`, and `connect_user(user.id, path)` has the rows.
  - `test_adopt_refuses_a_reserved_handle_and_changes_nothing`: handle `admin` raises `CommandError`, no user exists, `db.is_multiuser(path)` is false.
  - `test_adopt_failure_removes_the_new_user`: with `adopt_data` patched to raise, no user remains.
  - `test_adopt_twice`: second run reports already adopted; one user.
  - `test_createinvite_prints_a_code_that_signs_up`.
- [ ] **Step 2: Run both files.** Expected: FAIL.
- [ ] **Step 3: Implement.** Use one raw `sqlite3` connection with the new user file attached for the copy.
- [ ] **Step 4: Run both files, then both suites.** Expected: PASS.
- [ ] **Step 5: Rehearse on real data without touching it.** Copy `data/batchall.db` into the scratchpad. From the repo root run `BATCHALL_SECRET=<50 x> BATCHALL_DB=<copy> BATCHALL_ACCOUNTS_DB=<scratchpad>/accounts.db .venv/bin/python manage.py adopt --handle rose --password-env REHEARSAL_PW` (set `REHEARSAL_PW` to a 12-character throwaway). Expected: `counts` match `python3 -m batchall --db <original copy of the file made before the run> collection list` (acceptance check 7 expects 62 entries and 71 minis, which was not verified when the requirements were written). Report any difference.
- [ ] **Step 6: Commit** `git commit -m "Add adopt to move an existing database to multi-user form"`

---

### Task 7: CLI against a multi-user database, and operator docs

**Files:**
- Create: `batchall/accountsdb.py`
- Modify: `batchall/cli.py`, `README.md`, `~/.claude/skills/battletech/SKILL.md`
- Test: `tests/test_cli_multiuser.py`, `hosted/tests/test_accountsdb_contract.py`

**Interfaces:**
- Produces:
  - `accountsdb.lookup_user_id(handle: str | None = None, accounts_path=None) -> int`: opens `config.ACCOUNTS_DB_PATH` (or the given path) read-only with `sqlite3`, no Django import. With a handle it returns the `auth_user.id` whose `username` matches ignoring case and `is_active = 1`, else raises `ValueError("no such user")`. With no handle it returns the lowest id with `is_superuser = 1`, else raises `ValueError("no administrator account exists")`. A missing file raises `ValueError` naming `manage.py adopt`.
- Global option `--user HANDLE` on the CLI. On a multi-user database (`db.is_multiuser(args.db)`) every command runs against `db.connect_user(accountsdb.lookup_user_id(args.user), args.db)`. On a single-user database `--user` is an error: `database is not in multi-user mode`.
- `python3 -m batchall serve` on a multi-user database refuses with a message that says to run the hosted server (`manage.py runserver` or `gunicorn hosted.wsgi`). Without `--user`, `serve` on a single-user database is unchanged.

- [ ] **Step 1: Write the failing tests** in `tests/test_cli_multiuser.py`, following the invocation and output-capture pattern in `tests/test_cli_import.py`. Create the accounts file by hand with the minimal `auth_user` columns (`id`, `username`, `is_active`, `is_superuser`):
  - `test_collection_list_output_is_identical_after_adopt`: capture `collection list` JSON on a seeded single-user database, run `multiuser.adopt_data(path, 1)` and insert `auth_user` row 1 as superuser, capture again with no `--user`; the two JSON documents are equal.
  - `test_build_works_against_a_multiuser_database`: `build --target 3000 --formation lance --seed 1` returns the same units before and after.
  - `test_user_flag_selects_the_account`: two users with different collections; `--user bob` lists Bob's, the default lists the superuser's.
  - `test_user_flag_on_a_single_user_database_errors`.
  - `test_unknown_user_and_missing_accounts_file_give_clear_errors`.
  - `test_catalog_import_from_dir_works_on_a_multiuser_database`: `catalog import --from-dir` with the fixture used by the existing catalog tests fills the shared `units` table. If it fails through the user connection, open the shared file with `connect_shared` for `catalog import` and `boxes import` instead and keep this test.
  - `test_serve_refuses_a_multiuser_database`.
  In `hosted/tests/test_accountsdb_contract.py`: `test_lookup_works_against_a_real_django_accounts_database`: query `PRAGMA table_info(auth_user)` through Django's connection and assert the four columns `accountsdb` reads exist, and that the SQL string `accountsdb` uses runs against the test database.
- [ ] **Step 2: Run both.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run both suites.** Expected: PASS.
- [ ] **Step 5: Document.** Add a "Hosting for several people" section to `README.md`: `requirements-hosted.txt` and the venv, the environment settings (`BATCHALL_SECRET`, `BATCHALL_ALLOWED_HOSTS`, `BATCHALL_TRUSTED_ORIGINS`, `BATCHALL_HTTPS`, `BATCHALL_TRUSTED_PROXY`, `BATCHALL_ACCOUNTS_DB`, `BATCHALL_DEBUG`), `manage.py migrate`, `adopt`, `createinvite`, `collectstatic`, `runserver` for trying it and `gunicorn hosted.wsgi` for production, a Caddy snippet that forwards `Host`, `X-Forwarded-Proto` and serves `/static/`, and backup of `accounts.db`, the shared file and `data/users/` with `sqlite3 <file> ".backup <dest>"`. State that local mode needs no installs. Add one paragraph to the `battletech` skill doc: the CLI takes `--user HANDLE` on a multi-user database and defaults to the first administrator.
- [ ] **Step 6: Commit** `git add batchall/accountsdb.py batchall/cli.py README.md tests hosted/tests && git commit -m "Add --user and a multi-user check to the CLI"` (the skill doc lives outside the repo and is not committed).

---

### Task 8: Sign-in page and sign-out in the web UI

**Files:**
- Create: `web/login.html`, `web/login.js`
- Modify: `web/app.js` (`api()` near line 216 and `init()` near line 2284), `web/index.html`, `web/style.css`, `batchall/server.py` (local `/api/auth/me`)
- Test: `hosted/tests/test_web.py`, `tests/test_server.py` (one added test)

**Interfaces:**
- `login.html` has a sign-in form (handle, password) and a create-account form (handle, password, invite code), switchable. `login.js` first calls `GET /api/auth/me` to receive the CSRF cookie, then posts to `/api/auth/login` or `/api/auth/signup` with the `X-CSRFToken` header and goes to `/` on success. No inline script and no `style=` attributes.
- `app.js`: `api()` adds `X-CSRFToken` (the `csrftoken` cookie value) on every method except GET, and redirects to `/login.html` on a 401 response. `init()` calls `GET /api/auth/me`; when the response has no `mode: "local"`, it fills a new `<span id="account" class="muted" hidden>` in the topbar with the display name and a "Sign out" button that posts to `/api/auth/logout` and goes to `/login.html`.
- Local server: `GET /api/auth/me` returns `{"mode": "local"}` with status 200, so `app.js` works unchanged there.

- [ ] **Step 1: Write the failing tests.**
  - `hosted/tests/test_web.py`: `test_login_page_is_public_and_has_no_inline_script` (200 with no session, no `<script>` without `src`, no ` style="`); `test_index_page_has_no_inline_script_or_style_attribute`; `test_login_page_references_existing_files` (every `src` and `href` resolves to a served 200); `test_login_page_carries_the_security_headers`.
  - `tests/test_server.py`: `test_local_server_reports_local_mode` (`GET /api/auth/me` returns `{"mode": "local"}`).
- [ ] **Step 2: Run both.** Expected: FAIL.
- [ ] **Step 3: Build the page and wire the app.** Reuse `style.css` colors and fonts. Do not change panel text sizes.
- [ ] **Step 4: Run both suites.** Expected: PASS.
- [ ] **Step 5: Click through in a browser** using the `run` skill on scratchpad copies: `manage.py migrate`, `adopt`, `createinvite`, then `BATCHALL_SECRET=<50 x> BATCHALL_DEBUG=1 .venv/bin/python manage.py runserver`. Sign in as the adopted account and confirm the collection loads; sign out; create a second account with the invite and confirm it sees an empty collection; open `/admin/` as the superuser. Confirm the browser console shows no Content-Security-Policy violation and report anything the CSP blocks (inline styles set by `app.js`, fonts, photo blobs).
- [ ] **Step 6: Update `HANDOFF.md`** (gitignored): phase 1 done on Django, the hybrid design (Django for accounts, per-user SQLite files for catalogs), commands, and what remains for phase 2.
- [ ] **Step 7: Commit** `git add web batchall/server.py hosted/tests tests && git commit -m "Add a sign-in page and sign-out to the web UI"`

---

## Self-review

**Spec coverage (phase 1 scope):**
- R1 accounts: Tasks 2, 3, 4. Rules, same-message failures, rate limiting, password change signs out others, deletion with password and typed confirmation. Django's authenticate hashes against a dummy for unknown handles.
- R2 sessions: Tasks 1, 4. Cookie flags, new key per sign-in, logout ends the session, CSRF on every write. Deviation 4 covers hashed tokens.
- R3 ownership: Tasks 1 (done), 5. Shared reference data is writable only through the CLI and `manage.py`.
- R10 host and proxy: Task 1 (`ALLOWED_HOSTS`, trusted origins, proxy header, secret, headers). R11 local mode: Tasks 7 and 8 and the stdlib suite. R12 CLI: Task 7. R13 migration: Task 6.
- Decisions of 2026-11-06 (invite-only, no email, admin resets by CLI): Tasks 3, 6; passwords reset with `manage.py changepassword`.
- Not covered on purpose: R4 to R9, R14, the JSON export in R15.

**Step scan:** each step names a file, a signature or a test with its assertion. No step defers a decision.

**Type consistency:** `connect_user(user_id, path)` and `user_db_path(path, user_id)` are used with those names in Tasks 4, 5, 6, 7. `create_user(handle, password, display_name=None, superuser=False)` is called that way in Tasks 5 and 6. `RateLimiter` methods match between Tasks 2 and 4. `adopt_data(path, user_id)` matches between Tasks 6 and 7. `config.ACCOUNTS_DB_PATH` is defined in Task 1 and used by Task 7.

**Proportion:** a few times the phase-1 requirements it implements. Code bodies are left to the implementer; signatures, settings values and test assertions are fixed.
