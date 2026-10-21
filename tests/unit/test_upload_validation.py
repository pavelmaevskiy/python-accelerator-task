import pytest

from app.core.errors import DomainValidationError
from app.core.uploads import MAX_BYTES, UploadKind, detect_image_type, validate_upload

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 20
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 20
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"0" * 20
GIF = b"GIF89a" + b"0" * 20
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>'


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (JPEG, "jpeg"),
        (PNG, "png"),
        (WEBP, "webp"),
        (SVG, "svg"),
        (GIF, None),
        (b"", None),
        (b"MZ", None),
    ],
)
def test_detect_image_type_uses_magic_bytes(data: bytes, expected: str | None) -> None:
    assert detect_image_type(data) == expected


@pytest.mark.parametrize(
    ("kind", "data", "ok"),
    [
        (UploadKind.AVATAR, JPEG, True),
        (UploadKind.AVATAR, PNG, True),
        (UploadKind.AVATAR, WEBP, True),
        (UploadKind.AVATAR, SVG, False),  # avatars are raster only
        (UploadKind.AVATAR, GIF, False),
        (UploadKind.LOGO, SVG, True),
        (UploadKind.LOGO, PNG, True),
        (UploadKind.LOGO, WEBP, True),
        (UploadKind.LOGO, JPEG, False),  # logos: png/svg/webp only
    ],
)
def test_allowed_types_per_kind(kind: UploadKind, data: bytes, ok: bool) -> None:
    if ok:
        validate_upload(kind, data)
    else:
        with pytest.raises(DomainValidationError) as e:
            validate_upload(kind, data)
        assert e.value.code == "UPLOAD_INVALID"


@pytest.mark.parametrize("kind", list(UploadKind))
def test_size_cap_is_inclusive_at_the_limit(kind: UploadKind) -> None:
    at_limit = PNG + b"0" * (MAX_BYTES[kind] - len(PNG))
    over = at_limit + b"0"
    validate_upload(kind, at_limit)
    with pytest.raises(DomainValidationError):
        validate_upload(kind, over)


def test_svg_is_sanitised_on_validate() -> None:
    dirty = b'<svg xmlns="http://www.w3.org/2000/svg"><script>x()</script><rect/></svg>'
    kind, data = validate_upload(UploadKind.LOGO, dirty)
    assert kind == "svg" and b"script" not in data
