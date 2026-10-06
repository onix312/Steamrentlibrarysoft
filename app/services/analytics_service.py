"""Аналитика и рекомендации (ТЗ §17). Рекомендации отделены от фактов."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.core.clock import Clock
from app.database import models, repositories
from app.database.session import Database
from app.domain.enums import EligibilityStatus, OrderStatus


@dataclass(frozen=True)
class Recommendation:
    kind: str      # buy_copy | drop_listing | raise_price | create_listing
    game_id: int | None
    text: str


class AnalyticsService:
    def __init__(self, db: Database, clock: Clock) -> None:
        self.db = db
        self.clock = clock

    @staticmethod
    def _utc(dt: datetime | None) -> datetime:
        if dt is None:
            return datetime.now(timezone.utc)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

    # ------------------------------------------------------------ Dashboard
    def dashboard(self) -> dict:
        now = self.clock.now()
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        with self.db.session() as session:
            order_repo = repositories.OrderRepo(session)
            lease_repo = repositories.LeaseRepo(session)

            new_today = len([
                o for o in order_repo.all()
                if self._utc(o.purchased_at) >= day_start and o.status != "cancelled"
            ])
            revenue_today = order_repo.revenue_between(day_start, day_start + timedelta(days=1))
            revenue_7d = order_repo.revenue_between(now - timedelta(days=7), now)
            revenue_30d = order_repo.revenue_between(now - timedelta(days=30), now)
            revenue_all = order_repo.revenue_between(datetime(2000, 1, 1, tzinfo=timezone.utc), now + timedelta(days=1))

            open_leases = list(lease_repo.open_leases())
            active_client_ids = {lease.client_id for lease in open_leases}
            expiring = list(lease_repo.expiring_soon(now, 24))

            games = list(session.scalars(select(models.Game)))
            sellable = [g for g in games if not g.is_free and g.eligibility_status == EligibilityStatus.AVAILABLE.value]
            licenses = list(session.scalars(select(models.GameLicense)))
            busy_license_ids = {
                row[0] for row in session.execute(
                    select(models.AccessLease.license_id)
                    .where(models.AccessLease.status.in_(["scheduled", "active", "expiring"]),
                           models.AccessLease.starts_at <= now, models.AccessLease.expires_at > now)
                )
            }
            accounts = list(session.scalars(select(models.SteamAccount)))
            error_accounts = [a for a in accounts if a.status == "error"]
            listings = list(session.scalars(select(models.Listing)))
            top_games = [tuple(row) for row in order_repo.top_games_by_revenue(5)]
            unread_notifications = len(repositories.NotificationRepo(session).unread())

        return {
            "today": {
                "new_orders": new_today,
                "active_clients": len(active_client_ids),
                "revenue": revenue_today,
                "expiring_leases": len(expiring),
            },
            "library": {
                "total_games": len(games),
                "sellable_games": len(sellable),
                "licenses_total": len(licenses),
                "licenses_busy": len(busy_license_ids),
                "licenses_free": len(licenses) - len(busy_license_ids),
            },
            "steam": {
                "accounts": len(accounts),
                "errors": len(error_accounts),
                "last_sync": max((a.last_sync_at for a in accounts if a.last_sync_at), default=None),
            },
            "funpay": {
                "active_listings": len([l for l in listings if l.status == "active"]),
                "ready_listings": len([l for l in listings if l.status == "ready"]),
            },
            "finance": {
                "today": revenue_today,
                "days_7": revenue_7d,
                "days_30": revenue_30d,
                "all_time": revenue_all,
            },
            "top_games": top_games,
            "unread_notifications": unread_notifications,
        }

    # -------------------------------------------------------------- графики
    def revenue_per_day(self, days: int = 30) -> list[tuple[str, float]]:
        now = self.clock.now()
        start = (now - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
        buckets: dict[str, float] = defaultdict(float)
        with self.db.session() as session:
            orders = session.scalars(
                select(models.Order).where(models.Order.purchased_at >= start,
                                           models.Order.status != "cancelled")
            ).all()
            for order in orders:
                buckets[self._utc(order.purchased_at).date().isoformat()] += order.price
        result: list[tuple[str, float]] = []
        for i in range(days):
            day = (start + timedelta(days=i)).date().isoformat()
            result.append((day, buckets.get(day, 0.0)))
        return result

    def sales_per_game(self, limit: int = 15) -> list[tuple[str, int, float]]:
        with self.db.session() as session:
            rows = repositories.OrderRepo(session).top_games_by_revenue(limit)
            return [(str(name), int(sales), float(revenue)) for name, sales, revenue in rows]

    def license_utilization(self) -> list[tuple[str, int, int]]:
        """(игра, всего копий, занято) — только по продаваемым."""
        now = self.clock.now()
        result = []
        with self.db.session() as session:
            from app.services.access_service import LicenseAllocator

            allocator = LicenseAllocator(session, self.clock)
            games = session.scalars(select(models.Game)).all()
            for game in games:
                if game.is_free or game.eligibility_status != EligibilityStatus.AVAILABLE.value:
                    continue
                availability = allocator.availability(game.id)
                if availability.total:
                    result.append((game.name, availability.total, availability.busy))
        return sorted(result, key=lambda row: (-row[2], -row[1]))

    def repeat_clients(self) -> int:
        with self.db.session() as session:
            rows = session.execute(
                select(models.Order.client_id, func.count(models.Order.id))
                .where(models.Order.status != "cancelled")
                .group_by(models.Order.client_id)
            ).all()
        return sum(1 for _, count in rows if count > 1)

    def average_check(self) -> float:
        with self.db.session() as session:
            value = session.scalar(
                select(func.avg(models.Order.price)).where(models.Order.status != "cancelled")
            )
        return float(value or 0.0)

    # ------------------------------------------------------- рекомендации
    def recommendations(self) -> list[Recommendation]:
        """Эвристики. Это СОВЕТЫ, а не факты — в UI выводятся отдельным блоком."""
        now = self.clock.now()
        recs: list[Recommendation] = []
        with self.db.session() as session:
            from app.services.access_service import LicenseAllocator

            allocator = LicenseAllocator(session, self.clock)
            lease_repo = repositories.LeaseRepo(session)
            games = list(session.scalars(select(models.Game)))
            listings_by_game = {
                listing.game_id: listing
                for listing in session.scalars(select(models.Listing)).all()
            }
            last_order_by_game: dict[int, datetime] = {}
            for order in session.scalars(select(models.Order)).all():
                if order.game_id is None:
                    continue
                prev = last_order_by_game.get(order.game_id)
                if prev is None or order.purchased_at > prev:
                    last_order_by_game[order.game_id] = self._utc(order.purchased_at)

            for game in games:
                availability = allocator.availability(game.id)
                sellable = (not game.is_free) and game.eligibility_status == EligibilityStatus.AVAILABLE.value

                # Постоянно занятые копии + высокий спрос → докупить копию.
                if sellable and availability.total > 0 and availability.free == 0 and game.demand_score >= 60:
                    recs.append(Recommendation(
                        "buy_copy", game.id,
                        f"«{game.name}» постоянно занята ({availability.busy}/{availability.total} копий) "
                        f"при Demand Score {game.demand_score:.0f} — имеет смысл приобрести вторую копию.",
                    ))
                # Давно не продавалась → снять объявление.
                listing = listings_by_game.get(game.id)
                last_sale = last_order_by_game.get(game.id)
                if listing is not None and listing.status == "active" and (
                    last_sale is None or (now - last_sale) > timedelta(days=60)
                ):
                    recs.append(Recommendation(
                        "drop_listing", game.id,
                        f"«{game.name}» не продавалась более 60 дней — можно отключить объявление.",
                    ))
                # Высокий спрос, но подозрительно низкая наша цена.
                if sellable and game.demand_score >= 75 and listing is not None and game.price:
                    if listing.price < game.price * 0.15:
                        recs.append(Recommendation(
                            "raise_price", game.id,
                            f"Спрос на «{game.name}» высокий ({game.demand_score:.0f}), "
                            f"а цена объявления ниже 15% цены Steam — проверьте, не занижена ли она.",
                        ))
                # Продаваемая игра без объявления.
                if sellable and game.demand_grade in ("S", "A") and listing is None:
                    recs.append(Recommendation(
                        "create_listing", game.id,
                        f"«{game.name}» имеет грейд {game.demand_grade}, но объявления нет — создайте его.",
                    ))
        return recs
