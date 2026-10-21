"""Read-only lookups used by email jobs."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import UserStatus
from app.models.identity import (
    PlayerProfile,
    Profile,
    PurchaseApproval,
    ShareLink,
    TrainerProfile,
    User,
)


class NotificationRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_recipient(self, user_id: uuid.UUID) -> tuple[str, str] | None:
        row = (
            await self._session.execute(
                select(User.email, Profile.first_name)
                .join(Profile, Profile.user_id == User.id)
                .where(User.id == user_id, User.status != UserStatus.DELETED)
            )
        ).one_or_none()
        return None if row is None else (str(row[0]), row[1])

    async def get_coach_invite(self, share_link_id: uuid.UUID) -> tuple[str, str, str] | None:
        row = (
            await self._session.execute(
                select(ShareLink.target_email, ShareLink.code, TrainerProfile.business_name)
                .join(TrainerProfile, TrainerProfile.user_id == ShareLink.trainer_id)
                .where(ShareLink.id == share_link_id, ShareLink.target_email.is_not(None))
            )
        ).one_or_none()
        return None if row is None else (str(row[0]), row[1], row[2])

    async def get_approval_recipient(self, approval_id: uuid.UUID) -> tuple[str, str, str] | None:
        row = (
            await self._session.execute(
                select(User.email, Profile.first_name, PlayerProfile.display_name)
                .select_from(PurchaseApproval)
                .join(User, User.id == PurchaseApproval.parent_user_id)
                .join(Profile, Profile.user_id == User.id)
                .join(PlayerProfile, PlayerProfile.id == PurchaseApproval.player_profile_id)
                .where(PurchaseApproval.id == approval_id, User.status != UserStatus.DELETED)
            )
        ).one_or_none()
        return None if row is None else (str(row[0]), row[1], row[2])
