"""Репозитории — единственный слой между сервисами/UI и ORM-запросами."""
from __future__ import annotations

from datetime import datetime
from typing import Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.database import models
from app.domain.enums import OPEN_LEASE_STATUSES, LeaseStatus


class AccountRepo:
    def __init__(self, session: Session) -> None:
        self.s = session

    def all(self) -> Sequence[models.SteamAccount]:
        return self.s.scalars(select(models.SteamAccount).order_by(models.SteamAccount.id)).all()

    def by_steam_id(self, steam_id64: str) -> Optional[models.SteamAccount]:
        return self.s.scalar(select(models.SteamAccount).where(models.SteamAccount.steam_id64 == str(steam_id64)))

    def get(self, account_id: int) -> Optional[models.SteamAccount]:
        return self.s.get(models.SteamAccount, account_id)

    def add(self, account: models.SteamAccount) -> models.SteamAccount:
        self.s.add(account)
        self.s.flush()
        return account

    def delete(self, account_id: int) -> None:
        account = self.get(account_id)
        if account:
            self.s.delete(account)


class GameRepo:
    def __init__(self, session: Session) -> None:
        self.s = session

    def all(self) -> Sequence[models.Game]:
        return self.s.scalars(select(models.Game).order_by(models.Game.name)).all()

    def by_app_id(self, app_id: int) -> Optional[models.Game]:
        return self.s.scalar(select(models.Game).where(models.Game.app_id == app_id))

    def get(self, game_id: int) -> Optional[models.Game]:
        return self.s.get(models.Game, game_id)

    def upsert_by_app_id(self, app_id: int, **fields) -> tuple[models.Game, bool]:
        game = self.by_app_id(app_id)
        created = False
        if game is None:
            game = models.Game(app_id=app_id, name=fields.pop("name", f"App {app_id}"))
            self.s.add(game)
            created = True
        for key, value in fields.items():
            if value is not None:
                setattr(game, key, value)
        self.s.flush()
        return game, created

    def owners(self, game_id: int) -> Sequence[models.GameLicense]:
        return self.s.scalars(
            select(models.GameLicense).where(models.GameLicense.game_id == game_id)
        ).all()


class LicenseRepo:
    def __init__(self, session: Session) -> None:
        self.s = session

    def for_game(self, game_id: int) -> Sequence[models.GameLicense]:
        return self.s.scalars(
            select(models.GameLicense).where(models.GameLicense.game_id == game_id)
        ).all()

    def get(self, license_id: int) -> Optional[models.GameLicense]:
        return self.s.get(models.GameLicense, license_id)

    def upsert(self, game_id: int, account_id: int, source: str) -> tuple[models.GameLicense, bool]:
        lic = self.s.scalar(
            select(models.GameLicense).where(
                models.GameLicense.game_id == game_id,
                models.GameLicense.account_id == account_id,
            )
        )
        if lic is None:
            lic = models.GameLicense(game_id=game_id, account_id=account_id, source=source)
            self.s.add(lic)
            self.s.flush()
            return lic, True
        lic.source = source
        return lic, False

    def active_lease(self, license_id: int, now: datetime) -> Optional[models.AccessLease]:
        return self.s.scalar(
            select(models.AccessLease).where(
                models.AccessLease.license_id == license_id,
                models.AccessLease.status.in_([s.value for s in OPEN_LEASE_STATUSES]),
                models.AccessLease.starts_at <= now,
                models.AccessLease.expires_at > now,
            )
        )

    def any_open_lease(self, license_id: int) -> Optional[models.AccessLease]:
        return self.s.scalar(
            select(models.AccessLease).where(
                models.AccessLease.license_id == license_id,
                models.AccessLease.status.in_([s.value for s in OPEN_LEASE_STATUSES]),
            )
        )


