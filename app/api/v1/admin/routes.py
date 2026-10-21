import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import (
    AdminUserServiceDep,
    PrincipalDep,
    default_rate_limit,
    reject_impersonation,
    require_role,
)
from app.api.responses import error_responses
from app.api.schemas import Page
from app.api.v1.admin.schemas import (
    AdminUpdateUser,
    AdminUserFilter,
    AdminUserResponse,
    ProvisionTrainer,
    ProvisionTrainerResponse,
)
from app.core.enums import UserRole
from app.repositories.user_repo import UserFilter
from app.services.admin_user_service import ProvisionTrainerInput

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[
        Depends(reject_impersonation),
        Depends(require_role(UserRole.SUPER_ADMIN)),
        Depends(default_rate_limit),
    ],
    responses=error_responses(401, 403, 429),
)


@router.get(
    "/users",
    response_model=Page[AdminUserResponse],
    summary="List users (cursor-paged, filterable)",
    responses=error_responses(422),
)
async def list_users(
    params: Annotated[AdminUserFilter, Query()], service: AdminUserServiceDep
) -> Page[AdminUserResponse]:
    page = await service.list(
        UserFilter(
            role=params.role, status=params.status, trainer_id=params.trainer, query=params.query
        ),
        cursor=params.cursor,
        limit=params.limit,
    )
    return Page[AdminUserResponse](
        items=[AdminUserResponse.model_validate(i) for i in page.items],
        next_cursor=page.next_cursor,
        has_more=page.has_more,
    )


@router.post(
    "/users/trainers",
    response_model=ProvisionTrainerResponse,
    status_code=201,
    summary="Provision a trainer account (pending until the setup link is used)",
    responses=error_responses(409, 422),
)
async def provision_trainer(
    payload: ProvisionTrainer, principal: PrincipalDep, service: AdminUserServiceDep
) -> ProvisionTrainerResponse:
    view = await service.provision_trainer(
        principal,
        ProvisionTrainerInput(
            email=payload.email,
            first_name=payload.first_name,
            last_name=payload.last_name,
            phone=payload.phone,
            business_name=payload.business_name,
            slug=payload.slug,
        ),
    )
    return ProvisionTrainerResponse.model_validate(view)


@router.get(
    "/users/{user_id}",
    response_model=AdminUserResponse,
    summary="Get a user",
    responses=error_responses(404),
)
async def get_user(user_id: uuid.UUID, service: AdminUserServiceDep) -> AdminUserResponse:
    return AdminUserResponse.model_validate(await service.get(user_id))


@router.patch(
    "/users/{user_id}",
    response_model=AdminUserResponse,
    summary="Edit a user's profile fields and flags",
    responses=error_responses(404, 409, 422),
)
async def update_user(
    user_id: uuid.UUID,
    payload: AdminUpdateUser,
    principal: PrincipalDep,
    service: AdminUserServiceDep,
) -> AdminUserResponse:
    view = await service.update(principal, user_id, payload.model_dump(exclude_unset=True))
    return AdminUserResponse.model_validate(view)


@router.post(
    "/users/{user_id}/deactivate",
    response_model=AdminUserResponse,
    summary="Deactivate a user (idempotent)",
    responses=error_responses(404, 409),
)
async def deactivate_user(
    user_id: uuid.UUID, principal: PrincipalDep, service: AdminUserServiceDep
) -> AdminUserResponse:
    return AdminUserResponse.model_validate(await service.deactivate(principal, user_id))


@router.post(
    "/users/{user_id}/reactivate",
    response_model=AdminUserResponse,
    summary="Reactivate a deactivated user",
    responses=error_responses(404, 409),
)
async def reactivate_user(
    user_id: uuid.UUID, principal: PrincipalDep, service: AdminUserServiceDep
) -> AdminUserResponse:
    return AdminUserResponse.model_validate(await service.reactivate(principal, user_id))
