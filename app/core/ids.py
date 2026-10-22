"""Application-generated UUIDv7 identifiers (time-sortable, non-enumerable)."""

from uuid import UUID

import uuid_utils


def new_id() -> UUID:
    return UUID(bytes=uuid_utils.uuid7().bytes)
