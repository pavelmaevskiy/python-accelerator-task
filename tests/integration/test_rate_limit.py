import pytest

from app.core.errors import RateLimitedError
from app.core.rate_limit import RateLimiter

pytestmark = pytest.mark.integration


async def test_fixed_window_blocks_after_limit_and_reports_retry_after(redis) -> None:
    rl = RateLimiter(redis)
    for _ in range(5):
        await rl.hit("login:email:a@b.co", limit=5, window_seconds=60)
    with pytest.raises(RateLimitedError) as e:
        await rl.hit("login:email:a@b.co", limit=5, window_seconds=60)
    assert 0 < e.value.retry_after <= 60


async def test_keys_are_independent(redis) -> None:
    rl = RateLimiter(redis)
    for _ in range(3):
        await rl.hit("a", limit=3, window_seconds=60)
    await rl.hit("b", limit=3, window_seconds=60)  # different key: not blocked


async def test_window_resets_after_expiry(redis) -> None:
    rl = RateLimiter(redis)
    await rl.hit("w", limit=1, window_seconds=1)
    with pytest.raises(RateLimitedError):
        await rl.hit("w", limit=1, window_seconds=1)
    await redis.delete("rl:w")  # simulate the TTL elapsing
    await rl.hit("w", limit=1, window_seconds=1)
