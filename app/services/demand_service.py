"""Demand Score / Commercial Score (ТЗ §5–6).

Архитектура расширяемая: каждый фактор — отдельный класс, веса берутся из
настроек, грейды — из порогов. Расчёт детерминирован и прозрачен
(разбивка по факторам сохраняется в БД).
"""
from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import func, select

from app.core.clock import Clock
from app.core.config import AppConfig
from app.core.events import TOPIC_DATA_CHANGED, EventBus
from app.database import models
from app.database.session import Database
from app.domain.enums import AuditAction, DemandGrade, EligibilityStatus
from app.services.audit_service import AuditService

CURRENT_YEAR = 2026

#: Жанры/теги, которые сейчас заметно подогревают спрос (редактируемо).
HOT_TAGS = {"Horror", "Co-op", "Survival", "Open World", "Roguelike", "RPG", "Multiplayer"}


@dataclass
class DemandContext:
    sales_last_90d: dict[int, int] = field(default_factory=dict)   # game_id -> продажи
    free_copies: dict[int, int] = field(default_factory=dict)     # game_id -> свободные копии


class DemandFactor(ABC):
    name: str = "base"

    @abstractmethod
    def score(self, game: models.Game, ctx: DemandContext) -> float:
        """0..100."""


class PopularityFactor(DemandFactor):
    name = "popularity"

    def score(self, game, ctx) -> float:
        owners = game.approx_owners or game.review_count or 0
        if owners <= 0:
            return 20.0
        # log-шкала: 100 тыс. владельцев ≈ 40, 10 млн ≈ 100
        value = (math.log10(max(owners, 1)) - 3.0) / 4.0
        return max(0.0, min(100.0, value * 100))


class ReviewsFactor(DemandFactor):
    name = "reviews"

    def score(self, game, ctx) -> float:
        if game.review_score is None:
            return 40.0
        confidence = min(1.0, (game.review_count or 0) / 50_000)
        neutral = 50.0
        return neutral + (game.review_score - neutral) * (0.3 + 0.7 * confidence)


class PriceFactor(DemandFactor):
    """Дорогие игры чаще берут в аренду — выше мотивация сэкономить."""
    name = "price"

    def score(self, game, ctx) -> float:
        if game.is_free or not game.price:
            return 0.0
        return min(100.0, game.price / 25.0)  # 2500 руб. ≈ 100


class RecencyFactor(DemandFactor):
    name = "recency"

    def score(self, game, ctx) -> float:
        year = _parse_year(game.release_date)
        if year is None:
            return 40.0
        age = max(0, CURRENT_YEAR - year)
        return max(0.0, 100.0 - age * 3.5)


class MultiplayerFactor(DemandFactor):
    name = "multiplayer"

    def score(self, game, ctx) -> float:
        if game.has_coop:
            return 100.0
        if game.has_multiplayer:
            return 75.0
        return 25.0


class TrendFactor(DemandFactor):
    name = "trend"

    def score(self, game, ctx) -> float:
        tags = set(game.genres or []) | set(game.tags or [])
        hot_hits = len(tags & HOT_TAGS)
        base = min(70.0, hot_hits * 25.0)
        year = _parse_year(game.release_date)
        if year is not None and CURRENT_YEAR - year <= 2:
            base += 30.0  # свежие релизы на хайпе
        return min(100.0, base)


class MarketplaceFactor(DemandFactor):
    """Спрос по собственным продажам (данные конкурентов — только через адаптер)."""
    name = "marketplace"

    def score(self, game, ctx) -> float:
        sales = ctx.sales_last_90d.get(game.id, 0)
        if sales == 0:
            return 50.0  # нейтрально, данных нет
        return min(100.0, sales * 12.0)


def _parse_year(value: str | None) -> int | None:
    if not value:
        return None
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if len(digits) >= 4:
        return int(digits[:4])
    return None


