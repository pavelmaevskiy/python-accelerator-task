from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.core.errors import DomainValidationError
from app.core.pagination import decode_cursor, encode_cursor


def test_cursor_round_trip() -> None:
    ts, uid = datetime(2026, 10, 21, tzinfo=UTC), uuid4()
    assert decode_cursor(encode_cursor(ts, uid)) == (ts, uid)


@pytest.mark.parametrize("bad", ["not-base64!!", "e30", "bm90LWpzb24"])
def test_bad_cursor_is_422(bad: str) -> None:
    with pytest.raises(DomainValidationError) as e:
        decode_cursor(bad)
    assert e.value.code == "INVALID_CURSOR"
