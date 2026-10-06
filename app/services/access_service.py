"""Управление доступами: AccessLease + LicenseAllocator (ТЗ §12).

Инварианты:
* одна лицензия-копия — не более одной ОТКРЫТОЙ аренды одновременно
  (частичный уникальный индекс в БД + проверка в транзакции);
* аренда с истёкшим сроком автоматически освобождает лицензию;
* аренда всегда указывает конкретные лицензию и аккаунт-владельца.
"""
from __future__ import annotations

import logging
from contextlib import nullcontext
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.clock import Clock
from app.core.events import (
    TOPIC_DATA_CHANGED,
    TOPIC_LEASE_EXPIRED,
    TOPIC_LEASE_EXPIRING,
    TOPIC_LICENSE_FREED,
    TOPIC_NOTIFY,
    EventBus,
)
from app.database import models, repositories
from app.database.session import Database
from app.domain.enums import (
    Actor,
    AuditAction,
    LeaseStatus,
    NotificationKind,
    OPEN_LEASE_STATUSES,
    OrderStatus,
)
from app.domain.value_objects import AllocationError, Availability
from app.services.audit_service import AuditService

log = logging.getLogger(__name__)


class LicenseAllocator:
    """Выбирает оптимальную свободную копию игры. Работает внутри сессии."""

    def __init__(self, session: Session, clock: Clock) -> None:
        self.s = session
        self.clock = clock

    # ----------------------------------------------------------- запросы
    def licenses(self, game_id: int) -> list[models.GameLicense]:
        return list(
            self.s.scalars(
                select(models.GameLicense).where(models.GameLicense.game_id == game_id)
            )
        )

    def busy_license_ids(self, game_id: int, at: datetime | None = None) -> set[int]:
        now = at or self.clock.now()
        rows = self.s.execute(
            select(models.AccessLease.license_id)
            .where(
                models.AccessLease.game_id == game_id,
                models.AccessLease.status.in_([s.value for s in OPEN_LEASE_STATUSES]),
                models.AccessLease.starts_at <= now,
                models.AccessLease.expires_at > now,
            )
        ).all()
        return {int(row[0]) for row in rows}

    def availability(self, game_id: int, at: datetime | None = None) -> Availability:
        licenses = [lic for lic in self.licenses(game_id) if not lic.hidden_from_family]
        busy = self.busy_license_ids(game_id, at)
        return Availability(total=len(licenses), busy=len(busy & {lic.id for lic in licenses}))

    def free_licenses(self, game_id: int, at: datetime | None = None) -> list[models.GameLicense]:
        now = at or self.clock.now()
        busy = self.busy_license_ids(game_id, now)
        free = [lic for lic in self.licenses(game_id) if not lic.hidden_from_family and lic.id not in busy]
        return free

    # ------------------------------------------------------------- выбор
    def allocate(self, game_id: int, at: datetime | None = None) -> models.GameLicense:
        free = self.free_licenses(game_id, at)
        if not free:
            availability = self.availability(game_id, at)
            raise AllocationError(
                f"Нет свободных копий игры (всего {availability.total}, занято {availability.busy}). "
                "Дождитесь окончания аренды или добавьте копию."
            )
        # Равномерная загрузка копий: выбираем лицензию с наименьшим числом прошлых аренд.
        usage = dict(
            self.s.execute(
                select(models.AccessLease.license_id, func.count(models.AccessLease.id))
                .where(models.AccessLease.license_id.in_([lic.id for lic in free]))
                .group_by(models.AccessLease.license_id)
            ).all()
        )
        free.sort(key=lambda lic: (usage.get(lic.id, 0), lic.id))
        return free[0]


