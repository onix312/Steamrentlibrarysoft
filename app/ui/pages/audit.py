"""Экран «Журнал»: audit log всех значимых действий (ТЗ §20)."""
from __future__ import annotations

import json

from PySide6.QtWidgets import QVBoxLayout

from app.ui.pages.base import BasePage, page_header
from app.ui.widgets import fill_cell, make_table


class AuditPage(BasePage):
    title = "Журнал"

    def __init__(self, ctx, parent=None) -> None:
        super().__init__(ctx, parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(8)
        root.addWidget(page_header(
            "Журнал операций",
            "Все значимые действия: заказы, лицензии, цены, сообщения, ошибки. Позволяет восстановить ход событий.",
        ))
        self.table = make_table(["Время", "Кто", "Действие", "Объект", "Детали"])
        self.table.setColumnWidth(0, 130)
        self.table.setColumnWidth(1, 90)
        self.table.setColumnWidth(2, 200)
        self.table.setColumnWidth(3, 120)
        root.addWidget(self.table, 1)

    def refresh(self) -> None:
        entries = self.ctx.audit.latest(300)
        self.table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            fill_cell(self.table, row, 0, entry.ts.strftime("%d.%m.%Y %H:%M:%S"))
            fill_cell(self.table, row, 1, entry.actor)
            fill_cell(self.table, row, 2, entry.action)
            entity = entry.entity or ""
            if entry.entity_id:
                entity += f" #{entry.entity_id}"
            fill_cell(self.table, row, 3, entity)
            try:
                details = json.dumps(entry.details, ensure_ascii=False) if entry.details else ""
            except (TypeError, ValueError):
                details = str(entry.details)
            fill_cell(self.table, row, 4, details if len(details) <= 160 else details[:157] + "…")
