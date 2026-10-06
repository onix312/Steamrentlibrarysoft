"""Мост EventBus → главный поток Qt.

События публикуются из любых потоков (воркеров), а подписчики-сигналы
доставляются в GUI-поток через очередные соединения.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from app.core.events import (
    TOPIC_DATA_CHANGED,
    TOPIC_LEASE_EXPIRED,
    TOPIC_LEASE_EXPIRING,
    TOPIC_LICENSE_FREED,
    TOPIC_NOTIFY,
    TOPIC_SYNC_FAILED,
    TOPIC_SYNC_FINISHED,
    TOPIC_SYNC_STARTED,
    EventBus,
)


class UiBridge(QObject):
    data_changed = Signal(str)           # section
    sync_started = Signal(str)           # kind
    sync_finished = Signal(object)       # report
    sync_failed = Signal(str)            # error
    notify = Signal(str, str, str)       # kind, title, body
    lease_event = Signal(str, object)    # "expiring"/"expired"/"freed", payload

    def __init__(self, events: EventBus) -> None:
        super().__init__()
        events.subscribe(TOPIC_DATA_CHANGED, self._on_data_changed)
        events.subscribe(TOPIC_SYNC_STARTED, self._on_sync_started)
        events.subscribe(TOPIC_SYNC_FINISHED, self._on_sync_finished)
        events.subscribe(TOPIC_SYNC_FAILED, self._on_sync_failed)
        events.subscribe(TOPIC_NOTIFY, self._on_notify)
        events.subscribe(TOPIC_LEASE_EXPIRING, self._on_lease_expiring)
        events.subscribe(TOPIC_LEASE_EXPIRED, self._on_lease_expired)
        events.subscribe(TOPIC_LICENSE_FREED, self._on_license_freed)

    # Слоты выполняются в потоке публикации; Signal.emit потокобезопасен —
    # доставка подписчикам в главном потоке идёт очередным соединением.
    def _on_data_changed(self, section: str = "") -> None:
        self.data_changed.emit(section)

    def _on_sync_started(self, kind: str = "") -> None:
        self.sync_started.emit(kind)

    def _on_sync_finished(self, kind: str = "", report=None) -> None:
        self.sync_finished.emit(report)

    def _on_sync_failed(self, kind: str = "", error: str = "") -> None:
        self.sync_failed.emit(error)

    def _on_notify(self, kind: str = "info", title: str = "", body: str = "", **kwargs) -> None:
        self.notify.emit(kind, title, body)

    def _on_lease_expiring(self, lease_id: int = 0, game: str = "", hours_left: float = 0.0) -> None:
        self.lease_event.emit("expiring", {"lease_id": lease_id, "game": game, "hours_left": hours_left})

    def _on_lease_expired(self, lease_id: int = 0, game: str = "") -> None:
        self.lease_event.emit("expired", {"lease_id": lease_id, "game": game})

    def _on_license_freed(self, game_id: int = 0, license_id: int = 0) -> None:
        self.lease_event.emit("freed", {"game_id": game_id, "license_id": license_id})
