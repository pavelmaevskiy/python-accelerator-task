"""Trainer contexts and dependent (child) player profiles owned by a parent."""

import uuid
from dataclasses import dataclass
from datetime import date, datetime

from app.core.clock import Clock, age_on
from app.core.commitments import CommitmentChecker
from app.core.enums import Gender, LinkSource, LinkStatus, SkillLevel
from app.core.errors import (
    ConflictError,
    DomainValidationError,
    NotFoundError,
    PermissionDeniedError,
)
from app.core.pagination import PageResult, build_page, decode_cursor
from app.core.principal import Principal
from app.core.storage import ObjectStorage
from app.core.unit_of_work import UnitOfWork
from app.models.identity import PlayerProfile, TrainerPlayerLink
from app.repositories.profile_repo import ProfileRepo
from app.repositories.tenancy_repo import LinkRow, TenancyRepo

MIN_DEPENDENT_AGE = 1
MAX_DEPENDENT_AGE = 18  # inclusive: valid until the 19th birthday (spec 1-18, C2)

_UPDATABLE_NULLABLE = (
    "school",
    "jersey_number",
    "emergency_contact_name",
    "emergency_contact_phone",
)


@dataclass(frozen=True, slots=True)
class TrainerContextView:
    trainer_id: uuid.UUID
    business_name: str
    slug: str
    logo_url: str | None
    brand_primary_color: str | None
    player_profile_id: uuid.UUID
    linked_at: datetime


@dataclass(frozen=True, slots=True)
class PlayerLinkView:
    trainer_id: uuid.UUID
    business_name: str
    status: LinkStatus
    linked_at: datetime


@dataclass(frozen=True, slots=True)
class PlayerView:
    id: uuid.UUID
    display_name: str
    birth_date: date
    gender: Gender
    skill_level: SkillLevel | None
    school: str | None
    jersey_number: int | None
    is_dependent: bool
    has_login: bool
    emergency_contact_name: str | None
    emergency_contact_phone: str | None
    token_spend_requires_approval: bool
    trainers: list[PlayerLinkView]


@dataclass(frozen=True, slots=True)
class CreatePlayerInput:
    display_name: str
    birth_date: date
    gender: Gender
    school: str | None
    jersey_number: int | None
    emergency_contact_name: str | None
    emergency_contact_phone: str | None
    trainer_ids: list[uuid.UUID]


def _link_view(row: LinkRow) -> PlayerLinkView:
    return PlayerLinkView(
        trainer_id=row.link.trainer_id,
        business_name=row.business_name,
        status=row.link.status,
        linked_at=row.link.linked_at,
    )


