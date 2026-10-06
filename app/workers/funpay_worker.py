"""Фоновая задача FunPay.

В ручном режиме сеть НЕ трогается. Если адаптер декларирует доступный
``read_only_polling`` (неофициальный режим с явного разрешения оператора),
опрашиваем свои заказы и отдаём их импортёру ОС: заказ → продукт → воркфлоу.
"""
from __future__ import annotations

import logging

from app.domain.enums import Capability

log = logging.getLogger(__name__)


def build_funpay_poll_job(context) -> callable:
    def job() -> dict:
        adapter = context.funpay.adapter
        capability = adapter.capability("read_only_polling")
        if capability != Capability.AVAILABLE:
            return {
                "mode": adapter.name,
                "polling": "выключено",
                "message": (
                    "Автоматический опрос FunPay недоступен в текущем режиме. "
                    "Заказы вносятся вручную на экране «FunPay»."
                ),
            }
        orders = context.funpay.poll_orders()
        lots = None
        if context.config.funpay.lot_auto_sync:
            lots = context.funpay.sync_listings(from_poller=True)
        if not orders:
            return {"mode": adapter.name, "polling": "включено", "imported": 0,
                    "lots": lots}
        result = context.importer.import_orders(orders)
        synced = context.importer.sync_order_statuses(orders)
        messages = context.funpay.poll_messages()
        log.info("FunPay poll: %s; статусы: закрыто %s, возвратов %s; сообщения: %s; лоты: %s",
                 result.summary, synced["completed"], synced["refunded"], messages,
                 {k: lots[k] for k in ("created", "opened", "closed")} if lots else None)
        return {"mode": adapter.name, "polling": "включено", **vars(result),
                "synced": synced, "messages": messages, "lots": lots}

    return job
