import base64
import binascii
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.core.errors import DomainValidationError


def encode_cursor(created_at: datetime, id_: UUID) -> str:
    raw = json.dumps({"t": created_at.isoformat(), "i": str(id_)}).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
        return datetime.fromisoformat(data["t"]), UUID(data["i"])
    except (binascii.Error, ValueError, KeyError, TypeError) as exc:
        raise DomainValidationError("INVALID_CURSOR", "Invalid pagination cursor") from exc


@dataclass(frozen=True, slots=True)
class PageResult[T]:
    items: list[T]
    next_cursor: str | None
    has_more: bool


def build_page[T](
    rows: Sequence[T], limit: int, key: Callable[[T], tuple[datetime, UUID]]
) -> PageResult[T]:
    """Trim a ``limit + 1`` fetch to a page and derive the opaque next cursor."""
    has_more = len(rows) > limit
    items = list(rows[:limit])
    next_cursor = encode_cursor(*key(items[-1])) if has_more and items else None
    return PageResult(items=items, next_cursor=next_cursor, has_more=has_more)
