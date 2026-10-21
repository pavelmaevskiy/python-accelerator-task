"""The authenticated caller, resolved once per request by ``api.deps.current_principal``."""

import uuid
from dataclasses import dataclass

from app.core.enums import UserRole


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: uuid.UUID
    role: UserRole
    trainer_id: uuid.UUID | None
    act: uuid.UUID | None  # impersonating admin, when present
    is_child: bool
    jti: str | None

    @property
    def is_impersonated(self) -> bool:
        return self.act is not None