class LeaseRepo:
    def __init__(self, session: Session) -> None:
        self.s = session

    def open_leases(self) -> Sequence[models.AccessLease]:
        return self.s.scalars(
            select(models.AccessLease)
            .where(models.AccessLease.status.in_([s.value for s in OPEN_LEASE_STATUSES]))
            .order_by(models.AccessLease.expires_at)
        ).all()

    def all(self) -> Sequence[models.AccessLease]:
        return self.s.scalars(select(models.AccessLease).order_by(models.AccessLease.id.desc())).all()

    def for_game(self, game_id: int, now: datetime) -> Sequence[models.AccessLease]:
        return self.s.scalars(
            select(models.AccessLease).where(
                models.AccessLease.game_id == game_id,
                models.AccessLease.status.in_([s.value for s in OPEN_LEASE_STATUSES]),
                models.AccessLease.starts_at <= now,
                models.AccessLease.expires_at > now,
            )
        ).all()

    def expiring_soon(self, now: datetime, hours: float) -> Sequence[models.AccessLease]:
        from datetime import timedelta

        horizon = now + timedelta(hours=hours)
        return self.s.scalars(
            select(models.AccessLease).where(
                models.AccessLease.status.in_(
                    [LeaseStatus.ACTIVE.value, LeaseStatus.EXPIRING.value, LeaseStatus.SCHEDULED.value]
                ),
                models.AccessLease.expires_at > now,
                models.AccessLease.expires_at <= horizon,
            )
        ).all()

    def expired(self, now: datetime) -> Sequence[models.AccessLease]:
        return self.s.scalars(
            select(models.AccessLease).where(
                models.AccessLease.status.in_(
                    [LeaseStatus.ACTIVE.value, LeaseStatus.EXPIRING.value, LeaseStatus.SCHEDULED.value]
                ),
                models.AccessLease.expires_at <= now,
            )
        ).all()

    def get(self, lease_id: int) -> Optional[models.AccessLease]:
        return self.s.get(models.AccessLease, lease_id)

    def history_for_client(self, client_id: int) -> Sequence[models.AccessLease]:
        return self.s.scalars(
            select(models.AccessLease)
            .where(models.AccessLease.client_id == client_id)
            .order_by(models.AccessLease.id.desc())
        ).all()


class ClientRepo:
    def __init__(self, session: Session) -> None:
        self.s = session

    def all(self) -> Sequence[models.Client]:
        return self.s.scalars(select(models.Client).order_by(models.Client.funpay_username)).all()

    def by_username(self, username: str) -> Optional[models.Client]:
        return self.s.scalar(select(models.Client).where(models.Client.funpay_username == username))

    def get(self, client_id: int) -> Optional[models.Client]:
        return self.s.get(models.Client, client_id)

    def get_or_create(self, username: str, display_name: str | None = None) -> models.Client:
        client = self.by_username(username)
        if client is None:
            client = models.Client(funpay_username=username, display_name=display_name or username)
            self.s.add(client)
            self.s.flush()
        return client


class OrderRepo:
    def __init__(self, session: Session) -> None:
        self.s = session

    def all(self) -> Sequence[models.Order]:
        return self.s.scalars(
            select(models.Order)
            .options(selectinload(models.Order.lease))
            .order_by(models.Order.purchased_at.desc())
        ).all()

    def get(self, order_id: int) -> Optional[models.Order]:
        return self.s.scalar(
            select(models.Order)
            .options(selectinload(models.Order.lease))
            .where(models.Order.id == order_id)
        )

    def by_funpay_id(self, funpay_order_id: str) -> Optional[models.Order]:
        return self.s.scalar(select(models.Order).where(models.Order.funpay_order_id == funpay_order_id))

    def revenue_between(self, start: datetime, end: datetime) -> float:
        value = self.s.scalar(
            select(func.coalesce(func.sum(models.Order.price), 0.0)).where(
                models.Order.purchased_at >= start,
                models.Order.purchased_at < end,
                models.Order.status.not_in(["cancelled"]),
            )
        )
        return float(value or 0.0)

    def top_games_by_revenue(self, limit: int = 10) -> Sequence[tuple]:
        return self.s.execute(
            select(
                models.Game.name,
                func.count(models.Order.id).label("sales"),
                func.coalesce(func.sum(models.Order.price), 0.0).label("revenue"),
            )
            .join(models.Order, models.Order.game_id == models.Game.id)
            .where(models.Order.status.not_in(["cancelled"]))
            .group_by(models.Game.id)
            .order_by(func.sum(models.Order.price).desc())
            .limit(limit)
        ).all()


