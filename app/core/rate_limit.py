"""Redis fixed-window rate limiter (atomic INCR + EXPIRE in one Lua script)."""

from redis.asyncio import Redis

from app.core.errors import RateLimitedError

_LUA = """
local c = redis.call('INCR', KEYS[1])
if c == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
local ttl = redis.call('TTL', KEYS[1])
if ttl < 0 then redis.call('EXPIRE', KEYS[1], ARGV[1]); ttl = tonumber(ARGV[1]) end
return {c, ttl}
"""


class RateLimiter:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def hit(self, key: str, *, limit: int, window_seconds: int) -> None:
        """Count one hit; raise ``RateLimitedError`` once ``limit`` is exceeded in the window."""
        count, ttl = await self._redis.eval(_LUA, 1, f"rl:{key}", str(window_seconds))  # type: ignore[misc]
        if int(count) > limit:
            raise RateLimitedError(
                "RATE_LIMITED",
                "Too many requests, slow down",
                retry_after=max(int(ttl), 1),
            )
