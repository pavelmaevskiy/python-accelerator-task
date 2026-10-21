"""Bulk anonymisation statements for GDPR erasure. Never commits; the service owns the tx."""

import uuid
from datetime import date, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import ApprovalStatus
from app.models.identity import (
    CoachMembership,
    CoachProfile,
    ImpersonationSession,
    OneTimeToken,
    PlayerProfile,
    Profile,
    PurchaseApproval,
    ShareLink,
    TrainerProfile,
    UserErasure,
)

DELETED_NAME = "Deleted User"
PLACEHOLDER_BIRTH_DATE = date(1900, 1, 2)  # dependents must keep a (non-identifying) birth date


class ErasureRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def owned_player_profiles(self, owner_user_id: uuid.UUID) -> list[PlayerProfile]:
        """Every profile the user owns, including deactivated ones."""
        stmt = select(PlayerProfile).where(PlayerProfile.owner_user_id == owner_user_id)
        return list((await self._session.execute(stmt)).scalars().all())

    async def anonymise_profile(self, user_id: uuid.UUID) -> str | None:
        """Blank the PII; returns the old avatar key so the object can be deleted."""
        profile = (
            await self._session.execute(select(Profile).where(Profile.user_id == user_id))
        ).scalar_one_or_none()
        if profile is None:
            return None
        old_avatar = profile.avatar_key
        profile.first_name = DELETED_NAME
        profile.last_name = ""
        profile.phone = None
        profile.school = None
        profile.avatar_key = None
        return old_avatar

    def anonymise_player_profile(self, profile: PlayerProfile) -> None:
        profile.display_name = DELETED_NAME
        profile.birth_date = PLACEHOLDER_BIRTH_DATE if profile.is_dependent else None
        profile.school = None
        profile.jersey_number = None
        profile.emergency_contact_name = None
        profile.emergency_contact_phone = None
        profile.is_active = False

    async def anonymise_trainer_profile(self, user_id: uuid.UUID) -> tuple[str, str | None] | None:
        """Returns ``(old_slug, old_logo_key)``; the slug is replaced by a unique placeholder."""
        tp = (
            await self._session.execute(
                select(TrainerProfile).where(TrainerProfile.user_id == user_id)
            )
        ).scalar_one_or_none()
        if tp is None:
            return None
        old = (tp.slug, tp.logo_key)
        tp.business_name = "Deleted Business"
        tp.slug = f"erased-{user_id.hex}"
        tp.address = None
        tp.website = None
        tp.description = None
        tp.logo_key = None
        tp.brand_primary_color = None
        tp.brand_secondary_color = None
        return old

    async def anonymise_coach(self, user_id: uuid.UUID, *, now: datetime) -> None:
        await self._session.execute(
            update(CoachProfile)
            .where(CoachProfile.user_id == user_id)
            .values(bio=None, credentials=None, certifications=[], is_public=False)
        )
        await self._session.execute(
            update(CoachMembership)
            .where(CoachMembership.coach_user_id == user_id, CoachMembership.is_active.is_(True))
            .values(is_active=False, left_at=now)
        )

    async def scrub_share_links(self, user_id: uuid.UUID, email: str) -> None:
        """Invitations addressed to the user, and (for trainers) every link the user issued."""
        await self._session.execute(
            update(ShareLink).where(ShareLink.target_email == email).values(target_email=None)
        )
        await self._session.execute(
            update(ShareLink)
            .where(ShareLink.trainer_id == user_id)
            .values(target_email=None, is_active=False)
        )

    async def scrub_token_payloads(self, user_id: uuid.UUID) -> None:
        """``email_change`` payloads hold a second email address."""
        await self._session.execute(
            update(OneTimeToken).where(OneTimeToken.user_id == user_id).values(payload=None)
        )

    async def scrub_free_text(self, user_id: uuid.UUID) -> None:
        await self._session.execute(
            update(PurchaseApproval)
            .where(PurchaseApproval.parent_user_id == user_id)
            .values(parent_note=None)
        )
        await self._session.execute(
            update(ImpersonationSession)
            .where(ImpersonationSession.target_user_id == user_id)
            .values(reason=None)
        )

    async def expire_pending_approvals(self, parent_user_id: uuid.UUID, *, now: datetime) -> None:
        await self._session.execute(
            update(PurchaseApproval)
            .where(
                PurchaseApproval.parent_user_id == parent_user_id,
                PurchaseApproval.status == ApprovalStatus.PENDING,
            )
            .values(status=ApprovalStatus.EXPIRED, responded_at=now)
        )

    async def add_ledger_row(self, row: UserErasure) -> None:
        self._session.add(row)
        await self._session.flush()
