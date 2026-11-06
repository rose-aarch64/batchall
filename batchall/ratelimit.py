"""In-memory rate limiter for sign-in attempts.

Failures are counted per key inside a sliding window. Reaching the failure
limit starts a lockout for that key. The table is guarded by a lock because
the server is threaded, and it is capped so that a flood of distinct keys
cannot grow it without bound.
"""

import math
import threading
import time
from collections import OrderedDict, deque

MAX_KEYS = 10_000


class _Entry:
    __slots__ = ("failures", "locked_until")

    def __init__(self):
        self.failures = deque()
        self.locked_until = 0.0


class RateLimiter:
    def __init__(self, max_failures=5, window_seconds=300,
                 lockout_seconds=300, clock=time.monotonic):
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self.lockout_seconds = lockout_seconds
        self._clock = clock
        self._lock = threading.Lock()
        # Ordered from least to most recently touched.
        self._entries = OrderedDict()

    def retry_after(self, *keys):
        """Whole seconds left on the longest lockout among keys, else 0."""
        with self._lock:
            now = self._clock()
            remaining = 0.0
            for key in keys:
                entry = self._entries.get(key)
                if entry is not None:
                    remaining = max(remaining, entry.locked_until - now)
            return math.ceil(remaining) if remaining > 0 else 0

    def record_failure(self, *keys):
        with self._lock:
            now = self._clock()
            for key in keys:
                entry = self._entries.get(key)
                if entry is None:
                    entry = self._entries[key] = _Entry()
                else:
                    self._entries.move_to_end(key)
                self._expire(entry, now)
                entry.failures.append(now)
                if len(entry.failures) >= self.max_failures:
                    entry.locked_until = now + self.lockout_seconds
                    entry.failures.clear()
            if len(self._entries) > MAX_KEYS:
                self._trim(now)

    def reset(self, *keys):
        with self._lock:
            for key in keys:
                self._entries.pop(key, None)

    def _expire(self, entry, now):
        cutoff = now - self.window_seconds
        while entry.failures and entry.failures[0] <= cutoff:
            entry.failures.popleft()

    def _is_idle(self, entry, now):
        self._expire(entry, now)
        return not entry.failures and entry.locked_until <= now

    def _trim(self, now):
        for key in [k for k, e in self._entries.items()
                    if self._is_idle(e, now)]:
            del self._entries[key]
        while len(self._entries) > MAX_KEYS:
            self._entries.popitem(last=False)
