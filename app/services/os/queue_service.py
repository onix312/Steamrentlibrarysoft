"""Операторы ручных услуг + очередь исполнения «сейчас/сегодня/завтра/ожидание»
(ТЗ §39, §40).

Правила планирования:
* ``сейчас`` — срок уже наступил или прошёл;
* ``сегодня`` — срок в пределах текущего дня;
* ``завтра`` — срок завтра;
* ``ожидание`` — без срока или позже завтрашнего дня.

Назначение оператора не блокирует автоматические воркфлоу: это план для
людей, исполняющих ручные шаги.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import func, select, update

from app.core.clock import Clock
from app.core.events import TOPIC_DATA_CHANGED, EventBus
from app.database import models
from app.database.session import Database
from app.domain.enums import Actor, OrderStatus
from app.domain.value_objects import DomainError
from app.services.audit_service import AuditService

log = logging.getLogger(__name__)

QUEUE_NOW = "now"
QUEUE_TODAY = "today"
QUEUE_TOMORROW = "tomorrow"
QUEUE_WAITING = "waiting"
QUEUE_ORDER = [QUEUE_NOW, QUEUE_TODAY, QUEUE_TOMORROW, QUEUE_WAITING]


class QueueService:
    def __init__(self, db: Database, clock: Clock, events: EventBus, audit: AuditService) -> None:
        self.db = db
        self.clock = clock
        self.events = events
        self.audit = audit

    # ------------------------------------------------------------- операторы
    def add_operator(self, name: str, skills: list[str] | None = None,
                     games: list[str] | None = None, schedule: str | None = None,
                     cost_per_order: float = 0.0) -> models.Operator:
        with self.db.session() as session:
            operator = models.Operator(
                name=name, skills=skills or [], games=games or [],
                schedule=schedule, cost_per_order=float(cost_per_order),
            )
            session.add(operator)
            session.flush()
            self.audit.log("operator.added", Actor.USER, entity="operator",
                            entity_id=operator.id, session=session, name=name)
            operator_id = operator.id
        self.events.publish(TOPIC_DATA_CHANGED, section="operators")
        return self._get(models.Operator, operator_id)

    def operators(self, active_only: bool = False) -> list[models.Operator]:
        with self.db.session() as session:
            stmt = select(models.Operator).order_by(models.Operator.id)
            if active_only:
                stmt = stmt.where(models.Operator.active.is_(True))
            return list(session.scalars(stmt))

    def set_operator_active(self, operator_id: int, active: bool) -> None:
        with self.db.session() as session:
            operator = session.get(models.Operator, operator_id)
            if operator is not None:
                operator.active = active

    def workload(self) -> dict[int, int]:
        """Открытые назначения по операторам."""
        with self.db.session() as session:
            rows = session.execute(
                select(models.OrderAssignment.operator_id,
                       func.count(models.OrderAssignment.id))
                .where(models.OrderAssignment.status == "open")
                .group_by(models.OrderAssignment.operator_id)
            ).all()
        return {operator_id: int(count) for operator_id, count in rows}

    def suggest_operator(self, game: str | None = None) -> models.Operator | None:
        """Свободнейший активный оператор (по игре — при совпадении специализации)."""
        operators = self.operators(active_only=True)
        if not operators:
            return None
        workload = self.workload()
        if game:
            preferred = [op for op in operators if game in (op.games or [])]
            if preferred:
                operators = preferred
        return min(operators, key=lambda op: (workload.get(op.id, 0), op.id))

    # ------------------------------------------------------------ назначения
    def assign(self, order_id: int, operator_id: int | None = None,
               scheduled_for=None, notes: str | None = None) -> models.OrderAssignment:
        """Назначает заказ оператору (или автоподбором) и ставит в очередь."""
        with self.db.session() as session:
            order = session.get(models.Order, order_id)
            if order is None:
                raise DomainError("Заказ не найден.")
        if operator_id is None:
            with self.db.session() as session:
                product = None
                order = session.get(models.Order, order_id)
                if order is not None and order.product_id is not None:
                    product = session.get(models.Product, order.product_id)
            game = product.game if product else None
            suggested = self.suggest_operator(game)
            if suggested is None:
                raise DomainError("Нет активных операторов для назначения.")
            operator_id = suggested.id
        with self.db.session() as session:
            assignment = models.OrderAssignment(
                order_id=order_id, operator_id=operator_id,
                scheduled_for=scheduled_for, notes=notes,
            )
            session.add(assignment)
            order = session.get(models.Order, order_id)
            if order is not None and order.status == OrderStatus.NEW.value:
                order.status = OrderStatus.QUEUED.value
            session.flush()
            assignment_id = assignment.id
            self.audit.log("order.assigned", Actor.USER, entity="order_assignment",
                            entity_id=assignment_id, session=session,
                            order_id=order_id, operator_id=operator_id)
        self.events.publish(TOPIC_DATA_CHANGED, section="queue")
        return self._get(models.OrderAssignment, assignment_id)

    def reschedule(self, assignment_id: int, scheduled_for) -> None:
        with self.db.session() as session:
            assignment = session.get(models.OrderAssignment, assignment_id)
            if assignment is not None:
                assignment.scheduled_for = scheduled_for
        self.events.publish(TOPIC_DATA_CHANGED, section="queue")

    def complete_assignment(self, assignment_id: int) -> None:
        with self.db.session() as session:
            assignment = session.get(models.OrderAssignment, assignment_id)
            if assignment is None:
                return
            assignment.status = "done"
            assignment.finished_at = self.clock.now()
            self.audit.log("assignment.done", Actor.USER, entity="order_assignment",
                            entity_id=assignment_id, session=session)
        self.events.publish(TOPIC_DATA_CHANGED, section="queue")

    def cancel_assignment(self, assignment_id: int) -> None:
        with self.db.session() as session:
            result = session.execute(
                update(models.OrderAssignment)
                .where(models.OrderAssignment.id == assignment_id,
                       models.OrderAssignment.status == "open")
                .values(status="cancelled")
            )
            if result.rowcount == 1:
                self.audit.log("assignment.cancelled", Actor.USER, entity="order_assignment",
                                entity_id=assignment_id, session=session)
        self.events.publish(TOPIC_DATA_CHANGED, section="queue")

    # ---------------------------------------------------------------- очередь
    def bucket_of(self, scheduled_for, now=None) -> str:
        if scheduled_for is None:
            return QUEUE_WAITING
        now = now or self.clock.now()
        if scheduled_for.tzinfo is None:
            from datetime import timezone
            scheduled_for = scheduled_for.replace(tzinfo=timezone.utc)
        if scheduled_for <= now:
            return QUEUE_NOW
        today_end = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        if scheduled_for < today_end:
            return QUEUE_TODAY
        if scheduled_for < today_end + timedelta(days=1):
            return QUEUE_TOMORROW
        return QUEUE_WAITING

    def queue_view(self) -> dict[str, list[dict]]:
        """Очередь исполнения с данными заказа/продукта/оператора."""
        now = self.clock.now()
        view: dict[str, list[dict]] = {key: [] for key in QUEUE_ORDER}
        with self.db.session() as session:
            assignments = session.scalars(
                select(models.OrderAssignment).where(models.OrderAssignment.status == "open")
            ).all()
            for assignment in assignments:
                order = session.get(models.Order, assignment.order_id)
                operator = session.get(models.Operator, assignment.operator_id)
                product = None
                if order is not None and order.product_id is not None:
                    product = session.get(models.Product, order.product_id)
                item = {
                    "assignment_id": assignment.id,
                    "order_id": assignment.order_id,
                    "operator": operator.name if operator else "?",
                    "product": product.name if product else (order.notes or "—" if order else "—"),
                    "price": order.price if order else 0.0,
                    "scheduled_for": assignment.scheduled_for,
                    "order_status": order.status if order else "?",
                }
                view[self.bucket_of(assignment.scheduled_for, now)].append(item)
        for key in view:
            view[key].sort(key=lambda item: (item["scheduled_for"] is None,
                                             item["scheduled_for"] or now, item["order_id"]))
        return view

    def overdue_count(self) -> int:
        return len(self.queue_view()[QUEUE_NOW])

    # -------------------------------------------------------------- служебное
    def _get(self, model, entity_id: int):
        with self.db.session() as session:
            entity = session.get(model, entity_id)
            if entity is not None:
                session.expunge(entity)
            return entity
