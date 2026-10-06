"""Сервисы Twitch Drops: аккаунты, кампании, сессии, верификация,
оценка стоимости и приоритеты планировщика (ТЗ §28–34).

Принципы:
* прогресс просмотра — только ОЦЕНКА локального таймера;
  фактический прогресс подтверждается пользователем по инвентарю;
* клэйм наград и просмотр — всегда действия пользователя;
* переходы статусов аккаунтов-стока — атомарные условные UPDATE.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import select, update

from app.core.clock import Clock
from app.core.config import AppConfig
from app.core.events import TOPIC_DATA_CHANGED, EventBus
from app.core.secret_store import SecretStore
from app.database import models
from app.database.session import Database
from app.domain.enums import (
    Actor,
    ClaimState,
    DropAccountStatus,
    DropCampaignStatus,
    SellRecommendation,
    TwitchAccountStatus,
    WatchSessionStatus,
)
from app.domain.value_objects import DomainError
from app.services.audit_service import AuditService

log = logging.getLogger(__name__)

ENDING_SOON_HOURS = 24
SELL_NOW_THRESHOLD_PER_HOUR = 40.0  # ₽/час дополнительной ценности


class DropsService:
    def __init__(self, db: Database, clock: Clock, events: EventBus,
                 audit: AuditService, config: AppConfig, secrets: SecretStore) -> None:
        self.db = db
        self.clock = clock
        self.events = events
        self.audit = audit
        self.config = config
        self.secrets = secrets

    # ================================================================ профили
    def add_profile(self, name: str, browser: str, profile_dir: str,
                    executable: str | None = None) -> models.BrowserProfile:
        with self.db.session() as session:
            profile = models.BrowserProfile(name=name, browser=browser,
                                            profile_dir=profile_dir, executable=executable)
            session.add(profile)
            session.flush()
            profile_id = profile.id
        return self._get(models.BrowserProfile, profile_id)

    def profiles(self) -> list[models.BrowserProfile]:
        with self.db.session() as session:
            return list(session.scalars(select(models.BrowserProfile).order_by(models.BrowserProfile.id)))

    # =============================================================== аккаунты
    def add_account(self, display_name: str, browser_profile_id: int | None = None,
                    email_label: str | None = None, twitch_login: str | None = None,
                    notes: str | None = None) -> models.TwitchAccount:
        with self.db.session() as session:
            account = models.TwitchAccount(
                display_name=display_name, browser_profile_id=browser_profile_id,
                email_label=email_label, twitch_login=twitch_login,
                status=TwitchAccountStatus.IDLE.value, notes=notes,
            )
            session.add(account)
            session.flush()
            self.audit.log("drop_account.added", Actor.USER, entity="twitch_account",
                            entity_id=account.id, session=session, name=display_name)
            account_id = account.id
        self.events.publish(TOPIC_DATA_CHANGED, section="drops")
        return self._get(models.TwitchAccount, account_id)

    def accounts(self) -> list[models.TwitchAccount]:
        with self.db.session() as session:
            return list(session.scalars(select(models.TwitchAccount).order_by(models.TwitchAccount.id)))

    def get_account(self, account_id: int) -> models.TwitchAccount | None:
        return self._get(models.TwitchAccount, account_id)

    def set_status(self, account_id: int, status: TwitchAccountStatus) -> bool:
        with self.db.session() as session:
            result = session.execute(
                update(models.TwitchAccount)
                .where(models.TwitchAccount.id == account_id)
                .values(status=status.value, last_activity_at=self.clock.now())
            )
            if result.rowcount == 1:
                self.audit.log("drop_account.status", Actor.APP, entity="twitch_account",
                                entity_id=account_id, session=session, new_status=status.value)
        self.events.publish(TOPIC_DATA_CHANGED, section="drops")
        return True

    # =============================================================== кампании
    def add_campaign(self, game_name: str, campaign_name: str, required_minutes: int,
                     rewards: list[str], estimated_value: float,
                     starts_at=None, ends_at=None, priority: int = 0,
                     source: str = "manual") -> models.DropCampaign:
        with self.db.session() as session:
            campaign = models.DropCampaign(
                game_name=game_name, campaign_name=campaign_name,
                starts_at=starts_at, ends_at=ends_at,
                required_minutes=int(required_minutes),
                rewards=list(rewards), reward_count=max(1, len(rewards)),
                estimated_value=float(estimated_value), priority=int(priority),
                source=source,
            )
            session.add(campaign)
            session.flush()
            self.audit.log("campaign.discovered", Actor.USER, entity="drop_campaign",
                            entity_id=campaign.id, session=session, name=campaign_name)
            campaign_id = campaign.id
        self.events.publish(TOPIC_DATA_CHANGED, section="drops_campaigns")
        return self._get(models.DropCampaign, campaign_id)

    def campaigns(self) -> list[tuple[models.DropCampaign, DropCampaignStatus]]:
        now = self.clock.now()
        with self.db.session() as session:
            rows = session.scalars(select(models.DropCampaign).order_by(models.DropCampaign.id)).all()
        return [(campaign, self.campaign_status(campaign, now)) for campaign in rows]

    def campaign_status(self, campaign: models.DropCampaign, now=None) -> DropCampaignStatus:
        now = now or self.clock.now()
        if campaign.ends_at is not None and campaign.ends_at <= now:
            return DropCampaignStatus.ENDED
        if campaign.starts_at is not None and campaign.starts_at > now:
            return DropCampaignStatus.UPCOMING
        if campaign.ends_at is not None and campaign.ends_at <= now + timedelta(hours=ENDING_SOON_HOURS):
            return DropCampaignStatus.ENDING_SOON
        return DropCampaignStatus.ACTIVE

    def ending_soon(self) -> list[models.DropCampaign]:
        return [c for c, status in self.campaigns() if status == DropCampaignStatus.ENDING_SOON]

    # ================================================================ прогресс
    def attach(self, account_id: int, campaign_id: int) -> models.AccountCampaign:
        with self.db.session() as session:
            progress = session.scalar(
                select(models.AccountCampaign).where(
                    models.AccountCampaign.account_id == account_id,
                    models.AccountCampaign.campaign_id == campaign_id)
            )
            if progress is None:
                progress = models.AccountCampaign(account_id=account_id, campaign_id=campaign_id)
                session.add(progress)
                session.flush()
            progress_id = progress.id
        return self._get(models.AccountCampaign, progress_id)

    def progress(self, account_id: int, campaign_id: int) -> models.AccountCampaign | None:
        with self.db.session() as session:
            return session.scalar(
                select(models.AccountCampaign).where(
                    models.AccountCampaign.account_id == account_id,
                    models.AccountCampaign.campaign_id == campaign_id)
            )

    def verify_progress(self, account_id: int, campaign_id: int, verified_minutes: int,
                        claimed_count: int, claim_states: list[str] | None = None) -> models.AccountCampaign:
        """Ручное подтверждение фактического прогресса по инвентарю (ТЗ §8)."""
        with self.db.session() as session:
            progress = session.scalar(
                select(models.AccountCampaign).where(
                    models.AccountCampaign.account_id == account_id,
                    models.AccountCampaign.campaign_id == campaign_id)
            )
            if progress is None:
                # ручной ввод прогресса без плановой сессии — привязываем на лету
                progress = models.AccountCampaign(account_id=account_id, campaign_id=campaign_id)
                session.add(progress)
                session.flush()
            campaign = session.get(models.DropCampaign, campaign_id)
            progress.verified_minutes = max(progress.verified_minutes, int(verified_minutes))
            progress.claimed_count = int(claimed_count)
            progress.last_verified_at = self.clock.now()
            if claim_states is not None:
                progress.claim_states = list(claim_states)
            account = session.get(models.TwitchAccount, account_id)

            needs_claim = ClaimState.READY_TO_CLAIM.value in (progress.claim_states or [])
            completed = (campaign is not None and
                         progress.claimed_count >= campaign.reward_count and
                         progress.verified_minutes >= campaign.required_minutes)
            if account is not None:
                if needs_claim:
                    account.status = TwitchAccountStatus.CLAIM_REQUIRED.value
                elif completed:
                    account.status = TwitchAccountStatus.COMPLETE.value
                elif account.status in (TwitchAccountStatus.IDLE.value,
                                        TwitchAccountStatus.WATCHING.value,
                                        TwitchAccountStatus.CLAIM_REQUIRED.value):
                    account.status = TwitchAccountStatus.WATCHING.value if not completed else account.status
            self.audit.log("progress.verified", Actor.USER, entity="account_campaign",
                            entity_id=progress.id, session=session,
                            verified_minutes=verified_minutes, claimed=claimed_count)
            progress_id = progress.id
        self.events.publish(TOPIC_DATA_CHANGED, section="drops")
        return self._get(models.AccountCampaign, progress_id)

    # ================================================================ сессии
    def plan_session(self, account_id: int, campaign_id: int, channel: str,
                     minutes: int | None = None) -> models.WatchSession:
        with self.db.session() as session:
            campaign = session.get(models.DropCampaign, campaign_id)
            if campaign is None:
                raise DomainError("Кампания не найдена.")
            planned = minutes or campaign.required_minutes
            watch_session = models.WatchSession(
                account_id=account_id, campaign_id=campaign_id, channel=channel,
                status=WatchSessionStatus.PLANNED.value, planned_minutes=int(planned),
            )
            session.add(watch_session)
            session.flush()
            self.audit.log("session.planned", Actor.APP, entity="watch_session",
                            entity_id=watch_session.id, session=session,
                            account_id=account_id, campaign_id=campaign_id, channel=channel)
            session_id = watch_session.id
        self.attach(account_id, campaign_id)
        return self._get(models.WatchSession, session_id)

    def start_session(self, session_id: int, browser_pid: int | None = None) -> models.WatchSession:
        now = self.clock.now()
        with self.db.session() as session:
            watch_session = session.get(models.WatchSession, session_id)
            if watch_session is None:
                raise DomainError("Сессия не найдена.")
            watch_session.status = WatchSessionStatus.ACTIVE.value
            watch_session.started_at = now
            watch_session.expected_finish = now + timedelta(minutes=watch_session.planned_minutes)
            watch_session.browser_pid = browser_pid
            account = session.get(models.TwitchAccount, watch_session.account_id)
            if account is not None:
                account.status = TwitchAccountStatus.WATCHING.value
            self.audit.log("session.started", Actor.APP, entity="watch_session",
                            entity_id=session_id, session=session)
        self.events.publish(TOPIC_DATA_CHANGED, section="drops_sessions")
        return self._get(models.WatchSession, session_id)

    def sessions(self, open_only: bool = True) -> list[models.WatchSession]:
        with self.db.session() as session:
            stmt = select(models.WatchSession).order_by(models.WatchSession.id.desc())
            if open_only:
                stmt = stmt.where(models.WatchSession.status.in_([
                    WatchSessionStatus.PLANNED.value, WatchSessionStatus.ACTIVE.value,
                    WatchSessionStatus.VERIFY_PROGRESS.value, WatchSessionStatus.CLAIM_REQUIRED.value,
                ]))
            return list(session.scalars(stmt))

    def tick_sessions(self) -> dict:
        """Плановая проверка: ожидаемое время вышло → напомнить о верификации."""
        now = self.clock.now()
        result = {"verify": [], "ending_campaigns": []}
        with self.db.session() as session:
            for watch_session in session.scalars(
                select(models.WatchSession).where(models.WatchSession.status == WatchSessionStatus.ACTIVE.value)
            ):
                if watch_session.expected_finish is not None and watch_session.expected_finish <= now:
                    watch_session.status = WatchSessionStatus.VERIFY_PROGRESS.value
                    result["verify"].append(watch_session.id)
                    self.audit.log("session.verify_requested", Actor.SCHEDULER,
                                    entity="watch_session", entity_id=watch_session.id, session=session)
        for campaign in self.ending_soon():
            result["ending_campaigns"].append(campaign.id)
        if result["verify"] or result["ending_campaigns"]:
            self.events.publish(TOPIC_DATA_CHANGED, section="drops_sessions")
        return result

    def verify_session(self, session_id: int, verified_minutes: int) -> models.WatchSession:
        """Оператор подтвердил прогресс после проверки инвентаря."""
        with self.db.session() as session:
            watch_session = session.get(models.WatchSession, session_id)
            if watch_session is None:
                raise DomainError("Сессия не найдена.")
            watch_session.actual_progress = int(verified_minutes)
            watch_session.status = WatchSessionStatus.DONE.value
            watch_session.ended_at = self.clock.now()
            progress = session.scalar(
                select(models.AccountCampaign).where(
                    models.AccountCampaign.account_id == watch_session.account_id,
                    models.AccountCampaign.campaign_id == watch_session.campaign_id)
            )
            if progress is not None:
                progress.expected_minutes += watch_session.planned_minutes
            self.audit.log("session.verified", Actor.USER, entity="watch_session",
                            entity_id=session_id, session=session, minutes=verified_minutes)
        self.events.publish(TOPIC_DATA_CHANGED, section="drops_sessions")
        return self._get(models.WatchSession, session_id)

    # ================================================================ оценка
    def account_value(self, account_id: int) -> float:
        """Ценность аккаунта = сумма ценностей кампаний по прогрессу."""
        with self.db.session() as session:
            rows = session.execute(
                select(models.AccountCampaign, models.DropCampaign)
                .join(models.DropCampaign, models.DropCampaign.id == models.AccountCampaign.campaign_id)
                .where(models.AccountCampaign.account_id == account_id)
            ).all()
            total = 0.0
            for progress, campaign in rows:
                fraction = 0.0
                if campaign.reward_count > 0:
                    fraction = progress.claimed_count / campaign.reward_count
                if fraction < 1.0 and campaign.required_minutes > 0:
                    time_fraction = min(1.0, progress.verified_minutes / campaign.required_minutes)
                    fraction = max(fraction, time_fraction * 0.9)  # не полностью заклеймлено
                total += campaign.estimated_value * min(1.0, fraction)
        return round(total, 2)

    def valuation_report(self, account_id: int) -> dict:
        """Сравнить «продать сейчас» и «доработать следующую кампанию» (ТЗ §34)."""
        current = self.account_value(account_id)
        now = self.clock.now()
        with self.db.session() as session:
            attached_ids = {
                row[0] for row in session.execute(
                    select(models.AccountCampaign.campaign_id)
                    .where(models.AccountCampaign.account_id == account_id)
                )
            }
            campaigns = session.scalars(select(models.DropCampaign)).all()
            account = session.get(models.TwitchAccount, account_id)

        best_next = None
        best_rate = -1.0
        for campaign in campaigns:
            status = self.campaign_status(campaign, now)
            if status == DropCampaignStatus.ENDED or campaign.id in attached_ids:
                continue
            hours = max(0.1, campaign.required_minutes / 60.0)
            rate = campaign.estimated_value / hours
            if rate > best_rate:
                best_rate = rate
                best_next = campaign

        report = {
            "current_value": current,
            "next_campaign": best_next.campaign_name if best_next else None,
            "after_value": round(current + best_next.estimated_value, 2) if best_next else current,
            "increment": round(best_next.estimated_value, 2) if best_next else 0.0,
            "additional_hours": round(best_next.required_minutes / 60.0, 1) if best_next else 0.0,
            "incremental_value_per_hour": round(best_rate, 1) if best_next else 0.0,
            "recommendation": SellRecommendation.HOLD.value,
        }
        if best_next is not None and best_rate >= SELL_NOW_THRESHOLD_PER_HOUR:
            report["recommendation"] = SellRecommendation.ADD_NEXT_CAMPAIGN.value
        elif current > 0:
            report["recommendation"] = SellRecommendation.SELL_NOW.value
        elif best_next is not None:
            report["recommendation"] = SellRecommendation.CONTINUE_FARMING.value
        if account is not None:
            report["account_status"] = account.status
        return report

    def value_per_hour(self, campaign: models.DropCampaign) -> float:
        hours = max(0.001, campaign.required_minutes / 60.0)
        return campaign.estimated_value / hours

    def campaign_priorities(self) -> list[tuple[models.DropCampaign, float]]:
        """Приоритет = ценность/час + срочность + ручной буст (ТЗ §32)."""
        now = self.clock.now()
        ranked: list[tuple[models.DropCampaign, float]] = []
        for campaign, status in self.campaigns():
            if status == DropCampaignStatus.ENDED:
                continue
            score = self.value_per_hour(campaign)
            if campaign.ends_at is not None:
                hours_left = max(0.0, (campaign.ends_at - now).total_seconds() / 3600.0)
                if hours_left <= ENDING_SOON_HOURS:
                    score += 100.0  # горящий дедлайн — выше некуда по срочности
                else:
                    score += max(0.0, 30.0 - hours_left / 8.0)
            score += campaign.priority * 10.0
            ranked.append((campaign, round(score, 1)))
        ranked.sort(key=lambda item: -item[1])
        return ranked

    # ================================================================== сток
    def mark_ready(self, account_id: int) -> None:
        self._account_transition(account_id, [TwitchAccountStatus.IDLE, TwitchAccountStatus.COMPLETE,
                                              TwitchAccountStatus.CLAIM_REQUIRED],
                                 TwitchAccountStatus.READY)

    def mark_listed(self, account_id: int) -> None:
        self._account_transition(account_id, [TwitchAccountStatus.READY], TwitchAccountStatus.LISTED)

    def reserve_account(self, account_id: int, order_id: int | None = None) -> bool:
        """Атомарно: только один заказ может зарезервировать аккаунт."""
        now = self.clock.now()
        with self.db.session() as session:
            result = session.execute(
                update(models.TwitchAccount)
                .where(models.TwitchAccount.id == account_id,
                       models.TwitchAccount.status.in_([
                           TwitchAccountStatus.READY.value, TwitchAccountStatus.LISTED.value]))
                .values(status=TwitchAccountStatus.RESERVED.value,
                        reserved_order_id=order_id, last_activity_at=now)
            )
            ok = result.rowcount == 1
            if ok:
                session.execute(
                    update(models.StockUnit)
                    .where(models.StockUnit.payload_ref == f"twitch_account:{account_id}",
                           models.StockUnit.status.in_(["ready", "listed"]))
                    .values(status="reserved", order_id=order_id, reserved_at=now)
                )
                self.audit.log("drop_account.reserved", Actor.APP, entity="twitch_account",
                                entity_id=account_id, session=session, order_id=order_id)
        self.events.publish(TOPIC_DATA_CHANGED, section="drops")
        return ok

    def release_account(self, account_id: int) -> bool:
        with self.db.session() as session:
            result = session.execute(
                update(models.TwitchAccount)
                .where(models.TwitchAccount.id == account_id,
                       models.TwitchAccount.status == TwitchAccountStatus.RESERVED.value)
                .values(status=TwitchAccountStatus.READY.value, reserved_order_id=None)
            )
            ok = result.rowcount == 1
            if ok:
                session.execute(
                    update(models.StockUnit)
                    .where(models.StockUnit.payload_ref == f"twitch_account:{account_id}",
                           models.StockUnit.status == "reserved")
                    .values(status="ready", order_id=None, reserved_at=None)
                )
                self.audit.log("drop_account.released", Actor.APP, entity="twitch_account",
                                entity_id=account_id, session=session)
        self.events.publish(TOPIC_DATA_CHANGED, section="drops")
        return ok

    def sell_account(self, account_id: int, purge_credentials: bool = True) -> bool:
        with self.db.session() as session:
            result = session.execute(
                update(models.TwitchAccount)
                .where(models.TwitchAccount.id == account_id,
                       models.TwitchAccount.status == TwitchAccountStatus.RESERVED.value)
                .values(status=TwitchAccountStatus.SOLD.value, sold_at=self.clock.now())
            )
            ok = result.rowcount == 1
            if ok:
                session.execute(
                    update(models.StockUnit)
                    .where(models.StockUnit.payload_ref == f"twitch_account:{account_id}",
                           models.StockUnit.status == "reserved")
                    .values(status="sold", sold_at=self.clock.now())
                )
                self.audit.log("drop_account.sold", Actor.APP, entity="twitch_account",
                                entity_id=account_id, session=session)
        if ok and purge_credentials:
            self.purge_credentials(account_id)
        self.events.publish(TOPIC_DATA_CHANGED, section="drops")
        return ok

    def purge_credentials(self, account_id: int) -> None:
        """После продажи: удалить секреты из keyring, оставить обезличенную историю."""
        with self.db.session() as session:
            account = session.get(models.TwitchAccount, account_id)
            if account is None:
                return
            ref = account.login_ref
            account.login_ref = None
            account.email_label = None
            account.notes = None
        if ref:
            secret_name = SecretStore.parse_ref(ref)
            if secret_name:
                self.secrets.delete(secret_name)
        self.audit.log("drop_account.credentials_removed", Actor.APP, entity="twitch_account",
                        entity_id=account_id)

    def delivery_package(self, account_id: int) -> dict:
        """Delivery package (ТЗ §14): только ссылки на секреты, не сами секреты."""
        account = self.get_account(account_id)
        if account is None:
            raise DomainError("Аккаунт не найден.")
        with self.db.session() as session:
            rows = session.execute(
                select(models.AccountCampaign, models.DropCampaign)
                .join(models.DropCampaign, models.DropCampaign.id == models.AccountCampaign.campaign_id)
                .where(models.AccountCampaign.account_id == account_id)
            ).all()
        included = [
            f"{campaign.game_name}: {progress.claimed_count}/{campaign.reward_count} наград"
            for progress, campaign in rows
        ]
        return {
            "account": account.display_name,
            "login": account.twitch_login or "—",
            "login_ref": account.login_ref,   # ссылка на keyring, НЕ сам пароль
            "email_label": account.email_label,
            "included_drops": included,
            "estimated_value": self.account_value(account_id),
            "notes": "Секреты передаются через системное хранилище; после продажи — удаляются.",
        }

    def sync_stock_unit(self, account_id: int, product_id: int) -> models.StockUnit:
        """Link a READY/active Drops account to the universal digital stock."""
        payload_ref = f"twitch_account:{account_id}"
        with self.db.session() as session:
            account = session.get(models.TwitchAccount, account_id)
            product = session.get(models.Product, product_id)
            if account is None:
                raise DomainError("Drops account not found.")
            if product is None:
                raise DomainError("Product not found.")
            unit = session.scalar(
                select(models.StockUnit).where(models.StockUnit.payload_ref == payload_ref)
            )
            status_map = {
                TwitchAccountStatus.READY.value: "ready",
                TwitchAccountStatus.LISTED.value: "listed",
                TwitchAccountStatus.RESERVED.value: "reserved",
                TwitchAccountStatus.SOLD.value: "sold",
            }
            target_status = status_map.get(account.status, "preparing")
            if unit is None:
                unit = models.StockUnit(
                    product_id=product_id,
                    payload_ref=payload_ref,
                    status=target_status,
                    created_at=self.clock.now(),
                    order_id=account.reserved_order_id,
                )
                session.add(unit)
                session.flush()
            else:
                unit.product_id = product_id
                unit.status = target_status
                unit.order_id = account.reserved_order_id
            unit_id = unit.id
            self.audit.log("drops.stock_synced", Actor.APP, entity="stock_unit",
                           entity_id=unit_id, session=session, account_id=account_id,
                           product_id=product_id)
        self.events.publish(TOPIC_DATA_CHANGED, section="stock")
        return self._get(models.StockUnit, unit_id)

    def unified_inventory(self) -> list[dict]:
        """Campaign -> Account -> StockUnit view for Operations/UI."""
        rows: list[dict] = []
        with self.db.session() as session:
            accounts = list(session.scalars(
                select(models.TwitchAccount).order_by(models.TwitchAccount.id)
            ))
            for account in accounts:
                progress_rows = session.execute(
                    select(models.AccountCampaign, models.DropCampaign)
                    .join(models.DropCampaign,
                          models.DropCampaign.id == models.AccountCampaign.campaign_id)
                    .where(models.AccountCampaign.account_id == account.id)
                ).all()
                stock = session.scalar(
                    select(models.StockUnit)
                    .where(models.StockUnit.payload_ref == f"twitch_account:{account.id}")
                    .order_by(models.StockUnit.id.desc())
                    .limit(1)
                )
                rows.append({
                    "account_id": account.id,
                    "account": account.display_name,
                    "status": account.status,
                    "browser_profile_id": account.browser_profile_id,
                    "estimated_value": self.account_value(account.id),
                    "stock_unit_id": stock.id if stock else None,
                    "stock_status": stock.status if stock else None,
                    "campaigns": [
                        {
                            "campaign_id": campaign.id,
                            "game": campaign.game_name,
                            "name": campaign.campaign_name,
                            "claimed": progress.claimed_count,
                            "rewards": campaign.reward_count,
                            "verified_minutes": progress.verified_minutes,
                            "required_minutes": campaign.required_minutes,
                        }
                        for progress, campaign in progress_rows
                    ],
                })
        return rows

    def production_queue(self) -> list[dict]:
        """Rank campaign/account work by deadline and value/hour."""
        campaigns = self.campaign_priorities()
        accounts = [a for a in self.accounts()
                    if a.status in (TwitchAccountStatus.IDLE.value,
                                    TwitchAccountStatus.WATCHING.value,
                                    TwitchAccountStatus.CLAIM_REQUIRED.value)]
        queue = []
        for campaign, score in campaigns:
            queue.append({
                "campaign_id": campaign.id,
                "campaign": campaign.campaign_name,
                "game": campaign.game_name,
                "score": score,
                "value_per_hour": round(self.value_per_hour(campaign), 2),
                "ends_at": campaign.ends_at,
                "candidate_accounts": [a.id for a in accounts],
            })
        return queue

    # -------------------------------------------------------------- служебное
    def _account_transition(self, account_id: int, from_statuses: list[TwitchAccountStatus],
                            target: TwitchAccountStatus) -> bool:
        with self.db.session() as session:
            result = session.execute(
                update(models.TwitchAccount)
                .where(models.TwitchAccount.id == account_id,
                       models.TwitchAccount.status.in_([s.value for s in from_statuses]))
                .values(status=target.value, last_activity_at=self.clock.now())
            )
            ok = result.rowcount == 1
        self.events.publish(TOPIC_DATA_CHANGED, section="drops")
        return ok

    def _get(self, model, entity_id: int):
        with self.db.session() as session:
            entity = session.get(model, entity_id)
            if entity is not None:
                session.expunge(entity)
            return entity
