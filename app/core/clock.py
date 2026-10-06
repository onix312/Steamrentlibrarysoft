"""Инжектируемые часы: вся логика времени должна использовать их,
чтобы тесты не зависели от системного времени."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


class Clock:
    """Часы по умолчанию — реальное время в UTC."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def today(self) -> datetime:
        now = self.now()
        return now.replace(hour=0, minute=0, second=0, microsecond=0)


class FixedClock(Clock):
    """Часы с фиксированным временем — для тестов и симуляций."""

    def __init__(self, fixed: datetime) -> None:
        self._now = fixed if fixed.tzinfo else fixed.replace(tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now = self._now + delta

    def set(self, fixed: datetime) -> None:
        self._now = fixed if fixed.tzinfo else fixed.replace(tzinfo=timezone.utc)
