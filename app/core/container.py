"""Process-wide dependencies, built once at startup and stored on ``app.state.container``."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.clock import Clock, SystemClock
from app.core.commitments import CommitmentChecker, NoCommitments
from app.core.config import Settings
from app.core.rate_limit import RateLimiter
from app.core.security import PasswordHasher, TokenCodec
from app.core.storage import ObjectStorage, S3ObjectStorage
from app.core.unit_of_work import JobQueue
from app.db.session import create_engine, create_sessionmaker


@dataclass
class Container:
    settings: Settings
    engine: AsyncEngine
    sessionmaker: async_sessionmaker[AsyncSession]
    redis: Redis
    hasher: PasswordHasher
    codec: TokenCodec
    clock: Clock
    queue: JobQueue
    rate_limiter: RateLimiter
    storage: ObjectStorage
    commitments: CommitmentChecker


@asynccontextmanager
async def build_container(settings: Settings) -> AsyncIterator[Container]:
    from arq import create_pool
    from arq.connections import RedisSettings

    from app.core.queue import ArqJobQueue

    engine = create_engine(settings.database_url)
    redis: Redis = Redis.from_url(settings.redis_url, decode_responses=True)
    arq_pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    try:
        yield Container(
            settings=settings,
            engine=engine,
            sessionmaker=create_sessionmaker(engine),
            redis=redis,
            hasher=PasswordHasher(),
            codec=TokenCodec(settings.jwt_keys, settings.jwt_current_kid),
            clock=SystemClock(),
            queue=ArqJobQueue(arq_pool),
            rate_limiter=RateLimiter(redis),
            storage=S3ObjectStorage(settings.s3_bucket, settings.media_base_url),
            commitments=NoCommitments(),
        )
    finally:
        await arq_pool.aclose()
        await redis.aclose()
        await engine.dispose()
