"""Центр уведомлений: запись в БД + публикация события для GUI/трея."""
from __future__ import annotations

import logging

from app.core.clock import Clock
from app.core.events import TOPIC_NOTIFY, EventBus
from app.database import models, repositories
from app.database.session import Database
from app.domain.enums import NotificationKind

log = logging.getLogger(__name__)


class NotificationService:
    def __init__(self, db: Database, clock: Clock, events: EventBus) -> None:
        self.db = db
        self.clock = clock
        self.events = events

    def notify(self, kind: NotificationKind | str, title: str, body: str | None = None) -> models.Notification:
        kind_value = kind.value if isinstance(kind, NotificationKind) else str(kind)
        with self.db.session() as session:
            item = repositories.NotificationRepo(session).add(kind_value, title, body)
            notification_id = item.id
        log.info("Уведомление [%s]: %s", kind_value, title)
        self.events.publish(TOPIC_NOTIFY, kind=kind_value, title=title, body=body or "", notification_id=notification_id)
        return item

    def unread(self) -> list[models.Notification]:
        with self.db.session() as session:
            return list(repositories.NotificationRepo(session).unread())

    def latest(self, limit: int = 100) -> list[models.Notification]:
        with self.db.session() as session:
            return list(repositories.NotificationRepo(session).latest(limit))

    def mark_read(self, notification_id: int) -> None:
        with self.db.session() as session:
            repositories.NotificationRepo(session).mark_read(notification_id)

    def mark_all_read(self) -> None:
        with self.db.session() as session:
            repositories.NotificationRepo(session).mark_all_read()
