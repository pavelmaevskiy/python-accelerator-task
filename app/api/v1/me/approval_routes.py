import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query

from app.api.deps import ApprovalServiceDep, PrincipalDep, reject_child, require_role
from app.api.responses import error_responses
from app.api.schemas import Page
from app.api.v1.me.approval_schemas import ApprovalDecision, ApprovalListParams, ApprovalResponse
from app.core.enums import UserRole

router = APIRouter(
    prefix="/approvals",
    dependencies=[Depends(require_role(UserRole.PLAYER)), Depends(reject_child)],
)

IdempotencyKeyHeader = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
        description="Required. Replaying the same key returns the stored response.",
    ),
]


@router.get(
    "",
    response_model=Page[ApprovalResponse],
    summary="List the parent's purchase approvals (default: pending)",
    responses=error_responses(403, 422),
)
async def list_approvals(
    params: Annotated[ApprovalListParams, Query()],
    principal: PrincipalDep,
    service: ApprovalServiceDep,
) -> Page[ApprovalResponse]:
    page = await service.list(
        principal, status=params.status, cursor=params.cursor, limit=params.limit
    )
    return Page[ApprovalResponse](
        items=[ApprovalResponse.model_validate(i) for i in page.items],
        next_cursor=page.next_cursor,
        has_more=page.has_more,
    )


@router.post(
    "/{approval_id}/approve",
    response_model=ApprovalResponse,
    summary="Approve a purchase (idempotent)",
    responses=error_responses(403, 404, 409, 422),
)
async def approve(
    approval_id: uuid.UUID,
    idempotency_key: IdempotencyKeyHeader,
    principal: PrincipalDep,
    service: ApprovalServiceDep,
    payload: ApprovalDecision | None = None,
) -> ApprovalResponse:
    note = payload.parent_note if payload else None
    view = await service.approve(principal, approval_id, idempotency_key, note)
    return ApprovalResponse.model_validate(view)


@router.post(
    "/{approval_id}/deny",
    response_model=ApprovalResponse,
    summary="Deny a purchase (idempotent)",
    responses=error_responses(403, 404, 409, 422),
)
async def deny(
    approval_id: uuid.UUID,
    idempotency_key: IdempotencyKeyHeader,
    principal: PrincipalDep,
    service: ApprovalServiceDep,
    payload: ApprovalDecision | None = None,
) -> ApprovalResponse:
    note = payload.parent_note if payload else None
    view = await service.deny(principal, approval_id, idempotency_key, note)
    return ApprovalResponse.model_validate(view)
