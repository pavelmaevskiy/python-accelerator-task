"""Weekly availability: replace-the-whole-set for players, coaches and dependents."""

import uuid
from dataclasses import dataclass
from datetime import time
from typing import Literal

from app.core.availability import Slot, has_overlap
from app.core.enums import UserRole
from app.core.errors import DomainValidationError, NotFoundError, PermissionDeniedError
from app.core.principal import Principal
from app.core.unit_of_work import UnitOfWork
from app.models.identity import AvailabilityWindow, PlayerProfile
from app.repositories.availability_repo import AvailabilityRepo
from app.repositories.profile_repo import ProfileRepo
from app.repositories.tenancy_repo import TenancyRepo

MAX_WINDOWS = 100


@dataclass(frozen=True, slots=True)
class WindowInput:
    weekday: int
    start_time: time
    end_time: time
    is_available: bool = True


@dataclass(frozen=True, slots=True)
class WindowView:
    id: uuid.UUID
    weekday: int
    start_time: time
    end_time: time
    is_available: bool


@dataclass(frozen=True, slots=True)
class AvailabilityView:
    subject_type: Literal["player", "coach"]
    subject_id: uuid.UUID
    windows: list[WindowView]


def _views(rows: list[AvailabilityWindow]) -> list[WindowView]:
    return [WindowView(r.id, r.weekday, r.start_time, r.end_time, r.is_available) for r in rows]


class AvailabilityService:
    def __init__(
        self,
        uow: UnitOfWork,
        availability: AvailabilityRepo,
        profiles: ProfileRepo,
        tenancy: TenancyRepo,
    ) -> None:
        self._uow = uow
        self._availability = availability
        self._profiles = profiles
        self._tenancy = tenancy

    @staticmethod
    def _validate(windows: list[WindowInput]) -> list[tuple[Slot, bool]]:
        if len(windows) > MAX_WINDOWS:
            raise DomainValidationError("TOO_MANY_WINDOWS", f"At most {MAX_WINDOWS} windows")
        slots = [(Slot(w.weekday, w.start_time, w.end_time), w.is_available) for w in windows]
        if any(s.start >= s.end for s, _ in slots):
            raise DomainValidationError("INVALID_WINDOW", "start_time must be before end_time")
        if has_overlap([s for s, _ in slots]):
            raise DomainValidationError("OVERLAPPING_WINDOWS", "Availability windows overlap")
        return slots

    async def _own_player_profile(self, principal: Principal) -> PlayerProfile:
        """Adult: own profile. Child login: the dependent profile it logs into."""
        profile = await self._profiles.get_player_profile_for_login(
            principal.user_id
        ) or await self._profiles.get_own_player_profile(principal.user_id)
        if profile is None:
            raise NotFoundError("NOT_FOUND", "Player profile not found")
        return profile

    async def get_own(self, principal: Principal) -> AvailabilityView:
        async with self._uow:
            if principal.role == UserRole.COACH:
                rows = await self._availability.list_for_coach(principal.user_id)
                return AvailabilityView("coach", principal.user_id, _views(rows))
            profile = await self._own_player_profile(principal)
            rows = await self._availability.list_for_player(profile.id)
            return AvailabilityView("player", profile.id, _views(rows))

    async def replace_own(
        self, principal: Principal, windows: list[WindowInput]
    ) -> AvailabilityView:
        slots = self._validate(windows)
        async with self._uow:
            if principal.role == UserRole.COACH:
                rows = await self._availability.replace_for_coach(principal.user_id, slots)
                return AvailabilityView("coach", principal.user_id, _views(rows))
            profile = await self._own_player_profile(principal)
            rows = await self._availability.replace_for_player(profile.id, slots)
            return AvailabilityView("player", profile.id, _views(rows))

    async def replace_for_dependent(
        self, principal: Principal, player_id: uuid.UUID, windows: list[WindowInput]
    ) -> AvailabilityView:
        if principal.is_child:
            raise PermissionDeniedError("CHILD_FORBIDDEN", "Not available to child accounts")
        slots = self._validate(windows)
        async with self._uow:
            owned = await self._profiles.get_owned_profiles(principal.user_id, [player_id])
            if not owned:
                raise NotFoundError("NOT_FOUND", "Player not found")
            rows = await self._availability.replace_for_player(owned[0].id, slots)
            return AvailabilityView("player", owned[0].id, _views(rows))

    async def get_for_trainer(self, principal: Principal, player_id: uuid.UUID) -> AvailabilityView:
        """Readable only for players actively linked to the caller's tenant (else 404)."""
        if principal.trainer_id is None:
            raise NotFoundError("NOT_FOUND", "Player not found")
        async with self._uow:
            link = await self._tenancy.get_active_link(principal.trainer_id, player_id)
            if link is None:
                raise NotFoundError("NOT_FOUND", "Player not found")
            rows = await self._availability.list_for_player(player_id)
            return AvailabilityView("player", player_id, _views(rows))
