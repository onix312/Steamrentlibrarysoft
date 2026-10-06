"""Market Radar + Pricing Engine + Opportunity Score (ТЗ §5, §6, §45).

Цены конкурентов собираются только из доступных публичных данных;
автоматический сбор не обходит защиты маркетплейса — ввод ручной
(см. docs/CAPABILITIES.md).
"""
from __future__ import annotations

from sqlalchemy import func, select

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


    # --------------------------------------------------------- Market Radar v2
    def timeline(self, game_name: str, limit: int = 100) -> list[models.MarketSnapshot]:
        """Chronological time series for one market."""
        with self.db.session() as session:
            rows = list(session.scalars(
                select(models.MarketSnapshot)
                .where(models.MarketSnapshot.game_name == game_name)
                .order_by(models.MarketSnapshot.captured_at.desc())
                .limit(limit)
            ))
        return list(reversed(rows))

    def trend(self, game_name: str, limit: int = 30) -> dict:
        rows = self.timeline(game_name, limit)
        if not rows:
            return {
                "samples": 0, "lowest_delta": 0.0, "median_delta": 0.0,
                "competitor_delta": 0, "new_competitors": 0, "left_competitors": 0,
            }
        first, last = rows[0], rows[-1]
        competitor_delta = int(last.competitors - first.competitors)
        return {
            "samples": len(rows),
            "lowest_delta": round(last.lowest_price - first.lowest_price, 2),
            "median_delta": round(last.median_price - first.median_price, 2),
            "listing_delta": int(last.listing_count - first.listing_count),
            "competitor_delta": competitor_delta,
            "new_competitors": max(0, competitor_delta),
            "left_competitors": max(0, -competitor_delta),
        }

    def sales_signal(self, game_name: str) -> dict:
        with self.db.session() as session:
            products = list(session.scalars(
                select(models.Product).where(models.Product.game == game_name)
            ))
            ids = [product.id for product in products]
            if not ids:
                return {"products": 0, "sales": 0, "revenue": 0.0}
            orders = list(session.scalars(
                select(models.Order).where(
                    models.Order.product_id.in_(ids),
                    models.Order.status != "cancelled",
                )
            ))
        return {
            "products": len(products),
            "sales": len(orders),
            "revenue": round(sum(order.price for order in orders), 2),
        }

    def opportunity_v2(self, game_name: str, *, manual_hours: float = 0.25,
                       demand_signal: float = 50.0, automation_pct: float = 90.0,
                       stock_deficit: int = 0) -> dict:
        snapshot = self.latest(game_name)
        if snapshot is None:
            return {"score": 0.0, "recommendation": "HOLD", "reason": "no market snapshots"}
        base = self.opportunity_score(
            median_price=snapshot.median_price,
            competitors=snapshot.competitors,
            watch_or_manual_hours=manual_hours,
            demand_signal=demand_signal,
            stock_deficit=stock_deficit,
            automation_pct=automation_pct,
        )
        trend = self.trend(game_name)
        sales = self.sales_signal(game_name)
        momentum = 0.0
        if trend["median_delta"] > 0:
            momentum += min(8.0, trend["median_delta"] / max(1.0, snapshot.median_price) * 100)
        if trend["competitor_delta"] < 0:
            momentum += min(8.0, abs(trend["competitor_delta"]) * 2.0)
        if sales["sales"] > 0:
            momentum += min(10.0, sales["sales"] * 1.5)
        score = round(max(0.0, min(100.0, base["score"] + momentum)), 1)

        has_product = sales["products"] > 0
        if score >= 72 and has_product and sales["sales"] >= 3:
            recommendation = "SCALE"
        elif score >= 68 and not has_product:
            recommendation = "CREATE"
        elif score < 28 and has_product and sales["sales"] == 0:
            recommendation = "KILL"
        else:
            recommendation = "HOLD"

        return {
            **base,
            "score": score,
            "recommendation": recommendation,
            "trend": trend,
            "sales": sales,
            "market": {
                "lowest": snapshot.lowest_price,
                "median": snapshot.median_price,
                "competitors": snapshot.competitors,
                "listings": snapshot.listing_count,
            },
        }

    def listing_price_suggestions(self, game_name: str) -> list[dict]:
        snapshot = self.latest(game_name)
        if snapshot is None:
            return []
        with self.db.session() as session:
            products = list(session.scalars(
                select(models.Product).where(
                    models.Product.game == game_name,
                    models.Product.active.is_(True),
                )
            ))
        result = []
        for product in products:
            result.append({
                "product_id": product.id,
                "code": product.code,
                "current": product.price,
                "suggested": self.suggest_price(product, PricingStrategy.BALANCED, snapshot),
                "minimum": product.minimum_price,
            })
        return result
