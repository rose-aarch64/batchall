"""Accounts: handles, scrypt password hashes and single-use invite codes.

Every function takes a connection from ``db.connect_shared``. Passwords and hashes are never logged or
returned; user dicts leave ``password_hash`` out.
"""

import hashlib
import hmac
import re
import secrets
import sqlite3
import unicodedata

MIN_PASSWORD = 10
MAX_PASSWORD = 256
MAX_DISPLAY_NAME = 60

RESERVED_HANDLES = frozenset({
    "api", "admin", "login", "logout", "signup", "signin", "u", "static", "assets", "index",
})

_HANDLE_RE = re.compile(r"[a-z0-9-]{3,30}")
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 16384, 8, 1
_SALT_BYTES = 16
_USER_COLUMNS = "id, handle, display_name, role, visibility, created_at"


class AccountError(ValueError):
    """A sign-up, login or account change was refused. The message is safe to show the user."""


def normalize_handle(raw):
    handle = (raw or "").strip().lower()
    if not _HANDLE_RE.fullmatch(handle):
        raise AccountError("handle must be 3 to 30 characters: lowercase letters, digits and hyphens")
    if handle in RESERVED_HANDLES:
        raise AccountError("that handle is reserved")
    return handle


def _scrypt(password, salt, n, r, p):
    # maxmem is raised above the 32 MiB default so a stored n larger than ours still verifies.
    return hashlib.scrypt(
        unicodedata.normalize("NFC", password).encode("utf-8"),
        salt=salt, n=n, r=r, p=p, maxmem=128 * 1024 * 1024,
    )


def _check_password(password):
    if not isinstance(password, str):
        raise AccountError("password is required")
    if len(password) < MIN_PASSWORD:
        raise AccountError(f"password must be at least {MIN_PASSWORD} characters")
    if len(password) > MAX_PASSWORD:
        raise AccountError(f"password must be at most {MAX_PASSWORD} characters")


def hash_password(password):
    _check_password(password)
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = _scrypt(password, salt, _SCRYPT_N, _SCRYPT_R, _SCRYPT_P)
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password, stored):
    try:
        scheme, n, r, p, salt_hex, hash_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = bytes.fromhex(hash_hex)
        actual = _scrypt(password, bytes.fromhex(salt_hex), int(n), int(r), int(p))
    except (ValueError, AttributeError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


# Verified against when a handle is unknown, so a miss costs about the same as a wrong password.
_DUMMY_HASH = hash_password("dummy-password-for-timing")


def _user_dict(row):
    return dict(row) if row is not None else None


def _display_name(display_name, handle):
    name = (display_name or "").strip() or handle
    if len(name) > MAX_DISPLAY_NAME:
        raise AccountError(f"display name must be at most {MAX_DISPLAY_NAME} characters")
    return name


def _insert_user(conn, handle, password, display_name, role):
    """Insert a user without committing. Returns the new id."""
    handle = normalize_handle(handle)
    name = _display_name(display_name, handle)
    if role not in ("user", "admin"):
        raise AccountError("role must be user or admin")
    stored = hash_password(password)
    try:
        cur = conn.execute(
            "INSERT INTO users (handle, display_name, password_hash, role) VALUES (?, ?, ?, ?)",
            (handle, name, stored, role),
        )
    except sqlite3.IntegrityError:
        raise AccountError("that handle is already taken") from None
    return cur.lastrowid


def create_user(conn, handle, password, display_name=None, role="user"):
    """Create a user with no invite check (admin setup and tests)."""
    try:
        user_id = _insert_user(conn, handle, password, display_name, role)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return get_user(conn, user_id)


def _code_hash(secret, code):
    return hmac.new(secret, code.encode(), "sha256").hexdigest()


def make_invite(conn, secret, created_by):
    """Create an invite. The code is returned once; only its keyed hash is stored."""
    code = secrets.token_urlsafe(16)
    conn.execute(
        "INSERT INTO invites (code_hash, created_by) VALUES (?, ?)", (_code_hash(secret, code), created_by)
    )
    conn.commit()
    return code


def sign_up(conn, secret, handle, password, invite_code, display_name=None):
    """Consume an invite and create the user in one BEGIN IMMEDIATE transaction."""
    if not isinstance(invite_code, str) or not invite_code:
        raise AccountError("invalid invite code")
    code_hash = _code_hash(secret, invite_code)
    previous = conn.isolation_level
    conn.commit()
    conn.isolation_level = None  # explicit transaction control for the duration
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            user_id = _insert_user(conn, handle, password, display_name, "user")
            used = conn.execute(
                "UPDATE invites SET used_by = ?, used_at = CURRENT_TIMESTAMP "
                "WHERE code_hash = ? AND used_by IS NULL",
                (user_id, code_hash),
            )
            if used.rowcount != 1:
                raise AccountError("invalid invite code")
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    finally:
        conn.isolation_level = previous
    return get_user(conn, user_id)


def authenticate(conn, handle, password):
    """Return the user dict for a correct handle and password, else None."""
    try:
        handle = normalize_handle(handle)
    except AccountError:
        handle = None
    row = None
    if handle is not None:
        row = conn.execute(
            f"SELECT {_USER_COLUMNS}, password_hash FROM users WHERE handle = ?", (handle,)
        ).fetchone()
    stored = row["password_hash"] if row is not None else _DUMMY_HASH
    ok = verify_password(password if isinstance(password, str) else "", stored)
    if row is None or not ok:
        return None
    user = dict(row)
    del user["password_hash"]
    return user


def get_user(conn, user_id):
    return _user_dict(conn.execute(f"SELECT {_USER_COLUMNS} FROM users WHERE id = ?", (user_id,)).fetchone())


def get_user_by_handle(conn, handle):
    return _user_dict(
        conn.execute(
            f"SELECT {_USER_COLUMNS} FROM users WHERE handle = ?", ((handle or "").strip().lower(),)
        ).fetchone()
    )


def list_users(conn):
    return [dict(r) for r in conn.execute(f"SELECT {_USER_COLUMNS} FROM users ORDER BY id")]


def first_admin(conn):
    return _user_dict(
        conn.execute(f"SELECT {_USER_COLUMNS} FROM users WHERE role = 'admin' ORDER BY id LIMIT 1").fetchone()
    )
