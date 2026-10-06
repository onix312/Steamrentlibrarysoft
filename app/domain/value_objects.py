"""Value objects домена: неизменяемые структуры, передаваемые между слоями."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.domain.enums import EligibilitySource, EligibilityStatus


@dataclass(frozen=True)
class Availability:
    """Доступность копий игры."""
    total: int
    busy: int

    @property
    def free(self) -> int:
        return max(0, self.total - self.busy)

    @property
    def has_free(self) -> bool:
        return self.free > 0


@dataclass(frozen=True)
class EligibilityResult:
    status: EligibilityStatus
    reason: str
    source: EligibilitySource
    checked_at: datetime

    @property
    def sellable(self) -> bool:
        """Можно ли продавать доступ через Steam Families при таком статусе."""
        return self.status == EligibilityStatus.AVAILABLE


@dataclass(frozen=True)
class DemandBreakdown:
    """Прозрачная расшифровка расчёта спроса (фактор → вклад 0..100)."""
    factors: dict[str, float] = field(default_factory=dict)

    def total(self) -> float:
        return sum(self.factors.values())


@dataclass(frozen=True)
class OwnedGame:
    """Игра, полученная из источника (Steam API / симуляция)."""
    app_id: int
    name: str
    is_free: bool = False
    playtime_minutes: int = 0
    family_shared: bool = False  # доступна владельцу через чужую семью
    extra: dict = field(default_factory=dict)


@dataclass
class SyncReport:
    kind: str
    accounts_processed: int = 0
    games_imported: int = 0
    licenses_upserted: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


class DomainError(Exception):
    """Базовая ошибка домена с человекочитаемым сообщением."""


class AllocationError(DomainError):
    """Нет свободной копии / конфликт лицензий."""


class CapabilityError(DomainError):
    """Действие не поддерживается выбранным адаптером."""


class DryRunBlocked(DomainError):
    """Действие запрещено в режиме DRY RUN."""
