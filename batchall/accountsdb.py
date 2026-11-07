"""Look up an account id in the hosted accounts database, using only the standard library.

The command line runs without Django installed, so this reads Django's ``auth_user`` table directly
and read-only. The queries are checked against a real Django database by
``hosted/tests/test_accountsdb_contract.py``.
"""

import sqlite3
from pathlib import Path

from . import config

BY_HANDLE_SQL = "SELECT id FROM auth_user WHERE username = ? COLLATE NOCASE AND is_active = 1"
FIRST_ADMIN_SQL = "SELECT id FROM auth_user WHERE is_superuser = 1 AND is_active = 1 ORDER BY id LIMIT 1"

_ADVICE = "run manage.py migrate and manage.py adopt first"


def lookup_user_id(handle=None, accounts_path=None):
    """The account id for a handle, or for the first administrator when no handle is given."""
    path = Path(accounts_path or config.ACCOUNTS_DB_PATH)
    if not path.is_file():
        raise ValueError(f"no accounts database at {path}; {_ADVICE}")
    try:
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise ValueError(f"cannot open the accounts database {path}: {exc}; {_ADVICE}")
    try:
        if handle:
            row = conn.execute(BY_HANDLE_SQL, (handle,)).fetchone()
        else:
            row = conn.execute(FIRST_ADMIN_SQL).fetchone()
    except sqlite3.DatabaseError as exc:
        raise ValueError(f"the accounts database {path} is not usable ({exc}); {_ADVICE}")
    finally:
        conn.close()
    if row is None:
        raise ValueError("no such user" if handle else "no administrator account exists")
    return row[0]
