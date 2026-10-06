"""SQLAlchemy declarative base + TZ-aware DateTime для SQLite.

SQLite не хранит часовой пояс, поэтому все даты приложения храним в UTC
и принудительно возвращаем как aware-значения (единый формат сравнений).
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, TypeDecorator
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class TZDateTime(TypeDecorator):
    """DateTime, который всегда хранит и возвращает UTC-aware значения."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
