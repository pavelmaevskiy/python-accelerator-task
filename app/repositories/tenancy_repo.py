"""Tenancy joins: coach memberships and trainer-player links."""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import ColumnElement, Select, exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import LinkStatus
from app.models.identity import (
    CoachMembership,
    PlayerProfile,
    TrainerPlayerLink,
    TrainerProfile,
)


@dataclass(frozen=True, slots=True)
class LinkRow:
    link: TrainerPlayerLink
    business_name: str
    slug: str
    logo_key: str | None
    brand_primary_color: str | None


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

    async def get_link(
        self, trainer_id: uuid.UUID, player_profile_id: uuid.UUID
    ) -> TrainerPlayerLink | None:
        """Link in any status (the pair is UNIQUE)."""
        stmt = select(TrainerPlayerLink).where(
            TrainerPlayerLink.trainer_id == trainer_id,
            TrainerPlayerLink.player_profile_id == player_profile_id,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def add_link(self, link: TrainerPlayerLink) -> TrainerPlayerLink:
        self._session.add(link)
        await self._session.flush()
        return link

    async def get_active_membership(self, coach_user_id: uuid.UUID) -> CoachMembership | None:
        stmt = select(CoachMembership).where(
            CoachMembership.coach_user_id == coach_user_id, CoachMembership.is_active.is_(True)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def add_membership(self, membership: CoachMembership) -> CoachMembership:
        self._session.add(membership)
        await self._session.flush()
        return membership

    @staticmethod
    def _link_rows(
        *where: ColumnElement[bool],
    ) -> Select[TrainerPlayerLink, str, str, str | None, str | None]:
        return (
            select(
                TrainerPlayerLink,
                TrainerProfile.business_name,
                TrainerProfile.slug,
                TrainerProfile.logo_key,
                TrainerProfile.brand_primary_color,
            )
            .join(TrainerProfile, TrainerProfile.user_id == TrainerPlayerLink.trainer_id)
            .where(*where)
        )

    async def list_active_contexts(self, user_id: uuid.UUID) -> list[LinkRow]:
        """Active links of every active profile the user owns or logs into (uses ix_tpl_player)."""
        profile_ids = select(PlayerProfile.id).where(
            PlayerProfile.is_active.is_(True),
            (PlayerProfile.owner_user_id == user_id) | (PlayerProfile.login_user_id == user_id),
        )
        stmt = self._link_rows(
            TrainerPlayerLink.status == LinkStatus.ACTIVE,
            TrainerPlayerLink.player_profile_id.in_(profile_ids),
        )
        rows = (await self._session.execute(stmt.order_by(TrainerPlayerLink.linked_at))).all()
        return [LinkRow(r[0], r[1], r[2], r[3], r[4]) for r in rows]

    async def list_links_for_players(self, player_ids: Sequence[uuid.UUID]) -> list[LinkRow]:
        if not player_ids:
            return []
        stmt = self._link_rows(TrainerPlayerLink.player_profile_id.in_(list(player_ids)))
        rows = (await self._session.execute(stmt.order_by(TrainerPlayerLink.linked_at))).all()
        return [LinkRow(r[0], r[1], r[2], r[3], r[4]) for r in rows]

    async def get_link_row(
        self, trainer_id: uuid.UUID, player_profile_id: uuid.UUID
    ) -> LinkRow | None:
        stmt = self._link_rows(
            TrainerPlayerLink.trainer_id == trainer_id,
            TrainerPlayerLink.player_profile_id == player_profile_id,
        )
        row = (await self._session.execute(stmt)).one_or_none()
        return None if row is None else LinkRow(row[0], row[1], row[2], row[3], row[4])

    async def get_user_link_to(
        self, user_id: uuid.UUID, trainer_id: uuid.UUID
    ) -> TrainerPlayerLink | None:
        """An active link between the user's own/login profile and the trainer, if any."""
        stmt = (
            select(TrainerPlayerLink)
            .join(PlayerProfile, PlayerProfile.id == TrainerPlayerLink.player_profile_id)
            .where(
                TrainerPlayerLink.trainer_id == trainer_id,
                TrainerPlayerLink.status == LinkStatus.ACTIVE,
                PlayerProfile.is_active.is_(True),
                (PlayerProfile.owner_user_id == user_id) | (PlayerProfile.login_user_id == user_id),
            )
            .order_by(TrainerPlayerLink.linked_at)
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def deactivate_links_for_player(self, player_profile_id: uuid.UUID) -> None:
        await self._session.execute(
            update(TrainerPlayerLink)
            .where(TrainerPlayerLink.player_profile_id == player_profile_id)
            .values(status=LinkStatus.INACTIVE)
        )
