"""Upload rules shared by avatar and logo flows: size caps and magic-byte detection."""

from enum import StrEnum

from app.core.errors import DomainValidationError
from app.core.svg_sanitizer import sanitize_svg


class UploadKind(StrEnum):
    AVATAR = "avatar"
    LOGO = "logo"


MAX_BYTES = {UploadKind.AVATAR: 5 * 1024 * 1024, UploadKind.LOGO: 2 * 1024 * 1024}
ALLOWED_TYPES = {
    UploadKind.AVATAR: frozenset({"jpeg", "png", "webp"}),
    UploadKind.LOGO: frozenset({"png", "svg", "webp"}),
}
CONTENT_TYPES = {
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "svg": "image/svg+xml",
}
EXTENSIONS = {"jpeg": "jpg", "png": "png", "webp": "webp", "svg": "svg"}


def detect_image_type(data: bytes) -> str | None:
    """Detect by magic bytes (never by client-declared content type)."""
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    head = data[:512].lstrip().lower()
    if head.startswith((b"<?xml", b"<svg")) and b"<svg" in data[:2048].lower():
        return "svg"
    return None


def validate_upload(kind: UploadKind, data: bytes) -> tuple[str, bytes]:
    """Return ``(image_type, bytes_to_store)``; SVGs are sanitised, others pass through."""
    if len(data) > MAX_BYTES[kind]:
        raise DomainValidationError("UPLOAD_INVALID", "File is too large")
    image_type = detect_image_type(data)
    if image_type is None or image_type not in ALLOWED_TYPES[kind]:
        raise DomainValidationError("UPLOAD_INVALID", "Unsupported or corrupt image")
    if image_type == "svg":
        data = sanitize_svg(data)
    return image_type, data
