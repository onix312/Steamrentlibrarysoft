"""FunPayAdapter — интерфейс доступа к FunPay.

Официального публичного API у FunPay нет (см. docs/RESEARCH.md), поэтому:
* каждая возможность декларируется через ``Capability``;
* GUI использует ТОЛЬКО этот интерфейс и не знает про способ получения данных;
* режим по умолчанию — ручной (``ManualFunPayAdapter``);
* любые рискованные действия требуют явного согласия и подтверждения.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from app.domain.enums import Capability
from app.domain.value_objects import CapabilityError


@dataclass(frozen=True)
class FunPayOrderDTO:
    external_id: str
    buyer_username: str
    lot_title: str
    price: float
    currency: str
    purchased_at: datetime
    status: str = "new"


@dataclass(frozen=True)
class FunPayMessageDTO:
    external_id: str
    counterpart_username: str
    text: str
    received_at: datetime
    direction: str = "in"


@dataclass(frozen=True)
class FunPayListingDTO:
    external_id: str
    title: str
    price: float
    currency: str
    is_active: bool


class FunPayAdapter(ABC):
    """Интерфейс адаптера. Конкретная реализация декларирует свои возможности."""

    name: str = "abstract"
    description: str = ""

    # -------------------------------------------------------- capabilities
    @abstractmethod
    def capabilities(self) -> dict[str, Capability]:
        """Набор возможностей. Ключи см. в CAPABILITY_KEYS."""

    def capability(self, key: str) -> Capability:
        return self.capabilities().get(key, Capability.UNSUPPORTED)

    # ------------------------------------------------------------ операции
    def get_orders(self) -> list[FunPayOrderDTO]:
        raise CapabilityError(f"get_orders недоступен в адаптере «{self.name}»")

    def get_messages(self) -> list[FunPayMessageDTO]:
        raise CapabilityError(f"get_messages недоступен в адаптере «{self.name}»")

    def get_listings(self, node_id: int | str | None = None) -> list[FunPayListingDTO]:
        raise CapabilityError(f"get_listings недоступен в адаптере «{self.name}»")

    def update_listing(self, listing_id: str, **fields) -> None:
        raise CapabilityError(f"update_listing недоступен в адаптере «{self.name}»")

    def create_listing(self, node_id: int | str, title: str, description: str,
                       price: float, payment_message: str = "") -> str | None:
        """Создаёт лот и возвращает его внешний ID (или None, если ID не удалось узнать)."""
        raise CapabilityError(f"create_listing недоступен в адаптере «{self.name}»")

    def close_listing(self, listing_id: str) -> None:
        """«Закрывает» лот: деактивация без удаления (обратимо)."""
        raise CapabilityError(f"close_listing недоступен в адаптере «{self.name}»")

    def open_listing(self, listing_id: str) -> None:
        """Повторно активирует ранее закрытый лот."""
        raise CapabilityError(f"open_listing недоступен в адаптере «{self.name}»")

    def send_message(self, username: str, text: str) -> None:
        raise CapabilityError(f"send_message недоступен в адаптере «{self.name}»")

    def deliver_order_text(self, funpay_order_id: str, text: str) -> str:
        """Выдача товара сообщением в чат конкретного заказа (автовыдача)."""
        raise CapabilityError(f"deliver_order_text недоступен в адаптере «{self.name}»")


#: Ключи возможностей (единый словарь для всех адаптеров).
CAPABILITY_KEYS = (
    "get_orders",
    "get_messages",
    "get_listings",
    "update_listing",
    "create_listing",
    "send_message",
    "read_only_polling",
    "prepare_texts",
)


def format_capability_report(adapter: FunPayAdapter) -> str:
    """Человекочитаемый отчёт о возможностях адаптера (для экрана FunPay)."""
    labels = {
        Capability.AVAILABLE: "доступно",
        Capability.MANUAL: "вручную",
        Capability.UNSUPPORTED: "не поддерживается",
    }
    lines = [f"Адаптер: {adapter.name} — {adapter.description}"]
    caps = adapter.capabilities()
    for key in CAPABILITY_KEYS:
        lines.append(f"  • {key}: {labels[caps.get(key, Capability.UNSUPPORTED)]}")
    return "\n".join(lines)
