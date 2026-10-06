"""Контейнер зависимостей приложения (ТЗ §50, FunPay OS §51).

``build_context(data_dir)`` создаёт один экземпляр контекста и отдаёт его
в главное окно, вкладки и воркеры. Порядок сборки:

конфиг → БД → часы/события/секреты → интеграции → репозитории →
бизнес-сервисы базового контура → сервисы FunPay Automation OS →
Drops Control Center.

Слои разделены (ТЗ §55): здесь только сборка, бизнес-логика — в сервисах,
интеграции — в ``app/integrations``.
"""
from __future__ import annotations

import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

from app.browser.profile_manager import BrowserProfileManager
from app.core.clock import Clock
from app.core.config import CONFIG_FILE_NAME, AppConfig
from app.core.events import EventBus
from app.core.secret_store import SecretStore
from app.database.session import Database
from app.database import repositories
from app.domain.enums import Actor
from app.integrations.steam.client import SteamWebClient
from app.integrations.steam.exclusions import ExclusionDataset
from app.integrations.steam.metadata import (
    MetadataProvider,
    SimulationMetadataProvider,
    StoreMetadataProvider,
)
from app.integrations.steam.base import SteamLibrarySource
from app.integrations.steam.simulator import SimulationSteamSource
from app.services.access_service import AccessService
from app.services.analytics_service import AnalyticsService
from app.services.audit_service import AuditService
from app.services.bundle_service import BundleService
from app.services.demand_service import DemandService
from app.services.drops.drops_service import DropsService
from app.services.eligibility_service import FamilyEligibilityService
from app.services.family_service import FamilyService
from app.services.funpay_service import FunPayService
from app.services.library_service import LibraryService
from app.services.listing_service import ListingService
from app.services.notification_service import NotificationService
from app.services.order_service import OrderService
from app.services.os.analytics_service import OSAnalyticsService
from app.services.os.import_service import FunPayImportService
from app.services.os.market_service import MarketService
from app.services.os.product_service import ProductService
from app.services.os.queue_service import QueueService
from app.services.os.sales_service import SalesService
from app.services.os.stock_service import StockService
from app.services.os.workflow_engine import WorkflowEngine
from app.engines.rules_engine import RulesEngine

log = logging.getLogger(__name__)

BACKUP_KEEP = 7

#: Имена секретов (значения — только в системном хранилище, в конфиге — ссылки).
STEAM_API_SECRET = "steam/api_key"
TWITCH_CLIENT_ID_SECRET = "twitch/client_id"
TWITCH_CLIENT_SECRET_SECRET = "twitch/client_secret"


