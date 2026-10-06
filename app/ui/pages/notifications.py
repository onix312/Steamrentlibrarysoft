"""Экран «Уведомления»: центр уведомлений приложения."""
from __future__ import annotations

from PySide6.QtWidgets import QPushButton, QVBoxLayout

from app.ui.pages.base import BasePage, page_header, toolbar_row
from app.ui.widgets import add_pill_cell, fill_cell, make_table, set_row_id, selected_row_id

KIND_COLORS = {
    "order": "#4aa8e0",
    "lease": "#f0b232",
    "license": "#2fbf71",
    "sync": "#5b6ef2",
    "error": "#ef5b62",
    "action_required": "#ef5b62",
    "info": "#7d8590",
}


class NotificationsPage(BasePage):
    title = "Уведомления"

    def __init__(self, ctx, parent=None) -> None:
        super().__init__(ctx, parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(8)
        root.addWidget(page_header("Уведомления", "Центр уведомлений: заказы, аренды, синхронизация, ошибки."))

        self.read_button = QPushButton("Отметить прочитанным")
        self.read_button.clicked.connect(self._mark_read)
        self.read_all_button = QPushButton("Прочитать все")
        self.read_all_button.clicked.connect(self._mark_all)
        root.addWidget(toolbar_row(self.read_button, self.read_all_button))

        self.table = make_table(["ID", "Время", "Тип", "Заголовок", "Текст"])
        self.table.setColumnWidth(0, 40)
        self.table.setColumnWidth(1, 140)
        self.table.setColumnWidth(2, 120)
        self.table.setColumnWidth(3, 300)
        root.addWidget(self.table, 1)

    def refresh(self) -> None:
        items = self.ctx.notifications.latest(200)
        self.table.setRowCount(len(items))
        for row, item in enumerate(items):
            set_row_id(self.table, row, item.id)
            fill_cell(self.table, row, 1, item.ts.strftime("%d.%m %H:%M"))
            color = KIND_COLORS.get(item.kind, "#7d8590")
            add_pill_cell(self.table, row, 2, item.kind, color)
            title = item.title if item.read else f"● {item.title}"
            fill_cell(self.table, row, 3, title, None if item.read else color)
            fill_cell(self.table, row, 4, item.body or "")

    def _mark_read(self) -> None:
        item_id = selected_row_id(self.table)
        if item_id is not None:
            self.ctx.notifications.mark_read(item_id)
            self.refresh()

    def _mark_all(self) -> None:
        self.ctx.notifications.mark_all_read()
        self.refresh()
