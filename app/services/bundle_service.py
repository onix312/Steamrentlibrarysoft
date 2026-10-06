"""Пакеты игр: ручные + автопредложения по жанрам и спросу (ТЗ §9)."""
from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.clock import Clock
from app.core.events import TOPIC_DATA_CHANGED, EventBus
from app.database import models
from app.database.session import Database
from app.domain.enums import AuditAction, BundleStatus, DemandGrade, EligibilityStatus
from app.services.audit_service import AuditService

SELLABLE_GRADES = {DemandGrade.S, DemandGrade.A, DemandGrade.B}
MIN_GAMES_IN_BUNDLE = 3
MAX_GAMES_IN_BUNDLE = 5
MIN_DEMAND_FOR_SUGGESTION = 40.0


class BundleService:
    def __init__(self, db: Database, clock: Clock, events: EventBus, audit: AuditService) -> None:
        self.db = db
        self.clock = clock
        self.events = events
        self.audit = audit

    # ------------------------------------------------------------------ API
    def all(self) -> list[models.Bundle]:
        with self.db.session() as session:
            return list(session.scalars(
                select(models.Bundle)
                .options(selectinload(models.Bundle.games))
                .order_by(models.Bundle.created_at.desc())
            ))

    def get(self, bundle_id: int) -> models.Bundle | None:
        with self.db.session() as session:
            return session.scalar(
                select(models.Bundle)
                .options(selectinload(models.Bundle.games))
                .where(models.Bundle.id == bundle_id)
            )

    def create(self, name: str, game_ids: list[int], price: float = 0.0,
               description: str = "", status: BundleStatus = BundleStatus.ACTIVE,
               suggested_reason: str | None = None) -> models.Bundle:
        with self.db.session() as session:
            bundle = models.Bundle(name=name, description=description, price=float(price),
                                   status=status.value, suggested_reason=suggested_reason)
            session.add(bundle)
            session.flush()
            for game_id in dict.fromkeys(game_ids):
                session.add(models.BundleGame(bundle_id=bundle.id, game_id=game_id))
            self.audit.log(AuditAction.BUNDLE_CREATED, entity="bundle", entity_id=bundle.id,
                            session=session, name=name, games=len(game_ids))
            bundle_id = bundle.id
        self.events.publish(TOPIC_DATA_CHANGED, section="bundles")
        return self.get(bundle_id)

    def set_status(self, bundle_id: int, status: BundleStatus) -> None:
        with self.db.session() as session:
            bundle = session.get(models.Bundle, bundle_id)
            if bundle is not None:
                bundle.status = status.value
        self.events.publish(TOPIC_DATA_CHANGED, section="bundles")

    def delete(self, bundle_id: int) -> None:
        with self.db.session() as session:
            bundle = session.get(models.Bundle, bundle_id)
            if bundle is not None:
                session.delete(bundle)
        self.events.publish(TOPIC_DATA_CHANGED, section="bundles")

    # ----------------------------------------------------------- предложения
    def suggest(self) -> list[models.Bundle]:
        """Предлагает пакеты по общему ведущему жанру среди продаваемых игр."""
        created: list[models.Bundle] = []
        with self.db.session() as session:
            games = list(session.scalars(select(models.Game)))
            sellable = [
                g for g in games
                if not g.is_free
                and g.eligibility_status == EligibilityStatus.AVAILABLE.value
                and DemandGrade(g.demand_grade) in SELLABLE_GRADES
                and g.demand_score >= MIN_DEMAND_FOR_SUGGESTION
            ]
            by_genre: dict[str, list[models.Game]] = defaultdict(list)
            for game in sellable:
                genres = game.genres or []
                if genres:
                    by_genre[genres[0]].append(game)

            existing_names = {b.name for b in session.scalars(select(models.Bundle))}
            for genre, group in sorted(by_genre.items(), key=lambda kv: -len(kv[1])):
                if len(group) < MIN_GAMES_IN_BUNDLE:
                    continue
                name = f"{genre} Pack"
                if name in existing_names:
                    continue
                top = sorted(group, key=lambda g: -g.demand_score)[:MAX_GAMES_IN_BUNDLE]
                price = round(sum(g.price or 0 for g in top) * 0.2, -1)
                bundle = models.Bundle(
                    name=name,
                    description=f"Автоподборка: {len(top)} игр жанра «{genre}» с высоким спросом.",
                    price=max(199.0, price),
                    status=BundleStatus.SUGGESTED.value,
                    suggested_reason=(
                        f"Собрано автоматически: общий жанр «{genre}», "
                        f"средний Demand Score {sum(g.demand_score for g in top) / len(top):.0f}."
                    ),
                )
                session.add(bundle)
                session.flush()
                for game in top:
                    session.add(models.BundleGame(bundle_id=bundle.id, game_id=game.id))
                created.append(bundle)
                existing_names.add(name)
            created_ids = [bundle.id for bundle in created]
        if created:
            self.events.publish(TOPIC_DATA_CHANGED, section="bundles")
        return [self.get(bundle_id) for bundle_id in created_ids if bundle_id]
