"""Profiles (generic, trainer, coach) and player profiles. Never commits."""

import uuid
from collections.abc import Sequence
from datetime import date, datetime

from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.identity import CoachProfile, PlayerProfile, Profile, TrainerProfile


class ProfileRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_profile(self, profile: Profile) -> Profile:
        self._session.add(profile)
        await self._session.flush()
        return profile

    async def add_trainer_profile(self, profile: TrainerProfile) -> TrainerProfile:
        self._session.add(profile)
        await self._session.flush()
        return profile

    async def add_coach_profile(self, profile: CoachProfile) -> CoachProfile:
        self._session.add(profile)
        await self._session.flush()
        return profile

    async def add_player_profile(self, profile: PlayerProfile) -> PlayerProfile:
        self._session.add(profile)
        await self._session.flush()
        return profile

    async def get_own_player_profile(self, owner_user_id: uuid.UUID) -> PlayerProfile | None:
        """The adult's own (non-dependent) player profile; at most one per owner."""
        stmt = select(PlayerProfile).where(
            PlayerProfile.owner_user_id == owner_user_id, PlayerProfile.is_dependent.is_(False)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_owned_profiles(
        self, owner_user_id: uuid.UUID, ids: Sequence[uuid.UUID]
    ) -> list[PlayerProfile]:
        stmt = select(PlayerProfile).where(
            PlayerProfile.owner_user_id == owner_user_id,
            PlayerProfile.id.in_(list(ids)),
            PlayerProfile.is_active.is_(True),
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def get_trainer_profile(self, user_id: uuid.UUID) -> TrainerProfile | None:
        stmt = select(TrainerProfile).where(TrainerProfile.user_id == user_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_trainer_profile_by_slug(self, slug: str) -> TrainerProfile | None:
        stmt = select(TrainerProfile).where(TrainerProfile.slug == slug)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_coach_profile(self, user_id: uuid.UUID) -> CoachProfile | None:
        stmt = select(CoachProfile).where(CoachProfile.user_id == user_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_player_profile_for_login(self, login_user_id: uuid.UUID) -> PlayerProfile | None:
        """The dependent profile a child login belongs to."""
        stmt = select(PlayerProfile).where(
            PlayerProfile.login_user_id == login_user_id, PlayerProfile.is_active.is_(True)
        )
        return (await self._session.execute(stmt)).scalars().first()

    async def get_owned_dependent(
        self, owner_user_id: uuid.UUID, player_id: uuid.UUID
    ) -> PlayerProfile | None:
        stmt = select(PlayerProfile).where(
            PlayerProfile.id == player_id,
            PlayerProfile.owner_user_id == owner_user_id,
            PlayerProfile.is_dependent.is_(True),
            PlayerProfile.is_active.is_(True),
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_dependents_page(
        self,
        owner_user_id: uuid.UUID,
        cursor: tuple[datetime, uuid.UUID] | None,
        limit: int,
    ) -> list[PlayerProfile]:
        stmt = select(PlayerProfile).where(
            PlayerProfile.owner_user_id == owner_user_id,
            PlayerProfile.is_dependent.is_(True),
            PlayerProfile.is_active.is_(True),
        )
        if cursor is not None:
            stmt = stmt.where(tuple_(PlayerProfile.created_at, PlayerProfile.id) < tuple_(*cursor))
        stmt = stmt.order_by(PlayerProfile.created_at.desc(), PlayerProfile.id.desc()).limit(
            limit + 1
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def find_duplicate_dependent(
        self,
        owner_user_id: uuid.UUID,
        display_name: str,
        birth_date: date,
        *,
        exclude_id: uuid.UUID | None = None,
    ) -> bool:
        stmt = select(PlayerProfile.id).where(
            PlayerProfile.owner_user_id == owner_user_id,
            PlayerProfile.is_dependent.is_(True),
            PlayerProfile.is_active.is_(True),
            func.lower(PlayerProfile.display_name) == display_name.lower(),
            PlayerProfile.birth_date == birth_date,
        )
        if exclude_id is not None:
            stmt = stmt.where(PlayerProfile.id != exclude_id)
        return (await self._session.execute(stmt.limit(1))).first() is not None
