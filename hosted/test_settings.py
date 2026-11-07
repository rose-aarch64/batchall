"""Settings for the Django test suite."""

import os
import tempfile

os.environ.setdefault("BATCHALL_SECRET", "x" * 50)

from hosted.settings import *  # noqa: E402,F401,F403
from hosted.settings import DATABASES  # noqa: E402

# No test may open the real data/accounts.db or data/batchall.db, so both point at temporary files.
_TEMP = tempfile.gettempdir()
DATABASES["default"]["NAME"] = os.path.join(_TEMP, f"batchall-test-accounts-base-{os.getpid()}.db")
BATCHALL_SHARED_DB = os.path.join(_TEMP, f"batchall-test-shared-{os.getpid()}.db")

# One named file, so threads in a test share a single database.
DATABASES["default"]["TEST"] = {
    "NAME": os.path.join(_TEMP, f"batchall-test-accounts-{os.getpid()}.db"),
}
