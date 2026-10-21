"""Identity background jobs (ARQ). Jobs take ids, re-read state, and are idempotent.

Raw tokens are passed through the queue but must never be logged.
"""

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


IDENTITY_JOBS = [
    send_verification_email,
    send_welcome_email,
    send_password_reset_email,
    send_trainer_invite_email,
    send_coach_invite_email,
    send_email_change_email,
    send_approval_notification,
]
