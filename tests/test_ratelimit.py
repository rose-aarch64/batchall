import threading
import unittest

from batchall.ratelimit import RateLimiter


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class RateLimiterTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.limiter = RateLimiter(clock=self.clock)
        self.key = ("handle", "rose")

    def fail(self, times, *keys):
        for _ in range(times):
            self.limiter.record_failure(*(keys or (self.key,)))

    def test_four_failures_do_not_lock(self):
        self.fail(4)
        self.assertEqual(self.limiter.retry_after(self.key), 0)

    def test_fifth_failure_locks_for_300_seconds(self):
        self.fail(5)
        self.assertEqual(self.limiter.retry_after(self.key), 300)

    def test_lockout_expires(self):
        self.fail(5)
        self.clock.advance(301)
        self.assertEqual(self.limiter.retry_after(self.key), 0)

    def test_failures_outside_window_do_not_count(self):
        self.fail(4)
        self.clock.advance(301)
        self.fail(1)
        self.assertEqual(self.limiter.retry_after(self.key), 0)
        self.fail(3)
        self.assertEqual(self.limiter.retry_after(self.key), 0)
        self.fail(1)
        self.assertEqual(self.limiter.retry_after(self.key), 300)

    def test_reset_clears_lockout(self):
        self.fail(5)
        self.limiter.reset(self.key)
        self.assertEqual(self.limiter.retry_after(self.key), 0)
        self.fail(4)
        self.assertEqual(self.limiter.retry_after(self.key), 0)

    def test_keys_are_independent(self):
        self.fail(5)
        self.assertEqual(self.limiter.retry_after(("handle", "other")), 0)

    def test_retry_after_returns_larger_of_two_keys(self):
        ip = ("ip", "10.0.0.1")
        self.fail(5, self.key)
        self.clock.advance(100)
        self.fail(5, ip)
        self.assertEqual(self.limiter.retry_after(self.key, ip), 300)
        self.assertEqual(self.limiter.retry_after(self.key), 200)

    def test_retry_after_rounds_up(self):
        self.fail(5)
        self.clock.advance(299.6)
        self.assertEqual(self.limiter.retry_after(self.key), 1)

    def test_table_is_capped(self):
        for i in range(10001):
            self.clock.advance(0.001)
            self.limiter.record_failure(("handle", str(i)))
        self.assertLessEqual(len(self.limiter._entries), 10000)

    def test_cap_work_per_call_is_bounded(self):
        for i in range(10000):
            self.limiter.record_failure(("handle", str(i)))
        examined = []
        original = self.limiter._is_idle

        def counting(entry, now):
            examined.append(1)
            return original(entry, now)

        self.limiter._is_idle = counting
        for i in range(10000, 10200):
            self.limiter.record_failure(("handle", str(i)))
        self.assertLessEqual(len(self.limiter._entries), 10000)
        self.assertLessEqual(len(examined), 200 * 8)

    def test_concurrent_failures_are_not_lost(self):
        limiter = RateLimiter(max_failures=1000, clock=self.clock)
        self.limiter = limiter

        def many():
            for _ in range(400):
                limiter.record_failure(self.key)

        threads = [threading.Thread(target=many) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(limiter._entries[self.key].failures), 800)


if __name__ == "__main__":
    unittest.main()
