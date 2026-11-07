from batchall.ratelimit import RateLimiter
from hosted import authviews


def reset_limiters():
    """Clear every module-level sign-in limiter so tests do not leak lockouts."""
    for value in vars(authviews).values():
        if isinstance(value, RateLimiter):
            value.reset(*list(value._entries))
