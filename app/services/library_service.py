"""Синхронизация библиотек и объединение игр по AppID."""
from __future__ import annotations

import logging
from contextlib import nullcontext

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import Clock
from app.core.events import (
    TOPIC_DATA_CHANGED,
    TOPIC_SYNC_FAILED,
    TOPIC_SYNC_FINISHED,
    TOPIC_SYNC_STARTED,
    EventBus,
)
from app.database import models, repositories
from app.database.session import Database
from app.domain.enums import AccountStatus, AuditAction, LicenseSource
from app.domain.value_objects import OwnedGame, SyncReport
from app.integrations.steam.base import SteamLibrarySource, SteamSourceError
from app.integrations.steam.metadata import MetadataProvider
from app.services.audit_service import AuditService

log = logging.getLogger(__name__)


class LibraryService:
    def __init__(
        self,
        db: Database,
        clock: Clock,
        events: EventBus,
        source: SteamLibrarySource,
        metadata: MetadataProvider,
        audit: AuditService,
    ) -> None:
        self.db = db
        self.clock = clock
        self.events = events
        self.source = source
        self.metadata = metadata
        self.audit = audit

    # ------------------------------------------------------------- аккаунты
    def add_account(self, steam_id64: str, display_name: str | None = None,
                    family_role: str = "adult") -> models.SteamAccount:
        with self.db.session() as session:
            existing = repositories.AccountRepo(session).by_steam_id(steam_id64)
            if existing is not None:
                raise ValueError(f"Аккаунт {steam_id64} уже добавлен.")
            account = models.SteamAccount(
                steam_id64=str(steam_id64),
                display_name=display_name or f"Steam {steam_id64[-4:]}",
                status=AccountStatus.PENDING.value,
                family_role=family_role,
            )
            session.add(account)
            session.flush()
            self.audit.log(AuditAction.ACCOUNT_ADDED, entity="steam_account",
                            entity_id=account.id, session=session,
                            steam_id64=steam_id64, name=account.display_name)
            return account

    def remove_account(self, account_id: int) -> None:
        with self.db.session() as session:
            account = repositories.AccountRepo(session).get(account_id)
            if account is None:
                return
            name = account.display_name
            session.query(models.GameLicense).filter_by(account_id=account_id).delete()
            session.query(models.AccessLease).filter_by(owner_account_id=account_id).update({"status": "cancelled"})
            session.query(models.AccountGame).filter_by(account_id=account_id).delete()
            session.delete(account)
            self.audit.log(AuditAction.ACCOUNT_REMOVED, entity="steam_account",
                            entity_id=account_id, session=session, name=name)

    def accounts(self) -> list[models.SteamAccount]:
        with self.db.session() as session:
            return list(repositories.AccountRepo(session).all())

    # ---------------------------------------------------------- синхронизация
    def sync_all(self) -> SyncReport:
        report = SyncReport(kind="steam")
        self.events.publish(TOPIC_SYNC_STARTED, kind="steam")

        accounts = self.accounts()
        if not accounts:
            report.errors.append("Нет добавленных Steam-аккаунтов.")
            return report

        started = self.clock.now()
        with self.db.session() as session:
            sync_record = repositories.SyncRepo(session).start("steam", started)
            session_id = sync_record.id

        for account in accounts:
            try:
                games_imported, licenses = self._sync_account(account.id)
                report.accounts_processed += 1
                report.games_imported += games_imported
                report.licenses_upserted += licenses
            except SteamSourceError as exc:
                report.errors.append(f"{account.display_name}: {exc}")
                self._mark_account_error(account.id, str(exc))
            except Exception as exc:  # noqa: BLE001 - фиксируем и продолжаем с другими
                log.exception("Синхронизация аккаунта %s упала", account.id)
                report.errors.append(f"{account.display_name}: непредвиденная ошибка {exc}")
                self._mark_account_error(account.id, str(exc))

        finished = self.clock.now()
        status = "success" if report.ok else ("partial" if report.accounts_processed else "failed")
        with self.db.session() as session:
            record = session.get(models.SyncHistory, session_id)
            if record:
                repositories.SyncRepo(session).finish(
                    record, status, finished,
                    {
                        "accounts": report.accounts_processed,
                        "games": report.games_imported,
                        "licenses": report.licenses_upserted,
                        "errors": report.errors,
                    },
                )

        self.audit.log(
            AuditAction.SYNC_FINISHED if report.ok else AuditAction.SYNC_FAILED,
            entity="sync_history", entity_id=session_id,
            accounts=report.accounts_processed, errors=report.errors,
        )
        self.events.publish(TOPIC_SYNC_FINISHED, kind="steam", report=report)
        self.events.publish(TOPIC_DATA_CHANGED, section="library")
        return report

    def _sync_account(self, account_id: int) -> tuple[int, int]:
        with self.db.session() as session:
            account = repositories.AccountRepo(session).get(account_id)
            if account is None:
                raise SteamSourceError("Аккаунт не найден.")
            steam_id64 = account.steam_id64

        profile = self.source.fetch_profile(steam_id64)
        owned = self.source.fetch_owned_games(steam_id64)

        games_imported = 0
        licenses = 0
        with self.db.session() as session:
            account = repositories.AccountRepo(session).get(account_id)
            account.display_name = profile.persona_name or account.display_name
            account.avatar_url = profile.avatar_url
            account.profile_url = profile.profile_url
            if profile.loc_country:
                account.region = profile.loc_country
            account.status = AccountStatus.ACTIVE.value if profile.community_visibility == 3 else AccountStatus.LIMITED.value
            account.last_error = None

            account_games = repositories.AccountRepo(session)  # noqa: F841 - для читаемости
            existing_pairs = {
                (ag.account_id, ag.app_id) for ag in
                session.scalars(select(models.AccountGame).where(models.AccountGame.account_id == account_id))
            }
            games_repo = repositories.GameRepo(session)
            license_repo = repositories.LicenseRepo(session)
            now = self.clock.now()

            paid = free = 0
            for owned_game in owned:
                game, created = games_repo.upsert_by_app_id(owned_game.app_id, name=owned_game.name)
                if created:
                    games_imported += 1
                if owned_game.is_free:
                    game.is_free = True
                    free += 1
                else:
                    paid += 1

                ag = session.scalar(
                    select(models.AccountGame).where(
                        models.AccountGame.account_id == account_id,
                        models.AccountGame.app_id == owned_game.app_id,
                    )
                )
                source_value = LicenseSource.FAMILY.value if owned_game.family_shared else LicenseSource.OWNED.value
                if ag is None:
                    ag = models.AccountGame(account_id=account_id, app_id=owned_game.app_id, source=source_value)
                    session.add(ag)
                ag.playtime_minutes = owned_game.playtime_minutes
                ag.source = source_value

                # Лицензия-копия только для СОБСТВЕННЫХ (не расшаренных нам) игр.
                if not owned_game.family_shared:
                    _, created_license = license_repo.upsert(game.id, account_id, LicenseSource.OWNED.value)
                    if created_license:
                        licenses += 1

            account.games_count = len(owned)
            account.paid_count = paid
            account.free_count = free
            account.last_sync_at = now
            session.flush()

        return games_imported, licenses

    def _mark_account_error(self, account_id: int, message: str) -> None:
        with self.db.session() as session:
            account = repositories.AccountRepo(session).get(account_id)
            if account:
                account.status = AccountStatus.ERROR.value
                account.last_error = message

    # ------------------------------------------------------------ метаданные
    def enrich_games(self, only_missing: bool = True, limit: int = 0) -> int:
        """Обновляет цены/жанры/признаки игр из провайдера метаданных."""
        updated = 0
        with self.db.session() as session:
            games_repo = repositories.GameRepo(session)
            games = list(session.scalars(select(models.Game).order_by(models.Game.id)))
            for game in games:
                if limit and updated >= limit:
                    break
                if only_missing and game.price_updated_at is not None:
                    continue
                meta = self.metadata.get(game.app_id)
                if meta is None:
                    continue
                self._apply_metadata(session, game, meta)
                updated += 1
        if updated:
            self.events.publish(TOPIC_DATA_CHANGED, section="library")
        return updated

    def _apply_metadata(self, session: Session, game: models.Game, meta: dict) -> None:
        game.name = meta.get("name") or game.name
        game.is_free = bool(meta.get("is_free", game.is_free))
        price = meta.get("price")
        if price is not None and (game.price != price or game.discount_pct != int(meta.get("discount_pct", 0))):
            session.add(
                models.PriceHistory(game_id=game.id, price=float(price),
                                    discount_pct=int(meta.get("discount_pct", 0)),
                                    recorded_at=self.clock.now())
            )
            game.price = float(price)
            game.discount_pct = int(meta.get("discount_pct", 0))
            game.currency = meta.get("currency", game.currency)
        game.genres = meta.get("genres", game.genres)
        game.categories = meta.get("categories", game.categories)
        game.release_date = meta.get("release_date") or game.release_date
        game.header_image_url = meta.get("header_image") or game.header_image_url
        game.store_url = meta.get("store_url") or game.store_url
        game.requires_third_party_launcher = bool(meta.get("third_party_launcher", game.requires_third_party_launcher))
        game.requires_third_party_account = bool(meta.get("third_party_account", game.requires_third_party_account))
        if meta.get("multiplayer") is not None:
            game.has_multiplayer = bool(meta["multiplayer"])
        if meta.get("coop") is not None:
            game.has_coop = bool(meta["coop"])
        if meta.get("anticheat"):
            game.anticheat = meta["anticheat"]
        if meta.get("review_score") is not None:
            game.review_score = int(meta["review_score"])
        if meta.get("review_count") is not None:
            game.review_count = int(meta["review_count"])
        if meta.get("owners") is not None:
            game.approx_owners = int(meta["owners"])
        game.price_updated_at = self.clock.now()