class AccessService:
    def __init__(self, db: Database, clock: Clock, events: EventBus, audit: AuditService,
                 warn_hours: int = 24) -> None:
        self.db = db
        self.clock = clock
        self.events = events
        self.audit = audit
        self.warn_hours = warn_hours

    # -------------------------------------------------------------- аренду
    def create_lease(
        self,
        client_id: int,
        game_id: int,
        days: int | None = None,
        expires_at: datetime | None = None,
        starts_at: datetime | None = None,
        order_id: int | None = None,
        license_id: int | None = None,
        session: Session | None = None,
    ) -> models.AccessLease:
        if days is None and expires_at is None:
            raise ValueError("Укажите срок доступа: дни или дату окончания.")
        now = self.clock.now()
        start = starts_at or now
        end = expires_at or (start + timedelta(days=int(days or 0)))
        if end <= start:
            raise ValueError("Дата окончания аренды должна быть позже даты начала.")

        ctx = nullcontext(session) if session is not None else self.db.session()
        with ctx as s:
            allocator = LicenseAllocator(s, self.clock)
            if license_id is not None:
                license_ = s.get(models.GameLicense, license_id)
                if license_ is None or license_.game_id != game_id:
                    raise AllocationError("Выбранная лицензия не найдена или принадлежит другой игре.")
                busy = allocator.busy_license_ids(game_id, start)
                if license_id in busy:
                    raise AllocationError("Выбранная лицензия уже занята другой активной арендой.")
            else:
                license_ = allocator.allocate(game_id, start)

            lease = models.AccessLease(
                client_id=client_id,
                order_id=order_id,
                game_id=game_id,
                owner_account_id=license_.account_id,
                license_id=license_.id,
                starts_at=start,
                expires_at=end,
                status=LeaseStatus.ACTIVE.value,
            )
            s.add(lease)
            s.flush()

            if order_id is not None:
                order = s.get(models.Order, order_id)
                if order is not None:
                    order.assigned_license_id = license_.id
                    order.access_starts_at = start
                    order.access_ends_at = end
                    order.status = OrderStatus.ACTIVE.value

            game = s.get(models.Game, game_id)
            account = s.get(models.SteamAccount, license_.account_id)
            self.audit.log(
                AuditAction.LEASE_CREATED, Actor.APP, entity="access_lease", entity_id=lease.id,
                session=s, game=game.name if game else game_id,
                license_id=license_.id, account=account.display_name if account else license_.account_id,
                expires_at=end.isoformat(),
            )
            self.audit.log(
                AuditAction.LICENSE_ASSIGNED, Actor.APP, entity="game_license", entity_id=license_.id,
                session=s, lease_id=lease.id, client_id=client_id,
            )
            lease_id = lease.id

        self.events.publish(TOPIC_DATA_CHANGED, section="leases")
        return self.get(lease_id)

    def get(self, lease_id: int) -> models.AccessLease | None:
        with self.db.session() as s:
            return repositories.LeaseRepo(s).get(lease_id)

    # ------------------------------------------------------------- статусы
    def extend_lease(self, lease_id: int, extra_days: int) -> models.AccessLease:
        with self.db.session() as s:
            lease = s.get(models.AccessLease, lease_id)
            if lease is None:
                raise AllocationError("Аренда не найдена.")
            lease.expires_at = lease.expires_at + timedelta(days=extra_days)
            if lease.status == LeaseStatus.EXPIRED.value:
                lease.status = LeaseStatus.ACTIVE.value
                lease.terminated_at = None
            self.audit.log(AuditAction.LEASE_EXTENDED, Actor.USER, entity="access_lease",
                            entity_id=lease_id, session=s, extra_days=extra_days,
                            new_expires_at=lease.expires_at.isoformat())
        self.events.publish(TOPIC_DATA_CHANGED, section="leases")
        return self.get(lease_id)

    def complete_lease(self, lease_id: int) -> None:
        self._close(lease_id, LeaseStatus.COMPLETED, AuditAction.LEASE_COMPLETED)

    def cancel_lease(self, lease_id: int) -> None:
        self._close(lease_id, LeaseStatus.CANCELLED, AuditAction.LEASE_CANCELLED)

    def _close(self, lease_id: int, status: LeaseStatus, action: AuditAction) -> None:
        with self.db.session() as s:
            lease = s.get(models.AccessLease, lease_id)
            if lease is None:
                return
            lease.status = status.value
            lease.terminated_at = self.clock.now()
            game_id = lease.game_id
            license_id = lease.license_id
            if lease.order_id is not None:
                order = s.get(models.Order, lease.order_id)
                if order is not None and order.status not in (OrderStatus.CANCELLED.value,):
                    order.status = (
                        OrderStatus.COMPLETED.value if status == LeaseStatus.COMPLETED
                        else OrderStatus.CANCELLED.value
                    )
            self.audit.log(action, Actor.USER, entity="access_lease", entity_id=lease_id, session=s)
            self.audit.log(AuditAction.LICENSE_RELEASED, Actor.APP, entity="game_license",
                            entity_id=license_id, session=s, lease_id=lease_id)
        self.events.publish(TOPIC_LICENSE_FREED, game_id=game_id, license_id=license_id)
        self.events.publish(TOPIC_DATA_CHANGED, section="leases")

    # ------------------------------------------------------ истечение срока
    def process_expirations(self) -> dict:
        """Плановая проверка: истёкшие аренды закрываем, о скорых — предупреждаем."""
        now = self.clock.now()
        result = {"expired": [], "warning": []}
        with self.db.session() as s:
            lease_repo = repositories.LeaseRepo(s)

            # 1) истёкшие → освобождаем лицензии автоматически
            for lease in lease_repo.expired(now):
                lease.status = LeaseStatus.EXPIRED.value
                lease.terminated_at = now
                if lease.order_id is not None:
                    order = s.get(models.Order, lease.order_id)
                    if order is not None and order.status in (
                        OrderStatus.ACTIVE.value, OrderStatus.EXPIRING.value
                    ):
                        order.status = OrderStatus.COMPLETED.value
                game = s.get(models.Game, lease.game_id)
                game_name = game.name if game else str(lease.game_id)
                self.audit.log(AuditAction.LEASE_EXPIRED, Actor.SCHEDULER, entity="access_lease",
                                entity_id=lease.id, session=s, game=game_name)
                result["expired"].append(lease.id)
                self.events.publish(TOPIC_LEASE_EXPIRED, lease_id=lease.id, game=game_name)

            # 2) скоро истекают → статус EXPIRING + однократное уведомление
            for lease in lease_repo.expiring_soon(now, self.warn_hours):
                if lease.status == LeaseStatus.ACTIVE.value:
                    lease.status = LeaseStatus.EXPIRING.value
                    game = s.get(models.Game, lease.game_id)
                    game_name = game.name if game else str(lease.game_id)
                    hours_left = max(0.0, (lease.expires_at - now).total_seconds() / 3600)
                    self.audit.log(AuditAction.ORDER_STATUS_CHANGED, Actor.SCHEDULER,
                                    entity="access_lease", entity_id=lease.id, session=s,
                                    new_status="expiring")
                    result["warning"].append(lease.id)
                    self.events.publish(TOPIC_LEASE_EXPIRING, lease_id=lease.id,
                                        game=game_name, hours_left=round(hours_left, 1))

        if result["expired"] or result["warning"]:
            self.events.publish(TOPIC_DATA_CHANGED, section="leases")
        return result

    # -------------------------------------------------------------- отчёты
    def availability_map(self, game_ids: list[int] | None = None) -> dict[int, Availability]:
        with self.db.session() as s:
            allocator = LicenseAllocator(s, self.clock)
            ids = game_ids if game_ids is not None else [
                g.id for g in s.scalars(select(models.Game.id))
            ]
            return {game_id: allocator.availability(game_id) for game_id in ids}
