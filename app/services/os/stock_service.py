"""Универсальный цифровой склад: StockUnit + атомарное резервирование (ТЗ §22–23).

Инвариант: один готовый юнит может быть зарезервирован только одним заказом.
Гарантия — условный UPDATE в транзакции + проверка количества изменённых строк:
при двух одновременных заказах товар получит ровно один.
"""
from __future__ import annotations

import logging

from sqlalchemy import func, select, update

from app.core.clock import Clock
from app.core.events import TOPIC_DATA_CHANGED, EventBus
from app.database import models, repositories
from app.database.session import Database
from app.domain.enums import Actor, StockStatus
from app.domain.value_objects import DomainError
from app.services.audit_service import AuditService

log = logging.getLogger(__name__)


class StockUnavailable(DomainError):
    """Нет свободного юнита для резервирования."""


class StockService:
    def __init__(self, db: Database, clock: Clock, events: EventBus, audit: AuditService) -> None:
        self.db = db
        self.clock = clock
        self.events = events
        self.audit = audit

    # ------------------------------------------------------------------ CRUD
    def add_unit(self, product_id: int, payload_ref: str | None = None,
                 status: StockStatus = StockStatus.READY, notes: str | None = None) -> models.StockUnit:
        with self.db.session() as session:
            unit = models.StockUnit(product_id=product_id, payload_ref=payload_ref,
                                    status=status.value, notes=notes)
            session.add(unit)
            session.flush()
            unit_id = unit.id
            self.audit.log("stock.unit_added", Actor.USER, entity="stock_unit", entity_id=unit_id,
                            session=session, product_id=product_id)
        self.events.publish(TOPIC_DATA_CHANGED, section="stock")
        return self.get(unit_id)

    def get(self, unit_id: int) -> models.StockUnit | None:
        with self.db.session() as session:
            return session.get(models.StockUnit, unit_id)

    def for_product(self, product_id: int) -> list[models.StockUnit]:
        with self.db.session() as session:
            return list(session.scalars(
                select(models.StockUnit).where(models.StockUnit.product_id == product_id)
            ))

    def counts(self, product_id: int) -> dict[str, int]:
        with self.db.session() as session:
            rows = session.execute(
                select(models.StockUnit.status, func.count(models.StockUnit.id))
                .where(models.StockUnit.product_id == product_id)
                .group_by(models.StockUnit.status)
            ).all()
        result = {status.value: 0 for status in StockStatus}
        for status, count in rows:
            result[status] = int(count)
        return result

    # -------------------------------------------------------- атомарные переходы
    def reserve_for_order(self, product_id: int, order_id: int) -> models.StockUnit:
        """Резервирует ОДИН готовый юнит. Бросает StockUnavailable, если юнит
        уже ушёл другому заказу (гонка двух заказов — только один победитель)."""
        now = self.clock.now()
        with self.db.session() as session:
            candidate = session.scalar(
                select(models.StockUnit)
                .where(models.StockUnit.product_id == product_id,
                       models.StockUnit.status == StockStatus.READY.value)
                .order_by(models.StockUnit.id)
                .limit(1)
            )
            if candidate is None:
                raise StockUnavailable("Нет готового товара на складе для этого продукта.")
            result = session.execute(
                update(models.StockUnit)
                .where(models.StockUnit.id == candidate.id,
                       models.StockUnit.status == StockStatus.READY.value)
                .values(status=StockStatus.RESERVED.value, reserved_at=now, order_id=order_id)
            )
            if result.rowcount != 1:  # pragma: no cover - гонка между транзакциями
                raise StockUnavailable("Юнит был зарезервирован другим заказом одновременно.")
            unit_id = candidate.id
            self.audit.log("stock.reserved", Actor.APP, entity="stock_unit", entity_id=unit_id,
                            session=session, order_id=order_id, product_id=product_id)
        self.events.publish(TOPIC_DATA_CHANGED, section="stock")
        return self.get(unit_id)

    def release(self, unit_id: int, reason: str = "") -> None:
        """RESERVED -> READY (отмена заказа)."""
        with self.db.session() as session:
            result = session.execute(
                update(models.StockUnit)
                .where(models.StockUnit.id == unit_id,
                       models.StockUnit.status == StockStatus.RESERVED.value)
                .values(status=StockStatus.READY.value, reserved_at=None, order_id=None)
            )
            if result.rowcount == 1:
                self.audit.log("stock.released", Actor.APP, entity="stock_unit", entity_id=unit_id,
                                session=session, reason=reason)
        self.events.publish(TOPIC_DATA_CHANGED, section="stock")

    def mark_sold(self, unit_id: int, order_id: int | None = None) -> None:
        now = self.clock.now()
        with self.db.session() as session:
            result = session.execute(
                update(models.StockUnit)
                .where(models.StockUnit.id == unit_id,
                       models.StockUnit.status.in_([StockStatus.RESERVED.value, StockStatus.READY.value]))
                .values(status=StockStatus.SOLD.value, sold_at=now, order_id=order_id)
            )
            if result.rowcount == 1:
                self.audit.log("stock.sold", Actor.APP, entity="stock_unit", entity_id=unit_id,
                                session=session, order_id=order_id)
        self.events.publish(TOPIC_DATA_CHANGED, section="stock")

    def mark_problem(self, unit_id: int, reason: str) -> None:
        with self.db.session() as session:
            session.execute(
                update(models.StockUnit)
                .where(models.StockUnit.id == unit_id)
                .values(status=StockStatus.PROBLEM.value, notes=reason)
            )
        self.events.publish(TOPIC_DATA_CHANGED, section="stock")

    # ----------------------------------------------------- пополнение (ТЗ §46)
    def check_replenishment(self, products: list[models.Product]) -> list[models.ReplenishmentTask]:
        """ready < target → задача пополнения (не чаще одной открытой на продукт)."""
        created: list[models.ReplenishmentTask] = []
        with self.db.session() as session:
            for product in products:
                if not product.active or product.target_stock <= 0:
                    continue
                ready = session.scalar(
                    select(func.count(models.StockUnit.id)).where(
                        models.StockUnit.product_id == product.id,
                        models.StockUnit.status == StockStatus.READY.value)
                ) or 0
                if ready >= product.target_stock:
                    continue
                open_task = session.scalar(
                    select(models.ReplenishmentTask).where(
                        models.ReplenishmentTask.product_id == product.id,
                        models.ReplenishmentTask.status == "open")
                )
                if open_task is not None:
                    continue
                quantity = max(1, product.target_stock - int(ready))
                task = models.ReplenishmentTask(product_id=product.id, quantity=quantity,
                                                notes=f"ready={ready} < target={product.target_stock}")
                session.add(task)
                session.flush()
                created.append(task)
                self.audit.log("stock.replenishment_task", Actor.APP, entity="replenishment_task",
                                entity_id=task.id, session=session, product_id=product.id,
                                quantity=quantity)
        if created:
            self.events.publish(TOPIC_DATA_CHANGED, section="stock")
        return created

    def open_replenishments(self) -> list[models.ReplenishmentTask]:
        with self.db.session() as session:
            return list(session.scalars(
                select(models.ReplenishmentTask).where(models.ReplenishmentTask.status == "open")
            ))

    def close_replenishment(self, task_id: int) -> None:
        with self.db.session() as session:
            task = session.get(models.ReplenishmentTask, task_id)
            if task is not None:
                task.status = "done"
