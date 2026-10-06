"""Экран «Клиенты» (ТЗ §11): только необходимые данные, без избыточных ПД."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QHBoxLayout,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from app.ui.dialogs import AddClientDialog
from app.ui.pages.base import BasePage, page_header, toolbar_row
from app.ui.widgets import add_pill_cell, fill_cell, make_table, set_row_id, selected_row_id
from sqlalchemy import select

from app.database import models
from app.domain.enums import AuditAction, Actor


class ClientsPage(BasePage):
    title = "Клиенты"

    def __init__(self, ctx, parent=None) -> None:
        super().__init__(ctx, parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(8)
        root.addWidget(page_header(
            "Клиенты",
            "Минимально необходимые данные: ник, статистика покупок, заметки. Чёрный список блокирует новые заказы.",
        ))

        self.add_button = QPushButton("Новый клиент")
        self.add_button.setProperty("accent", True)
        self.add_button.clicked.connect(self._add_client)
        self.blacklist_button = QPushButton("Чёрный список: вкл/выкл")
        self.blacklist_button.clicked.connect(self._toggle_blacklist)
        self.notes_button = QPushButton("Заметки")
        self.notes_button.clicked.connect(self._edit_notes)
        root.addWidget(toolbar_row(self.add_button, self.blacklist_button, self.notes_button))

        self.table = make_table([
            "ID", "FunPay username", "Заказов", "Первая покупка", "Последняя покупка",
            "Активных доступов", "Потрачено", "Чёрный список",
        ])
        self.table.setColumnWidth(0, 40)
        self.table.setColumnWidth(1, 220)
        root.addWidget(self.table, 1)

    def refresh(self) -> None:
        with self.ctx.db.session() as session:
            clients = list(session.scalars(select(models.Client).order_by(models.Client.funpay_username)))
            self.table.setRowCount(len(clients))
            for row, client in enumerate(clients):
                orders = [o for o in client.orders]
                active = len([
                    o for o in orders
                    if o.status in ("new", "processing", "waiting_client", "steam_setup", "active", "expiring")
                ])
                set_row_id(self.table, row, client.id)
                fill_cell(self.table, row, 1, client.funpay_username)
                fill_cell(self.table, row, 2, str(len(orders)), align_right=True)
                fill_cell(self.table, row, 3, client.first_order_at.strftime("%d.%m.%Y") if client.first_order_at else "—")
                fill_cell(self.table, row, 4, client.last_order_at.strftime("%d.%m.%Y") if client.last_order_at else "—")
                fill_cell(self.table, row, 5, str(active), align_right=True)
                fill_cell(self.table, row, 6, f"{client.total_spent:.0f} ₽", align_right=True)
                if client.blacklisted:
                    add_pill_cell(self.table, row, 7, "в чёрном списке", "#ef5b62")
                else:
                    fill_cell(self.table, row, 7, "")

    def _add_client(self) -> None:
        dialog = AddClientDialog(self)
        if not dialog.exec():
            return
        username = dialog.username.text().strip()
        if not username:
            QMessageBox.warning(self, "Клиенты", "Укажите FunPay username.")
            return
        with self.ctx.db.session() as session:
            from app.database import repositories

            client = repositories.ClientRepo(session).get_or_create(username, dialog.display_name.text().strip() or None)
            notes = dialog.notes.toPlainText().strip()
            if notes:
                client.notes = notes
            self.ctx.audit.log(AuditAction.CLIENT_CREATED, Actor.USER, entity="client",
                                entity_id=client.id, session=session, username=username)
        self.refresh()

    def _toggle_blacklist(self) -> None:
        client_id = selected_row_id(self.table)
        if client_id is None:
            return
        with self.ctx.db.session() as session:
            client = session.get(models.Client, client_id)
            if client is None:
                return
            client.blacklisted = not client.blacklisted
            self.ctx.audit.log(AuditAction.CLIENT_BLACKLISTED, Actor.USER, entity="client",
                                entity_id=client_id, session=session, blacklisted=client.blacklisted)
        self.refresh()

    def _edit_notes(self) -> None:
        client_id = selected_row_id(self.table)
        if client_id is None:
            return
        with self.ctx.db.session() as session:
            client = session.get(models.Client, client_id)
            if client is None:
                return
            username = client.funpay_username
            notes = client.notes or ""
            problems = client.problems or ""

        from PySide6.QtWidgets import QDialog, QFormLayout, QDialogButtonBox

        dialog = QDialog(self)
        dialog.setWindowTitle(f"Заметки: {username}")
        dialog.setMinimumWidth(480)
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        notes_edit = QPlainTextEdit(notes)
        problems_edit = QPlainTextEdit(problems)
        notes_edit.setFixedHeight(90)
        problems_edit.setFixedHeight(70)
        form.addRow("Заметки", notes_edit)
        form.addRow("Проблемы", problems_edit)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec():
            with self.ctx.db.session() as session:
                client = session.get(models.Client, client_id)
                client.notes = notes_edit.toPlainText() or None
                client.problems = problems_edit.toPlainText() or None
                self.ctx.audit.log(AuditAction.NOTE_UPDATED, Actor.USER, entity="client",
                                    entity_id=client_id, session=session)
            self.refresh()
