"""Operations Center: one prioritized screen for everything requiring attention."""
from __future__ import annotations

from PySide6.QtWidgets import QGridLayout, QVBoxLayout, QWidget

from app.ui.pages.base import BasePage, page_header
from app.ui.widgets import StatCard, fill_cell, make_table


class OperationsPage(BasePage):
    title = "Operations"

    def __init__(self, ctx, parent=None) -> None:
        super().__init__(ctx, parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(12)
        root.addWidget(page_header(
            "Operations Center",
            "Единая очередь того, что требует внимания — от ошибок интеграций до заказов и Drops.",
        ))

        holder = QWidget()
        grid = QGridLayout(holder)
        grid.setContentsMargins(0, 0, 0, 0)
        self.cards = {}
        for idx, (key, label) in enumerate([
            ("total", "Требуют внимания"),
            ("critical", "Критичные"),
            ("manual", "Заказы / ручные"),
            ("stock", "Пополнение склада"),
            ("drops", "Drops actions"),
        ]):
            card = StatCard(label)
            self.cards[key] = card
            grid.addWidget(card, 0, idx)
        root.addWidget(holder)

        self.table = make_table(["Priority", "Тип", "Действие", "Детали", "Раздел"])
        self.table.setColumnWidth(0, 70)
        self.table.setColumnWidth(1, 120)
        self.table.setColumnWidth(2, 280)
        self.table.setColumnWidth(3, 480)
        self.table.setColumnWidth(4, 140)
        root.addWidget(self.table, 1)

    def refresh(self) -> None:
        summary = self.ctx.operations.summary()
        for key, card in self.cards.items():
            card.set_value(str(summary[key]))

        actions = self.ctx.operations.actions()
        self.table.setRowCount(len(actions))
        for row, action in enumerate(actions):
            fill_cell(self.table, row, 0, str(action.priority), align_right=True)
            fill_cell(self.table, row, 1, action.kind)
            fill_cell(self.table, row, 2, action.title)
            fill_cell(self.table, row, 3, action.detail)
            fill_cell(self.table, row, 4, action.section)
