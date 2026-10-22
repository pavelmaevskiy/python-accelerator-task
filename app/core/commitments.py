"""Seam to Epic-02/05: future paid commitments that block removing a player or a link."""

import uuid
from typing import Protocol


class CommitmentChecker(Protocol):
    async def active_for_player(
        self, player_profile_id: uuid.UUID, trainer_id: uuid.UUID | None
    ) -> list[uuid.UUID]:
        """RSVP ids that block removal; ``trainer_id=None`` means across all trainers."""
        ...


class NoCommitments:
    """Default until Epic-02/05 register a real checker: nothing ever blocks."""

    async def active_for_player(
        self, player_profile_id: uuid.UUID, trainer_id: uuid.UUID | None
    ) -> list[uuid.UUID]:
        return []
