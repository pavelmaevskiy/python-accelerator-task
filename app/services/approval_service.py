"""Purchase approvals: lifecycle, 48 h expiry and idempotent approve/deny (handler seam)."""

import uuid
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.approvals import ApprovalHandlerRegistry, ApprovalView
from app.core.clock import Clock
from app.core.enums import ApprovalStatus, ApprovalSubjectType
from app.core.errors import ConflictError, DomainValidationError, NotFoundError
from app.core.idempotency import request_fingerprint
from app.core.pagination import PageResult, build_page, decode_cursor
from app.core.principal import Principal
from app.core.unit_of_work import JobQueue, UnitOfWork
from app.models.identity import PurchaseApproval
from app.repositories.approval_repo import ApprovalRepo, ApprovalRow
from app.repositories.idempotency_repo import IdempotencyRepo

APPROVAL_TTL = timedelta(hours=48)


class ApprovalService:
    def __init__(
        self,
        uow: UnitOfWork,
        approvals: ApprovalRepo,
        idempotency: IdempotencyRepo,
        registry: ApprovalHandlerRegistry,
        clock: Clock,
    ) -> None:
        self._uow = uow
        self._approvals = approvals
        self._idempotency = idempotency
        self._registry = registry
        self._clock = clock

    @property
    def _session(self) -> AsyncSession:
        return self._uow.session

    # --- creation (called by Epic-02/05 when a child initiates a purchase) -----------------

    async def request(
        self,
        *,
        parent_user_id: uuid.UUID,
        player_profile_id: uuid.UUID,
        trainer_id: uuid.UUID,
        subject_type: ApprovalSubjectType,
        subject_id: uuid.UUID,
        amount_cents: int | None = None,
        token_amount: int | None = None,
    ) -> uuid.UUID:
        """Create a pending approval expiring in 48 h and notify the parent after commit."""
        now = self._clock.now()
        async with self._uow:
            approval = await self._approvals.add(
                PurchaseApproval(
                    player_profile_id=player_profile_id,
                    parent_user_id=parent_user_id,
                    trainer_id=trainer_id,
                    subject_type=subject_type,
                    subject_id=subject_id,
                    amount_cents=amount_cents,
                    token_amount=token_amount,
                    requested_at=now,
                    expires_at=now + APPROVAL_TTL,
                )
            )
            self._uow.enqueue("send_approval_notification", approval_id=str(approval.id))
            return approval.id

    # --- reads ----------------------------------------------------------------------------

    async def list(
        self,
        principal: Principal,
        *,
        status: ApprovalStatus,
        cursor: str | None,
        limit: int,
    ) -> PageResult[ApprovalView]:
        decoded = decode_cursor(cursor) if cursor else None
        async with self._uow:
            rows = await self._approvals.list_page(principal.user_id, status, decoded, limit)
            page = build_page(rows, limit, lambda r: (r.approval.requested_at, r.approval.id))
            return PageResult([r.view() for r in page.items], page.next_cursor, page.has_more)

    # --- decisions ------------------------------------------------------------------------

    async def approve(
        self, principal: Principal, approval_id: uuid.UUID, key: str, parent_note: str | None
    ) -> ApprovalView:
        return await self._decide(principal, approval_id, key, parent_note, approve=True)

    async def deny(
        self, principal: Principal, approval_id: uuid.UUID, key: str, parent_note: str | None
    ) -> ApprovalView:
        return await self._decide(principal, approval_id, key, parent_note, approve=False)

    async def _decide(
        self,
        principal: Principal,
        approval_id: uuid.UUID,
        key: str,
        parent_note: str | None,
        *,
        approve: bool,
    ) -> ApprovalView:
        action = "approve" if approve else "deny"
        endpoint = f"{action}:{approval_id}"
        fingerprint = request_fingerprint(action, str(approval_id), parent_note)
        now = self._clock.now()
        async with self._uow:
            if not await self._idempotency.claim(principal.user_id, key, endpoint, fingerprint):
                return await self._replay(principal.user_id, key, fingerprint)
            row = await self._approvals.get_for_parent(
                approval_id, principal.user_id, for_update=True
            )
            if row is None:
                raise NotFoundError("NOT_FOUND", "Approval not found")
            approval = row.approval
            if approval.status != ApprovalStatus.PENDING or approval.expires_at <= now:
                raise ConflictError("APPROVAL_NOT_PENDING", "Approval is no longer pending")
            handler = self._registry.get(approval.subject_type)
            if approve:
                if handler is None:
                    raise ConflictError(
                        "APPROVAL_SUBJECT_UNAVAILABLE",
                        "No handler is registered for this approval subject",
                    )
                await handler.on_approved(self._session, row.view())
            elif handler is not None:
                await handler.on_denied_or_expired(self._session, row.view())
            approval.status = ApprovalStatus.APPROVED if approve else ApprovalStatus.DENIED
            approval.responded_at = now
            approval.parent_note = parent_note
            view = row.view()
            await self._idempotency.store_response(principal.user_id, key, 200, view.to_json())
            return view

    async def _replay(self, principal_id: uuid.UUID, key: str, fingerprint: str) -> ApprovalView:
        stored = await self._idempotency.get(principal_id, key)
        if stored is None or stored.response_body is None:
            raise ConflictError("IDEMPOTENCY_IN_PROGRESS", "A request with this key is in progress")
        if stored.request_hash != fingerprint:
            raise DomainValidationError(
                "IDEMPOTENCY_KEY_REUSED", "This Idempotency-Key was used for a different request"
            )
        return ApprovalView.from_json(stored.response_body)

    # --- expiry cron ----------------------------------------------------------------------

    async def expire_due(self) -> int:
        """Expire pending approvals past 48 h; idempotent (a second run finds nothing)."""
        now = self._clock.now()
        async with self._uow:
            ids = await self._approvals.expire_due(now=now)
            rows: list[ApprovalRow] = await self._approvals.get_rows(ids)
            for row in rows:
                handler = self._registry.get(row.approval.subject_type)
                if handler is not None:
                    await handler.on_denied_or_expired(self._session, row.view())
            return len(ids)


def make_approval_service(
    session: AsyncSession, queue: JobQueue, registry: ApprovalHandlerRegistry, clock: Clock
) -> ApprovalService:
    """Factory for workers: jobs may use services but never repositories directly."""
    return ApprovalService(
        UnitOfWork(session, queue), ApprovalRepo(session), IdempotencyRepo(session), registry, clock
    )
