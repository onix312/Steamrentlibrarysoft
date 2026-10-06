"""Аналитика FunPay Automation OS (ТЗ §48–49).

Считает по заказам, связанным с продуктами:
* выручка и прибыль по типам AUTO / SEMI_AUTO / MANUAL;
* ручное время и прибыль/час;
* рекомендации KILL / OPTIMIZE / SCALE / KEEP по продуктам.

Данные — только из локальной БД; офлайн-первый подход.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import select

from app.core.clock import Clock
from app.database import models
from app.database.session import Database
from app.services.os.product_service import ProductService

log = logging.getLogger(__name__)

CANCELLED = "cancelled"
PAID_STATUSES = ("completed", "ready_to_deliver", "delivered", "active", "processing")


class OSAnalyticsService:
    def __init__(self, db: Database, clock: Clock, products: ProductService) -> None:
        self.db = db
        self.clock = clock
        self.products = products

    def summary(self, days: int = 30) -> dict:
        since = self.clock.now() - timedelta(days=days)
        with self.db.session() as session:
            orders = session.scalars(
                select(models.Order).where(models.Order.product_id.is_not(None),
                                           models.Order.purchased_at >= since)
            ).all()
            products = {p.id: p for p in session.scalars(select(models.Product))}

        by_type: dict[str, dict] = {}
        for product_type in ("auto", "semi_auto", "manual"):
            by_type[product_type] = {"sales": 0, "revenue": 0.0, "manual_minutes": 0.0}

        total_revenue = 0.0
        total_manual_minutes = 0.0
        refunds = 0
        for order in orders:
            product = products.get(order.product_id)
            if product is None:
                continue
            if order.status == CANCELLED:
                refunds += 1
                continue
            stats = by_type.setdefault(product.type, {"sales": 0, "revenue": 0.0, "manual_minutes": 0.0})
            stats["sales"] += 1
            stats["revenue"] += float(order.price or 0.0)
            stats["manual_minutes"] += float(product.estimated_manual_minutes or 0)
            total_revenue += float(order.price or 0.0)
            total_manual_minutes += float(product.estimated_manual_minutes or 0)

        cost = 0.0
        for order in orders:
            product = products.get(order.product_id)
            if product is None or order.status == CANCELLED:
                continue
            cost += float(product.cost or 0.0)
        profit = total_revenue - cost
        manual_hours = total_manual_minutes / 60.0

        return {
            "days": days,
            "sales": sum(stats["sales"] for stats in by_type.values()),
            "refunds": refunds,
            "revenue": round(total_revenue, 2),
            "cost": round(cost, 2),
            "profit": round(profit, 2),
            "manual_hours": round(manual_hours, 1),
            "profit_per_hour": round(profit / manual_hours, 2) if manual_hours > 0 else round(profit, 2),
            "by_type": {key: {**stats, "revenue": round(stats["revenue"], 2)}
                        for key, stats in by_type.items()},
        }

    def product_board(self) -> list[dict]:
        """Таблица продуктов с метриками и рекомендацией."""
        board = []
        for product in self.products.all():
            metrics = self.products.metrics(product.id)
            board.append({
                "product_id": product.id,
                "code": product.code,
                "name": product.name,
                "type": product.type,
                "sales": metrics["sales"],
                "revenue": round(metrics["revenue"], 2),
                "profit": round(metrics["profit"], 2),
                "profit_per_hour": round(metrics["profit_per_hour"], 2),
                "automation_pct": metrics["automation_pct"],
                "recommendation": self.products.recommendation(product.id),
            })
        board.sort(key=lambda row: -row["revenue"])
        return board
