"""Market Radar + Pricing Engine + Opportunity Score (ТЗ §5, §6, §45).

Цены конкурентов собираются только из доступных публичных данных;
автоматический сбор не обходит защиты маркетплейса — ввод ручной
(см. docs/CAPABILITIES.md).
"""
from __future__ import annotations

from sqlalchemy import select

from app.core.clock import Clock
from app.core.events import TOPIC_DATA_CHANGED, EventBus
from app.database import models
from app.database.session import Database
from app.domain.enums import PricingStrategy
from app.services.audit_service import AuditService


class MarketService:
    def __init__(self, db: Database, clock: Clock, events: EventBus, audit: AuditService) -> None:
        self.db = db
        self.clock = clock
        self.events = events
        self.audit = audit

    # ------------------------------------------------------------- снапшоты
    def record_snapshot(self, game_name: str, competitors: int, lowest: float,
                        median: float, highest: float, listing_count: int = 0,
                        source: str = "manual") -> models.MarketSnapshot:
        with self.db.session() as session:
            snapshot = models.MarketSnapshot(
                game_name=game_name, captured_at=self.clock.now(),
                competitors=int(competitors), lowest_price=float(lowest),
                median_price=float(median), highest_price=float(highest),
                listing_count=int(listing_count), source=source,
            )
            session.add(snapshot)
            session.flush()
            snapshot_id = snapshot.id
            self.audit.log("market.snapshot", entity="market_snapshot", entity_id=snapshot_id,
                            session=session, game=game_name, median=median)
        self.events.publish(TOPIC_DATA_CHANGED, section="market")
        return self.latest(game_name)

    def latest(self, game_name: str) -> models.MarketSnapshot | None:
        with self.db.session() as session:
            return session.scalar(
                select(models.MarketSnapshot)
                .where(models.MarketSnapshot.game_name == game_name)
                .order_by(models.MarketSnapshot.id.desc())
                .limit(1)
            )

    def snapshots(self, limit: int = 50) -> list[models.MarketSnapshot]:
        with self.db.session() as session:
            return list(session.scalars(
                select(models.MarketSnapshot).order_by(models.MarketSnapshot.id.desc()).limit(limit)
            ))

    # ------------------------------------------------------- ценообразование
    def suggest_price(self, product: models.Product, strategy: PricingStrategy,
                      snapshot: models.MarketSnapshot | None = None) -> float:
        """Стратегии ТЗ §45 + ограничения минимум/ручное переопределение."""
        if snapshot is None and product.game:
            snapshot = self.latest(product.game)
        median = snapshot.median_price if snapshot else product.price
        lowest = snapshot.lowest_price if snapshot else product.price

        if strategy == PricingStrategy.AGGRESSIVE:
            price = max(1.0, lowest - 1.0)
        elif strategy == PricingStrategy.BALANCED:
            price = median * 0.95
        elif strategy == PricingStrategy.PREMIUM:
            price = median * 1.10
        else:  # MARKET
            price = median

        price = max(price, product.minimum_price or 0.0)
        return round(price, 2)

    # ------------------------------------------------------ opportunity score
    def opportunity_score(self, *, median_price: float, competitors: int,
                          watch_or_manual_hours: float, demand_signal: float = 50.0,
                          stock_deficit: int = 0, automation_pct: float = 100.0) -> dict:
        """0..100: цена, конкуренция, время, спрос, дефицит, автоматизация."""
        price_score = min(100.0, median_price / 5.0)                 # 500 ₽ ≈ 100
        competition_score = max(0.0, 100.0 - competitors * 8.0)      # 12+ продавцов ≈ 0
        time_score = max(0.0, 100.0 - watch_or_manual_hours * 12.5)  # 8 ч ≈ 0
        demand_score = max(0.0, min(100.0, demand_signal))
        deficit_score = min(100.0, max(0.0, stock_deficit) * 20.0)

        score = (
            price_score * 0.25 + competition_score * 0.25 + time_score * 0.20
            + demand_score * 0.15 + deficit_score * 0.05 + automation_pct * 0.10
        )
        score = round(max(0.0, min(100.0, score)), 1)
        if score >= 70:
            recommendation = "CREATE PRODUCT"
        elif score >= 45:
            recommendation = "WATCH"
        else:
            recommendation = "SKIP"
        value_per_hour = round(median_price / watch_or_manual_hours, 1) if watch_or_manual_hours > 0 else median_price
        return {
            "score": score,
            "recommendation": recommendation,
            "value_per_hour": value_per_hour,
            "factors": {
                "price": round(price_score, 1),
                "competition": round(competition_score, 1),
                "time": round(time_score, 1),
                "demand": round(demand_score, 1),
                "deficit": round(deficit_score, 1),
                "automation": round(automation_pct, 1),
            },
        }
