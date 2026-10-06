"""Связка «заказ → продукт → склад → workflow» (ТЗ §35–37)."""
from __future__ import annotations

import logging

from app.core.clock import Clock
from app.core.config import AppConfig
from app.core.events import TOPIC_DATA_CHANGED, EventBus
from app.database import models, repositories
from app.database.session import Database
from app.domain.enums import (
    Actor,
    AuditAction,
    AutomationMode,
    OrderStatus,
    RunStatus,
)
from app.domain.value_objects import DomainError
from app.services.audit_service import AuditService
from app.services.os.product_service import ProductService
from app.services.os.stock_service import StockService
from app.services.os.workflow_engine import WorkflowEngine

log = logging.getLogger(__name__)


class SalesService:
    def __init__(self, db: Database, clock: Clock, events: EventBus, audit: AuditService,
                 products: ProductService, stock: StockService, engine: WorkflowEngine,
                 config: AppConfig) -> None:
        self.db = db
        self.clock = clock
        self.events = events
        self.audit = audit
        self.products = products
        self.stock = stock
        self.engine = engine
        self.config = config

    # ------------------------------------------------------------------ заказ
    def create_product_order(
        self,
        customer_username: str,
        product_code: str,
        price: float | None = None,
        funpay_order_id: str | None = None,
        context: dict | None = None,
    ) -> models.Order:
        product = self.products.by_code(product_code)
        if product is None:
            raise DomainError(f"Продукт не найден: {product_code}")
        if not product.active:
            raise DomainError(f"Продукт {product_code} неактивен.")

        with self.db.session() as session:
            client = repositories.ClientRepo(session).get_or_create(customer_username)
            if client.blacklisted:
                raise DomainError(f"Клиент {customer_username} в чёрном списке.")
            if funpay_order_id:
                existing = repositories.OrderRepo(session).by_funpay_id(funpay_order_id)
                if existing is not None:
                    raise DomainError(f"Заказ {funpay_order_id} уже зарегистрирован.")
            order = models.Order(
                funpay_order_id=funpay_order_id,
                client_id=client.id,
                product_id=product.id,
                price=product.price if price is None else float(price),
                status=OrderStatus.NEW.value,
                purchased_at=self.clock.now(),
                created_by="funpay" if funpay_order_id else "manual",
            )
            session.add(order)
            session.flush()
            self.audit.log(AuditAction.ORDER_CREATED, Actor.APP, entity="order",
                            entity_id=order.id, session=session,
                            product=product.code, customer=customer_username)
            order_id = order.id

        mode = AutomationMode(self.config.automation_mode)
        if mode != AutomationMode.OFF and product.workflow_code:
            self.engine.start(order_id, product.id, product.workflow_code, context=context)
        return self._get_order(order_id)

    # -------------------------------------------------------------- отмена
    def cancel_order(self, order_id: int, reason: str = "") -> None:
        with self.db.session() as session:
            order = repositories.OrderRepo(session).get(order_id)
            if order is None:
                return
            run_id = order.workflow_run_id
        if run_id:
            self.engine.cancel(run_id, reason=reason)  # освободит резерв склада
        with self.db.session() as session:
            order = repositories.OrderRepo(session).get(order_id)
            if order is not None and order.status != OrderStatus.COMPLETED.value:
                order.status = OrderStatus.CANCELLED.value
            self.audit.log(AuditAction.ORDER_STATUS_CHANGED, Actor.USER, entity="order",
                            entity_id=order_id, session=session, new_status="cancelled", reason=reason)
        self.events.publish(TOPIC_DATA_CHANGED, section="orders")

    # ------------------------------------------------------------- подтверждение выдачи
    def confirm_delivery(self, order_id: int) -> None:
        """Оператор фактически отправил товар клиенту."""
        with self.db.session() as session:
            order = repositories.OrderRepo(session).get(order_id)
            if order is None:
                return
            order.status = OrderStatus.DELIVERED.value
            run_id = order.workflow_run_id
            self.audit.log("order.delivery_confirmed", Actor.USER, entity="order",
                            entity_id=order_id, session=session)
        if run_id is not None:
            run = self.engine.get_run(run_id)
            if run is not None and run.status == RunStatus.WAITING_MANUAL.value:
                self.engine.complete_manual(run_id, {"delivered": True})
        self.events.publish(TOPIC_DATA_CHANGED, section="orders")

    # ------------------------------------------------------- закрытие извне
    def complete_order(self, order_id: int, reason: str = "") -> None:
        """Заказ закрыт на стороне маркетплейса: завершаем прогон и помечаем
        заказ выполненным (товар считается выданным)."""
        with self.db.session() as session:
            order = repositories.OrderRepo(session).get(order_id)
            if order is None or order.status == OrderStatus.COMPLETED.value:
                return
            run_id = order.workflow_run_id
        if run_id is not None:
            self.engine.complete_run(run_id, reason=reason or "закрыт на маркетплейсе")
        with self.db.session() as session:
            order = repositories.OrderRepo(session).get(order_id)
            if order is not None and order.status != OrderStatus.COMPLETED.value:
                order.status = OrderStatus.COMPLETED.value
            self.audit.log(AuditAction.ORDER_STATUS_CHANGED, Actor.APP, entity="order",
                            entity_id=order_id, session=session,
                            new_status="completed", reason=reason or "внешнее закрытие")
        self.events.publish(TOPIC_DATA_CHANGED, section="orders")

    def _get_order(self, order_id: int) -> models.Order | None:
        with self.db.session() as session:
            return repositories.OrderRepo(session).get(order_id)
