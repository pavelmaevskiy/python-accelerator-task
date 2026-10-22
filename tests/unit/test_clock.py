from datetime import UTC, date, datetime

from app.core.clock import FixedClock, SystemClock, age_on


def test_age_on_exact_birthday_boundaries() -> None:
    assert age_on(date(2010, 10, 21), date(2026, 10, 20)) == 15
    assert age_on(date(2010, 10, 21), date(2026, 10, 21)) == 16
    assert age_on(date(2008, 2, 29), date(2026, 2, 28)) == 17


def test_leap_day_birth_turns_older_on_mar_1_in_non_leap_year() -> None:
    assert age_on(date(2008, 2, 29), date(2026, 3, 1)) == 18
    assert age_on(date(2008, 2, 29), date(2028, 2, 29)) == 20


def test_fixed_clock() -> None:
    c = FixedClock(datetime(2026, 10, 21, 12, 0, tzinfo=UTC))
    assert c.today() == date(2026, 10, 21)
    assert c.now().tzinfo is not None


def test_system_clock_is_tz_aware() -> None:
    assert SystemClock().now().tzinfo is not None
