"""Адаптер на основе сессионного ``golden_key`` (НЕОФИЦИАЛЬНЫЙ доступ).

ВАЖНО (см. docs/RESEARCH.md §4, §6):
* официального публичного API у FunPay НЕТ;
* доступ через ``golden_key`` — НЕОФИЦИАЛЬНЫЙ, работает с внутренними
  страницами СВОЕГО аккаунта и несёт риск ограничений аккаунта;
* включается только явным согласием оператора (``unofficial_allowed``);
* лимиты частоты уважаются (клиент ждёт, а не обходит);
* никакого обхода антибот-защиты и CAPTCHA.

Реализовано (чистая реализация клиента — ``golden_key_client.py``):
* чтение списка своих продаж (заказов) — поллинг;
* отправка сообщения в чат заказа — для автовыдачи цифрового товара.

Секрет хранится только в системном хранилище (keyring → Windows Credential
Manager); в БД и конфиге — лишь ссылка ``keyring://funpay/golden_key``.
"""
from __future__ import annotations

import logging

from app.core.config import FunPaySettings
from app.core.secret_store import SecretStore
from app.domain.enums import Capability
from app.domain.value_objects import CapabilityError
from app.integrations.funpay.base import FunPayAdapter, FunPayOrderDTO
from app.integrations.funpay.golden_key_client import (
    FloodError,
    GoldenKeyClient,
    GoldenKeyError,
    UnauthorizedError,
)

log = logging.getLogger(__name__)

GOLDEN_KEY_SECRET = "funpay/golden_key"

MAX_UNREAD_CHATS = 10      # сколько непрочитанных чатов читаем за один опрос
MAX_MESSAGES_PER_CHAT = 5  # сколько последних входящих берём из чата


