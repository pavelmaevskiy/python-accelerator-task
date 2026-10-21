"""Tenancy joins: coach memberships and trainer-player links."""

import uuid

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import LinkStatus
from app.models.identity import CoachMembership, PlayerProfile, TrainerPlayerLink


class TenancyRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_active_trainer_of_coach(self, coach_user_id: uuid.UUID) -> uuid.UUID | None:
        stmt = select(CoachMembership.trainer_id).where(
            CoachMembership.coach_user_id == coach_user_id, CoachMembership.is_active.is_(True)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def user_has_active_link_to(self, user_id: uuid.UUID, trainer_id: uuid.UUID) -> bool:
        """True when a player profile owned by, or logged into by, the user is actively linked."""
        stmt = select(
            exists().where(
                TrainerPlayerLink.trainer_id == trainer_id,
                TrainerPlayerLink.status == LinkStatus.ACTIVE,
                TrainerPlayerLink.player_profile_id == PlayerProfile.id,
                PlayerProfile.is_active.is_(True),
                (PlayerProfile.owner_user_id == user_id) | (PlayerProfile.login_user_id == user_id),
            )
        )
        return bool((await self._session.execute(stmt)).scalar_one())

    async def is_child_login(self, user_id: uuid.UUID) -> bool:
        stmt = select(
            exists().where(
                PlayerProfile.login_user_id == user_id, PlayerProfile.is_dependent.is_(True)
            )
        )
        return bool((await self._session.execute(stmt)).scalar_one())

    async def get_active_link(
        self, trainer_id: uuid.UUID, player_profile_id: uuid.UUID
    ) -> TrainerPlayerLink | None:
        stmt = select(TrainerPlayerLink).where(
            TrainerPlayerLink.trainer_id == trainer_id,
            TrainerPlayerLink.player_profile_id == player_profile_id,
            TrainerPlayerLink.status == LinkStatus.ACTIVE,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()
