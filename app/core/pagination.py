import base64
import binascii
import json
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
