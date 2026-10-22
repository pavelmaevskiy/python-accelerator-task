import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.core.approvals import ApprovalHandlerRegistry, ApprovalView
from app.core.enums import ApprovalStatus, ApprovalSubjectType
from app.core.errors import ConflictError, DomainValidationError, NotFoundError
from app.models.identity import PurchaseApproval
from app.repositories.approval_repo import ApprovalRow
from app.services.approval_service import ApprovalService
from tests.fakes import FakeApprovalHandler, MutableClock

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
PARENT = SimpleNamespace(user_id=uuid.uuid4())


class FakeUow:
    session = object()

    def __init__(self) -> None:
        self.entered = 0
        self.committed = 0
        self.rolled_back = 0
        self.enqueued: list[str] = []

    async def __aenter__(self) -> "FakeUow":
        self.entered += 1
        return self

    async def __aexit__(self, exc_type: object, *rest: object) -> None:
        if exc_type is None:
            self.committed += 1
        else:
            self.rolled_back += 1

    def enqueue(self, name: str, **kw: object) -> None:
        self.enqueued.append(name)


class FakeApprovals:
    def __init__(self) -> None:
        self.rows: dict[uuid.UUID, ApprovalRow] = {}

    def make(
        self,
        *,
        status: ApprovalStatus = ApprovalStatus.PENDING,
        age_h: float = 1,
        subject: str = "event_rsvp",
    ) -> ApprovalRow:
        requested = NOW - timedelta(hours=age_h)
        a = PurchaseApproval(
            id=uuid.uuid4(), player_profile_id=uuid.uuid4(), parent_user_id=PARENT.user_id,
            trainer_id=uuid.uuid4(), subject_type=ApprovalSubjectType(subject),
            subject_id=uuid.uuid4(), amount_cents=100, token_amount=None, status=status,
            requested_at=requested, expires_at=requested + timedelta(hours=48),
            responded_at=None, parent_note=None,
        )  # fmt: skip
        row = ApprovalRow(a, "Sam", "Club")
        self.rows[a.id] = row
        return row

    async def get_for_parent(self, approval_id, parent_id, *, for_update=False):  # noqa: ANN001
        row = self.rows.get(approval_id)
        return row if row and row.approval.parent_user_id == parent_id else None

    async def expire_due(self, *, now):  # noqa: ANN001
        ids = []
        for row in self.rows.values():
            a = row.approval
            if a.status == ApprovalStatus.PENDING and a.expires_at < now:
                a.status, a.responded_at = ApprovalStatus.EXPIRED, now
                ids.append(a.id)
        return ids

    async def get_rows(self, ids):  # noqa: ANN001
        return [self.rows[i] for i in ids]


class FakeIdempotency:
    def __init__(self) -> None:
        self.store: dict[tuple[uuid.UUID, str], SimpleNamespace] = {}

    async def claim(self, principal_id, key, endpoint, request_hash):  # noqa: ANN001
        if (principal_id, key) in self.store:
            return False
        self.store[(principal_id, key)] = SimpleNamespace(
            request_hash=request_hash, response_body=None, response_status=None
        )
        return True

    async def get(self, principal_id, key):  # noqa: ANN001
        return self.store.get((principal_id, key))

    async def store_response(self, principal_id, key, status, body):  # noqa: ANN001
        rec = self.store[(principal_id, key)]
        rec.response_status, rec.response_body = status, body


@pytest.fixture
def setup():
    approvals, idem, registry, uow = (
        FakeApprovals(),
        FakeIdempotency(),
        ApprovalHandlerRegistry(),
        FakeUow(),
    )
    service = ApprovalService(uow, approvals, idem, registry, MutableClock(NOW))  # type: ignore[arg-type]
    return SimpleNamespace(
        service=service, approvals=approvals, idem=idem, registry=registry, uow=uow
    )


async def test_approve_calls_handler_then_sets_status_in_one_transaction(setup) -> None:
    handler = FakeApprovalHandler()
    setup.registry.register(handler)
    row = setup.approvals.make()
    view = await setup.service.approve(PARENT, row.approval.id, "key-12345", "ok by me")
    assert view.status == ApprovalStatus.APPROVED and view.parent_note == "ok by me"
    assert row.approval.status == ApprovalStatus.APPROVED and row.approval.responded_at == NOW
    assert len(handler.approved) == 1 and handler.approved[0].status == ApprovalStatus.PENDING
    assert setup.uow.committed == 1 and setup.uow.rolled_back == 0


async def test_handler_failure_rolls_back_and_leaves_status_untouched(setup) -> None:
    setup.registry.register(FakeApprovalHandler(fail_on_approve=True))
    row = setup.approvals.make()
    with pytest.raises(RuntimeError):
        await setup.service.approve(PARENT, row.approval.id, "key-12345", None)
    assert row.approval.status == ApprovalStatus.PENDING and setup.uow.rolled_back == 1


async def test_subject_type_without_handler_is_409_APPROVAL_SUBJECT_UNAVAILABLE_and_status_unchanged(
    setup,
) -> None:
    row = setup.approvals.make(subject="content")
    with pytest.raises(ConflictError) as e:
        await setup.service.approve(PARENT, row.approval.id, "key-12345", None)
    assert e.value.code == "APPROVAL_SUBJECT_UNAVAILABLE"
    assert row.approval.status == ApprovalStatus.PENDING


