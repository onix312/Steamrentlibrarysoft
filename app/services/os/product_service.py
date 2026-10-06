"""Каталог продуктов (ТЗ §7–9): типы, уровни автоматизации, фабрики шаблонов."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select

from app.core.clock import Clock
from app.core.events import TOPIC_DATA_CHANGED, EventBus
from app.database import models
from app.database.session import Database
from app.domain.enums import Actor, AutomationLevel, ProductType
from app.services.audit_service import AuditService
from app.services.os.product_packs import ProductPackService


class ProductService:
    def __init__(self, db: Database, clock: Clock, events: EventBus, audit: AuditService) -> None:
        self.db = db
        self.clock = clock
        self.events = events
        self.audit = audit
        self.packs = ProductPackService(db)

    # ------------------------------------------------------------------ CRUD
    def create(self, **fields) -> models.Product:
        with self.db.session() as session:
            existing = session.scalar(select(models.Product).where(models.Product.code == fields["code"]))
            if existing is not None:
                raise ValueError(f"Продукт с кодом {fields['code']} уже существует.")
            product = models.Product(**fields)
            session.add(product)
            session.flush()
            product_id = product.id
            self.audit.log("product.created", Actor.USER, entity="product", entity_id=product_id,
                            session=session, code=product.code, type=product.type)
        self.events.publish(TOPIC_DATA_CHANGED, section="products")
        return self.get(product_id)

    def get(self, product_id: int) -> models.Product | None:
        with self.db.session() as session:
            return session.get(models.Product, product_id)

    def by_code(self, code: str) -> models.Product | None:
        with self.db.session() as session:
            return session.scalar(select(models.Product).where(models.Product.code == code))

    def all(self, active_only: bool = False) -> list[models.Product]:
        with self.db.session() as session:
            stmt = select(models.Product).order_by(models.Product.id)
            if active_only:
                stmt = stmt.where(models.Product.active.is_(True))
            return list(session.scalars(stmt))

    def set_active(self, product_id: int, active: bool) -> None:
        with self.db.session() as session:
            product = session.get(models.Product, product_id)
            if product is not None:
                product.active = active
        self.events.publish(TOPIC_DATA_CHANGED, section="products")

    # ---------------------------------------------------------------- фабрика
    def factory_games(self) -> list[str]:
        """Игры из versioned JSON product packs."""
        return self.packs.games()

    def create_from_factory(self, game: str) -> list[models.Product]:
        """Импортирует/обновляет pack для игры, сохраняя ручные цены."""
        self.packs.import_game(game)
        with self.db.session() as session:
            return list(session.scalars(
                select(models.Product)
                .where(models.Product.game == game)
                .order_by(models.Product.id)
            ))

    def import_product_packs(self) -> dict:
        return self.packs.import_all()

    # ---------------------------------------------------------------- метрики
    def metrics(self, product_id: int) -> dict:
        """Продажи, выручка, возвраты, прибыль/час (ТЗ §48)."""
        now = self.clock.now()
        with self.db.session() as session:
            orders = session.scalars(
                select(models.Order).where(models.Order.product_id == product_id)
            ).all()
            product = session.get(models.Product, product_id)
        sales = [o for o in orders if o.status not in ("cancelled",)]
        refunds = [o for o in orders if o.status == "cancelled"]
        revenue = sum(o.price for o in sales)
        manual_minutes = (product.estimated_manual_minutes or 0) * len(sales) if product else 0
        profit = revenue - (product.cost or 0.0) * len(sales) if product else revenue
        hours = manual_minutes / 60.0
        return {
            "sales": len(sales),
            "revenue": revenue,
            "refunds": len(refunds),
            "manual_minutes": manual_minutes,
            "automation_pct": 0.0 if manual_minutes == 0 and not sales else (
                100.0 if manual_minutes == 0 else max(0.0, 100.0 - manual_minutes / max(1, len(sales)) )),
            "profit": profit,
            "profit_per_hour": profit / hours if hours > 0 else profit,
        }

    def sold_last(self, product_id: int, hours: int) -> int:
        since = self.clock.now() - timedelta(hours=hours)
        with self.db.session() as session:
            return int(session.scalar(
                select(func.count(models.Order.id)).where(
                    models.Order.product_id == product_id,
                    models.Order.purchased_at >= since,
                    models.Order.status.not_in(["cancelled"]))
            ) or 0)

    # ------------------------------------------------ kill/scale (ТЗ §49)
    def recommendation(self, product_id: int) -> str:
        metrics = self.metrics(product_id)
        product = self.get(product_id)
        if product is None:
            return "KEEP"
        if metrics["sales"] == 0:
            return "OPTIMIZE" if product.active else "KILL"
        if metrics["refunds"] / max(1, metrics["sales"]) > 0.3:
            return "OPTIMIZE"
        if metrics["manual_minutes"] == 0 and metrics["sales"] >= 10:
            return "SCALE"
        if metrics["sales"] >= 5 and metrics["manual_minutes"] <= 5 * metrics["sales"]:
            return "SCALE"
        if metrics["sales"] < 3:
            return "KEEP"
        return "KEEP"