class DemandService:
    def __init__(self, db: Database, clock: Clock, config: AppConfig,
                 audit: AuditService, events: EventBus) -> None:
        self.db = db
        self.clock = clock
        self.config = config
        self.audit = audit
        self.events = events
        self.factors: list[DemandFactor] = [
            PopularityFactor(),
            ReviewsFactor(),
            PriceFactor(),
            RecencyFactor(),
            MultiplayerFactor(),
            TrendFactor(),
            MarketplaceFactor(),
        ]

    # ------------------------------------------------------------------ API
    def recalculate_all(self) -> int:
        weights = self.config.demand_weights
        weight_map = {
            "popularity": weights.popularity,
            "reviews": weights.reviews,
            "price": weights.price,
            "recency": weights.recency,
            "multiplayer": weights.multiplayer,
            "trend": weights.trend,
            "marketplace": weights.marketplace,
        }
        total_weight = sum(weight_map.values()) or 1.0

        with self.db.session() as session:
            ctx = self._build_context(session)
            cw = self.config.commercial_weights
            games = list(session.scalars(select(models.Game)))
            now = self.clock.now()
            for game in games:
                breakdown = self.compute_breakdown(game, ctx)
                raw = sum(weight_map.get(name, 0.0) * value for name, value in breakdown.items())
                demand = max(0.0, min(100.0, raw / total_weight))

                grade = self.grade_for(game, demand)
                game.demand_score = round(demand, 1)
                game.demand_grade = grade.value
                game.demand_breakdown = {name: round(value, 1) for name, value in breakdown.items()}
                game.commercial_score = round(self._commercial(game, demand, ctx, cw), 1)
                game.score_computed_at = now
            count = len(games)
        self.audit.log(AuditAction.SETTINGS_CHANGED, entity="demand", details={"recalculated": count})
        self.events.publish(TOPIC_DATA_CHANGED, section="demand")
        return count

    def compute_breakdown(self, game: models.Game, ctx: DemandContext) -> dict[str, float]:
        return {factor.name: round(factor.score(game, ctx), 2) for factor in self.factors}

    def grade_for(self, game: models.Game, demand: float) -> DemandGrade:
        if game.is_free or game.eligibility_status == EligibilityStatus.FREE.value:
            return DemandGrade.FREE
        thresholds = self.config.grade_thresholds
        if demand >= thresholds.s:
            return DemandGrade.S
        if demand >= thresholds.a:
            return DemandGrade.A
        if demand >= thresholds.b:
            return DemandGrade.B
        if demand >= thresholds.c:
            return DemandGrade.C
        return DemandGrade.D

    # -------------------------------------------------------------- внутри
    def _commercial(self, game, demand: float, ctx: DemandContext, cw) -> float:
        price_norm = 0.0 if game.is_free else min(1.0, (game.price or 0.0) / 2000.0)
        free_copies_norm = min(1.0, ctx.free_copies.get(game.id, 0) / 3.0)
        competition = 0.0  # данные конкурентов — только через адаптер, по умолчанию нейтрально
        raw = (
            demand * cw.demand
            + price_norm * 100.0 * cw.price
            + free_copies_norm * 100.0 * cw.free_copies
            + competition * cw.competition
        )
        ceiling = 100.0 * max(0.0001, cw.demand + cw.price + cw.free_copies)
        return max(0.0, min(100.0, raw / ceiling * 100.0))

    def _build_context(self, session) -> DemandContext:
        now = self.clock.now()
        since = now - timedelta(days=90)
        sales_rows = session.execute(
            select(models.Order.game_id, func.count(models.Order.id))
            .where(models.Order.purchased_at >= since, models.Order.game_id.is_not(None))
            .group_by(models.Order.game_id)
        ).all()
        sales = {int(game_id): int(cnt) for game_id, cnt in sales_rows if game_id is not None}

        lease_rows = session.execute(
            select(models.AccessLease.game_id, func.count(models.AccessLease.id))
            .where(models.AccessLease.status.in_(["scheduled", "active", "expiring"]),
                   models.AccessLease.expires_at > now)
            .group_by(models.AccessLease.game_id)
        ).all()
        busy = {int(game_id): int(cnt) for game_id, cnt in lease_rows if game_id is not None}

        total_rows = session.execute(
            select(models.GameLicense.game_id, func.count(models.GameLicense.id))
            .group_by(models.GameLicense.game_id)
        ).all()
        free_copies = {
            int(game_id): max(0, int(cnt) - busy.get(int(game_id), 0))
            for game_id, cnt in total_rows
        }
        return DemandContext(sales_last_90d=sales, free_copies=free_copies)
