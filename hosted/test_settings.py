"""Settings for the Django test suite."""

import os
import tempfile

os.environ.setdefault("BATCHALL_SECRET", "x" * 50)

from hosted.settings import *  # noqa: E402,F401,F403
from hosted.settings import DATABASES  # noqa: E402

# One named file, so threads in a test share a single database.
DATABASES["default"]["TEST"] = {
    "NAME": os.path.join(tempfile.gettempdir(), f"batchall-test-accounts-{os.getpid()}.db"),
}
