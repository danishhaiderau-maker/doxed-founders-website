"""Frozen EXT2 W1 calendar: windows, folds (A14), censor boundaries (A15).

Every length here is a frozen card value. `FROZEN` is the only calendar a
real run accepts (runner checks `is_frozen`). Unit tests build small
calendars with `Calendar(...)` to exercise the same code on short data; a
real run refuses them with STOP_CALENDAR.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

DAY_MS = 86_400_000
MINUTE_MS = 60_000


def _d(text: str) -> date:
    return date.fromisoformat(text)


def day_ms(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp() * 1000)


def days_between(start: date, end: date) -> tuple[date, ...]:
    out = []
    cursor = start
    while cursor <= end:
        out.append(cursor)
        cursor += timedelta(days=1)
    return tuple(out)


@dataclass(frozen=True)
class Fold:
    name: str
    fit: tuple[date, date]
    purge: tuple[date, date]
    val: tuple[date, date]
    embargo: tuple[date, date]
    test: tuple[date, date]

    @property
    def train(self) -> tuple[date, date]:
        return (self.fit[0], self.val[1])

    def test_days(self) -> tuple[date, ...]:
        return days_between(*self.test)

    def val_days(self) -> tuple[date, ...]:
        return days_between(*self.val)

    def train_days(self) -> tuple[date, ...]:
        return days_between(*self.train)

    def fit_days(self) -> tuple[date, ...]:
        return days_between(*self.fit)

    def test_censor_ms(self) -> int:
        """Fold-test END boundary (HM P1): 00:00Z of the day after the last test day."""
        return day_ms(self.test[1] + timedelta(days=1))

    def val_censor_ms(self) -> int:
        """Inner-val segment end (switching legs only). Purge/embargo are never scored."""
        return day_ms(self.val[1] + timedelta(days=1))


@dataclass(frozen=True)
class Calendar:
    warmup: tuple[date, date]
    w1: tuple[date, date]
    embargo: tuple[date, date]
    h1: tuple[date, date]
    folds: tuple[Fold, ...]
    lookbacks: tuple[int, ...] = (5, 15, 60, 240)
    percentile_bars: int = 30 * 1440
    rv_bars: int = 1440
    r24_bars: int = 1440
    hour_bars: int = 60
    sigma_hours: int = 29 * 24
    f6_window_ms: int = 30 * DAY_MS
    label: str = "test"
    extra: dict = field(default_factory=dict, compare=False)

    def days(self) -> tuple[date, ...]:
        return days_between(self.warmup[0], self.w1[1])

    def origin_ms(self) -> int:
        return day_ms(self.warmup[0])

    def test_days(self) -> tuple[date, ...]:
        out: list[date] = []
        for fold in self.folds:
            out.extend(fold.test_days())
        return tuple(out)

    def censor_boundaries_ms(self) -> tuple[int, ...]:
        return tuple(fold.test_censor_ms() for fold in self.folds)

    def describe(self) -> dict:
        def span(pair: tuple[date, date]) -> list[str]:
            return [pair[0].isoformat(), pair[1].isoformat()]
        return {
            "label": self.label,
            "windows": {"warmup": span(self.warmup), "w1": span(self.w1),
                        "embargo": span(self.embargo), "h1": span(self.h1)},
            "folds": [
                {"name": f.name, "fit": span(f.fit), "purge": span(f.purge), "val": span(f.val),
                 "train": span(f.train), "embargo": span(f.embargo), "test": span(f.test),
                 "test_censor_ms": f.test_censor_ms()}
                for f in self.folds
            ],
            "lookbacks": list(self.lookbacks),
            "percentile_bars": self.percentile_bars,
            "rv_bars": self.rv_bars,
            "r24_bars": self.r24_bars,
            "hour_bars": self.hour_bars,
            "sigma_hours": self.sigma_hours,
            "f6_window_ms": self.f6_window_ms,
        }


FROZEN = Calendar(
    warmup=(_d("2024-10-30"), _d("2024-11-28")),
    w1=(_d("2024-11-29"), _d("2025-01-28")),
    embargo=(_d("2025-01-29"), _d("2025-01-30")),
    h1=(_d("2025-01-31"), _d("2025-04-01")),
    folds=(
        Fold("F1", (_d("2024-11-29"), _d("2024-12-08")), (_d("2024-12-09"), _d("2024-12-10")),
             (_d("2024-12-11"), _d("2024-12-14")), (_d("2024-12-15"), _d("2024-12-16")),
             (_d("2024-12-17"), _d("2024-12-27"))),
        Fold("F2", (_d("2024-11-29"), _d("2024-12-16")), (_d("2024-12-17"), _d("2024-12-18")),
             (_d("2024-12-19"), _d("2024-12-25")), (_d("2024-12-26"), _d("2024-12-27")),
             (_d("2024-12-28"), _d("2025-01-07"))),
        Fold("F3", (_d("2024-11-29"), _d("2024-12-24")), (_d("2024-12-25"), _d("2024-12-26")),
             (_d("2024-12-27"), _d("2025-01-05")), (_d("2025-01-06"), _d("2025-01-07")),
             (_d("2025-01-08"), _d("2025-01-18"))),
        Fold("F4", (_d("2024-11-29"), _d("2025-01-01")), (_d("2025-01-02"), _d("2025-01-03")),
             (_d("2025-01-04"), _d("2025-01-16")), (_d("2025-01-17"), _d("2025-01-18")),
             (_d("2025-01-19"), _d("2025-01-28"))),
    ),
    label="FROZEN-v3",
)


def is_frozen(calendar: Calendar) -> bool:
    return calendar == FROZEN and calendar.label == "FROZEN-v3"
