"""ARQ worker settings: ``uv run arq app.jobs.worker.WorkerSettings``."""

from arq import cron
from arq.connections import ArqRedis, RedisSettings, create_pool
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.approvals import ApprovalHandlerRegistry
from app.core.clock import SystemClock
from app.core.config import get_settings
from app.core.email import LoggingEmailSender
from app.core.queue import ArqJobQueue
from app.core.security import TokenCodec
from app.core.storage import S3ObjectStorage
from app.db.session import create_engine, create_sessionmaker
from app.jobs.identity_jobs import (
    IDENTITY_JOBS,
    JobContext,
    expire_impersonation_sessions,
    expire_purchase_approvals,
)
from app.services.notification_service import NotificationService


async def on_startup(ctx: JobContext) -> None:
    settings = get_settings()
    engine = create_engine(settings.database_url, pool_size=5)
    sessionmaker = create_sessionmaker(engine)
    pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    ctx["engine"] = engine
    ctx["arq_pool"] = pool
    ctx["sessionmaker"] = sessionmaker
    ctx["clock"] = SystemClock()
    ctx["queue"] = ArqJobQueue(pool)
    ctx["codec"] = TokenCodec(settings.jwt_keys, settings.jwt_current_kid)
    ctx["approval_registry"] = ApprovalHandlerRegistry()  # Epic-02/05 register handlers here
    ctx["storage"] = S3ObjectStorage(settings.s3_bucket, settings.media_base_url)
    ctx["notifications"] = NotificationService(sessionmaker, LoggingEmailSender(), settings)


async def on_shutdown(ctx: JobContext) -> None:
    pool = ctx.get("arq_pool")
    if isinstance(pool, ArqRedis):
        await pool.aclose()
    engine = ctx.get("engine")
    if isinstance(engine, AsyncEngine):
        await engine.dispose()


class WorkerSettings:
    functions = IDENTITY_JOBS
    cron_jobs = [
        cron(expire_purchase_approvals, minute=set(range(0, 60, 5)), run_at_startup=False),
        cron(expire_impersonation_sessions, minute=set(range(0, 60, 5)), run_at_startup=False),
    ]
    on_startup = on_startup
    on_shutdown = on_shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    max_tries = 5
