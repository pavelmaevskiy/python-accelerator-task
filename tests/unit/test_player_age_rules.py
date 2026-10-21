from datetime import UTC, date, datetime

import pytest

from app.core.clock import FixedClock
from app.core.errors import DomainValidationError
from app.services.player_service import PlayerService

TODAY = FixedClock(datetime(2026, 10, 21, 9, 0, tzinfo=UTC))


def service() -> PlayerService:
    return PlayerService(None, None, None, TODAY, None, None)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("birth", "ok"),
    [
        ("2025-10-21", True),  # exactly 1 year old
        ("2025-10-22", False),  # 0 years, 364 days
        ("2008-10-22", True),  # 17y 364d
        ("2008-10-21", True),  # turned 18 today: 18 is inside the 1-18 bound
        ("2007-10-22", True),  # 18y 364d: still 18
        ("2007-10-21", False),  # turned 19 today: out
        ("2000-01-01", False),
        ("2027-01-01", False),  # future date
        ("2026-10-21", False),  # born today
    ],
)
def test_dependent_age_is_1_to_18_inclusive_by_exact_date(birth: str, ok: bool) -> None:
    if ok:
        service()._validate_age(date.fromisoformat(birth))
    else:
        with pytest.raises(DomainValidationError) as e:
            service()._validate_age(date.fromisoformat(birth))
        assert e.value.code == "INVALID_AGE"


def test_feb_29_birthday_turns_a_year_older_on_mar_1_in_non_leap_years() -> None:
    clock = FixedClock(datetime(2027, 2, 28, tzinfo=UTC))  # 2027 is not a leap year
    svc = PlayerService(None, None, None, clock, None, None)  # type: ignore[arg-type]
    svc._validate_age(date(2008, 2, 29))  # 18 on Mar 1 2026 -> still 18 on Feb 28 2027
    clock = FixedClock(datetime(2027, 3, 1, tzinfo=UTC))
    svc = PlayerService(None, None, None, clock, None, None)  # type: ignore[arg-type]
    with pytest.raises(DomainValidationError):
        svc._validate_age(date(2008, 2, 29))  # 19 on Mar 1 2027
