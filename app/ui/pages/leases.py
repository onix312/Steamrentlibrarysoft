"""Экран «Доступы»: активные аренды, таймеры, освобождение лицензий."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)
from sqlalchemy import select

from app.database import models
from app.domain.enums import EligibilityStatus, LeaseStatus
from app.domain.value_objects import DomainError
from app.services.access_service import LicenseAllocator
from app.ui.dialogs import NewLeaseDialog
from app.ui.pages.base import BasePage, page_header, toolbar_row
from app.ui.theme import LEASE_STATUS_META
from app.ui.widgets import add_pill_cell, fill_cell, make_table, set_row_id, selected_row_id


class LeasesPage(BasePage):
    title = "Доступы"

    def __init__(self, ctx, parent=None) -> None:
        super().__init__(ctx, parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(8)
        root.addWidget(page_header(
            "Доступы (аренды)",
            "Какая лицензия, какому клиенту и до какой даты отдана. Таймер обновляется автоматически.",
        ))

        self.new_button = QPushButton("Выдать доступ вручную")
        self.new_button.setProperty("accent", True)
        self.new_button.clicked.connect(self._new_lease)
        self.show_all = QCheckBox("Показывать завершённые")
        self.show_all.toggled.connect(self.refresh)
        root.addWidget(toolbar_row(self.new_button, self.show_all))

        self.table = make_table([
            "ID", "Клиент", "Игра", "Аккаунт-владелец", "Копия", "Начало",
            "Окончание", "Осталось", "Статус",
        ])
        self.table.setColumnWidth(0, 40)
        self.table.setColumnWidth(1, 150)
        self.table.setColumnWidth(2, 220)
        self.table.setColumnWidth(3, 170)
        self.table.setColumnWidth(7, 130)
        root.addWidget(self.table, 1)

        actions = QHBoxLayout()
        self.extend_button = QPushButton("Продлить (+дни)")
        self.extend_button.clicked.connect(self._extend)
        self.complete_button = QPushButton("Завершить доступ")
        self.complete_button.clicked.connect(self._complete)
        self.cancel_button = QPushButton("Отменить аренду")
        self.cancel_button.setProperty("danger", True)
        self.cancel_button.clicked.connect(self._cancel)
        actions.addWidget(self.extend_button)
        actions.addWidget(self.complete_button)
        actions.addWidget(self.cancel_button)
        actions.addStretch(1)
        root.addLayout(actions)

    # ------------------------------------------------------------- отображение
    def refresh(self) -> None:
        now = self.ctx.clock.now()
        with self.ctx.db.session() as session:
            leases = list(session.scalars(select(models.AccessLease).order_by(models.AccessLease.expires_at)))
            rows = []
            for lease in leases:
                if not self.show_all.isChecked() and lease.status in ("expired", "completed", "cancelled"):
                    continue
                client = session.get(models.Client, lease.client_id)
                game = session.get(models.Game, lease.game_id)
                account = session.get(models.SteamAccount, lease.owner_account_id)
                rows.append((lease, client, game, account))

        self.table.setRowCount(len(rows))
        for row, (lease, client, game, account) in enumerate(rows):
            set_row_id(self.table, row, lease.id)
            fill_cell(self.table, row, 1, client.funpay_username if client else "?")
            fill_cell(self.table, row, 2, game.name if game else "?")
            fill_cell(self.table, row, 3, account.display_name if account else "?")
            fill_cell(self.table, row, 4, f"#{lease.license_id}", align_right=True)
            fill_cell(self.table, row, 5, lease.starts_at.strftime("%d.%m.%Y %H:%M"))
            fill_cell(self.table, row, 6, lease.expires_at.strftime("%d.%m.%Y %H:%M"))
            if lease.status in ("expired", "completed", "cancelled"):
                left_text = "—"
                left_color = "#8f959e"
            else:
                delta = lease.expires_at - now
                total_minutes = int(delta.total_seconds() // 60)
                if total_minutes < 0:
                    left_text = "истекла"
                    left_color = "#ef5b62"
                else:
                    days, minutes = divmod(total_minutes, 1440)
                    hours, minutes = divmod(minutes, 60)
                    left_text = f"{days}д {hours:02d}:{minutes:02d}" if days else f"{hours:02d}:{minutes:02d}"
                    left_color = "#f0b232" if delta.total_seconds() < 24 * 3600 else "#2fbf71"
            fill_cell(self.table, row, 7, left_text, left_color, align_right=True)
            text, color = LEASE_STATUS_META.get(lease.status, (lease.status, "#8f959e"))
            add_pill_cell(self.table, row, 8, text, color)

    # --------------------------------------------------------------- действия
    def _new_lease(self) -> None:
        with self.ctx.db.session() as session:
            games = session.scalars(
                select(models.Game).where(
                    models.Game.is_free.is_(False),
                    models.Game.eligibility_status == EligibilityStatus.AVAILABLE.value,
                ).order_by(models.Game.name)
            ).all()
            allocator = LicenseAllocator(session, self.ctx.clock)
            options = []
            for game in games:
                availability = allocator.availability(game.id)
                if availability.free > 0:
                    options.append((game.id, game.name, availability.free))
            clients = [c.funpay_username for c in session.scalars(select(models.Client))]

        if not options:
            QMessageBox.information(self, "Доступы", "Нет игр со свободными копиями.")
            return
        dialog = NewLeaseDialog(options, clients, self)
        if not dialog.exec():
            return
        username = dialog.client.currentText().strip()
        game_id = dialog.game.currentData()
        if not username or game_id is None:
            return
        with self.ctx.db.session() as session:
            from app.database import repositories

            client = repositories.ClientRepo(session).get_or_create(username)
            client_id = client.id
        try:
            lease = self.ctx.access.create_lease(client_id=client_id, game_id=game_id, days=dialog.days.value())
        except DomainError as exc:
            QMessageBox.warning(self, "Доступы", str(exc))
            return
        self.refresh()
        QMessageBox.information(self, "Доступ выдан",
                                f"Аренда #{lease.id}: лицензия #{lease.license_id} (аккаунт #{lease.owner_account_id}) "
                                f"до {lease.expires_at.strftime('%d.%m.%Y %H:%M')}.")

    def _extend(self) -> None:
        lease_id = selected_row_id(self.table)
        if lease_id is None:
            return
        days, ok = QInputDialog.getInt(self, "Продление доступа", "Добавить дней:", 7, 1, 365)
        if not ok:
            return
        self.ctx.access.extend_lease(lease_id, days)
        self.refresh()

    def _complete(self) -> None:
        lease_id = selected_row_id(self.table)
        if lease_id is None:
            return
        self.ctx.access.complete_lease(lease_id)
        self.refresh()

    def _cancel(self) -> None:
        lease_id = selected_row_id(self.table)
        if lease_id is None:
            return
        answer = QMessageBox.question(self, "Отмена аренды", "Отменить аренду? Лицензия освободится немедленно.")
        if answer == QMessageBox.StandardButton.Yes:
            self.ctx.access.cancel_lease(lease_id)
            self.refresh()
