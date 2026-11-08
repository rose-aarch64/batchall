from batchall.ratelimit import RateLimiter
from hosted import authviews, shared


def reset_limiters():
    """Clear every module-level rate limiter so tests do not leak lockouts."""
    for module in (authviews, shared):
        for value in vars(module).values():
            if isinstance(value, RateLimiter):
                value.reset(*list(value._entries))
