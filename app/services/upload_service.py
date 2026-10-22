"""Two-phase uploads: presign into quarantine, then confirm (validate, copy, clean up)."""

import uuid
from dataclasses import dataclass

from app.core.errors import DomainValidationError, NotFoundError
from app.core.ids import new_id
from app.core.storage import ObjectStorage
from app.core.uploads import (
    CONTENT_TYPES,
    EXTENSIONS,
    MAX_BYTES,
    UploadKind,
    validate_upload,
)

PRESIGN_TTL_SECONDS = 300
_UPLOAD_NOT_FOUND = "UPLOAD_NOT_FOUND"


@dataclass(frozen=True, slots=True)
class PresignView:
    upload_url: str
    upload_headers: dict[str, str]
    key: str
    expires_in: int


class UploadService:
    def __init__(self, storage: ObjectStorage) -> None:
        self._storage = storage

    @staticmethod
    def _prefix(owner_id: uuid.UUID) -> str:
        return f"quarantine/{owner_id}/"

    async def presign(
        self, owner_id: uuid.UUID, *, content_type: str, content_length: int
    ) -> PresignView:
        key = f"{self._prefix(owner_id)}{new_id()}"
        upload = await self._storage.presign_put(
            key, content_type, content_length, PRESIGN_TTL_SECONDS
        )
        return PresignView(upload.url, upload.headers, key, PRESIGN_TTL_SECONDS)

    async def confirm(self, owner_id: uuid.UUID, key: str, kind: UploadKind) -> str:
        """Validate a quarantined object and publish it under a random final key."""
        if not key.startswith(self._prefix(owner_id)):
            raise NotFoundError(_UPLOAD_NOT_FOUND, "Upload not found")
        meta = await self._storage.head(key)
        if meta is None:
            raise NotFoundError(_UPLOAD_NOT_FOUND, "Upload not found")
        try:
            if meta.size > MAX_BYTES[kind]:
                # Reject on the HEAD before downloading an oversized object.
                raise DomainValidationError("UPLOAD_INVALID", "File is too large")
            image_type, data = validate_upload(kind, await self._storage.get(key))
        except Exception:
            await self._storage.delete(key)  # never leave rejected content in quarantine
            raise
        final_key = f"{kind.value}s/{owner_id}/{new_id()}.{EXTENSIONS[image_type]}"
        if image_type == "svg":
            await self._storage.put(final_key, data, CONTENT_TYPES[image_type])  # sanitised copy
        else:
            await self._storage.copy(key, final_key)
        await self._storage.delete(key)
        return final_key
