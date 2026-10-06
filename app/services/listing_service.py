"""Объявления: генерация текстов и локальный учёт (ТЗ §8).

Публикация на FunPay — ручная (см. FunPayAdapter). Здесь готовим и храним
редактируемые тексты, цены и статусы.
"""
from __future__ import annotations

from sqlalchemy import select

from app.core.clock import Clock
from app.core.events import TOPIC_DATA_CHANGED, EventBus
from app.database import models, repositories
from app.database.session import Database
from app.domain.enums import Actor, AuditAction, ListingStatus
from app.integrations.funpay.templates import build_listing_text
from app.services.access_service import AccessService
from app.services.audit_service import AuditService


class ListingService:
    def __init__(self, db: Database, clock: Clock, events: EventBus,
                 audit: AuditService, access: AccessService) -> None:
        self.db = db
        self.clock = clock
        self.events = events
        self.audit = audit
        self.access = access

    # ------------------------------------------------------------- генерация
    def generate_for_game(self, game_id: int, access_days: int = 30, price: float | None = None) -> models.Listing:
        with self.db.session() as session:
            game = session.get(models.Game, game_id)
            if game is None:
                raise ValueError("Игра не найдена.")
            availability = self._availability(session, game_id)
            owner_names = self._owner_names(session, game_id)
            price_value = price if price is not None else max(99.0, round(game.price * 0.25, -1))
            text = build_listing_text([game.name], access_days, price_value, availability.free, owner_names)
            listing = self._upsert(session, game_id=game_id, bundle_id=None,
                                   game_names=[game.name], access_days=access_days,
                                   price_value=price_value, text=text)
            listing_id = listing.id
        self.events.publish(TOPIC_DATA_CHANGED, section="listings")
        return self.get(listing_id)

    def generate_for_bundle(self, bundle_id: int, access_days: int = 30, price: float | None = None) -> models.Listing:
        with self.db.session() as session:
            bundle = session.get(models.Bundle, bundle_id)
            if bundle is None:
                raise ValueError("Пакет не найден.")
            game_ids = [bg.game_id for bg in bundle.games]
            games = [session.get(models.Game, gid) for gid in game_ids]
            names = [g.name for g in games if g is not None]
            total_free = min((self._availability(session, gid).free for gid in game_ids), default=0)
            price_value = price if price is not None else (bundle.price or max(99.0, sum(g.price or 0 for g in games if g) * 0.2))
            text = build_listing_text(names, access_days, price_value, total_free, [], bundle_name=bundle.name)
            listing = self._upsert(session, game_id=None, bundle_id=bundle_id,
                                   game_names=names, access_days=access_days,
                                   price_value=price_value, text=text)
            listing_id = listing.id
        self.events.publish(TOPIC_DATA_CHANGED, section="listings")
        return self.get(listing_id)

    # ------------------------------------------------------------ управление
    def all(self) -> list[models.Listing]:
        with self.db.session() as session:
            return list(repositories.ListingRepo(session).all())

    def get(self, listing_id: int) -> models.Listing | None:
        with self.db.session() as session:
            return repositories.ListingRepo(session).get(listing_id)

    def update_texts(self, listing_id: int, **fields) -> None:
        allowed = {"title", "short_description", "description", "faq", "terms", "post_purchase_template"}
        with self.db.session() as session:
            listing = repositories.ListingRepo(session).get(listing_id)
            if listing is None:
                return
            for key, value in fields.items():
                if key in allowed:
                    setattr(listing, key, value)
            listing.updated_at = self.clock.now()
            self.audit.log(AuditAction.LISTING_UPDATED, Actor.USER, entity="listing",
                            entity_id=listing_id, session=session, fields=list(fields))
        self.events.publish(TOPIC_DATA_CHANGED, section="listings")

    def set_status(self, listing_id: int, status: ListingStatus) -> None:
        with self.db.session() as session:
            listing = repositories.ListingRepo(session).get(listing_id)
            if listing is None:
                return
            listing.status = status.value
            listing.updated_at = self.clock.now()
            self.audit.log(AuditAction.LISTING_STATUS_CHANGED, Actor.USER, entity="listing",
                            entity_id=listing_id, session=session, new_status=status.value)
        self.events.publish(TOPIC_DATA_CHANGED, section="listings")

    def set_price(self, listing_id: int, price: float) -> None:
        with self.db.session() as session:
            listing = repositories.ListingRepo(session).get(listing_id)
            if listing is None:
                return
            old = listing.price
            listing.price = float(price)
            listing.updated_at = self.clock.now()
            self.audit.log(AuditAction.PRICE_CHANGED, Actor.USER, entity="listing",
                            entity_id=listing_id, session=session, old=old, new=price)
        self.events.publish(TOPIC_DATA_CHANGED, section="listings")

    def record_sale(self, listing_id: int, price: float) -> None:
        with self.db.session() as session:
            listing = repositories.ListingRepo(session).get(listing_id)
            if listing is None:
                return
            listing.sales_count += 1
            listing.revenue += float(price)
            listing.updated_at = self.clock.now()

    # --------------------------------------------------------------- внутри
    def _upsert(self, session, *, game_id, bundle_id, game_names, access_days, price_value, text) -> models.Listing:
        listing = session.scalar(
            select(models.Listing).where(
                models.Listing.game_id == game_id,
                models.Listing.bundle_id == bundle_id,
            )
        )
        if listing is None:
            listing = models.Listing(game_id=game_id, bundle_id=bundle_id, title=text.title)
            session.add(listing)
        listing.title = text.title
        listing.short_description = text.short_description
        listing.description = text.description
        listing.faq = text.faq
        listing.terms = text.terms
        listing.post_purchase_template = text.post_purchase
        listing.access_days = access_days
        listing.price = price_value
        listing.status = ListingStatus.READY.value
        listing.updated_at = self.clock.now()
        session.flush()
        self.audit.log(AuditAction.LISTING_CREATED, Actor.APP, entity="listing",
                        entity_id=listing.id, session=session, games=game_names)
        return listing

    def _availability(self, session, game_id: int):
        from app.services.access_service import LicenseAllocator

        return LicenseAllocator(session, self.clock).availability(game_id)

    @staticmethod
    def _owner_names(session, game_id: int) -> list[str]:
        rows = session.execute(
            select(models.SteamAccount.display_name)
            .join(models.GameLicense, models.GameLicense.account_id == models.SteamAccount.id)
            .where(models.GameLicense.game_id == game_id)
        ).all()
        return [row[0] for row in rows]