class AppContext:
    """Долгоживущие сервисы + управление их жизненным циклом."""

    def __init__(self, data_dir: Path, clock: Clock | None = None) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.config = AppConfig.load(self.data_dir / CONFIG_FILE_NAME)
        self.config.data_dir = str(self.data_dir)

        self.clock = clock or Clock()
        self.events = EventBus()
        self.db = Database(self.config.db_url)
        self.secrets = SecretStore(fallback_dir=self.data_dir)

        # Датасет исключений (семейный доступ): поставки + правки пользователя.
        self.dataset = ExclusionDataset.load(self.data_dir / "exclusions.json")

        # Аудит нужен всем остальным сервисам.
        self.audit = AuditService(self.db, self.clock)

        # Интеграции (адаптеры с явными режимами, ТЗ §31).
        self.source, self.metadata = self._build_steam_source()

        # Базовый контур: библиотека, доступ, заказы, цены, спрос.
        self.family = FamilyService(self.db)
        self.library = LibraryService(self.db, self.clock, self.events,
                                      self.source, self.metadata, self.audit)
        self.eligibility = FamilyEligibilityService(self.db, self.clock, self.dataset,
                                                    self.audit, self.events)
        self.demand = DemandService(self.db, self.clock, self.config,
                                    self.audit, self.events)
        self.access = AccessService(self.db, self.clock, self.events, self.audit,
                                    warn_hours=self.config.scheduler.lease_expiry_warn_hours)
        self.orders = OrderService(self.db, self.clock, self.events, self.audit, self.access)
        self.listings = ListingService(self.db, self.clock, self.events, self.audit, self.access)
        self.bundles = BundleService(self.db, self.clock, self.events, self.audit)
        self.notifications = NotificationService(self.db, self.clock, self.events)
        self.analytics = AnalyticsService(self.db, self.clock)
        self.funpay = FunPayService(self.db, self.config, self.secrets,
                                    self.events, self.audit)

        # FunPay Automation OS: склад → продукты → воркфлоу → продажи → рынок.
        self.stock = StockService(self.db, self.clock, self.events, self.audit)
        self.products = ProductService(self.db, self.clock, self.events, self.audit)
        self.engine = WorkflowEngine(self.db, self.clock, self.events,
                                     self.audit, self.config, self.stock)
        self.sales = SalesService(self.db, self.clock, self.events, self.audit,
                                  self.products, self.stock, self.engine, self.config)
        self.market = MarketService(self.db, self.clock, self.events, self.audit)
        self.importer = FunPayImportService(self.db, self.clock, self.events, self.audit,
                                            self.sales, notifications=self.notifications)
        self.queue = QueueService(self.db, self.clock, self.events, self.audit)
        self.os_analytics = OSAnalyticsService(self.db, self.clock, self.products)
        self.engine.seed_builtin()

        # Drops Control Center.
        self.drops = DropsService(self.db, self.clock, self.events, self.audit,
                                  self.config, self.secrets)
        self.browser = BrowserProfileManager(self.config)

        # Движок правил: видит склад, дропы, воркфлоу, продукты, очередь.
        self.rules = RulesEngine(stock=self.stock, drops=self.drops, engine=self.engine,
                                 products=self.products, notifications=self.notifications,
                                 db=self.db, clock=self.clock, queue=self.queue)

        # Хелперы, назначаются из main()/UI.
        self.scheduler = None
        self.on_settings_applied = None
        self.reschedule_all = None
        self._helix = None

    # ------------------------------------------------------------ интеграции
    def _build_steam_source(self) -> tuple[SteamLibrarySource, MetadataProvider]:
        mode = self.config.steam.mode
        if mode == "live":
            api_key = self.secrets.resolve(self.config.steam.api_key_ref) or ""
            if not api_key:
                log.warning("Steam live-режим без API-ключа: падаем в симулятор.")
                return SimulationSteamSource(), SimulationMetadataProvider()
            return (
                SteamWebClient(api_key),
                StoreMetadataProvider(min_interval=self.config.steam.metadata_rate_limit_sec),
            )
        return SimulationSteamSource(), SimulationMetadataProvider()

    def rebuild_steam_source(self) -> None:
        """После смены настроек пересобрать источник библиотеки."""
        self.source, self.metadata = self._build_steam_source()
        self.library.source = self.source
        self.library.metadata = self.metadata
        self.audit.log("steam.source_rebuilt", Actor.APP, entity="settings",
                        mode=self.config.steam.mode)

    # ------------------------------------------------------- автовыдача
    def _funpay_delivery_sink(self, funpay_order_id: str, delivery: dict) -> dict:
        """Канал выдачи движка воркфлоу: сообщение с товаром в чат заказа.

        Безопасность: в DRY RUN — только превью; без явного разрешения
        неофициального доступа — пометка «отправить вручную».
        """
        product_name = delivery.get("product", "")
        payload = delivery.get("payload_ref") or "—"
        text = (
            f"Здравствуйте! Спасибо за заказ «{product_name}».\n\n"
            f"Ваш товар:\n{payload}\n\n"
            "Если возникнут вопросы — напишите сюда, поможем."
        )
        if self.config.dry_run:
            return {"status": "dry_run", "preview": text}
        if not (self.config.funpay.unofficial_allowed
                and self.config.funpay.mode == "golden_key"):
            return {"status": "manual_required",
                    "info": "Автовыдача выключена: отправьте товар в чате FunPay вручную."}
        try:
            info = self.funpay.deliver_order(funpay_order_id, text)
        except Exception as exc:  # noqa: BLE001 - канал не должен ронять прогон
            return {"status": "error", "error": str(exc)}
        return {"status": "sent", "info": info}

    def helix(self):
        """Ленивый Helix-клиент: нужен свой Twitch Developer app (ТЗ §40)."""
        if self._helix is None:
            from app.integrations.twitch.helix import HelixClient
            client_id = self.secrets.resolve(self.config.twitch.client_id_ref) or ""
            client_secret = self.secrets.resolve(self.config.twitch.client_secret_ref) or ""
            self._helix = HelixClient(client_id, client_secret)
        return self._helix

    # ---------------------------------------------------------------- бэкапы
    def backup_database(self) -> Path | None:
        """Копия БД в ``data/backups`` с ротацией последних 7 (ТЗ §25)."""
        db_file = self.data_dir / "steamrent.db"
        if not db_file.exists():
            log.warning("Бэкап пропущен: файл БД не найден (%s)", db_file)
            return None
        backups_dir = self.data_dir / "backups"
        backups_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        target = backups_dir / f"steamrent_{stamp}.db"
        try:
            shutil.copy2(db_file, target)
        except OSError as exc:
            log.error("Не удалось создать резервную копию: %s", exc)
            return None
        old = sorted(backups_dir.glob("steamrent_*.db"))
        for stale in old[:-BACKUP_KEEP]:
            try:
                stale.unlink()
            except OSError as exc:
                log.warning("Не удалось удалить старый бэкап %s: %s", stale, exc)
        return target

    # -------------------------------------------------------------- lifecycle
    def shutdown(self) -> None:
        if self.scheduler is not None and hasattr(self.scheduler, "stop_all"):
            self.scheduler.stop_all()
        try:
            self.db.engine.dispose()
        except Exception as exc:  # noqa: BLE001 - корректное завершение
            log.error("Ошибка при закрытии БД: %s", exc)
        log.info("Приложение остановлено штатно.")


def build_context(data_dir: Path, clock: Clock | None = None) -> AppContext:
    return AppContext(Path(data_dir), clock=clock)
