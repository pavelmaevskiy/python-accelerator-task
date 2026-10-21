from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.api.v1.trainer.schemas import CreateShareLink
from app.services.sharelink_service import is_redeemable

NOW = datetime(2026, 10, 21, tzinfo=UTC)


def link(**kw: object) -> SimpleNamespace:
    base = {"is_active": True, "expires_at": None, "max_uses": None, "use_count": 0}
    return SimpleNamespace(**{**base, **kw})


@pytest.mark.parametrize(
    ("kw", "expected"),
    [
        ({}, True),
        ({"is_active": False}, False),
        ({"expires_at": NOW + timedelta(seconds=1)}, True),
        ({"expires_at": NOW}, False),  # expiry instant is exclusive
        ({"expires_at": NOW - timedelta(days=1)}, False),
        ({"max_uses": 1, "use_count": 0}, True),
        ({"max_uses": 1, "use_count": 1}, False),
        ({"max_uses": None, "use_count": 10_000}, True),
    ],
)
def test_is_redeemable(kw: dict[str, object], expected: bool) -> None:
    assert is_redeemable(link(**kw), NOW) is expected  # type: ignore[arg-type]


def test_coach_unique_requires_target_email() -> None:
    with pytest.raises(ValidationError):
        CreateShareLink(kind="coach_unique")


def test_player_static_forbids_target_email() -> None:
    with pytest.raises(ValidationError):
        CreateShareLink(kind="player_static", target_email="a@b.co")


def test_valid_combinations() -> None:
    assert CreateShareLink(kind="player_static").target_email is None
    assert CreateShareLink(kind="coach_unique", target_email="a@b.co").kind == "coach_unique"
