import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from app.api.deps import PlayerServiceDep, PrincipalDep, forbid_child, reject_child, require_role
from app.api.responses import error_responses
from app.api.schemas import CursorParams, Page
from app.api.v1.me.player_schemas import (
    AddPlayerTrainer,
    ContextsResponse,
    CreatePlayer,
    PlayerLinkOut,
    PlayerResponse,
    TrainerContext,
    UpdatePlayer,
)
from app.core.enums import UserRole
from app.services.player_service import CreatePlayerInput

router = APIRouter()
_player_only = [Depends(require_role(UserRole.PLAYER))]
_parent_only = [Depends(require_role(UserRole.PLAYER)), Depends(reject_child)]


@router.get(
    "/contexts",
    response_model=ContextsResponse,
    dependencies=_player_only,
    summary="List the trainer contexts the player can switch between",
    responses=error_responses(403),
)
async def get_contexts(principal: PrincipalDep, service: PlayerServiceDep) -> ContextsResponse:
    items = await service.contexts(principal)
    return ContextsResponse(items=[TrainerContext.model_validate(i) for i in items])


@router.get(
    "/players",
    response_model=Page[PlayerResponse],
    dependencies=_parent_only,
    summary="List the caller's dependents",
    responses=error_responses(403, 422),
)
async def list_players(
    params: Annotated[CursorParams, Query()], principal: PrincipalDep, service: PlayerServiceDep
) -> Page[PlayerResponse]:
    page = await service.list(principal, cursor=params.cursor, limit=params.limit)
    return Page[PlayerResponse](
        items=[PlayerResponse.model_validate(p) for p in page.items],
        next_cursor=page.next_cursor,
        has_more=page.has_more,
    )


@router.post(
    "/players",
    response_model=PlayerResponse,
    status_code=201,
    dependencies=[
        Depends(require_role(UserRole.PLAYER)),
        Depends(
            forbid_child("CHILD_CANNOT_CREATE_DEPENDENT", "Child accounts cannot create dependents")
        ),
    ],
    summary="Create a dependent player profile",
    responses=error_responses(403, 404, 409, 422),
)
async def create_player(
    payload: CreatePlayer,
    principal: PrincipalDep,
    service: PlayerServiceDep,
    allow_duplicate: bool = False,
) -> PlayerResponse:
    view = await service.create(
        principal,
        CreatePlayerInput(
            display_name=payload.display_name,
            birth_date=payload.birth_date,
            gender=payload.gender,
            school=payload.school,
            jersey_number=payload.jersey_number,
            emergency_contact_name=payload.emergency_contact_name,
            emergency_contact_phone=payload.emergency_contact_phone,
            trainer_ids=payload.trainer_ids,
        ),
        allow_duplicate=allow_duplicate,
    )
    return PlayerResponse.model_validate(view)


@router.get(
    "/players/{player_id}",
    response_model=PlayerResponse,
    dependencies=_parent_only,
    summary="Get one of the caller's dependents",
    responses=error_responses(403, 404),
)
async def get_player(
    player_id: uuid.UUID, principal: PrincipalDep, service: PlayerServiceDep
) -> PlayerResponse:
    return PlayerResponse.model_validate(await service.get(principal, player_id))


@router.patch(
    "/players/{player_id}",
    response_model=PlayerResponse,
    dependencies=_parent_only,
    summary="Update a dependent",
    responses=error_responses(403, 404, 409, 422),
)
async def update_player(
    player_id: uuid.UUID,
    payload: UpdatePlayer,
    principal: PrincipalDep,
    service: PlayerServiceDep,
) -> PlayerResponse:
    view = await service.update(principal, player_id, payload.model_dump(exclude_unset=True))
    return PlayerResponse.model_validate(view)


@router.delete(
    "/players/{player_id}",
    status_code=204,
    dependencies=_parent_only,
    summary="Deactivate a dependent (soft delete)",
    responses=error_responses(403, 404, 409),
)
async def delete_player(
    player_id: uuid.UUID, principal: PrincipalDep, service: PlayerServiceDep
) -> Response:
    await service.deactivate(principal, player_id)
    return Response(status_code=204)


@router.post(
    "/players/{player_id}/trainers",
    response_model=PlayerLinkOut,
    status_code=201,
    dependencies=_parent_only,
    summary="Link a dependent to one of the parent's trainers",
    responses={
        200: {"model": PlayerLinkOut, "description": "Link already active"},
        **error_responses(403, 404, 422),
    },
)
async def add_player_trainer(
    player_id: uuid.UUID,
    payload: AddPlayerTrainer,
    response: Response,
    principal: PrincipalDep,
    service: PlayerServiceDep,
) -> PlayerLinkOut:
    link, created = await service.add_trainer(principal, player_id, payload.trainer_id)
    if not created:
        response.status_code = 200
    return PlayerLinkOut.model_validate(link)


@router.delete(
    "/players/{player_id}/trainers/{trainer_id}",
    status_code=204,
    dependencies=_parent_only,
    summary="Unlink a dependent from a trainer (sets the link inactive)",
    responses=error_responses(403, 404, 409),
)
async def remove_player_trainer(
    player_id: uuid.UUID,
    trainer_id: uuid.UUID,
    principal: PrincipalDep,
    service: PlayerServiceDep,
) -> Response:
    await service.remove_trainer(principal, player_id, trainer_id)
    return Response(status_code=204)
