"""Заказы: ручной ввод и связка с доступами (ТЗ §10)."""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select

from app.core.clock import Clock
from app.core.events import TOPIC_DATA_CHANGED, EventBus
from app.database import models, repositories
from app.database.session import Database
from app.domain.enums import Actor, AuditAction, OrderStatus
from app.domain.value_objects import AllocationError, DomainError
from app.services.access_service import AccessService
from app.services.audit_service import AuditService

log = logging.getLogger(__name__)


class OrderService:
    def __init__(self, db: Database, clock: Clock, events: EventBus,
                 audit: AuditService, access: AccessService) -> None:
        self.db = db
        self.clock = clock
        self.events = events
        self.audit = audit
        self.access = access

    # ------------------------------------------------------------------ API
    def create_order(
        self,
        client_username: str,
        game_id: int | None = None,
        bundle_id: int | None = None,
        price: float = 0.0,
        access_days: int = 30,
        funpay_order_id: str | None = None,
        notes: str | None = None,
        auto_assign: bool = True,
        purchased_at: datetime | None = None,
        created_by: str = "manual",
    ) -> models.Order:
        if game_id is None and bundle_id is None:
            raise DomainError("Заказ должен ссылаться на игру или пакет.")
        with self.db.session() as session:
            client_repo = repositories.ClientRepo(session)
            client = client_repo.get_or_create(client_username)
            if client.blacklisted:
                raise DomainError(f"Клиент {client_username} в чёрном списке — заказ заблокирован.")

            if funpay_order_id:
                existing = repositories.OrderRepo(session).by_funpay_id(funpay_order_id)
                if existing is not None:
                    raise DomainError(f"Заказ FunPay {funpay_order_id} уже импортирован.")

            order = models.Order(
                funpay_order_id=funpay_order_id or None,
                client_id=client.id,
                game_id=game_id,
                bundle_id=bundle_id,
                price=float(price),
                access_days=int(access_days),
                status=OrderStatus.NEW.value,
                purchased_at=purchased_at or self.clock.now(),
                notes=notes,
                created_by=created_by,
            )
            session.add(order)
            session.flush()

            self.audit.log(
                AuditAction.ORDER_CREATED, Actor.USER, entity="order", entity_id=order.id,
                session=session, client=client_username, game_id=game_id,
                bundle_id=bundle_id, price=price, funpay=funpay_order_id,
            )
            self._touch_client_stats(session, client)
            order_id = order.id
            client_id = client.id

        # Назначение лицензии — отдельной транзакцией, чтобы ошибка аллокатора
        # не откатывала сам факт заказа (заказ остаётся в статусе «Новый»).
        if auto_assign and game_id is not None:
            try:
                self.access.create_lease(
                    client_id=client_id, game_id=game_id, days=access_days, order_id=order_id
                )
            except AllocationError as exc:
                log.warning("Заказ %s создан, но лицензия не назначена: %s", order_id, exc)
                self.set_status(order_id, OrderStatus.PROBLEM, note=str(exc))
        return self.get(order_id)

    def get(self, order_id: int) -> models.Order | None:
        with self.db.session() as session:
            return repositories.OrderRepo(session).get(order_id)

    def all(self) -> list[models.Order]:
        with self.db.session() as session:
            return list(repositories.OrderRepo(session).all())

    def set_status(self, order_id: int, status: OrderStatus, note: str | None = None) -> None:
        with self.db.session() as session:
            order = repositories.OrderRepo(session).get(order_id)
            if order is None:
                return
            old = order.status
            order.status = status.value
            if note:
                order.notes = f"{order.notes}\n{note}" if order.notes else note
            self.audit.log(AuditAction.ORDER_STATUS_CHANGED, Actor.USER, entity="order",
                            entity_id=order_id, session=session, old=old, new=status.value)
        self.events.publish(TOPIC_DATA_CHANGED, section="orders")

    def import_funpay_orders(self, dtos: list) -> int:
        """Импорт заказов из адаптера (для будущих доступных возможностей)."""
        created = 0
        for dto in dtos:
            try:
                self.create_order(
                    client_username=dto.buyer_username,
                    price=dto.price,
                    funpay_order_id=dto.external_id,
                    purchased_at=dto.purchased_at,
                    created_by="funpay",
                )
                created += 1
            except DomainError as exc:
                log.warning("Импорт заказа %s пропущен: %s", dto.external_id, exc)
        return created

    # -------------------------------------------------------------- внутри
    @staticmethod
    def _touch_client_stats(session, client: models.Client) -> None:
        orders = session.scalars(
            select(models.Order).where(models.Order.client_id == client.id)
        ).all()
        active_statuses = {"new", "processing", "waiting_client", "steam_setup", "active", "expiring"}
        completed = [o for o in orders if o.status != "cancelled"]
        client.first_order_at = min((o.purchased_at for o in completed), default=None)
        client.last_order_at = max((o.purchased_at for o in completed), default=None)
        client.total_spent = sum(o.price for o in completed)
