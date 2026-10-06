"""Журнал значимых действий (см. ТЗ §20)."""
from __future__ import annotations

from contextlib import nullcontext

from sqlalchemy.orm import Session

from app.core.clock import Clock
from app.database import models, repositories
from app.database.session import Database
from app.domain.enums import Actor, AuditAction


class AuditService:
    def __init__(self, db: Database, clock: Clock) -> None:
        self.db = db
        self.clock = clock

    def log(
        self,
        action: AuditAction | str,
        actor: Actor = Actor.APP,
        entity: str | None = None,
        entity_id: str | int | None = None,
        session: Session | None = None,
        **details,
    ) -> None:
        ctx = nullcontext(session) if session is not None else self.db.session()
        with ctx as s:
            repositories.AuditRepo(s).add(
                actor=actor.value if isinstance(actor, Actor) else str(actor),
                action=action.value if isinstance(action, AuditAction) else str(action),
                entity=entity,
                entity_id=entity_id,
                details=details or None,
            )

    def latest(self, limit: int = 200) -> list[models.AuditLog]:
        with self.db.session() as s:
            return list(repositories.AuditRepo(s).latest(limit))
