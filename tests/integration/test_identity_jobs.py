import pytest

from app.core.config import Settings
from app.jobs import identity_jobs as jobs
from app.services.notification_service import NotificationService

pytestmark = pytest.mark.integration


class RecordingSender:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, dict[str, str]]] = []

    async def send(self, template: str, to: str, context: dict[str, str]) -> None:
        self.sent.append((template, to, context))


@pytest.fixture
def sender() -> RecordingSender:
    return RecordingSender()


@pytest.fixture
def ctx(sessionmaker_, sender) -> dict[str, object]:
    settings = Settings(
        env="test", jwt_keys={"k1": "x" * 40}, ip_hash_salt="s", public_base_url="http://app"
    )
    return {"notifications": NotificationService(sessionmaker_, sender, settings)}


async def test_verification_job_sends_link_to_user(ctx, sender, make_user) -> None:
    user = await make_user(email="ann@x.co", first_name="Ann")
    await jobs.send_verification_email(ctx, user_id=str(user.id), token="tok123")
    [(template, to, context)] = sender.sent
    assert (template, to) == ("verify_email", "ann@x.co")
    assert context == {"first_name": "Ann", "link": "http://app/verify-email?token=tok123"}


async def test_jobs_skip_erased_or_missing_users(ctx, sender, make_user) -> None:
    gone = await make_user(status="deleted")
    await jobs.send_welcome_email(ctx, user_id=str(gone.id))
    await jobs.send_password_reset_email(
        ctx, user_id="00000000-0000-0000-0000-000000000000", token="t"
    )
    assert sender.sent == []


async def test_email_change_job_mails_the_new_address(ctx, sender, make_user) -> None:
    user = await make_user(email="old@x.co")
    await jobs.send_email_change_email(ctx, user_id=str(user.id), token="t", new_email="new@x.co")
    assert sender.sent[0][:2] == ("email_change", "new@x.co")


async def test_every_job_is_registered_for_the_worker() -> None:
    from app.jobs.worker import WorkerSettings

    assert {f.__name__ for f in WorkerSettings.functions} == {
        "send_verification_email",
        "send_welcome_email",
        "send_password_reset_email",
        "send_trainer_invite_email",
        "send_coach_invite_email",
        "send_email_change_email",
        "send_approval_notification",
        "delete_s3_object",
    }


async def test_delete_s3_object_job_is_idempotent(fake_storage) -> None:
    fake_storage.seed("avatars/x/y.png", b"data")
    ctx = {"storage": fake_storage}
    await jobs.delete_s3_object(ctx, key="avatars/x/y.png")
    await jobs.delete_s3_object(ctx, key="avatars/x/y.png")  # second run: no error
    assert fake_storage.keys() == []
