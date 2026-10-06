"""Экран «Drops Центр»: аккаунты, кампании, приоритеты, оценка, сток (ТЗ §28–34)."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from app.domain.enums import TwitchAccountStatus
from app.ui.pages.base import BasePage, page_header, toolbar_row
from app.ui.widgets import add_pill_cell, fill_cell, make_table, selected_row_id, set_row_id

ACCOUNT_STATUS_META = {
    "idle": ("Idle", "#9aa0a6"),
    "watching": ("Смотрит", "#4aa8e0"),
    "claim_required": ("Нужен клэйм", "#e0b341"),
    "complete": ("Готов", "#2fbf71"),
    "ready": ("В сток", "#2fbf71"),
    "listed": ("Выставлен", "#4aa8e0"),
    "reserved": ("Резерв", "#e0b341"),
    "sold": ("Продан", "#55585f"),
    "problem": ("Проблема", "#e05555"),
}

CAMPAIGN_STATUS_META = {
    "upcoming": ("Скоро", "#9aa0a6"),
    "active": ("Активна", "#2fbf71"),
    "ending_soon": ("Заканчивается", "#e0b341"),
    "ended": ("Завершена", "#55585f"),
}


class DropsPage(BasePage):
    title = "Drops Центр"

    def __init__(self, ctx, parent=None) -> None:
        super().__init__(ctx, parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(8)
        root.addWidget(page_header(
            "Drops Control Center",
            "Аккаунты и кампании. Прогресс — только подтверждённый по инвентарю; "
            "фальшивый просмотр исключён архитектурой.",
        ))

        self.add_account_button = QPushButton("➕ Аккаунт")
        self.add_account_button.setProperty("accent", True)
        self.add_account_button.clicked.connect(self._add_account)
        self.add_campaign_button = QPushButton("➕ Кампания")
        self.add_campaign_button.clicked.connect(self._add_campaign)
        self.refresh_button = QPushButton("Обновить")
        self.refresh_button.clicked.connect(self.refresh)
        root.addWidget(toolbar_row(self.add_account_button, self.add_campaign_button,
                                   self.refresh_button))

        self.accounts_table = make_table([
            "ID", "Аккаунт", "Статус", "Ценность, ₽", "Рекомендация", "Следующая кампания",
        ])
        for col, width in ((0, 40), (1, 180), (2, 130), (3, 100), (4, 160), (5, 260)):
            self.accounts_table.setColumnWidth(col, width)
        root.addWidget(self.accounts_table, 1)

        stock_row = QHBoxLayout()
        self.ready_button = QPushButton("В сток (готов)")
        self.ready_button.clicked.connect(self._mark_ready)
        self.list_button = QPushButton("Выставить")
        self.list_button.clicked.connect(self._mark_listed)
        self.package_button = QPushButton("Пакет передачи")
        self.package_button.clicked.connect(self._package)
        stock_row.addWidget(QLabel("Действия со стоком:"))
        stock_row.addWidget(self.ready_button)
        stock_row.addWidget(self.list_button)
        stock_row.addWidget(self.package_button)
        stock_row.addStretch(1)
        root.addLayout(stock_row)

        self.campaigns_table = make_table([
            "ID", "Игра", "Кампания", "Статус", "Награда", "Часы", "Ценность, ₽", "₽/ч", "Приоритет",
        ])
        for col, width in ((0, 40), (1, 160), (2, 220), (3, 130), (4, 100),
                           (5, 60), (6, 100), (7, 70), (8, 90)):
            self.campaigns_table.setColumnWidth(col, width)
        self.campaigns_table.setMaximumHeight(220)
        root.addWidget(self.campaigns_table)

    def refresh(self) -> None:
        accounts = self.ctx.drops.accounts()
        self.accounts_table.setRowCount(len(accounts))
        for row, account in enumerate(accounts):
            report = self.ctx.drops.valuation_report(account.id)
            label, color = ACCOUNT_STATUS_META.get(account.status, (account.status, "#9aa0a6"))
            set_row_id(self.accounts_table, row, account.id)
            fill_cell(self.accounts_table, row, 1, account.display_name)
            add_pill_cell(self.accounts_table, row, 2, label, color)
            fill_cell(self.accounts_table, row, 3, f"{report['current_value']:.0f}", align_right=True)
            fill_cell(self.accounts_table, row, 4, report["recommendation"])
            fill_cell(self.accounts_table, row, 5, report["next_campaign"] or "—")

        ranked = {c.id: score for c, score in self.ctx.drops.campaign_priorities()}
        campaigns = self.ctx.drops.campaigns()
        campaigns.sort(key=lambda item: -ranked.get(item[0].id, -1))
        self.campaigns_table.setRowCount(len(campaigns))
        for row, (campaign, status) in enumerate(campaigns):
            s_label, s_color = CAMPAIGN_STATUS_META.get(status.value, (status.value, "#9aa0a6"))
            set_row_id(self.campaigns_table, row, campaign.id)
            fill_cell(self.campaigns_table, row, 1, campaign.game_name)
            fill_cell(self.campaigns_table, row, 2, campaign.campaign_name)
            add_pill_cell(self.campaigns_table, row, 3, s_label, s_color)
            fill_cell(self.campaigns_table, row, 4, f"наград: {campaign.reward_count}")
            fill_cell(self.campaigns_table, row, 5, f"{campaign.required_minutes / 60.0:.1f}", align_right=True)
            fill_cell(self.campaigns_table, row, 6, f"{campaign.estimated_value:.0f}", align_right=True)
            fill_cell(self.campaigns_table, row, 7, f"{self.ctx.drops.value_per_hour(campaign):.0f}",
                      align_right=True)
            fill_cell(self.campaigns_table, row, 8, f"{ranked.get(campaign.id, 0):.0f}", align_right=True)

    # ------------------------------------------------------------ аккаунты
    def _add_account(self) -> None:
        name, ok = QInputDialog.getText(self, "Аккаунт", "Отображаемое имя аккаунта:")
        if ok and name.strip():
            self.ctx.drops.add_account(name.strip())
            self.refresh()

    def _selected_account_id(self) -> int | None:
        account_id = selected_row_id(self.accounts_table)
        if account_id is None:
            QMessageBox.information(self, "Drops", "Выберите аккаунт в таблице.")
        return account_id

    def _mark_ready(self) -> None:
        account_id = self._selected_account_id()
        if account_id is None:
            return
        if self.ctx.drops.mark_ready(account_id):
            self.refresh()
        else:
            QMessageBox.warning(self, "Сток", "Аккаунт нельзя перевести в сток из текущего статуса.")

    def _mark_listed(self) -> None:
        account_id = self._selected_account_id()
        if account_id is None:
            return
        if self.ctx.drops.mark_listed(account_id):
            self.refresh()
        else:
            QMessageBox.warning(self, "Сток", "Выставлять можно только аккаунты в статусе «В сток».")

    def _package(self) -> None:
        account_id = self._selected_account_id()
        if account_id is None:
            return
        package = self.ctx.drops.delivery_package(account_id)
        QMessageBox.information(
            self, "Пакет передачи",
            f"Аккаунт: {package['account']}\nЛогин: {package['login']}\n"
            f"Секрет: {package['login_ref'] or '—'} (только ссылка на хранилище)\n"
            f"Дропы: {', '.join(package['included_drops']) or '—'}\n"
            f"Оценка: {package['estimated_value']:.0f} ₽",
        )

    # ------------------------------------------------------------- кампании
    def _add_campaign(self) -> None:
        dialog = CampaignDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.ctx.drops.add_campaign(
            game_name=dialog.game_input.text().strip() or "?",
            campaign_name=dialog.name_input.text().strip(),
            required_minutes=dialog.minutes_input.value(),
            rewards=[r.strip() for r in dialog.rewards_input.text().split(",") if r.strip()] or ["Награда"],
            estimated_value=dialog.value_input.value(),
            priority=dialog.priority_input.value(),
        )
        self.refresh()


class CampaignDialog(QDialog):
    """Ручной ввод кампании: официального списка кампаний у Twitch нет (см. Матрицу)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Новая кампания (вводится вручную)")
        self.setMinimumWidth(420)
        form = QFormLayout(self)
        self.game_input = QLineEdit()
        self.name_input = QLineEdit()
        self.rewards_input = QLineEdit()
        self.rewards_input.setPlaceholderText("Награды через запятую")
        self.minutes_input = QSpinBox(); self.minutes_input.setRange(1, 10000); self.minutes_input.setValue(120)
        self.value_input = QDoubleSpinBox(); self.value_input.setRange(0, 10**6); self.value_input.setValue(300)
        self.priority_input = QSpinBox(); self.priority_input.setRange(0, 10)
        form.addRow("Игра", self.game_input)
        form.addRow("Кампания", self.name_input)
        form.addRow("Минут просмотра", self.minutes_input)
        form.addRow("Награды", self.rewards_input)
        form.addRow("Ценность, ₽", self.value_input)
        form.addRow("Приоритет (буст)", self.priority_input)

        buttons = QHBoxLayout()
        ok_button = QPushButton("Добавить")
        ok_button.setProperty("accent", True)
        cancel_button = QPushButton("Отмена")
        buttons.addWidget(ok_button)
        buttons.addWidget(cancel_button)
        buttons.addStretch(1)
        form.addRow(buttons)
        ok_button.clicked.connect(self.accept)
        cancel_button.clicked.connect(self.reject)