class PlayerService:
    def __init__(
        self,
        uow: UnitOfWork,
        profiles: ProfileRepo,
        tenancy: TenancyRepo,
        clock: Clock,
        commitments: CommitmentChecker,
        storage: ObjectStorage,
    ) -> None:
        self._uow = uow
        self._profiles = profiles
        self._tenancy = tenancy
        self._clock = clock
        self._commitments = commitments
        self._storage = storage

    # --- helpers --------------------------------------------------------------------------

    @staticmethod
    def _reject_child(principal: Principal) -> None:
        if principal.is_child:
            raise PermissionDeniedError(
                "CHILD_CANNOT_CREATE_DEPENDENT", "Child accounts cannot manage dependents"
            )

    def _validate_age(self, birth_date: date) -> None:
        age = age_on(birth_date, self._clock.today())
        if not MIN_DEPENDENT_AGE <= age <= MAX_DEPENDENT_AGE:
            raise DomainValidationError(
                "INVALID_AGE",
                f"Dependents must be between {MIN_DEPENDENT_AGE} and {MAX_DEPENDENT_AGE} years old",
            )

    async def _owned(self, principal: Principal, player_id: uuid.UUID) -> PlayerProfile:
        profile = await self._profiles.get_owned_dependent(principal.user_id, player_id)
        if profile is None:
            raise NotFoundError("NOT_FOUND", "Player not found")
        return profile

    async def _views(self, profiles: list[PlayerProfile]) -> list[PlayerView]:
        rows = await self._tenancy.list_links_for_players([p.id for p in profiles])
        by_player: dict[uuid.UUID, list[PlayerLinkView]] = {p.id: [] for p in profiles}
        for row in rows:
            by_player[row.link.player_profile_id].append(_link_view(row))
        return [
            PlayerView(
                id=p.id,
                display_name=p.display_name,
                birth_date=p.birth_date,  # type: ignore[arg-type]  # dependents always have one
                gender=p.gender,
                skill_level=p.skill_level,
                school=p.school,
                jersey_number=p.jersey_number,
                is_dependent=p.is_dependent,
                has_login=p.login_user_id is not None,
                emergency_contact_name=p.emergency_contact_name,
                emergency_contact_phone=p.emergency_contact_phone,
                token_spend_requires_approval=p.token_spend_requires_approval,
                trainers=by_player[p.id],
            )
            for p in profiles
        ]

    # --- contexts -------------------------------------------------------------------------

    async def contexts(self, principal: Principal) -> list[TrainerContextView]:
        async with self._uow:
            rows = await self._tenancy.list_active_contexts(principal.user_id)
            return [
                TrainerContextView(
                    trainer_id=r.link.trainer_id,
                    business_name=r.business_name,
                    slug=r.slug,
                    logo_url=self._storage.public_url(r.logo_key),
                    brand_primary_color=r.brand_primary_color,
                    player_profile_id=r.link.player_profile_id,
                    linked_at=r.link.linked_at,
                )
                for r in rows
            ]

    # --- dependents -----------------------------------------------------------------------

    async def create(
        self, principal: Principal, data: CreatePlayerInput, *, allow_duplicate: bool
    ) -> PlayerView:
        self._reject_child(principal)
        self._validate_age(data.birth_date)
        async with self._uow:
            parent_links: dict[uuid.UUID, TrainerPlayerLink] = {}
            for trainer_id in dict.fromkeys(data.trainer_ids):
                link = await self._tenancy.get_user_link_to(principal.user_id, trainer_id)
                if link is None:
                    raise NotFoundError("NOT_FOUND", "Trainer not found")
                parent_links[trainer_id] = link
            if not allow_duplicate and await self._profiles.find_duplicate_dependent(
                principal.user_id, data.display_name, data.birth_date
            ):
                raise ConflictError("DUPLICATE_PLAYER", "This player already exists")
            profile = await self._profiles.add_player_profile(
                PlayerProfile(
                    owner_user_id=principal.user_id,
                    display_name=data.display_name,
                    birth_date=data.birth_date,
                    gender=data.gender,
                    school=data.school,
                    jersey_number=data.jersey_number,
                    emergency_contact_name=data.emergency_contact_name,
                    emergency_contact_phone=data.emergency_contact_phone,
                    is_dependent=True,
                )
            )
            for trainer_id, parent_link in parent_links.items():
                # Inherit the parent's share-link provenance where known, otherwise "admin".
                inherits = parent_link.source == LinkSource.SHARE_LINK
                await self._tenancy.add_link(
                    TrainerPlayerLink(
                        trainer_id=trainer_id,
                        player_profile_id=profile.id,
                        source=LinkSource.SHARE_LINK if inherits else LinkSource.ADMIN,
                        share_link_id=parent_link.share_link_id if inherits else None,
                    )
                )
            return (await self._views([profile]))[0]

    async def list(
        self, principal: Principal, *, cursor: str | None, limit: int
    ) -> PageResult[PlayerView]:
        self._reject_child(principal)
        decoded = decode_cursor(cursor) if cursor else None
        async with self._uow:
            rows = await self._profiles.list_dependents_page(principal.user_id, decoded, limit)
            page = build_page(rows, limit, lambda p: (p.created_at, p.id))
            return PageResult(await self._views(page.items), page.next_cursor, page.has_more)

    async def get(self, principal: Principal, player_id: uuid.UUID) -> PlayerView:
        self._reject_child(principal)
        async with self._uow:
            return (await self._views([await self._owned(principal, player_id)]))[0]

    async def update(
        self, principal: Principal, player_id: uuid.UUID, changes: dict[str, object]
    ) -> PlayerView:
        self._reject_child(principal)
        async with self._uow:
            profile = await self._owned(principal, player_id)
            if changes.get("display_name") is not None:
                profile.display_name = str(changes["display_name"])
            if isinstance(changes.get("birth_date"), date):
                profile.birth_date = changes["birth_date"]  # type: ignore[assignment]
            if changes.get("gender") is not None:
                profile.gender = Gender(str(changes["gender"]))
            if changes.get("token_spend_requires_approval") is not None:
                profile.token_spend_requires_approval = bool(
                    changes["token_spend_requires_approval"]
                )
            for name in _UPDATABLE_NULLABLE:
                if name in changes:
                    setattr(profile, name, changes[name])
            if "birth_date" in changes and profile.birth_date is not None:
                self._validate_age(profile.birth_date)
            if (
                "display_name" in changes or "birth_date" in changes
            ) and profile.birth_date is not None:
                if await self._profiles.find_duplicate_dependent(
                    principal.user_id,
                    profile.display_name,
                    profile.birth_date,
                    exclude_id=profile.id,
                ):
                    raise ConflictError("DUPLICATE_PLAYER", "This player already exists")
            return (await self._views([profile]))[0]

    async def deactivate(self, principal: Principal, player_id: uuid.UUID) -> None:
        """Soft-deactivate: the profile and all its trainer links stop granting access."""
        self._reject_child(principal)
        async with self._uow:
            profile = await self._owned(principal, player_id)
            blocking = await self._commitments.active_for_player(profile.id, None)
            if blocking:
                raise ConflictError(
                    "ACTIVE_COMMITMENTS",
                    "Player has active commitments",
                    details={"rsvp_ids": [str(i) for i in blocking]},
                )
            profile.is_active = False
            await self._tenancy.deactivate_links_for_player(profile.id)

    # --- child <-> trainer links ----------------------------------------------------------

    async def add_trainer(
        self, principal: Principal, player_id: uuid.UUID, trainer_id: uuid.UUID
    ) -> tuple[PlayerLinkView, bool]:
        """Returns ``(link, created)``; ``created`` is False when it was already active."""
        self._reject_child(principal)
        async with self._uow:
            profile = await self._owned(principal, player_id)
            parent_link = await self._tenancy.get_user_link_to(principal.user_id, trainer_id)
            if parent_link is None:
                raise NotFoundError("NOT_FOUND", "Trainer not found")
            existing = await self._tenancy.get_link(trainer_id, profile.id)
            if existing is not None and existing.status == LinkStatus.ACTIVE:
                created = False
            elif existing is not None:
                existing.status = LinkStatus.ACTIVE
                created = True
            else:
                inherits = parent_link.source == LinkSource.SHARE_LINK
                await self._tenancy.add_link(
                    TrainerPlayerLink(
                        trainer_id=trainer_id,
                        player_profile_id=profile.id,
                        source=LinkSource.SHARE_LINK if inherits else LinkSource.ADMIN,
                        share_link_id=parent_link.share_link_id if inherits else None,
                    )
                )
                created = True
            row = await self._tenancy.get_link_row(trainer_id, profile.id)
            assert row is not None  # noqa: S101
            return _link_view(row), created

    async def remove_trainer(
        self, principal: Principal, player_id: uuid.UUID, trainer_id: uuid.UUID
    ) -> None:
        self._reject_child(principal)
        async with self._uow:
            profile = await self._owned(principal, player_id)
            link = await self._tenancy.get_link(trainer_id, profile.id)
            if link is None:
                raise NotFoundError("NOT_FOUND", "Link not found")
            if link.status == LinkStatus.INACTIVE:
                return
            blocking = await self._commitments.active_for_player(profile.id, trainer_id)
            if blocking:
                raise ConflictError(
                    "ACTIVE_COMMITMENTS",
                    "Player has active commitments with this trainer",
                    details={"rsvp_ids": [str(i) for i in blocking]},
                )
            link.status = LinkStatus.INACTIVE
