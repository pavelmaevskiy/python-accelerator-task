from datetime import time

import pytest
from pydantic import ValidationError

from app.api.v1.me.schemas import AvailabilityWindowIn, ReplaceAvailability
from app.core.availability import Slot, half_open_overlap, has_overlap


def t(s: str) -> time:
    return time.fromisoformat(s)


@pytest.mark.parametrize(
    ("a", "b", "overlap"),
    [
        (("16:00", "18:00"), ("15:59", "16:00"), False),
        (("16:00", "18:00"), ("15:59", "16:01"), True),
        (("16:00", "18:00"), ("18:00", "20:00"), False),
        (("16:00", "18:00"), ("17:59", "18:01"), True),
        (("16:00", "18:00"), ("16:30", "17:00"), True),  # containment
        (("16:00", "18:00"), ("16:00", "18:00"), True),  # identical
    ],
)
def test_half_open_overlap(a: tuple[str, str], b: tuple[str, str], overlap: bool) -> None:
    assert half_open_overlap((t(a[0]), t(a[1])), (t(b[0]), t(b[1]))) is overlap
    assert half_open_overlap((t(b[0]), t(b[1])), (t(a[0]), t(a[1]))) is overlap  # symmetric


def test_has_overlap_only_within_the_same_weekday() -> None:
    mon_a = Slot(1, t("16:00"), t("18:00"))
    tue_same_hours = Slot(2, t("16:00"), t("18:00"))
    mon_adjacent = Slot(1, t("18:00"), t("20:00"))
    mon_clash = Slot(1, t("17:00"), t("19:00"))
    assert not has_overlap([mon_a, tue_same_hours, mon_adjacent])
    assert has_overlap([mon_a, mon_adjacent, mon_clash])
    assert not has_overlap([])


def test_has_overlap_detects_a_window_swallowed_by_an_earlier_longer_one() -> None:
    slots = [
        Slot(1, t("08:00"), t("20:00")),
        Slot(1, t("09:00"), t("10:00")),
        Slot(1, t("19:00"), t("19:30")),
    ]
    assert has_overlap(slots)


def test_start_must_be_before_end() -> None:
    with pytest.raises(ValidationError):
        AvailabilityWindowIn(weekday=1, start_time=t("10:00"), end_time=t("10:00"))
    with pytest.raises(ValidationError):
        AvailabilityWindowIn(weekday=1, start_time=t("11:00"), end_time=t("10:00"))
    assert AvailabilityWindowIn(weekday=1, start_time=t("10:00"), end_time=t("10:01"))


@pytest.mark.parametrize("weekday", [-1, 7])
def test_weekday_bounds(weekday: int) -> None:
    with pytest.raises(ValidationError):
        AvailabilityWindowIn(weekday=weekday, start_time=t("10:00"), end_time=t("11:00"))


def test_more_than_100_windows_rejected_by_schema() -> None:
    w = {"weekday": 1, "start_time": "10:00", "end_time": "11:00"}
    with pytest.raises(ValidationError):
        ReplaceAvailability.model_validate({"windows": [w] * 101})
