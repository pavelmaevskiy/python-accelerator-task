from fastapi import APIRouter, Depends, Response

from app.api.deps import PrincipalDep, UserServiceDep, current_principal, default_rate_limit
from app.api.responses import error_responses
from app.api.v1.me.schemas import EmailChangeRequest, ProfileResponse, UpdateProfile
from app.api.v1.uploads import PresignRequest, PresignResponse

router = APIRouter(
    prefix="/me",
    tags=["me"],
    dependencies=[Depends(current_principal), Depends(default_rate_limit)],
    responses=error_responses(401, 429),
)


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
