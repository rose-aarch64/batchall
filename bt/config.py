"""Paths and constants. Every path can be overridden with an environment variable."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("BT_DATA_DIR", ROOT / "data"))
DB_PATH = Path(os.environ.get("BT_DB", DATA_DIR / "battletech.db"))
CATALOG_DIR = DATA_DIR / "catalog"
BOX_PAGES_DIR = DATA_DIR / "box_pages"
ART_DIR = DATA_DIR / "art"
WEB_DIR = ROOT / "web"

MEKBAY_DB_URL = "https://db.mekbay.com"
MEKBAY_ART_URL = MEKBAY_DB_URL + "/images/fluff/"
CATALOG_FILES = ("units", "factions", "eras")
FLUFF_FILE = "units-fluff"  # unit lore; about 50 MB and optional, imports work without it
USER_AGENT = "battletech-collection/0.1"

DEFAULT_PORT = 8765
