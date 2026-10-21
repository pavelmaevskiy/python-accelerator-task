"""Identity background jobs (ARQ). Jobs take ids, re-read state, and are idempotent.

Raw tokens are passed through the queue but must never be logged.
"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.approvals import ApprovalHandlerRegistry
from app.core.clock import Clock
from app.core.storage import ObjectStorage
from app.core.unit_of_work import JobQueue
from app.services.approval_service import make_approval_service
from app.services.notification_service import NotificationService

JobContext = dict[str, object]


def _notifier(ctx: JobContext) -> NotificationService:
    service = ctx["notifications"]
    assert isinstance(service, NotificationService)  # noqa: S101
    return service


async def send_verification_email(ctx: JobContext, *, user_id: str, token: str) -> None:
    await _notifier(ctx).verification(user_id, token)


async def send_welcome_email(ctx: JobContext, *, user_id: str) -> None:
    await _notifier(ctx).welcome(user_id)


async def send_password_reset_email(ctx: JobContext, *, user_id: str, token: str) -> None:
    await _notifier(ctx).password_reset(user_id, token)


async def send_trainer_invite_email(ctx: JobContext, *, user_id: str, token: str) -> None:
    await _notifier(ctx).trainer_invite(user_id, token)


async def send_coach_invite_email(ctx: JobContext, *, share_link_id: str) -> None:
    await _notifier(ctx).coach_invite(share_link_id)


async def send_email_change_email(
    ctx: JobContext, *, user_id: str, token: str, new_email: str
) -> None:
    await _notifier(ctx).email_change(user_id, token, new_email)


async def send_approval_notification(ctx: JobContext, *, approval_id: str) -> None:
    await _notifier(ctx).approval_notification(approval_id)


async def delete_s3_object(ctx: JobContext, *, key: str) -> None:
    """Idempotent: deleting a missing key is a no-op in S3."""
    storage = ctx["storage"]
    assert isinstance(storage, ObjectStorage)  # noqa: S101
    await storage.delete(key)


async def expire_purchase_approvals(ctx: JobContext) -> int:
    """Cron: pending approvals older than 48 h become ``expired`` (idempotent, set-based)."""
    sessionmaker = ctx["sessionmaker"]
    registry = ctx["approval_registry"]
    clock = ctx["clock"]
    queue = ctx["queue"]
    assert isinstance(sessionmaker, async_sessionmaker)  # noqa: S101
    assert isinstance(registry, ApprovalHandlerRegistry)  # noqa: S101
    assert isinstance(clock, Clock)  # noqa: S101
    assert isinstance(queue, JobQueue)  # noqa: S101
    session: AsyncSession
    async with sessionmaker() as session:
        return await make_approval_service(session, queue, registry, clock).expire_due()


IDENTITY_JOBS = [
    expire_purchase_approvals,
    delete_s3_object,
    send_verification_email,
    send_welcome_email,
    send_password_reset_email,
    send_trainer_invite_email,
    send_coach_invite_email,
    send_email_change_email,
    send_approval_notification,
]
