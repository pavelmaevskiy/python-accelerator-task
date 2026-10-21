"""ARQ worker settings: ``uv run arq app.jobs.worker.WorkerSettings``."""

from arq.connections import RedisSettings
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import get_settings
from app.core.email import LoggingEmailSender
from app.db.session import create_engine, create_sessionmaker
from app.jobs.identity_jobs import IDENTITY_JOBS, JobContext
from app.services.notification_service import NotificationService


async def on_startup(ctx: JobContext) -> None:
    settings = get_settings()
    engine = create_engine(settings.database_url, pool_size=5)
    ctx["engine"] = engine
    ctx["notifications"] = NotificationService(
        create_sessionmaker(engine), LoggingEmailSender(), settings
    )


async def on_shutdown(ctx: JobContext) -> None:
    engine = ctx.get("engine")
    if isinstance(engine, AsyncEngine):
        await engine.dispose()


class WorkerSettings:
    functions = IDENTITY_JOBS
    on_startup = on_startup
    on_shutdown = on_shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    max_tries = 5
