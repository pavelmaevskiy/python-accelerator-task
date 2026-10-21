import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from app.api.deps import PrincipalDep, ShareLinkServiceDep, default_rate_limit, require_role
from app.api.responses import error_responses
from app.api.schemas import Page
from app.api.v1.trainer.schemas import CreateShareLink, ShareLinkListParams, ShareLinkResponse
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
