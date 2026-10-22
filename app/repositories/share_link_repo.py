import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import ShareLinkKind, UserStatus
from app.models.identity import ShareLink, ShareLinkEvent, TrainerProfile, User


@dataclass(frozen=True, slots=True)
class LinkWithTrainer:
    link: ShareLink
    business_name: str
    slug: str
    logo_key: str | None
    trainer_status: UserStatus


class ShareLinkRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, link: ShareLink) -> ShareLink:
        self._session.add(link)
        await self._session.flush()
        return link

    async def get_for_trainer(self, trainer_id: uuid.UUID, link_id: uuid.UUID) -> ShareLink | None:
        """Tenant-scoped lookup. There is deliberately no bare ``get(id)``."""
        stmt = select(ShareLink).where(ShareLink.id == link_id, ShareLink.trainer_id == trainer_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_page(
        self,
        trainer_id: uuid.UUID,
        *,
        kind: ShareLinkKind | None,
        is_active: bool | None,
        cursor: tuple[datetime, uuid.UUID] | None,
        limit: int,
    ) -> list[ShareLink]:
        stmt = select(ShareLink).where(ShareLink.trainer_id == trainer_id)
        if kind is not None:
            stmt = stmt.where(ShareLink.kind == kind)
        if is_active is not None:
            stmt = stmt.where(ShareLink.is_active.is_(is_active))
        if cursor is not None:
            stmt = stmt.where(tuple_(ShareLink.created_at, ShareLink.id) < tuple_(*cursor))
        stmt = stmt.order_by(ShareLink.created_at.desc(), ShareLink.id.desc()).limit(limit + 1)
        return list((await self._session.execute(stmt)).scalars().all())

    async def get_by_code(self, code: str) -> LinkWithTrainer | None:
        stmt = (
            select(
                ShareLink,
                TrainerProfile.business_name,
                TrainerProfile.slug,
                TrainerProfile.logo_key,
                User.status,
            )
            .join(TrainerProfile, TrainerProfile.user_id == ShareLink.trainer_id)
            .join(User, User.id == ShareLink.trainer_id)
            .where(ShareLink.code == code)
        )
        row = (await self._session.execute(stmt)).one_or_none()
        if row is None:
            return None
        return LinkWithTrainer(row[0], row[1], row[2], row[3], row[4])

    async def redeem_unique(self, link_id: uuid.UUID, *, now: datetime) -> bool:
        """C8: atomic conditional increment; zero rows means unavailable."""
        result = await self._session.execute(
            update(ShareLink)
            .where(
                ShareLink.id == link_id,
                ShareLink.is_active.is_(True),
                ShareLink.expires_at > now,
                ShareLink.use_count < ShareLink.max_uses,
            )
            .values(use_count=ShareLink.use_count + 1)
            .returning(ShareLink.id)
        )
        return result.first() is not None

    async def redeem_static(self, link_id: uuid.UUID) -> bool:
        """C8: lock-free increment for unbounded static links (still requires it to be active)."""
        result = await self._session.execute(
            update(ShareLink)
            .where(ShareLink.id == link_id, ShareLink.is_active.is_(True))
            .values(use_count=ShareLink.use_count + 1)
            .returning(ShareLink.id)
        )
        return result.first() is not None

    async def add_event(self, event: ShareLinkEvent) -> None:
        self._session.add(event)
        await self._session.flush()
