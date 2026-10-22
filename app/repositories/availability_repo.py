import uuid
from collections.abc import Sequence

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.availability import Slot
from app.core.errors import DomainValidationError
from app.models.identity import AvailabilityWindow


class AvailabilityRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_player(self, player_profile_id: uuid.UUID) -> list[AvailabilityWindow]:
        return await self._list(AvailabilityWindow.player_profile_id == player_profile_id)

    async def list_for_coach(self, coach_user_id: uuid.UUID) -> list[AvailabilityWindow]:
        return await self._list(AvailabilityWindow.coach_user_id == coach_user_id)

    async def _list(self, *where: object) -> list[AvailabilityWindow]:
        stmt = (
            select(AvailabilityWindow)
            .where(*where)  # type: ignore[arg-type]
            .order_by(AvailabilityWindow.weekday, AvailabilityWindow.start_time)
            .execution_options(populate_existing=True)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def replace_for_player(
        self, player_profile_id: uuid.UUID, windows: Sequence[tuple[Slot, bool]]
    ) -> list[AvailabilityWindow]:
        await self._session.execute(
            delete(AvailabilityWindow).where(
                AvailabilityWindow.player_profile_id == player_profile_id
            )
        )
        rows = [
            AvailabilityWindow(
                player_profile_id=player_profile_id,
                weekday=s.weekday,
                start_time=s.start,
                end_time=s.end,
                is_available=ok,
            )
            for s, ok in windows
        ]
        return await self._insert(rows)

    async def replace_for_coach(
        self, coach_user_id: uuid.UUID, windows: Sequence[tuple[Slot, bool]]
    ) -> list[AvailabilityWindow]:
        await self._session.execute(
            delete(AvailabilityWindow).where(AvailabilityWindow.coach_user_id == coach_user_id)
        )
        rows = [
            AvailabilityWindow(
                coach_user_id=coach_user_id,
                weekday=s.weekday,
                start_time=s.start,
                end_time=s.end,
                is_available=ok,
            )
            for s, ok in windows
        ]
        return await self._insert(rows)

    async def _insert(self, rows: list[AvailabilityWindow]) -> list[AvailabilityWindow]:
        self._session.add_all(rows)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            # The gist exclusion constraint (C4) backstops races the Python check cannot see.
            if "ex_availability" in str(exc.orig):
                raise DomainValidationError(
                    "OVERLAPPING_WINDOWS", "Availability windows overlap"
                ) from exc
            raise
        return sorted(rows, key=lambda r: (r.weekday, r.start_time))