class GoldenKeyFunPayAdapter(FunPayAdapter):
    name = "golden_key (неофициальный)"
    description = (
        "НЕОФИЦИАЛЬНЫЙ доступ к своему аккаунту через golden_key. "
        "По умолчанию выключен; включается в настройках под ответственность оператора. "
        "Реализовано: чтение своих заказов и отправка сообщения в чат (автовыдача)."
    )

    def __init__(self, settings: FunPaySettings, secrets: SecretStore,
                 client: GoldenKeyClient | None = None) -> None:
        self._settings = settings
        self._secrets = secrets
        self._client = client  # инъекция для тестов

    # ----------------------------------------------------------- helpers
    def has_credential(self) -> bool:
        return self._secrets.has(GOLDEN_KEY_SECRET)

    def is_enabled(self) -> bool:
        return bool(self._settings.unofficial_allowed and self.has_credential())

    def status(self) -> str:
        if not self._settings.unofficial_allowed:
            return (
                "Выключен: неофициальный доступ не разрешён в настройках. "
                "Рекомендуется оставаться в ручном режиме."
            )
        if not self.has_credential():
            return "Разрешён, но не сохранён golden_key (Настройки → FunPay)."
        return (
            "Включён (неофициальный режим): чтение своих заказов и выдача сообщений "
            "в чат. Риски ограничений аккаунта — на операторе."
        )

    def client(self) -> GoldenKeyClient:
        """Ленивый клиент. Только при включённом режиме и сохранённом ключе."""
        if not self.is_enabled():
            raise CapabilityError(
                "Неофициальный доступ не включён (Настройки → FunPay)."
            )
        if self._client is None:
            golden_key = self._secrets.get(GOLDEN_KEY_SECRET) or ""
            self._client = GoldenKeyClient(golden_key,
                                           user_agent=self._settings.user_agent or None)
        return self._client

    # -------------------------------------------------------- capabilities
    def capabilities(self) -> dict[str, Capability]:
        if not self.is_enabled():
            polling = Capability.UNSUPPORTED
            orders = Capability.UNSUPPORTED
            send = Capability.MANUAL
            lots = Capability.MANUAL
        else:
            polling = Capability.AVAILABLE
            orders = Capability.AVAILABLE
            send = Capability.AVAILABLE  # но только через подтверждение/безопасный режим
            lots = Capability.AVAILABLE  # создание/закрытие лотов — под ответственность оператора
        return {
            "get_orders": orders,
            "get_messages": orders,                 # список чатов и история — тем же доступом
            "get_listings": lots,                   # свои лоты в подкатегории
            "update_listing": lots,                 # активация/деактивация лота
            "create_listing": lots,                 # создание лота
            "send_message": send,
            "read_only_polling": polling,
            "prepare_texts": Capability.AVAILABLE,
        }

    # -------------------------------------------------------------- проверка
    def check_connection(self) -> dict:
        """Инициализация сессии: проверяет ключ и сообщает данные аккаунта."""
        try:
            client = self.client()
            client.init()
        except UnauthorizedError as exc:
            raise CapabilityError(f"FunPay не принял доступ: {exc}") from exc
        except GoldenKeyError as exc:
            raise CapabilityError(f"Ошибка доступа к FunPay: {exc}") from exc
        return {"username": client.username, "user_id": client.user_id}

    # -------------------------------------------------------------- чтение
    def get_orders(self) -> list[FunPayOrderDTO]:
        """Свои продажи/заказы со страницы «Мои продажи»."""
        try:
            sales = self.client().get_sales()
        except UnauthorizedError as exc:
            raise CapabilityError(f"FunPay не принял доступ: {exc}") from exc
        except FloodError as exc:
            raise CapabilityError(f"FunPay ограничил частоту запросов: {exc}") from exc
        except GoldenKeyError as exc:
            raise CapabilityError(f"Ошибка доступа к FunPay: {exc}") from exc
        return [
            FunPayOrderDTO(
                external_id=sale.order_id,
                buyer_username=sale.buyer_username,
                lot_title=sale.description,
                price=sale.price,
                currency=sale.currency,
                purchased_at=sale.purchased_at,
                status=sale.status,
            )
            for sale in sales
        ]

    def get_messages(self) -> list:
        """Входящие сообщения из непрочитанных чатов (последние в каждом)."""
        from datetime import datetime, timezone

        from app.integrations.funpay.base import FunPayMessageDTO

        try:
            chats = self.client().get_chats()
        except UnauthorizedError as exc:
            raise CapabilityError(f"FunPay не принял доступ: {exc}") from exc
        except FloodError as exc:
            raise CapabilityError(f"FunPay ограничил частоту запросов: {exc}") from exc
        except GoldenKeyError as exc:
            raise CapabilityError(f"Ошибка доступа к чатам: {exc}") from exc

        unread = [chat for chat in chats if chat.unread][:MAX_UNREAD_CHATS]
        dtos = []
        for chat in unread:
            try:
                history = self.client().get_chat_history(chat.chat_id)
            except GoldenKeyError as exc:
                log.warning("История чата %s не прочитана: %s", chat.chat_id, exc)
                continue
            incoming = [m for m in history if not m.by_me and m.author_id != 0]
            for message in incoming[-MAX_MESSAGES_PER_CHAT:]:
                dtos.append(FunPayMessageDTO(
                    external_id=f"{chat.chat_id}:{message.message_id}",
                    counterpart_username=chat.name,
                    text=message.text,
                    received_at=datetime.now(timezone.utc),
                    direction="in",
                ))
        return dtos

    def get_listings(self, node_id: int | str | None = None) -> list:
        """Свои лоты в подкатегории (нужен её ID — см. Настройки → подкатегории лотов)."""
        if node_id is None:
            raise CapabilityError("Укажите подкатегорию лотов (Настройки → «Подкатегории лотов»).")
        from app.integrations.funpay.base import FunPayListingDTO

        try:
            lots = self.client().get_own_lots(node_id)
        except UnauthorizedError as exc:
            raise CapabilityError(f"FunPay не принял доступ: {exc}") from exc
        except FloodError as exc:
            raise CapabilityError(f"FunPay ограничил частоту запросов: {exc}") from exc
        except GoldenKeyError as exc:
            raise CapabilityError(f"Ошибка чтения лотов: {exc}") from exc
        return [
            FunPayListingDTO(external_id=lot.lot_id, title=lot.description,
                             price=lot.price, currency="", is_active=lot.active)
            for lot in lots
        ]

    def update_listing(self, listing_id: str, **fields) -> None:
        active = fields.get("active")
        price = fields.get("price")
        if active is None and price is None:
            raise CapabilityError(
                "Изменение лота поддерживается только в части активности и цены.")
        try:
            if active is not None:
                self.client().set_lot_active(listing_id, bool(active))
            if price is not None:
                self.client().set_lot_price(listing_id, float(price))
        except UnauthorizedError as exc:
            raise CapabilityError(f"FunPay не принял доступ: {exc}") from exc
        except FloodError as exc:
            raise CapabilityError(f"FunPay ограничил частоту запросов: {exc}") from exc
        except GoldenKeyError as exc:
            raise CapabilityError(f"Не удалось изменить лот: {exc}") from exc

    def create_listing(self, node_id: int | str, title: str, description: str,
                       price: float, payment_message: str = "") -> str | None:
        try:
            return self.client().create_lot(node_id, title, description, price,
                                            payment_message=payment_message)
        except UnauthorizedError as exc:
            raise CapabilityError(f"FunPay не принял доступ: {exc}") from exc
        except FloodError as exc:
            raise CapabilityError(f"FunPay ограничил частоту запросов: {exc}") from exc
        except GoldenKeyError as exc:
            raise CapabilityError(f"Не удалось создать лот: {exc}") from exc

    def close_listing(self, listing_id: str) -> None:
        self._set_lot_active(listing_id, False)

    def open_listing(self, listing_id: str) -> None:
        self._set_lot_active(listing_id, True)

    def _set_lot_active(self, listing_id: str, active: bool) -> None:
        try:
            self.client().set_lot_active(listing_id, active)
        except UnauthorizedError as exc:
            raise CapabilityError(f"FunPay не принял доступ: {exc}") from exc
        except FloodError as exc:
            raise CapabilityError(f"FunPay ограничил частоту запросов: {exc}") from exc
        except GoldenKeyError as exc:
            raise CapabilityError(f"Не удалось изменить активность лота: {exc}") from exc

    # ------------------------------------------------------------- отправка
    def send_message(self, username: str, text: str) -> None:
        """Отправка по нику: требует сначала найти чат по заказам покупателя."""
        sale = self._find_sale_by_buyer(username)
        if sale is None:
            raise CapabilityError(
                f"Не найден активный заказ покупателя {username} — чат для отправки неизвестен."
            )
        self._send_to_chat(sale.chat_id, text)

    def deliver_order_text(self, funpay_order_id: str, text: str) -> str:
        """Автовыдача: сообщение с товаром в чат конкретного заказа."""
        sale = self._find_sale_by_id(funpay_order_id)
        if sale is None:
            raise CapabilityError(
                f"Заказ {funpay_order_id} не найден в списке продаж (возможно, ещё не загружен)."
            )
        self._send_to_chat(sale.chat_id, text)
        return f"Сообщение отправлено в чат заказа {funpay_order_id} ({sale.buyer_username})."

    # ------------------------------------------------------------- внутри
    def _find_sale_by_id(self, order_id: str):
        try:
            sales = self.client().get_sales()
        except GoldenKeyError as exc:
            raise CapabilityError(f"Ошибка доступа к FunPay: {exc}") from exc
        return next((s for s in sales if s.order_id == order_id.lstrip("#")), None)

    def _find_sale_by_buyer(self, username: str):
        try:
            sales = self.client().get_sales()
        except GoldenKeyError as exc:
            raise CapabilityError(f"Ошибка доступа к FunPay: {exc}") from exc
        open_sales = [s for s in sales if s.status == "paid"] or sales
        return next((s for s in open_sales if s.buyer_username == username), None)

    def _send_to_chat(self, chat_id: str, text: str) -> None:
        try:
            self.client().send_chat_message(chat_id, text)
        except UnauthorizedError as exc:
            raise CapabilityError(f"FunPay не принял доступ: {exc}") from exc
        except FloodError as exc:
            raise CapabilityError(f"FunPay ограничил частоту отправки: {exc}") from exc
        except GoldenKeyError as exc:
            raise CapabilityError(f"Не удалось отправить сообщение: {exc}") from exc
