import uuid

from fastapi import APIRouter, Depends, Response

from app.api.deps import (
    AvailabilityServiceDep,
    PrincipalDep,
    UserServiceDep,
    current_principal,
    default_rate_limit,
    reject_child,
    require_role,
)
from app.api.responses import error_responses
from app.api.v1.me.approval_routes import router as approval_router
from app.api.v1.me.player_routes import router as player_router
from app.api.v1.me.schemas import (
    AvailabilityResponse,
    EmailChangeRequest,
    ProfileResponse,
    ReplaceAvailability,
    UpdateProfile,
)
from app.api.v1.uploads import PresignRequest, PresignResponse
from app.core.enums import UserRole
from app.services.availability_service import WindowInput

router = APIRouter(
    prefix="/me",
    tags=["me"],
    dependencies=[Depends(current_principal), Depends(default_rate_limit)],
    responses=error_responses(401, 429),
)

router.include_router(player_router)
router.include_router(approval_router)


@router.get("", response_model=ProfileResponse, summary="Get the current user's profile")
async def get_me(principal: PrincipalDep, service: UserServiceDep) -> ProfileResponse:
    return ProfileResponse.model_validate(await service.get_me(principal))


@router.patch(
    "",
    response_model=ProfileResponse,
    summary="Update the current user's profile (and confirm an avatar upload)",
    responses=error_responses(404, 422),
)
async def update_me(
    payload: UpdateProfile, principal: PrincipalDep, service: UserServiceDep
) -> ProfileResponse:
    view = await service.update_me(principal, payload.model_dump(exclude_unset=True))
    return ProfileResponse.model_validate(view)


@router.post(
    "/email-change",
    status_code=202,
    summary="Request an email change (confirmed through POST /auth/verify-email)",
    responses=error_responses(401, 403, 409, 422),
)
async def request_email_change(
    payload: EmailChangeRequest, principal: PrincipalDep, service: UserServiceDep
) -> Response:
    await service.request_email_change(principal, payload.new_email, payload.current_password)
    return Response(status_code=202)


@router.put(
    "/avatar",
    response_model=PresignResponse,
    summary="Obtain a presigned upload slot for the avatar",
    responses=error_responses(422),
)
async def presign_avatar(
    payload: PresignRequest, principal: PrincipalDep, service: UserServiceDep
) -> PresignResponse:
    view = await service.presign_avatar(
        principal, content_type=payload.content_type, content_length=payload.content_length
    )
    return PresignResponse.model_validate(view)


def _windows(payload: ReplaceAvailability) -> list[WindowInput]:
    return [
        WindowInput(w.weekday, w.start_time, w.end_time, w.is_available) for w in payload.windows
    ]


@router.get(
    "/availability",
    response_model=AvailabilityResponse,
    dependencies=[Depends(require_role(UserRole.PLAYER, UserRole.COACH))],
    summary="Get my weekly availability",
    responses=error_responses(403),
)
async def get_availability(
    principal: PrincipalDep, service: AvailabilityServiceDep
) -> AvailabilityResponse:
    return AvailabilityResponse.model_validate(await service.get_own(principal))


@router.put(
    "/availability",
    response_model=AvailabilityResponse,
    dependencies=[Depends(require_role(UserRole.PLAYER, UserRole.COACH))],
    summary="Replace my whole weekly availability",
    responses=error_responses(403, 422),
)
async def replace_availability(
    payload: ReplaceAvailability, principal: PrincipalDep, service: AvailabilityServiceDep
) -> AvailabilityResponse:
    view = await service.replace_own(principal, _windows(payload))
    return AvailabilityResponse.model_validate(view)


@router.put(
    "/players/{player_id}/availability",
    response_model=AvailabilityResponse,
    dependencies=[Depends(require_role(UserRole.PLAYER)), Depends(reject_child)],
    summary="Replace a dependent's whole weekly availability",
    responses=error_responses(403, 404, 422),
)
async def replace_dependent_availability(
    player_id: uuid.UUID,
    payload: ReplaceAvailability,
    principal: PrincipalDep,
    service: AvailabilityServiceDep,
) -> AvailabilityResponse:
    view = await service.replace_for_dependent(principal, player_id, _windows(payload))
    return AvailabilityResponse.model_validate(view)
