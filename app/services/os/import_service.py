"""Импорт заказов: сырой заказ → продукт → воркфлоу (ТЗ §35).

Автоматического доступа к заказам FunPay нет (см. Матрицу возможностей),
поэтому импорт работает с тем, что предоставил оператор/адаптер:
* совпадение по коду продукта в названии лота (``PZ_CONFIG_BEGINNER …``);
* совпадение по названию продукта (без учёта регистра);
* иначе заказ помечается ``не сопоставлен`` и ждёт ручной привязки.

Никаких сетевых вызовов здесь нет — только сопоставление и создание записей.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from app.core.clock import Clock
from app.core.events import TOPIC_DATA_CHANGED, EventBus
from app.database import models, repositories
from app.database.session import Database
from app.domain.enums import Actor, OrderStatus
from app.integrations.funpay.base import FunPayOrderDTO
from app.services.audit_service import AuditService
from app.services.os.sales_service import SalesService

log = logging.getLogger(__name__)


@dataclass
class ImportResult:
    imported: int = 0
    duplicated: int = 0
    unmatched: list[str] | None = None

    def __post_init__(self) -> None:
        if self.unmatched is None:
            self.unmatched = []

    @property
    def summary(self) -> str:
        parts = [f"импортировано {self.imported}"]
        if self.duplicated:
            parts.append(f"дубликатов пропущено {self.duplicated}")
        if self.unmatched:
            parts.append(f"без продукта {len(self.unmatched)} (нужна ручная привязка)")
        return ", ".join(parts)


class FunPayImportService:
    def __init__(self, db: Database, clock: Clock, events: EventBus,
                 audit: AuditService, sales: SalesService, notifications=None) -> None:
        self.db = db
        self.clock = clock
        self.events = events
        self.audit = audit
        self.sales = sales
        self.notifications = notifications

    def _notify(self, kind: str, title: str, body: str | None = None) -> None:
        if self.notifications is not None:
            self.notifications.notify(kind, title, body)

    # ------------------------------------------------------------- импорт
    def import_orders(self, orders: list[FunPayOrderDTO]) -> ImportResult:
        result = ImportResult()
        for dto in orders:
            outcome = self.import_order(dto)
            if outcome == "imported":
                result.imported += 1
            elif outcome == "duplicated":
                result.duplicated += 1
            else:
                result.unmatched.append(dto.external_id)
        if result.imported or result.unmatched:
            self.events.publish(TOPIC_DATA_CHANGED, section="orders")
        return result

    def import_order(self, dto: FunPayOrderDTO) -> str:
        """Возвращает ``imported | duplicated | unmatched``."""
        product = self.match_product(dto.lot_title)
        if product is None:
            self._register_unmatched(dto)
            return "unmatched"
        try:
            self.sales.create_product_order(
                customer_username=dto.buyer_username,
                product_code=product.code,
                price=dto.price or None,
                funpay_order_id=dto.external_id,
                context={"lot_title": dto.lot_title, "currency": dto.currency},
            )
        except Exception as exc:  # noqa: BLE001 - классифицируем ниже
            if "уже зарегистрирован" in str(exc):
                return "duplicated"
            log.error("Импорт заказа %s не выполнен: %s", dto.external_id, exc)
            self._register_unmatched(dto, reason=str(exc))
            return "unmatched"
        self.audit.log("funpay.order_imported", Actor.APP, entity="order",
                        funpay_id=dto.external_id, product=product.code)
        self._notify("order", f"Новый заказ {dto.external_id} с FunPay",
                     f"{dto.buyer_username}: {dto.lot_title} · {dto.price:.0f} {dto.currency}")
        return "imported"

    # --------------------------------------------------------- сопоставление
    def match_product(self, lot_title: str) -> models.Product | None:
        title = (lot_title or "").strip().lower()
        if not title:
            return None
        products = self.sales.products.all(active_only=True)
        # 1) точный код продукта в названии лота
        for product in products:
            if product.code.lower() in title:
                return product
        # 2) полное название продукта
        for product in products:
            if product.name.lower() in title:
                return product
        # 3) самое длинное частичное совпадение названия (слово в слово)
        best, best_len = None, 0
        for product in products:
            words = [w for w in product.name.lower().split() if len(w) >= 4]
            matched = sum(1 for word in words if word in title)
            if matched >= max(2, len(words)) and len(product.name) > best_len:
                best, best_len = product, len(product.name)
        return best

    # ------------------------------------------------- синхронизация статусов
    def sync_order_statuses(self, orders: list[FunPayOrderDTO]) -> dict:
        """Статусы из маркетплейса → наши заказы.

        * ``refunded`` — возврат: заказ отменяется, резерв склада освобождается;
        * ``closed`` — закрыт покупателем: товар выдан, заказ завершается.

        Идемпотентно: повторный поллинг ничего не меняет.
        """
        result = {"completed": 0, "refunded": 0}
        for dto in orders:
            if dto.status not in ("closed", "refunded"):
                continue
            with self.db.session() as session:
                order = repositories.OrderRepo(session).by_funpay_id(dto.external_id)
                if order is None:
                    continue
                order_id = order.id
                current_status = order.status
            if dto.status == "refunded":
                if current_status == OrderStatus.CANCELLED.value:
                    continue
                self.sales.cancel_order(order_id, reason="возврат средств на FunPay")
                self.audit.log("funpay.order_refunded", Actor.APP, entity="order",
                                entity_id=order_id, funpay_id=dto.external_id)
                self._notify("action_required", f"Возврат по заказу {dto.external_id}",
                             f"{dto.buyer_username}: заказ возвращён на FunPay. "
                             "Резерв склада освобождён.")
                result["refunded"] += 1
            else:
                if current_status == OrderStatus.COMPLETED.value:
                    continue
                self.sales.complete_order(order_id, reason="закрыт на FunPay")
                self.audit.log("funpay.order_closed", Actor.APP, entity="order",
                                entity_id=order_id, funpay_id=dto.external_id)
                self._notify("order", f"Заказ {dto.external_id} закрыт на FunPay",
                             f"{dto.buyer_username}: товар выдан, заказ завершён.")
                result["completed"] += 1
        if result["completed"] or result["refunded"]:
            self.events.publish(TOPIC_DATA_CHANGED, section="orders")
        return result

    # ------------------------------------------------------- не сопоставлено
    def _register_unmatched(self, dto: FunPayOrderDTO, reason: str = "") -> None:
        """Заказ без продукта: сохраняем как новый без воркфлоу, ждём привязки."""
        with self.db.session() as session:
            existing = repositories.OrderRepo(session).by_funpay_id(dto.external_id)
            if existing is not None:
                return
            client = repositories.ClientRepo(session).get_or_create(dto.buyer_username)
            order = models.Order(
                funpay_order_id=dto.external_id,
                client_id=client.id,
                price=float(dto.price or 0.0),
                currency=dto.currency or "RUB",
                status=OrderStatus.PROBLEM.value,
                purchased_at=dto.purchased_at or self.clock.now(),
                created_by="funpay",
                notes=f"Не сопоставлен с продуктом: {dto.lot_title}. {reason}".strip(),
            )
            session.add(order)
            session.flush()
            self.audit.log("funpay.order_unmatched", Actor.APP, entity="order",
                            entity_id=order.id, session=session,
                            funpay_id=dto.external_id, lot=dto.lot_title)
        self.events.publish(TOPIC_DATA_CHANGED, section="orders")
        self._notify("action_required", f"Заказ {dto.external_id} без продукта",
                     f"{dto.buyer_username}: {dto.lot_title}. Привяжите продукт вручную.")

    def link_order_to_product(self, order_id: int, product_code: str) -> models.Order:
        """Ручная привязка не сопоставленного заказа и запуск воркфлоу."""
        product = self.sales.products.by_code(product_code)
        if product is None:
            raise ValueError(f"Продукт не найден: {product_code}")
        with self.db.session() as session:
            order = session.get(models.Order, order_id)
            if order is None:
                raise ValueError("Заказ не найден.")
            if order.status not in (OrderStatus.PROBLEM.value, OrderStatus.NEW.value):
                raise ValueError("Заказ уже в работе — привязка не требуется.")
            funpay_id = order.funpay_order_id
            client_username = order.client.funpay_username if order.client else f"client-{order.client_id}"
            price = order.price
            # освобождаем внешний идентификатор, чтобы новый заказ прошёл дедупликацию
            order.funpay_order_id = None
            order.status = OrderStatus.CANCELLED.value
            order.notes = "Заменён заказом, привязанным к продукту."
        new_order = self.sales.create_product_order(
            customer_username=client_username, product_code=product_code,
            price=price or None, funpay_order_id=funpay_id,
        )
        self.audit.log("funpay.order_linked", Actor.USER, entity="order",
                        entity_id=new_order.id, product=product_code)
        return new_order
