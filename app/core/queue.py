"""ARQ-backed implementation of the ``JobQueue`` Protocol."""

from arq.connections import ArqRedis

from app.core.unit_of_work import JobArg


class ArqJobQueue:
    def __init__(self, pool: ArqRedis) -> None:
        self._pool = pool

    async def enqueue(self, name: str, **kwargs: JobArg) -> None:
        await self._pool.enqueue_job(name, **kwargs)  # type: ignore[arg-type]  # job kwargs share names with arq options only by coincidence of typing
