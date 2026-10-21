"""Test doubles shared by unit, integration and e2e tests."""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from app.core.unit_of_work import JobArg


class MutableClock:
    """Starts at the real current time (JWT expiry uses real time) and can be advanced."""

    def __init__(self, at: datetime | None = None) -> None:
        self._at = at or datetime.now(UTC)

    def now(self) -> datetime:
        return self._at

    def today(self) -> date:
        return self._at.date()

    def advance(self, **kwargs: float) -> None:
        self._at += timedelta(**kwargs)


@dataclass(frozen=True)
class RecordedJob:
    name: str
    kwargs: dict[str, JobArg]


@dataclass
class FakeQueue:
    jobs: list[RecordedJob] = field(default_factory=list)

    async def enqueue(self, name: str, **kwargs: JobArg) -> None:
        self.jobs.append(RecordedJob(name, kwargs))

    def named(self, name: str) -> list[RecordedJob]:
        return [j for j in self.jobs if j.name == name]
