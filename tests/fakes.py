"""Test doubles shared by unit, integration and e2e tests."""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from app.core.storage import ObjectMeta, PresignedUpload
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


class FakeObjectStorage:
    """In-memory ObjectStorage. ``seed`` simulates the client's direct upload to quarantine."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.content_types: dict[str, str] = {}
        self.presigned: list[tuple[str, str, int, int]] = []

    def seed(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        self.objects[key] = data
        self.content_types[key] = content_type

    async def presign_put(
        self, key: str, content_type: str, content_length: int, expires_in: int
    ) -> PresignedUpload:
        self.presigned.append((key, content_type, content_length, expires_in))
        return PresignedUpload(
            url=f"https://storage.test/{key}?sig=fake",
            headers={"Content-Type": content_type, "Content-Length": str(content_length)},
        )

    async def head(self, key: str) -> ObjectMeta | None:
        if key not in self.objects:
            return None
        return ObjectMeta(len(self.objects[key]), self.content_types.get(key))

    async def get(self, key: str) -> bytes:
        return self.objects[key]

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        self.seed(key, data, content_type)

    async def copy(self, src: str, dst: str) -> None:
        self.seed(dst, self.objects[src], self.content_types.get(src, ""))

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)
        self.content_types.pop(key, None)

    def public_url(self, key: str | None) -> str | None:
        return None if key is None else f"https://cdn.test/{key}"

    def keys(self, prefix: str = "") -> list[str]:
        return sorted(k for k in self.objects if k.startswith(prefix))


class FakeApprovalHandler:
    """ApprovalSubjectHandler double that records calls."""

    def __init__(self, subject_type: str = "event_rsvp", *, fail_on_approve: bool = False) -> None:
        from app.core.enums import ApprovalSubjectType

        self.subject_type = ApprovalSubjectType(subject_type)
        self.fail_on_approve = fail_on_approve
        self.approved: list[object] = []
        self.denied_or_expired: list[object] = []

    async def on_approved(self, session: object, approval: object) -> None:
        if self.fail_on_approve:
            raise RuntimeError("handler exploded")
        self.approved.append(approval)

    async def on_denied_or_expired(self, session: object, approval: object) -> None:
        self.denied_or_expired.append(approval)
