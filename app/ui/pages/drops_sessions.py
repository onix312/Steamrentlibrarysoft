"""Экран «Drops Сессии»: план/запуск/верификация сессий и прогресса (ТЗ §29, §33).

Локальный таймер — только оценка. Фактический прогресс подтверждается
оператором по инвентарю Twitch; фальсификация просмотра невозможна по дизайну.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from app.browser.profile_manager import TWITCH_CAMPAIGNS_URL, TWITCH_INVENTORY_URL
from app.ui.pages.base import BasePage, page_header, toolbar_row
from app.ui.widgets import add_pill_cell, fill_cell, make_table, selected_row_id, set_row_id

SESSION_STATUS_META = {
    "planned": ("Запланирована", "#9aa0a6"),
    "active": ("Идёт", "#4aa8e0"),
    "interrupted": ("Прервана", "#e05555"),
    "verify_progress": ("Проверить прогресс", "#e0b341"),
    "claim_required": ("Забрать награду", "#e0b341"),
    "done": ("Готово", "#2fbf71"),
}


class DropsSessionsPage(BasePage):
    title = "Drops Сессии"

    def __init__(self, ctx, parent=None) -> None:
        super().__init__(ctx, parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(8)
        root.addWidget(page_header(
            "Сессии просмотра",
            "Сессия открывает браузерный профиль аккаунта на канале с дропами. "
            "Прогресс всегда сверяется вручную по инвентарю.",
        ))

        self.account_combo = QComboBox()
        self.campaign_combo = QComboBox()
        self.plan_button = QPushButton("🗓 Запланировать сессию")
        self.plan_button.setProperty("accent", True)
        self.plan_button.clicked.connect(self._plan)
        self.open_campaigns_button = QPushButton("Открыть кампании в браузере")
        self.open_campaigns_button.clicked.connect(lambda: self._open_url(TWITCH_CAMPAIGNS_URL))
        self.open_inventory_button = QPushButton("Открыть инвентарь в браузере")
        self.open_inventory_button.clicked.connect(lambda: self._open_url(TWITCH_INVENTORY_URL))
        root.addWidget(toolbar_row(self.account_combo, self.campaign_combo, self.plan_button,
                                   self.open_campaigns_button, self.open_inventory_button))

        self.table = make_table([
            "ID", "Аккаунт", "Кампания", "Канал", "Статус", "План, мин", "Факт, мин", "Старт",
        ])
        for col, width in ((0, 40), (1, 150), (2, 200), (3, 150), (4, 150), (5, 80), (6, 80), (7, 150)):
            self.table.setColumnWidth(col, width)
        root.addWidget(self.table, 1)

        actions = QHBoxLayout()
        self.start_button = QPushButton("▶ Запустить (браузер)")
        self.start_button.clicked.connect(self._start)
        self.check_button = QPushButton("⏱ Проверить ожидаемое время")
        self.check_button.clicked.connect(self._tick)
        self.verify_button = QPushButton("✔ Подтвердить прогресс")
        self.verify_button.clicked.connect(self._verify)
        actions.addWidget(self.start_button)
        actions.addWidget(self.check_button)
        actions.addWidget(self.verify_button)
        actions.addStretch(1)
        root.addLayout(actions)

    def refresh(self) -> None:
        accounts = self.ctx.drops.accounts()
        current_account = self.account_combo.currentData()
        self.account_combo.clear()
        for account in accounts:
            self.account_combo.addItem(account.display_name, account.id)
        if current_account is not None:
            index = self.account_combo.findData(current_account)
            if index >= 0:
                self.account_combo.setCurrentIndex(index)

        current_campaign = self.campaign_combo.currentData()
        self.campaign_combo.clear()
        for campaign, status in self.ctx.drops.campaigns():
            if status.value == "ended":
                continue
            self.campaign_combo.addItem(campaign.campaign_name, campaign.id)
        if current_campaign is not None:
            index = self.campaign_combo.findData(current_campaign)
            if index >= 0:
                self.campaign_combo.setCurrentIndex(index)

        sessions = self.ctx.drops.sessions(open_only=False)
        with self.ctx.db.session() as session:
            from sqlalchemy import select
            from app.database import models
            account_names = {a.id: a.display_name for a in session.scalars(select(models.TwitchAccount))}
            campaign_names = {c.id: c.campaign_name for c in session.scalars(select(models.DropCampaign))}
        self.table.setRowCount(len(sessions))
        for row, watch_session in enumerate(sessions):
            label, color = SESSION_STATUS_META.get(watch_session.status,
                                                   (watch_session.status, "#9aa0a6"))
            set_row_id(self.table, row, watch_session.id)
            fill_cell(self.table, row, 1, account_names.get(watch_session.account_id, "?"))
            fill_cell(self.table, row, 2, campaign_names.get(watch_session.campaign_id, "?"))
            fill_cell(self.table, row, 3, watch_session.channel or "—")
            add_pill_cell(self.table, row, 4, label, color)
            fill_cell(self.table, row, 5, str(watch_session.planned_minutes), align_right=True)
            fill_cell(self.table, row, 6, str(watch_session.actual_progress), align_right=True)
            fill_cell(self.table, row, 7,
                      watch_session.started_at.strftime("%d.%m %H:%M") if watch_session.started_at else "—")

    def _plan(self) -> None:
        account_id = self.account_combo.currentData()
        campaign_id = self.campaign_combo.currentData()
        if account_id is None or campaign_id is None:
            QMessageBox.warning(self, "Сессия", "Нужны аккаунт и кампания.")
            return
        channel, ok = QInputDialog.getText(self, "Сессия", "Канал стрима (например, official_channel):")
        if not ok or not channel.strip():
            return
        self.ctx.drops.plan_session(account_id, campaign_id, channel.strip())
        self.refresh()

    def _selected_session(self):
        session_id = selected_row_id(self.table)
        if session_id is None:
            QMessageBox.information(self, "Сессия", "Выберите сессию в таблице.")
            return None
        with self.ctx.db.session() as db_session:
            from app.database import models
            return db_session.get(models.WatchSession, session_id)

    def _start(self) -> None:
        watch_session = self._selected_session()
        if watch_session is None:
            return
        if watch_session.status != "planned":
            QMessageBox.information(self, "Сессия", "Запускать можно только запланированные сессии.")
            return
        account = self.ctx.drops.get_account(watch_session.account_id)
        pid = None
        if account is not None and account.browser_profile_id:
            profile = None
            with self.ctx.db.session() as db_session:
                from app.database import models
                profile = db_session.get(models.BrowserProfile, account.browser_profile_id)
            if profile is not None:
                from app.browser.profile_manager import stream_url
                pid = self.ctx.browser.open(profile, stream_url(watch_session.channel or ""))
        self.ctx.drops.start_session(watch_session.id, browser_pid=pid)
        if pid is None:
            QMessageBox.information(
                self, "DRY RUN",
                "Сессия запущена логически, но браузер не открывался (сухой прогон).")
        self.refresh()

    def _tick(self) -> None:
        result = self.ctx.drops.tick_sessions()
        QMessageBox.information(
            self, "Проверка времени",
            f"Сессий на верификацию: {len(result['verify'])}. "
            f"Кампаний заканчивается в течение 24 ч: {len(result['ending_campaigns'])}.")
        self.refresh()

    def _verify(self) -> None:
        watch_session = self._selected_session()
        if watch_session is None:
            return
        if watch_session.status != "verify_progress":
            QMessageBox.information(self, "Верификация",
                                    "Сначала дождитесь статуса «Проверить прогресс» (или нажмите проверку).")
            return
        minutes, ok = QInputDialog.getInt(
            self, "Фактический прогресс",
            "Сколько минут подтверждено по инвентарю дропов?", 0, 0, 100000)
        if not ok:
            return
        self.ctx.drops.verify_session(watch_session.id, verified_minutes=minutes)
        self.refresh()

    def _open_url(self, url: str) -> None:
        account_id = self.account_combo.currentData()
        if account_id is None:
            QMessageBox.warning(self, "Браузер", "Выберите аккаунт.")
            return
        account = self.ctx.drops.get_account(account_id)
        if account is None or account.browser_profile_id is None:
            QMessageBox.warning(self, "Браузер",
                                "У аккаунта не настроен браузерный профиль — добавьте его в настройках дропов.")
            return
        with self.ctx.db.session() as db_session:
            from app.database import models
            profile = db_session.get(models.BrowserProfile, account.browser_profile_id)
        if profile is None:
            return
        pid = self.ctx.browser.open(profile, url)
        if pid is None:
            QMessageBox.information(self, "DRY RUN", "Браузер не запущен: режим сухого прогона.")
