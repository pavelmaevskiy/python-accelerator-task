"""Transaction boundary with post-commit job enqueueing.

A service wraps its work in ``async with uow:``. Jobs registered through ``uow.enqueue`` are sent
to the queue only after a successful commit, so a rolled-back transaction never sends an email.
"""

from dataclasses import dataclass, field
from types import TracebackType
from typing import Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession, AsyncSessionTransaction

JobArg = str | int | bool | None


@runtime_checkable
class JobQueue(Protocol):
    async def enqueue(self, name: str, **kwargs: JobArg) -> None: ...


@dataclass(frozen=True, slots=True)
class PendingJob:
    name: str
    kwargs: dict[str, JobArg]


@dataclass
class UnitOfWork:
    session: AsyncSession
    queue: JobQueue
    _tx: AsyncSessionTransaction | None = field(default=None, init=False, repr=False)
    _pending: list[PendingJob] = field(default_factory=list, init=False, repr=False)

    def enqueue(self, name: str, **kwargs: JobArg) -> None:
        self._pending.append(PendingJob(name, kwargs))

    async def __aenter__(self) -> "UnitOfWork":
        self._pending = []
        self._tx = self.session.begin()
        await self._tx.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        assert self._tx is not None  # noqa: S101
        tx, self._tx = self._tx, None
        await tx.__aexit__(exc_type, exc, tb)  # commits on success, rolls back on error
        pending, self._pending = self._pending, []
        if exc_type is None:
            for job in pending:
                await self.queue.enqueue(job.name, **job.kwargs)