async def test_deny_calls_on_denied_or_expired_and_works_without_a_handler(setup) -> None:
    handler = FakeApprovalHandler()
    setup.registry.register(handler)
    with_handler = setup.approvals.make()
    no_handler = setup.approvals.make(subject="token_purchase")
    denied = await setup.service.deny(PARENT, with_handler.approval.id, "key-aaaaaa", "no")
    assert denied.status == ApprovalStatus.DENIED and len(handler.denied_or_expired) == 1
    other = await setup.service.deny(PARENT, no_handler.approval.id, "key-bbbbbb", None)
    assert other.status == ApprovalStatus.DENIED


@pytest.mark.parametrize(
    "status", [ApprovalStatus.APPROVED, ApprovalStatus.DENIED, ApprovalStatus.EXPIRED]
)
async def test_resolved_approval_is_409_APPROVAL_NOT_PENDING(setup, status) -> None:
    setup.registry.register(FakeApprovalHandler())
    row = setup.approvals.make(status=status)
    with pytest.raises(ConflictError) as e:
        await setup.service.approve(PARENT, row.approval.id, "key-12345", None)
    assert e.value.code == "APPROVAL_NOT_PENDING"


async def test_pending_but_past_48h_is_treated_as_not_pending(setup) -> None:
    setup.registry.register(FakeApprovalHandler())
    boundary = setup.approvals.make(age_h=48)  # expires exactly now: no longer approvable
    with pytest.raises(ConflictError):
        await setup.service.approve(PARENT, boundary.approval.id, "key-12345", None)
    fresh = setup.approvals.make(age_h=47.99)
    assert (await setup.service.approve(PARENT, fresh.approval.id, "key-67890", None)).status == (
        ApprovalStatus.APPROVED
    )


async def test_other_parents_approval_is_not_found(setup) -> None:
    row = setup.approvals.make()
    with pytest.raises(NotFoundError):
        await setup.service.approve(
            SimpleNamespace(user_id=uuid.uuid4()), row.approval.id, "key-12345", None
        )


async def test_replay_with_same_key_returns_stored_response_without_second_handler_call(
    setup,
) -> None:
    handler = FakeApprovalHandler()
    setup.registry.register(handler)
    row = setup.approvals.make()
    first = await setup.service.approve(PARENT, row.approval.id, "key-12345", "n")
    second = await setup.service.approve(PARENT, row.approval.id, "key-12345", "n")
    assert first == second and len(handler.approved) == 1


async def test_same_key_for_a_different_request_is_422_IDEMPOTENCY_KEY_REUSED(setup) -> None:
    setup.registry.register(FakeApprovalHandler())
    a, b = setup.approvals.make(), setup.approvals.make()
    await setup.service.approve(PARENT, a.approval.id, "key-12345", None)
    with pytest.raises(DomainValidationError) as e:
        await setup.service.approve(PARENT, b.approval.id, "key-12345", None)
    assert e.value.code == "IDEMPOTENCY_KEY_REUSED"
    with pytest.raises(DomainValidationError):
        await setup.service.approve(PARENT, a.approval.id, "key-12345", "different note")


async def test_expiry_job_expires_only_pending_past_48h_and_is_idempotent(setup) -> None:
    handler = FakeApprovalHandler()
    setup.registry.register(handler)
    old = setup.approvals.make(age_h=49)
    fresh = setup.approvals.make(age_h=47)
    done = setup.approvals.make(age_h=100, status=ApprovalStatus.APPROVED)
    no_handler = setup.approvals.make(age_h=60, subject="content")
    assert await setup.service.expire_due() == 2
    assert old.approval.status == ApprovalStatus.EXPIRED
    assert no_handler.approval.status == ApprovalStatus.EXPIRED
    assert fresh.approval.status == ApprovalStatus.PENDING
    assert done.approval.status == ApprovalStatus.APPROVED
    assert len(handler.denied_or_expired) == 1  # only the handler-backed row
    assert await setup.service.expire_due() == 0  # second run is a no-op
    assert len(handler.denied_or_expired) == 1


async def test_request_creates_pending_row_expiring_in_48h_and_notifies_after_commit() -> None:
    created: list[PurchaseApproval] = []

    class Repo(FakeApprovals):
        async def add(self, approval):  # noqa: ANN001
            approval.id = uuid.uuid4()
            created.append(approval)
            return approval

    uow = FakeUow()
    service = ApprovalService(
        uow, Repo(), FakeIdempotency(), ApprovalHandlerRegistry(), MutableClock(NOW)
    )  # type: ignore[arg-type]
    await service.request(
        parent_user_id=uuid.uuid4(), player_profile_id=uuid.uuid4(), trainer_id=uuid.uuid4(),
        subject_type=ApprovalSubjectType.TOKEN_PURCHASE, subject_id=uuid.uuid4(), token_amount=5,
    )  # fmt: skip
    [row] = created
    assert row.expires_at - row.requested_at == timedelta(hours=48)
    assert uow.enqueued == ["send_approval_notification"]


def test_approval_view_json_round_trip() -> None:
    view = ApprovalView(
        id=uuid.uuid4(), player_profile_id=uuid.uuid4(), player_display_name="Sam",
        parent_user_id=uuid.uuid4(), trainer_id=uuid.uuid4(), business_name="Club",
        subject_type=ApprovalSubjectType.EVENT_RSVP, subject_id=uuid.uuid4(), amount_cents=5,
        token_amount=None, status=ApprovalStatus.APPROVED, requested_at=NOW,
        expires_at=NOW + timedelta(hours=48), responded_at=NOW, parent_note="n",
    )  # fmt: skip
    assert ApprovalView.from_json(view.to_json()) == view
