"""Шина событий приложения.

Чистый Python (без Qt), чтобы бизнес-логика и тесты не зависели от GUI.
UI подписывается на события и выполняет все изменения интерфейса
исключительно в главном потоке через ``QMetaObject.invokeMethod``/сигналы.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Callable

log = logging.getLogger(__name__)

Handler = Callable[..., None]


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[str, list[Handler]] = defaultdict(list)

    def subscribe(self, topic: str, handler: Handler) -> None:
        self._subs[topic].append(handler)

    def unsubscribe(self, topic: str, handler: Handler) -> None:
        handlers = self._subs.get(topic, [])
        if handler in handlers:
            handlers.remove(handler)

    def publish(self, topic: str, **payload: Any) -> None:
        for handler in list(self._subs.get(topic, [])):
            try:
                handler(**payload)
            except Exception:  # noqa: BLE001 - одна подписка не должна ронять остальные
                log.exception("Обработчик события %s завершился с ошибкой", topic)


# Темы событий
TOPIC_DATA_CHANGED = "data.changed"          # payload: section
TOPIC_SYNC_STARTED = "sync.started"          # payload: kind
TOPIC_SYNC_FINISHED = "sync.finished"        # payload: kind, report
TOPIC_SYNC_FAILED = "sync.failed"            # payload: kind, error
TOPIC_NOTIFY = "notify"                      # payload: kind, title, body
TOPIC_LEASE_EXPIRING = "lease.expiring"      # payload: lease_id, hours_left
TOPIC_LEASE_EXPIRED = "lease.expired"        # payload: lease_id
TOPIC_LICENSE_FREED = "license.freed"        # payload: game_id, license_id
TOPIC_ACTION_REQUIRED = "action.required"    # payload: title, body
