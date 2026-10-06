"""Ручной адаптер FunPay (режим по умолчанию).

Официального публичного API у FunPay нет, поэтому безопасный режим работы:
* объявления публикуются оператором вручную по подготовленным текстам;
* заказы и сообщения вносятся/отправляются оператором;
* приложение ведёт полный учёт локально (объявления, заказы, аренды,
  шаблоны сообщений, статистику).

Все сетевые возможности помечены MANUAL: приложение не выполняет
автоматических действий на сайте.
"""
from __future__ import annotations

from app.domain.enums import Capability
from app.integrations.funpay.base import (
    FunPayAdapter,
    FunPayListingDTO,
    FunPayMessageDTO,
    FunPayOrderDTO,
)
from app.integrations.funpay.templates import ListingText, build_listing_text


class ManualFunPayAdapter(FunPayAdapter):
    name = "manual"
    description = (
        "Ручной режим: приложение готовит тексты и ведёт учёт, "
        "все действия на FunPay выполняет оператор. Безопасно по умолчанию."
    )

    def capabilities(self) -> dict[str, Capability]:
        return {
            "get_orders": Capability.MANUAL,
            "get_messages": Capability.MANUAL,
            "get_listings": Capability.MANUAL,
            "update_listing": Capability.MANUAL,
            "create_listing": Capability.MANUAL,
            "send_message": Capability.MANUAL,
            "read_only_polling": Capability.UNSUPPORTED,
            "prepare_texts": Capability.AVAILABLE,
        }

    # Единственная полностью автоматическая и безопасная возможность —
    # локальная генерация текстов (без обращения к внешнему сервису).
    def prepare_listing_text(
        self,
        game_names: list[str],
        access_days: int,
        price: float,
        free_copies: int,
        owner_names: list[str],
        bundle_name: str | None = None,
    ) -> ListingText:
        return build_listing_text(game_names, access_days, price, free_copies, owner_names, bundle_name)

    # ----------------------------------------------------- явные заглушки
    def get_orders(self) -> list[FunPayOrderDTO]:
        return []  # заказы вносятся оператором через экран «Заказы»

    def get_messages(self) -> list[FunPayMessageDTO]:
        return []  # переписка ведётся оператором в чате FunPay

    def get_listings(self, node_id: int | str | None = None) -> list[FunPayListingDTO]:
        return []  # публикация и статус лотов — вручную

    def update_listing(self, listing_id: str, **fields) -> None:
        raise NotImplementedError(
            "В ручном режиме изменения публикуются оператором на FunPay вручную. "
            "Приложение уже обновило локальную карточку объявления."
        )

    def send_message(self, username: str, text: str) -> None:
        raise NotImplementedError(
            "Отправка в ручном режиме: скопируйте подготовленный текст и отправьте "
            "его в чате FunPay, затем отметьте сообщение как отправленное."
        )
