"""Главное окно: сайдбар, страницы, статус-бар, уведомления в трее."""
from __future__ import annotations

import logging

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QStatusBar,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from app.ui.bridge import UiBridge
from app.ui.pages.analytics import AnalyticsPage
from app.ui.pages.audit import AuditPage
from app.ui.pages.bundles import BundlesPage
from app.ui.pages.clients import ClientsPage
from app.ui.pages.dashboard import DashboardPage
from app.ui.pages.drops_page import DropsPage
from app.ui.pages.drops_sessions import DropsSessionsPage
from app.ui.pages.funpay import FunPayPage
from app.ui.pages.library import LibraryPage
from app.ui.pages.listings import ListingsPage
from app.ui.pages.leases import LeasesPage
from app.ui.pages.market_page import MarketPage
from app.ui.pages.notifications import NotificationsPage
from app.ui.pages.orders import OrdersPage
from app.ui.pages.products import ProductsPage
from app.ui.pages.queue_page import QueuePage
from app.ui.pages.revenue_page import RevenuePage
from app.ui.pages.settings import SettingsPage
from app.ui.pages.stock_page import StockPage
from app.ui.pages.steam import SteamPage
from app.ui.pages.workflows import WorkflowsPage
from app.ui.theme import COLORS
from app.ui.widgets import Pill

log = logging.getLogger(__name__)

NAV_ITEMS = [
    ("dashboard", "🏠  Dashboard"),
    ("orders", "📦  Заказы"),
    ("library", "🎮  Библиотека"),
    ("steam", "💾  Steam"),
    ("funpay", "🛒  FunPay"),
    ("products", "🧩  Продукты"),
    ("stock", "🗄  Склад"),
    ("workflows", "🔀  Воркфлоу"),
    ("market", "📡  Рынок"),
    ("drops", "🎁  Drops Центр"),
    ("drops_sessions", "⏲  Drops Сессии"),
    ("queue", "🗂  Очередь"),
    ("revenue", "💰  Доходы"),
    ("listings", "📢  Объявления"),
    ("clients", "👥  Клиенты"),
    ("leases", "🔑  Доступы"),
    ("bundles", "🧺  Пакеты"),
    ("analytics", "📈  Аналитика"),
    ("notifications", "🔔  Уведомления"),
    ("audit", "📜  Журнал"),
    ("settings", "⚙️  Настройки"),
]


def _make_icon() -> QIcon:
    pixmap = QPixmap(64, 64)
    pixmap.fill(QColor(COLORS["accent"]))
    return QIcon(pixmap)


