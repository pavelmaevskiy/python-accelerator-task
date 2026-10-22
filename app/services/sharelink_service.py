"""Trainer share links (issue / list / deactivate) and the public preview."""

import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.core.clock import Clock
from app.core.config import Settings
from app.core.enums import ShareLinkEventKind, ShareLinkKind, UserStatus
from app.core.errors import DomainValidationError, NotFoundError
from app.core.pagination import PageResult, build_page, decode_cursor
from app.core.principal import Principal
from app.core.storage import ObjectStorage
from app.core.unit_of_work import UnitOfWork
from app.models.identity import ShareLink, ShareLinkEvent
from app.repositories.share_link_repo import ShareLinkRepo

COACH_LINK_TTL = timedelta(days=7)


@dataclass(frozen=True, slots=True)
class ShareLinkView:
    id: uuid.UUID
    code: str
    url: str
    kind: ShareLinkKind
    target_email: str | None
    expires_at: datetime | None
    max_uses: int | None
    use_count: int
    is_active: bool
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ShareLinkPreviewView:
    trainer_business_name: str
    trainer_slug: str
    logo_url: str | None
    kind: ShareLinkKind


def is_redeemable(link: ShareLink, now: datetime) -> bool:
    """Single source of truth for link validity (preview and registration)."""
    if not link.is_active:
        return False
    if link.expires_at is not None and link.expires_at <= now:
        return False
    return link.max_uses is None or link.use_count < link.max_uses


class ShareLinkService:
    def __init__(
        self,
        uow: UnitOfWork,
        links: ShareLinkRepo,
        clock: Clock,
        settings: Settings,
        storage: ObjectStorage,
    ) -> None:
        self._uow = uow
        self._links = links
        self._storage = storage
        self._clock = clock
        self._base = settings.public_base_url.rstrip("/")

    def _view(self, link: ShareLink) -> ShareLinkView:
        return ShareLinkView(
            id=link.id,
            code=link.code,
            url=f"{self._base}/join/{link.code}",
            kind=link.kind,
            target_email=link.target_email,
            expires_at=link.expires_at,
            max_uses=link.max_uses,
            use_count=link.use_count,
            is_active=link.is_active,
            created_at=link.created_at,
        )

    @staticmethod
    def _trainer_id(principal: Principal) -> uuid.UUID:
        if principal.trainer_id is None:
            raise NotFoundError("NOT_FOUND", "Trainer not found")
        return principal.trainer_id

    async def create(
        self, principal: Principal, kind: ShareLinkKind, target_email: str | None
    ) -> ShareLinkView:
        if kind == ShareLinkKind.COACH_UNIQUE and not target_email:
            raise DomainValidationError("TARGET_EMAIL_REQUIRED", "Coach links need a target email")
        if kind == ShareLinkKind.PLAYER_STATIC and target_email:
            raise DomainValidationError("TARGET_EMAIL_FORBIDDEN", "Player links have no target")
        trainer_id = self._trainer_id(principal)
        is_coach = kind == ShareLinkKind.COACH_UNIQUE
        async with self._uow:
            link = await self._links.add(
                ShareLink(
                    code=secrets.token_urlsafe(16),
                    trainer_id=trainer_id,
                    created_by_user_id=principal.user_id,
                    kind=kind,
                    target_email=target_email if is_coach else None,
                    expires_at=self._clock.now() + COACH_LINK_TTL if is_coach else None,
                    max_uses=1 if is_coach else None,
                )
            )
            if is_coach:
                self._uow.enqueue("send_coach_invite_email", share_link_id=str(link.id))
            return self._view(link)

    async def list(
        self,
        principal: Principal,
        *,
        kind: ShareLinkKind | None,
        is_active: bool | None,
        cursor: str | None,
        limit: int,
    ) -> PageResult[ShareLinkView]:
        trainer_id = self._trainer_id(principal)
        decoded = decode_cursor(cursor) if cursor else None
        async with self._uow:
            rows = await self._links.list_page(
                trainer_id, kind=kind, is_active=is_active, cursor=decoded, limit=limit
            )
            page = build_page(rows, limit, lambda r: (r.created_at, r.id))
            return PageResult([self._view(r) for r in page.items], page.next_cursor, page.has_more)

    async def deactivate(self, principal: Principal, link_id: uuid.UUID) -> None:
        trainer_id = self._trainer_id(principal)
        async with self._uow:
            link = await self._links.get_for_trainer(trainer_id, link_id)
            if link is None:
                raise NotFoundError("NOT_FOUND", "Share link not found")
            link.is_active = False

    async def preview(self, code: str, *, ip_hash: str, ua_hash: str) -> ShareLinkPreviewView:
        invalid = NotFoundError("SHARE_LINK_INVALID", "This link is not valid")
        async with self._uow:
            found = await self._links.get_by_code(code)
            if (
                found is None
                or found.trainer_status != UserStatus.ACTIVE
                or not is_redeemable(found.link, self._clock.now())
            ):
                raise invalid
            await self._links.add_event(
                ShareLinkEvent(
                    share_link_id=found.link.id,
                    kind=ShareLinkEventKind.OPENED,
                    occurred_at=self._clock.now(),
                    ip_hash=ip_hash,
                    user_agent_hash=ua_hash,
                )
            )
            return ShareLinkPreviewView(
                trainer_business_name=found.business_name,
                trainer_slug=found.slug,
                logo_url=self._storage.public_url(found.logo_key),
                kind=found.link.kind,
            )
