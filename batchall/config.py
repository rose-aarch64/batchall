"""Paths and constants. Every path can be overridden with an environment variable."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _setting(name, old_name):
    """The BATCHALL_* setting, else the BT_* name it had before the rename, else None."""
    return os.environ.get(name) or os.environ.get(old_name) or None


DATA_DIR = Path(_setting("BATCHALL_DATA_DIR", "BT_DATA_DIR") or ROOT / "data")
_db_setting = _setting("BATCHALL_DB", "BT_DB")
DB_IS_DEFAULT = _db_setting is None  # a database chosen by a setting is never replaced by the old file
DB_PATH = Path(_db_setting or DATA_DIR / "batchall.db")
LEGACY_DB_PATH = DATA_DIR / "battletech.db"  # the database file name from before the rename
CATALOG_DIR = DATA_DIR / "catalog"
BOX_PAGES_DIR = DATA_DIR / "box_pages"
ART_DIR = DATA_DIR / "art"
WEB_DIR = ROOT / "web"

MEKBAY_DB_URL = "https://db.mekbay.com"
MEKBAY_ART_URL = MEKBAY_DB_URL + "/images/fluff/"
MEKBAY_SITE_URL = "https://mekbay.com"  # faction logos are served here, not by the data host
CATALOG_FILES = ("units", "factions", "eras")
FLUFF_FILE = "units-fluff"  # unit lore; about 50 MB and optional, imports work without it
USER_AGENT = "batchall/0.1"

DEFAULT_PORT = 8765