class MainWindow(QMainWindow):
    def __init__(self, ctx) -> None:
        super().__init__()
        self.ctx = ctx
        self.setWindowTitle("Steam Rent Manager — библиотека, семьи, FunPay")
        self.resize(1320, 860)
        self.setMinimumSize(1100, 720)
        self.setWindowIcon(_make_icon())

        self.bridge = UiBridge(ctx.events)
        self.pages: dict[str, QWidget] = {}
        self.nav_buttons: dict[str, QPushButton] = {}
        self._build_layout()
        self._build_tray()
        self._connect_bridge()

        # Таймер «тихой» проверки аренд для живых таймеров и уведомлений.
        self._lease_timer = QTimer(self)
        self._lease_timer.setInterval(30_000)
        self._lease_timer.timeout.connect(self._tick_leases)
        self._lease_timer.start()

        self._switch_to("dashboard")

    # ---------------------------------------------------------------- layout
    def _build_layout(self) -> None:
        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        sidebar = QFrame()
        sidebar.setStyleSheet(f"background:{COLORS['bg_sidebar']}; border-right:1px solid {COLORS['border']};")
        sidebar.setFixedWidth(232)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(12, 16, 12, 12)
        sidebar_layout.setSpacing(3)

        title = QLabel("Steam Rent")
        title.setStyleSheet("font-size:18px; font-weight:800; letter-spacing:0.3px;")
        subtitle = QLabel("Families → FunPay")
        subtitle.setStyleSheet(f"color:{COLORS['text_muted']}; font-size:11px; margin-bottom:10px;")
        sidebar_layout.addWidget(title)
        sidebar_layout.addWidget(subtitle)

        for key, label in NAV_ITEMS:
            button = QPushButton(label)
            button.setProperty("sidebar", True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _checked=False, k=key: self._switch_to(k))
            self.nav_buttons[key] = button
            sidebar_layout.addWidget(button)

        sidebar_layout.addStretch(1)
        self.mode_holder = QHBoxLayout()
        self.dry_run_pill = Pill("DRY RUN", COLORS["warning"])
        self.mode_pill = Pill("simulation", COLORS["steam_blue"])
        self.mode_holder.addWidget(self.dry_run_pill)
        self.mode_holder.addWidget(self.mode_pill)
        sidebar_layout.addLayout(self.mode_holder)
        root.addWidget(sidebar)

        self.stack = QStackedWidget()
        page_defs = [
            ("dashboard", DashboardPage), ("orders", OrdersPage), ("library", LibraryPage),
            ("steam", SteamPage), ("funpay", FunPayPage),
            ("products", ProductsPage), ("stock", StockPage), ("workflows", WorkflowsPage),
            ("market", MarketPage), ("drops", DropsPage), ("drops_sessions", DropsSessionsPage),
            ("queue", QueuePage), ("revenue", RevenuePage),
            ("listings", ListingsPage),
            ("clients", ClientsPage), ("leases", LeasesPage), ("bundles", BundlesPage),
            ("analytics", AnalyticsPage), ("notifications", NotificationsPage),
            ("audit", AuditPage), ("settings", SettingsPage),
        ]
        for key, page_cls in page_defs:
            page = page_cls(self.ctx)
            self.pages[key] = page
            self.stack.addWidget(page)
        root.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        status = QStatusBar()
        self.setStatusBar(status)
        self.status_label = QLabel("Готово")
        status.addWidget(self.status_label)

    def _build_tray(self) -> None:
        self.tray = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(_make_icon(), self)
            self.tray.setToolTip("Steam Rent Manager")
            self.tray.show()

    # ---------------------------------------------------------------- bridge
    def _connect_bridge(self) -> None:
        self.bridge.data_changed.connect(self._on_data_changed)
        self.bridge.notify.connect(self._on_notify)
        self.bridge.sync_started.connect(lambda kind: self.status_label.setText("Синхронизация…"))
        self.bridge.sync_finished.connect(self._on_sync_finished)
        self.bridge.sync_failed.connect(lambda error: self.status_label.setText(f"Ошибка: {error}"))
        self.bridge.lease_event.connect(self._on_lease_event)

    def _on_data_changed(self, section: str) -> None:
        current = self.stack.currentWidget()
        if hasattr(current, "refresh"):
            try:
                current.refresh()
            except Exception:  # noqa: BLE001
                log.exception("Не удалось обновить страницу %s", type(current).__name__)
        self._update_mode_badges()

    def _on_notify(self, kind: str, title: str, body: str) -> None:
        self._update_notifications_badge()
        if self.tray is not None:
            icon = (QSystemTrayIcon.MessageIcon.Warning if kind in ("error", "action_required")
                    else QSystemTrayIcon.MessageIcon.Information)
            self.tray.showMessage(title, body, icon, 6000)

    def _on_sync_finished(self, report) -> None:
        if report is None:
            return
        if report.ok:
            self.status_label.setText(
                f"Синхронизация: аккаунтов {report.accounts_processed}, игр {report.games_imported}"
            )
            self.ctx.notifications.notify(
                "sync", "Steam синхронизирован",
                f"Аккаунтов: {report.accounts_processed}, новых записей игр: {report.games_imported}, "
                f"лицензий: {report.licenses_upserted}.",
            )
        else:
            self.status_label.setText("Синхронизация завершена с ошибками")
            self.ctx.notifications.notify("error", "Ошибка синхронизации Steam", "; ".join(report.errors[:3]))

    def _on_lease_event(self, kind: str, payload: dict) -> None:
        if kind == "expiring":
            hours = payload.get("hours_left", 0)
            title = f"Доступ к «{payload.get('game', '?')}» заканчивается через {hours:.0f} ч"
            self.ctx.notifications.notify("lease", title)
        elif kind == "expired":
            title = f"Доступ к «{payload.get('game', '?')}» закончился"
            self.ctx.notifications.notify("lease", title, "Лицензия освобождена и снова доступна для продажи.")
        elif kind == "freed":
            self.ctx.notifications.notify("license", f"Освободилась лицензия (игра #{payload.get('game_id')})")
        self._refresh_pages("leases")

    def _tick_leases(self) -> None:
        try:
            self.ctx.access.process_expirations()
        except Exception as exc:  # noqa: BLE001
            log.error("Проверка аренд не выполнена: %s", exc)
        current = self.stack.currentWidget()
        if isinstance(current, (LeasesPage, DashboardPage)):
            current.refresh()

    # -------------------------------------------------------------- навигация
    def _switch_to(self, key: str) -> None:
        for name, button in self.nav_buttons.items():
            button.setProperty("active", name == key)
            button.style().unpolish(button)
            button.style().polish(button)
        self.stack.setCurrentWidget(self.pages[key])
        page = self.pages[key]
        if hasattr(page, "refresh"):
            try:
                page.refresh()
            except Exception:  # noqa: BLE001
                log.exception("Ошибка обновления страницы %s", key)
        self._update_mode_badges()
        self._update_notifications_badge()

    def _refresh_pages(self, *keys: str) -> None:
        for key in keys:
            page = self.pages.get(key)
            if page is not None and hasattr(page, "refresh"):
                page.refresh()

    def _update_mode_badges(self) -> None:
        if self.ctx.config.dry_run:
            self.dry_run_pill.setText("DRY RUN")
            self.dry_run_pill.set_color(COLORS["warning"])
        else:
            self.dry_run_pill.setText("LIVE")
            self.dry_run_pill.set_color(COLORS["danger"])
        mode = self.ctx.config.steam.mode
        self.mode_pill.setText(f"steam: {mode}")
        self.mode_pill.set_color(COLORS["steam_blue"] if mode == "simulation" else COLORS["success"])

    def _update_notifications_badge(self) -> None:
        unread = len(self.ctx.notifications.unread())
        button = self.nav_buttons["notifications"]
        button.setText(f"🔔  Уведомления ({unread})" if unread else "🔔  Уведомления")

    # --------------------------------------------------------------- настройки
    def apply_settings(self) -> None:
        """Вызывается страницей настроек после сохранения."""
        self._update_mode_badges()
        if hasattr(self.ctx, "reschedule_all"):
            self.ctx.reschedule_all()
        QMessageBox.information(self, "Настройки", "Интервалы планировщика будут пересобраны.")
