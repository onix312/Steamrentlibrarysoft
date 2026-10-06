"""FamilyEligibilityService — поддержка игр в Steam Families (ТЗ §4).

Не связан с GUI. Результаты кэшируются в полях игры вместе с датой и
источником проверки. Приоритет решений:

  ручное переопределение → датасет → правила по метаданным → «требует проверки»

Автоматика НЕ считает игру доступной по умолчанию: без подтверждения она
получает статус «Требует проверки» (исключение — подтверждённые записи
датасета).
"""
from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import Clock
from app.core.events import TOPIC_DATA_CHANGED, EventBus
from app.database import models, repositories
from app.database.session import Database
from app.domain.enums import (
    Actor,
    AuditAction,
    EligibilitySource,
    EligibilityStatus,
)
from app.domain.value_objects import EligibilityResult
from app.integrations.steam.exclusions import ExclusionDataset
from app.services.audit_service import AuditService

log = logging.getLogger(__name__)

#: Минимальная цена, ниже которой игру нет смысла продавать отдельно.
NO_SENSE_MIN_PRICE = 40.0
#: Кэш результатов проверки (дней).
CACHE_TTL_DAYS = 30


class FamilyEligibilityService:
    def __init__(
        self,
        db: Database,
        clock: Clock,
        dataset: ExclusionDataset,
        audit: AuditService,
        events: EventBus,
        min_sell_price: float = NO_SENSE_MIN_PRICE,
    ) -> None:
        self.db = db
        self.clock = clock
        self.dataset = dataset
        self.audit = audit
        self.events = events
        self.min_sell_price = min_sell_price

    # ---------------------------------------------------------------- API
    def evaluate(self, session: Session, game: models.Game, force: bool = False) -> EligibilityResult:
        """Пересчитывает статус игры и сохраняет результат (кэш)."""
        now = self.clock.now()
        if not force and self._cache_fresh(game, now):
            return EligibilityResult(
                status=EligibilityStatus(game.eligibility_status),
                reason=game.eligibility_reason or "",
                source=EligibilitySource(game.eligibility_source) if game.eligibility_source else EligibilitySource.RULE,
                checked_at=game.eligibility_checked_at or now,
            )

        result = self._compute(game, now)
        game.eligibility_status = result.status.value
        game.eligibility_reason = result.reason
        game.eligibility_source = result.source.value
        game.eligibility_checked_at = now
        return result

    def evaluate_all(self, force: bool = False) -> dict[str, int]:
        counters: dict[str, int] = {}
        with self.db.session() as session:
            games = list(session.scalars(select(models.Game)))
            for game in games:
                result = self.evaluate(session, game, force=force)
                counters[result.status.value] = counters.get(result.status.value, 0) + 1
        self.events.publish(TOPIC_DATA_CHANGED, section="eligibility")
        return counters

    def set_override(self, game_id: int, status: EligibilityStatus | None, reason: str = "") -> None:
        """Ручное переопределение оператора (или снятие переопределения: status=None)."""
        with self.db.session() as session:
            game = session.get(models.Game, game_id)
            if game is None:
                return
            game.eligibility_override = status.value if status else None
            now = self.clock.now()
            if status is not None:
                game.eligibility_status = status.value
                game.eligibility_reason = reason or "Решение оператора"
                game.eligibility_source = EligibilitySource.MANUAL.value
                game.eligibility_checked_at = now
            else:
                self.evaluate(session, game, force=True)
            self.audit.log(
                AuditAction.ELIGIBILITY_OVERRIDDEN, Actor.USER,
                entity="game", entity_id=game_id, session=session,
                status=status.value if status else "cleared", reason=reason,
            )
        self.events.publish(TOPIC_DATA_CHANGED, section="eligibility")

    def is_sellable(self, game: models.Game) -> bool:
        """Быстрая проверка без пересчёта: можно ли продавать игру."""
        if game.is_free:
            return False
        return game.eligibility_status == EligibilityStatus.AVAILABLE.value

    # ------------------------------------------------------------- внутри
    def _cache_fresh(self, game: models.Game, now) -> bool:
        if game.eligibility_checked_at is None:
            return False
        checked = game.eligibility_checked_at
        if checked.tzinfo is None:
            from datetime import timezone

            checked = checked.replace(tzinfo=timezone.utc)
        return (now - checked) < timedelta(days=CACHE_TTL_DAYS)

    def _compute(self, game: models.Game, now) -> EligibilityResult:
        # 1) ручное переопределение — высший приоритет
        if game.eligibility_override:
            return EligibilityResult(
                status=EligibilityStatus(game.eligibility_override),
                reason=game.eligibility_reason or "Ручное решение оператора",
                source=EligibilitySource.MANUAL,
                checked_at=now,
            )
        # 2) бесплатные игры не являются товаром и не раздаются через семью
        if game.is_free:
            return EligibilityResult(
                status=EligibilityStatus.FREE,
                reason="Бесплатная игра: не раздаётся через Steam Families и не является коммерческим преимуществом.",
                source=EligibilitySource.RULE,
                checked_at=now,
            )
        # 3) датасет известных исключений/подтверждений
        entry = self.dataset.get(game.app_id)
        if entry is not None:
            if entry.kind == "excluded":
                status = (
                    EligibilityStatus.THIRD_PARTY_LAUNCHER
                    if "лаунчер" in entry.reason.lower() or "launcher" in entry.reason.lower()
                    else EligibilityStatus.PUBLISHER_EXCLUDED
                )
                return EligibilityResult(status, entry.reason, EligibilitySource.DATASET, now)
            return EligibilityResult(
                status=EligibilityStatus.AVAILABLE,
                reason=entry.reason or "Подтверждено датасетом",
                source=EligibilitySource.DATASET,
                checked_at=now,
            )
        # 4) маркеры в метаданных
        if game.requires_third_party_launcher:
            return EligibilityResult(
                status=EligibilityStatus.THIRD_PARTY_LAUNCHER,
                reason="Игра требует сторонний лаунчер — не доступна в Steam Families.",
                source=EligibilitySource.RULE,
                checked_at=now,
            )
        if game.requires_third_party_account:
            return EligibilityResult(
                status=EligibilityStatus.PUBLISHER_EXCLUDED,
                reason="Игра требует сторонний аккаунт/ключ/подписку издателя — не доступна в Steam Families.",
                source=EligibilitySource.RULE,
                checked_at=now,
            )
        # 5) экономически бессмысленные
        if game.price and game.price < self.min_sell_price:
            return EligibilityResult(
                status=EligibilityStatus.NO_SENSE,
                reason=f"Цена {game.price:.0f} ниже порога рентабельности аренды.",
                source=EligibilitySource.RULE,
                checked_at=now,
            )
        # 6) по умолчанию — оптимистично НЕ считаем доступной
        return EligibilityResult(
            status=EligibilityStatus.NEEDS_CHECK,
            reason="Маркеров исключения нет, но официального подтверждения нет. Проверьте страницу игры в клиенте/магазине.",
            source=EligibilitySource.RULE,
            checked_at=now,
        )
