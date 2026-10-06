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

#: Шаблоны фабрики продуктов: порождают разные реальные продукты, не дубли.
FACTORY_TEMPLATES: dict[str, list[dict]] = {
    "Project Zomboid": [
        {"code": "PZ_CONFIG_BEGINNER", "name": "PZ Beginner Server Config", "type": "auto", "level": "A4",
         "price": 149, "manual": 0, "workflow": "auto_delivery", "template": {"kind": "config", "preset": "beginner"}},
        {"code": "PZ_CONFIG_HARDCORE", "name": "PZ Hardcore Server Config", "type": "auto", "level": "A4",
         "price": 149, "manual": 0, "workflow": "auto_delivery", "template": {"kind": "config", "preset": "hardcore"}},
        {"code": "PZ_CONFIG_COOP", "name": "PZ Co-op Server Config", "type": "auto", "level": "A4",
         "price": 179, "manual": 0, "workflow": "auto_delivery", "template": {"kind": "config", "preset": "coop"}},
        {"code": "PZ_MOD_QOL", "name": "PZ QoL Mod Setup", "type": "semi_auto", "level": "A3",
         "price": 499, "manual": 10, "workflow": "semi_auto_doctor", "template": {"kind": "mod_setup", "preset": "qol"}},
        {"code": "PZ_SERVER_SETUP", "name": "PZ Server Setup под ключ", "type": "semi_auto", "level": "A2",
         "price": 799, "manual": 25, "workflow": "server_setup", "template": {"kind": "server", "game": "project_zomboid"}},
        {"code": "PZ_SAVE_REPAIR", "name": "PZ Save Repair", "type": "semi_auto", "level": "A3",
         "price": 399, "manual": 15, "workflow": "semi_auto_doctor", "template": {"kind": "save_repair"}},
    ],
    "Minecraft": [
        {"code": "MC_SERVER_CONFIG", "name": "Minecraft Server Config", "type": "auto", "level": "A4",
         "price": 149, "manual": 0, "workflow": "auto_delivery", "template": {"kind": "config", "preset": "server"}},
        {"code": "MC_MODPACK_SETUP", "name": "Minecraft Modpack Setup", "type": "semi_auto", "level": "A2",
         "price": 699, "manual": 20, "workflow": "semi_auto_doctor", "template": {"kind": "mod_setup", "preset": "modpack"}},
    ],
}


class ProductService:
    def __init__(self, db: Database, clock: Clock, events: EventBus, audit: AuditService) -> None:
        self.db = db
        self.clock = clock
        self.events = events
        self.audit = audit

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
        return list(FACTORY_TEMPLATES)

    def create_from_factory(self, game: str) -> list[models.Product]:
        """Создаёт набор разных реальных продуктов по шаблонам фабрики игры."""
        templates = FACTORY_TEMPLATES.get(game, [])
        created: list[models.Product] = []
        for template in templates:
            try:
                product = self.create(
                    code=template["code"], name=template["name"], game=game,
                    type=template["type"], automation_level=template["level"],
                    price=float(template["price"]), minimum_price=float(template["price"]) * 0.7,
                    estimated_manual_minutes=int(template["manual"]),
                    workflow_code=template["workflow"],
                    stock_mode="stock" if template["type"] == "auto" else "none",
                    payload_template=template.get("template"),
                )
                created.append(product)
            except ValueError:
                continue  # уже создан ранее
        return created

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
