"""Фасад FunPay: выбор адаптера, возможности, шаблоны сообщений.

Отправка наружу — только при явном разрешении; по умолчанию действует
режим ``Manual approval`` (ТЗ §15).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.core.config import AppConfig
from app.core.events import TOPIC_DATA_CHANGED, EventBus
from app.core.secret_store import SecretStore
from app.database import models, repositories
from app.database.session import Database
from app.domain.enums import Actor, AuditAction, Capability, MessageStatus
from app.domain.value_objects import CapabilityError, DomainError, DryRunBlocked
from app.integrations.funpay.base import FunPayAdapter, format_capability_report
from app.integrations.funpay.golden_key_adapter import GoldenKeyFunPayAdapter
from app.integrations.funpay.manual_adapter import ManualFunPayAdapter
from app.integrations.funpay.templates import MESSAGE_TEMPLATES, render_message
from app.services.audit_service import AuditService

log = logging.getLogger(__name__)


class FunPayService:
    def __init__(self, db: Database, config: AppConfig, secrets: SecretStore,
                 events: EventBus, audit: AuditService) -> None:
        self.db = db
        self.config = config
        self.secrets = secrets
        self.events = events
        self.audit = audit

    # -------------------------------------------------------------- адаптер
    @property
    def adapter(self) -> FunPayAdapter:
        if self.config.funpay.mode == "golden_key":
            return GoldenKeyFunPayAdapter(self.config.funpay, self.secrets)
        return ManualFunPayAdapter()

    def capability_report(self) -> str:
        return format_capability_report(self.adapter)

    def is_readonly_polling_available(self) -> bool:
        return self.adapter.capability("read_only_polling") == Capability.AVAILABLE

    def poll_orders(self) -> list:
        """Опрос своих заказов через адаптер (только чтение).

        Возвращает список DTO; если адаптер не умеет — пустой список и запись в аудит.
        """
        adapter = self.adapter
        if adapter.capability("read_only_polling") != Capability.AVAILABLE:
            return []
        try:
            orders = adapter.get_orders()
        except CapabilityError as exc:
            log.error("Опрос заказов FunPay не выполнен: %s", exc)
            self.audit.log(AuditAction.FUNPAY_ACTION_REQUIRED, entity="funpay",
                            error=str(exc))
            return []
        self.audit.log("funpay.orders_polled", entity="funpay", count=len(orders))
        return orders

    def deliver_order(self, funpay_order_id: str, text: str) -> str:
        """Автовыдача: сообщение с товаром в чат заказа (учёт DRY RUN снаружи)."""
        adapter = self.adapter
        if adapter.capability("send_message") not in (Capability.AVAILABLE,):
            raise CapabilityError("Отправка недоступна в текущем режиме адаптера.")
        return adapter.deliver_order_text(funpay_order_id, text)

    # ------------------------------------------------------------- сообщения
    def poll_messages(self) -> dict:
        """Входящие сообщения: сохраняем, при совпадении команды — автоответ.

        Автоответ отправляется сам только если: режим сообщений ``auto_safe``,
        адаптер умеет отправлять и НЕ включён DRY RUN. Иначе — черновик на
        подтверждение оператору.
        """
        from sqlalchemy import select

        from app.database import models, repositories

        summary = {"received": 0, "replies_sent": 0, "reply_drafts": 0}
        adapter = self.adapter
        if adapter.capability("get_messages") != Capability.AVAILABLE:
            return summary
        try:
            dtos = adapter.get_messages()
        except CapabilityError as exc:
            log.error("Опрос сообщений FunPay не выполнен: %s", exc)
            self.audit.log(AuditAction.FUNPAY_ACTION_REQUIRED, entity="funpay", error=str(exc))
            return summary

        auto_send = (self.config.funpay.message_mode == "auto_safe"
                     and adapter.capability("send_message") == Capability.AVAILABLE
                     and not self.config.dry_run)
        for dto in dtos:
            with self.db.session() as session:
                exists = session.scalar(
                    select(models.Message.id).where(models.Message.external_id == dto.external_id)
                )
            if exists is not None:
                continue
            with self.db.session() as session:
                client = repositories.ClientRepo(session).get_or_create(dto.counterpart_username)
                repositories.MessageRepo(session).add(models.Message(
                    client_id=client.id, direction="in", body=dto.text,
                    status=MessageStatus.RECEIVED.value, external_id=dto.external_id,
                    chat_id=dto.external_id.split(":")[0] if ":" in dto.external_id else None,
                ))
                client_id = client.id
            summary["received"] += 1
            self.audit.log("funpay.message_received", entity="message",
                            funpay_id=dto.external_id, client=dto.counterpart_username)

            reply = self._match_auto_reply(dto.text)
            if reply is None:
                continue
            if auto_send:
                try:
                    adapter.send_message(dto.counterpart_username, reply)
                except CapabilityError as exc:
                    log.error("Автоответ не отправлен (%s): %s", dto.counterpart_username, exc)
                    self._save_reply_draft(client_id, reply)
                    summary["reply_drafts"] += 1
                    continue
                with self.db.session() as session:
                    repositories.MessageRepo(session).add(models.Message(
                        client_id=client_id, direction="out", body=reply,
                        template_key="auto_reply", status=MessageStatus.SENT.value,
                        sent_at=datetime.now(timezone.utc),
                    ))
                summary["replies_sent"] += 1
                self.audit.log(AuditAction.MESSAGE_SENT, entity="message",
                                client=dto.counterpart_username, auto_reply=True)
            else:
                self._save_reply_draft(client_id, reply)
                summary["reply_drafts"] += 1

        if summary["received"]:
            self.events.publish(TOPIC_DATA_CHANGED, section="messages")
            self.audit.log("funpay.messages_polled", entity="funpay", **summary)
        return summary

    def _match_auto_reply(self, text: str) -> str | None:
        """Команды автоответов из настроек (префиксное совпадение без регистра)."""
        lowered = (text or "").strip().lower()
        for command, reply in (self.config.funpay.auto_replies or {}).items():
            if command and lowered.startswith(str(command).strip().lower()):
                return str(reply)
        return None

    def _save_reply_draft(self, client_id: int, reply: str) -> None:
        from app.database import models, repositories

        with self.db.session() as session:
            repositories.MessageRepo(session).add(models.Message(
                client_id=client_id, direction="out", body=reply,
                template_key="auto_reply", status=MessageStatus.DRAFTED.value,
            ))

    # ------------------------------------------------------------------ лоты
    def plan_listing_sync(self) -> dict:
        """План синхронизации лотов с продуктами ОС (без внешних вызовов).

        * создать: активный продукт, товар в наличии (или без складского режима),
          лота ещё нет и настроена подкатегория;
        * открыть: лот есть, но закрыт, а продукт снова готов к продаже;
        * закрыть: лот активен, но продукт выключен или закончился товар;
        * цена: цена продукта отличается от цены лота больше допуска;
          цена ниже минимальной не публикуется (защита от демпинга).
        """
        from sqlalchemy import func, select

        from app.domain.enums import StockStatus

        cfg = self.config.funpay
        plan = {"create": [], "open": [], "close": [], "reprice": [],
                "blocked_below_min": [], "skipped_no_node": []}
        with self.db.session() as session:
            products = list(session.scalars(
                select(models.Product).order_by(models.Product.id)))
            for product in products:
                node = (cfg.lot_nodes or {}).get(product.game or "")
                ready = session.scalar(select(func.count(models.StockUnit.id)).where(
                    models.StockUnit.product_id == product.id,
                    models.StockUnit.status == StockStatus.READY.value)) or 0
                needs_stock = product.stock_mode == "stock"
                in_stock = (not needs_stock) or ready > 0
                info = {"id": product.id, "code": product.code, "name": product.name,
                        "game": product.game or "", "node": node,
                        "lot_id": product.funpay_lot_id, "ready": ready,
                        "price": product.price, "lot_price": product.funpay_lot_price}
                below_min = bool(product.minimum_price
                                 and product.price < product.minimum_price)
                if product.funpay_lot_id:
                    if product.funpay_lot_active and (
                            not product.active
                            or (cfg.lot_close_when_empty and not in_stock)):
                        plan["close"].append(info)
                    elif not product.funpay_lot_active and product.active and in_stock:
                        plan["open"].append(info)
                    elif product.funpay_lot_active and product.active and in_stock:
                        if self._price_changed(product.funpay_lot_price, product.price,
                                               cfg.lot_price_tolerance_pct):
                            if below_min:
                                plan["blocked_below_min"].append(info)
                            else:
                                plan["reprice"].append(info)
                elif product.active and in_stock:
                    if not node:
                        plan["skipped_no_node"].append(info)
                    elif below_min:
                        plan["blocked_below_min"].append(info)
                    else:
                        plan["create"].append(info)
        return plan

    @staticmethod
    def _price_changed(lot_price: float | None, price: float, tolerance_pct: float) -> bool:
        """Отличается ли цена продукта от цены лота больше допуска (%)."""
        if lot_price is None:
            return True
        tolerance = abs(lot_price) * max(tolerance_pct, 0.0) / 100.0
        return abs(price - lot_price) > tolerance + 1e-9

    def sync_listings(self, confirmed: bool = False, from_poller: bool = False) -> dict:
        """Применяет план лотов: создание/открытие/закрытие через адаптер.

        Защита: только при доступной возможности, не в DRY RUN и не в режиме
        «автоматизация выключена»; из поллера — только при явном включении
        автосинхронизации в режиме АВТО; из интерфейса — после подтверждения.
        """
        from app.domain.enums import Capability

        summary = {"created": 0, "opened": 0, "closed": 0, "repriced": 0,
                   "errors": [], "dry_run": False, "applied": False}
        adapter = self.adapter
        if adapter.capability("create_listing") != Capability.AVAILABLE:
            summary["errors"].append(
                "Лоты недоступны в текущем режиме адаптера (нужен неофициальный доступ).")
            return summary
        plan = self.plan_listing_sync()
        summary["plan"] = plan
        total = (len(plan["create"]) + len(plan["open"]) + len(plan["close"])
                 + len(plan["reprice"]))
        if total == 0 and not plan["blocked_below_min"]:
            return summary
        if self.config.dry_run:
            summary["dry_run"] = True
            self.audit.log("funpay.listings_sync_preview", entity="listing",
                           create=len(plan["create"]), open=len(plan["open"]),
                           close=len(plan["close"]), reprice=len(plan["reprice"]))
            return summary
        if self.config.automation_mode == "off":
            summary["errors"].append(
                "Автоматизация выключена — лоты не изменены (показан только план).")
            return summary
        if from_poller and not (self.config.funpay.lot_auto_sync
                                and self.config.automation_mode == "auto_safe"):
            return summary
        if not confirmed and not from_poller:
            summary["errors"].append("Требуется подтверждение оператора.")
            return summary

        cfg = self.config.funpay
        for info in plan["blocked_below_min"]:
            summary["errors"].append(
                f"{info['code']}: цена {info['price']} ниже минимальной — "
                "лот не изменён, поправьте цену продукта.")
        for info in plan["create"]:
            title = info["name"][:60]
            description = f"{info['name']}\nКод продукта: {info['code']}"
            try:
                lot_id = adapter.create_listing(
                    node_id=info["node"], title=title, description=description,
                    price=info["price"],
                    payment_message=cfg.lot_payment_message)
            except CapabilityError as exc:
                log.error("Лот для %s не создан: %s", info["code"], exc)
                summary["errors"].append(f"{info['code']}: {exc}")
                continue
            if lot_id is None:
                summary["errors"].append(
                    f"{info['code']}: лот создан, но ID не найден — проверьте на FunPay вручную.")
                self.audit.log("funpay.lot_created_no_id", entity="listing",
                               product=info["code"])
                continue
            with self.db.session() as session:
                product = session.get(models.Product, info["id"])
                if product is not None:
                    product.funpay_lot_id = str(lot_id)
                    product.funpay_lot_active = True
                    product.funpay_lot_price = info["price"]
            summary["created"] += 1
            self.audit.log("funpay.lot_created", entity="listing",
                           product=info["code"], lot_id=str(lot_id),
                           price=info["price"])

        for info in plan["reprice"]:
            try:
                adapter.update_listing(info["lot_id"], price=info["price"])
            except CapabilityError as exc:
                log.error("Цена лота %s не обновлена: %s", info["lot_id"], exc)
                summary["errors"].append(f"{info['code']}: {exc}")
                continue
            with self.db.session() as session:
                product = session.get(models.Product, info["id"])
                if product is not None:
                    product.funpay_lot_price = info["price"]
            summary["repriced"] += 1
            self.audit.log("funpay.lot_repriced", entity="listing",
                           product=info["code"], lot_id=str(info["lot_id"]),
                           old_price=info["lot_price"], new_price=info["price"])

        for info in plan["open"]:
            try:
                adapter.open_listing(info["lot_id"])
            except CapabilityError as exc:
                log.error("Лот %s не открыт: %s", info["lot_id"], exc)
                summary["errors"].append(f"{info['code']}: {exc}")
                continue
            self._mark_lot(info["id"], active=True)
            summary["opened"] += 1
            self.audit.log("funpay.lot_opened", entity="listing",
                           product=info["code"], lot_id=str(info["lot_id"]))

        for info in plan["close"]:
            try:
                adapter.close_listing(info["lot_id"])
            except CapabilityError as exc:
                log.error("Лот %s не закрыт: %s", info["lot_id"], exc)
                summary["errors"].append(f"{info['code']}: {exc}")
                continue
            self._mark_lot(info["id"], active=False)
            summary["closed"] += 1
            self.audit.log("funpay.lot_closed", entity="listing",
                           product=info["code"], lot_id=str(info["lot_id"]))

        summary["applied"] = True
        self.events.publish(TOPIC_DATA_CHANGED, section="listings")
        self.audit.log("funpay.listings_synced", entity="listing", **{
            key: summary[key] for key in ("created", "opened", "closed", "repriced")})
        return summary

    def _mark_lot(self, product_id: int, active: bool) -> None:
        with self.db.session() as session:
            product = session.get(models.Product, product_id)
            if product is not None:
                product.funpay_lot_active = active

    def _product_price(self, product_id: int) -> float:
        with self.db.session() as session:
            product = session.get(models.Product, product_id)
            return product.price if product else 0.0

    def check_connection(self) -> dict:
        """Проверка доступа (только чтение): имя аккаунта и его ID."""
        adapter = self.adapter
        if adapter.capability("read_only_polling") != Capability.AVAILABLE:
            return {"ok": False,
                    "message": "Неофициальный доступ не включён (Настройки → FunPay)."}
        try:
            info = adapter.check_connection()
        except CapabilityError as exc:
            return {"ok": False, "message": str(exc)}
        self.audit.log("funpay.connection_checked", entity="funpay",
                        username=info.get("username"))
        return {"ok": True, **info}

    # ------------------------------------------------------------- сообщения
    def draft_message(self, client_id: int, template_key: str, order_id: int | None = None,
                      **params) -> models.Message:
        """Готовит черновик автоответа (ничего не отправляет)."""
        if template_key not in MESSAGE_TEMPLATES:
            raise DomainError(f"Неизвестный шаблон: {template_key}")
        text = render_message(template_key, **params)
        with self.db.session() as session:
            message = repositories.MessageRepo(session).add(models.Message(
                client_id=client_id, order_id=order_id, direction="out",
                template_key=template_key, body=text, status=MessageStatus.DRAFTED.value,
            ))
            self.audit.log(AuditAction.MESSAGE_GENERATED, entity="message", entity_id=message.id,
                            session=session, template=template_key, client_id=client_id)
            message_id = message.id
            body = message.body
        self.events.publish(TOPIC_DATA_CHANGED, section="messages")
        return self._get_message(message_id)

    def approve_message(self, message_id: int) -> None:
        with self.db.session() as session:
            message = session.get(models.Message, message_id)
            if message is None:
                return
            message.status = MessageStatus.APPROVED.value
            self.audit.log(AuditAction.MESSAGE_APPROVED, Actor.USER, entity="message",
                            entity_id=message_id, session=session)

    def send_message(self, message_id: int) -> str:
        """Отправка сообщения с учётом режима и возможностей адаптера.

        Возвращает человекочитаемый результат действия.
        """
        adapter = self.adapter
        capability = adapter.capability("send_message")
        with self.db.session() as session:
            message = session.get(models.Message, message_id)
            if message is None:
                raise DomainError("Сообщение не найдено.")
            client = session.get(models.Client, message.client_id)
            username = client.funpay_username if client else "?"
            body = message.body

        if self.config.dry_run:
            self.audit.log(AuditAction.MESSAGE_SENT, entity="message", entity_id=message_id,
                            dry_run=True, username=username)
            raise DryRunBlocked(
                f"[DRY RUN] Сообщение для @{username} НЕ отправлено. Текст:\n{body}"
            )
        if capability == Capability.MANUAL or capability == Capability.UNSUPPORTED:
            self.audit.log(AuditAction.FUNPAY_ACTION_REQUIRED, entity="message",
                            entity_id=message_id, username=username)
            return (
                "Автоматическая отправка недоступна в текущем режиме. "
                "Скопируйте текст ниже и отправьте его в чате FunPay вручную, "
                "затем отметьте сообщение как «Отправлено вручную»."
            )
        # Ветвь для будущих официально доступных механизмов отправки.
        try:
            adapter.send_message(username, body)
        except CapabilityError as exc:
            raise DomainError(str(exc)) from exc
        with self.db.session() as session:
            message = session.get(models.Message, message_id)
            if message is not None:
                from datetime import datetime, timezone

                message.status = MessageStatus.SENT.value
                message.sent_at = datetime.now(timezone.utc)
                self.audit.log(AuditAction.MESSAGE_SENT, entity="message", entity_id=message_id,
                                session=session, username=username)
        return "Сообщение отправлено."

    def mark_sent_manually(self, message_id: int) -> None:
        from datetime import datetime, timezone

        with self.db.session() as session:
            message = session.get(models.Message, message_id)
            if message is None:
                return
            message.status = MessageStatus.MANUAL.value
            message.sent_at = datetime.now(timezone.utc)
            self.audit.log(AuditAction.MESSAGE_SENT, Actor.USER, entity="message",
                            entity_id=message_id, session=session, manually=True)
        self.events.publish(TOPIC_DATA_CHANGED, section="messages")

    def drafted(self) -> list[models.Message]:
        with self.db.session() as session:
            return list(repositories.MessageRepo(session).drafted())

    def _get_message(self, message_id: int) -> models.Message | None:
        with self.db.session() as session:
            return session.get(models.Message, message_id)
