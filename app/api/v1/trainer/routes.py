import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from app.api.deps import (
    AvailabilityServiceDep,
    BrandingServiceDep,
    PrincipalDep,
    ShareLinkServiceDep,
    default_rate_limit,
    require_role,
)
from app.api.responses import error_responses
from app.api.schemas import Page
from app.api.v1.me.schemas import AvailabilityResponse
from app.api.v1.trainer.schemas import (
    BrandingResponse,
    CreateShareLink,
    ShareLinkListParams,
    ShareLinkResponse,
    UpdateBranding,
)
from app.api.v1.uploads import LogoPresignRequest, PresignResponse
from app.core.enums import ShareLinkKind, UserRole

router = APIRouter(
    prefix="/trainer",
    tags=["trainer"],
    dependencies=[Depends(require_role(UserRole.TRAINER)), Depends(default_rate_limit)],
    responses=error_responses(401, 403, 429),
)


@router.get(
    "/share-links",
    response_model=Page[ShareLinkResponse],
    summary="List the trainer's share links",
    responses=error_responses(422),
)
async def list_share_links(
    params: Annotated[ShareLinkListParams, Query()],
    principal: PrincipalDep,
    service: ShareLinkServiceDep,
) -> Page[ShareLinkResponse]:
    page = await service.list(
        principal,
        kind=params.kind,
        is_active=params.is_active,
        cursor=params.cursor,
        limit=params.limit,
    )
    return Page[ShareLinkResponse](
        items=[ShareLinkResponse.model_validate(i) for i in page.items],
        next_cursor=page.next_cursor,
        has_more=page.has_more,
    )


@router.post(
    "/share-links",
    response_model=ShareLinkResponse,
    status_code=201,
    summary="Issue a player (static) or coach (unique) share link",
    responses=error_responses(422),
)
async def create_share_link(
    payload: CreateShareLink, principal: PrincipalDep, service: ShareLinkServiceDep
) -> ShareLinkResponse:
    view = await service.create(principal, ShareLinkKind(payload.kind), payload.target_email)
    return ShareLinkResponse.model_validate(view)


@router.delete(
    "/share-links/{link_id}",
    status_code=204,
    summary="Deactivate a share link",
    responses=error_responses(404),
)
async def delete_share_link(
    link_id: uuid.UUID, principal: PrincipalDep, service: ShareLinkServiceDep
) -> Response:
    await service.deactivate(principal, link_id)
    return Response(status_code=204)


@router.get(
    "/players/{player_id}/availability",
    response_model=AvailabilityResponse,
    summary="Read a linked player's availability",
    responses=error_responses(404),
)
async def get_player_availability(
    player_id: uuid.UUID, principal: PrincipalDep, service: AvailabilityServiceDep
) -> AvailabilityResponse:
    return AvailabilityResponse.model_validate(await service.get_for_trainer(principal, player_id))


@router.get(
    "/branding",
    response_model=BrandingResponse,
    summary="Get the trainer's portal branding",
)
async def get_branding(principal: PrincipalDep, service: BrandingServiceDep) -> BrandingResponse:
    return BrandingResponse.model_validate(await service.get(principal))


@router.patch(
    "/branding",
    response_model=BrandingResponse,
    summary="Update portal branding (and confirm a logo upload)",
    responses=error_responses(404, 422),
)
async def update_branding(
    payload: UpdateBranding, principal: PrincipalDep, service: BrandingServiceDep
) -> BrandingResponse:
    changes = payload.model_dump(exclude_unset=True, mode="json")  # URLs become plain strings
    return BrandingResponse.model_validate(await service.update(principal, changes))


@router.put(
    "/branding/logo",
    response_model=PresignResponse,
    summary="Obtain a presigned upload slot for the logo",
    responses=error_responses(422),
)
async def presign_logo(
    payload: LogoPresignRequest, principal: PrincipalDep, service: BrandingServiceDep
) -> PresignResponse:
    view = await service.presign_logo(
        principal, content_type=payload.content_type, content_length=payload.content_length
    )
    return PresignResponse.model_validate(view)