class ListingRepo:
    def __init__(self, session: Session) -> None:
        self.s = session

    def all(self) -> Sequence[models.Listing]:
        return self.s.scalars(select(models.Listing).order_by(models.Listing.updated_at.desc())).all()

    def get(self, listing_id: int) -> Optional[models.Listing]:
        return self.s.get(models.Listing, listing_id)

    def for_game(self, game_id: int) -> Sequence[models.Listing]:
        return self.s.scalars(select(models.Listing).where(models.Listing.game_id == game_id)).all()


class BundleRepo:
    def __init__(self, session: Session) -> None:
        self.s = session

    def all(self) -> Sequence[models.Bundle]:
        return self.s.scalars(select(models.Bundle).order_by(models.Bundle.created_at.desc())).all()

    def get(self, bundle_id: int) -> Optional[models.Bundle]:
        return self.s.get(models.Bundle, bundle_id)


class AuditRepo:
    def __init__(self, session: Session) -> None:
        self.s = session

    def add(self, actor: str, action: str, entity: str | None = None,
            entity_id: str | None = None, details: dict | None = None) -> models.AuditLog:
        entry = models.AuditLog(
            actor=actor, action=action, entity=entity,
            entity_id=str(entity_id) if entity_id is not None else None,
            details=details,
        )
        self.s.add(entry)
        self.s.flush()
        return entry

    def latest(self, limit: int = 200) -> Sequence[models.AuditLog]:
        return self.s.scalars(select(models.AuditLog).order_by(models.AuditLog.id.desc()).limit(limit)).all()


class NotificationRepo:
    def __init__(self, session: Session) -> None:
        self.s = session

    def add(self, kind: str, title: str, body: str | None = None) -> models.Notification:
        item = models.Notification(kind=kind, title=title, body=body)
        self.s.add(item)
        self.s.flush()
        return item

    def unread(self) -> Sequence[models.Notification]:
        return self.s.scalars(
            select(models.Notification)
            .where(models.Notification.read.is_(False))
            .order_by(models.Notification.id.desc())
        ).all()

    def latest(self, limit: int = 100) -> Sequence[models.Notification]:
        return self.s.scalars(select(models.Notification).order_by(models.Notification.id.desc()).limit(limit)).all()

    def mark_read(self, notification_id: int) -> None:
        item = self.s.get(models.Notification, notification_id)
        if item:
            item.read = True

    def mark_all_read(self) -> None:
        for item in self.unread():
            item.read = True


class SyncRepo:
    def __init__(self, session: Session) -> None:
        self.s = session

    def start(self, kind: str, now: datetime) -> models.SyncHistory:
        item = models.SyncHistory(kind=kind, started_at=now, status="running")
        self.s.add(item)
        self.s.flush()
        return item

    def finish(self, item: models.SyncHistory, status: str, now: datetime, details: dict | None = None) -> None:
        item.finished_at = now
        item.status = status
        item.details = details

    def latest(self, kind: str | None = None) -> Optional[models.SyncHistory]:
        stmt = select(models.SyncHistory).order_by(models.SyncHistory.id.desc())
        if kind:
            stmt = stmt.where(models.SyncHistory.kind == kind)
        return self.s.scalar(stmt.limit(1))


class MessageRepo:
    def __init__(self, session: Session) -> None:
        self.s = session

    def add(self, message: models.Message) -> models.Message:
        self.s.add(message)
        self.s.flush()
        return message

    def drafted(self) -> Sequence[models.Message]:
        return self.s.scalars(
            select(models.Message)
            .where(models.Message.direction == "out", models.Message.status.in_(["drafted", "approved"]))
            .order_by(models.Message.id.desc())
        ).all()

    def for_order(self, order_id: int) -> Sequence[models.Message]:
        return self.s.scalars(
            select(models.Message).where(models.Message.order_id == order_id).order_by(models.Message.id)
        ).all()
