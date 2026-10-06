"""Экран «Доходы»: аналитика ОС — выручка по типам, прибыль/час, рекомендации (ТЗ §48–49)."""
from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from app.ui.pages.base import BasePage, page_header, toolbar_row
from app.ui.widgets import StatCard, add_pill_cell, fill_cell, make_table, set_row_id

TYPE_LABELS = {"auto": "AUTO", "semi_auto": "SEMI_AUTO", "manual": "MANUAL"}
RECOMMENDATION_META = {
    "KILL": ("KILL", "#e05555"),
    "OPTIMIZE": ("OPTIMIZE", "#e0b341"),
    "SCALE": ("SCALE", "#2fbf71"),
    "KEEP": ("KEEP", "#9aa0a6"),
}


class RevenuePage(BasePage):
    title = "Доходы"

    def __init__(self, ctx, parent=None) -> None:
        super().__init__(ctx, parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(8)
        root.addWidget(page_header(
            "Доходы и эффективность",
            "Выручка, прибыль, ручное время и рекомендации по продуктам за последние 30 дней.",
        ))

        refresh_button = QPushButton("Обновить")
        refresh_button.clicked.connect(self.refresh)
        self.period_label = QLabel("Окно: 30 дней")
        self.period_label.setProperty("muted", True)
        root.addWidget(toolbar_row(refresh_button, self.period_label))

        cards_row = QHBoxLayout()
        self.revenue_card = StatCard("Выручка", "—")
        self.profit_card = StatCard("Прибыль", "—")
        self.profit_hour_card = StatCard("Прибыль/час", "—")
        self.manual_card = StatCard("Ручное время", "—")
        self.sales_card = StatCard("Продажи", "—")
        for card in (self.revenue_card, self.profit_card, self.profit_hour_card,
                     self.manual_card, self.sales_card):
            cards_row.addWidget(card)
        root.addLayout(cards_row)

        split_row = QHBoxLayout()
        self.auto_card = StatCard("AUTO — выручка", "—")
        self.semi_card = StatCard("SEMI_AUTO — выручка", "—")
        self.manual_sales_card = StatCard("MANUAL — выручка", "—")
        for card in (self.auto_card, self.semi_card, self.manual_sales_card):
            split_row.addWidget(card)
        root.addLayout(split_row)

        self.table = make_table([
            "ID", "Код", "Продукт", "Тип", "Продажи", "Выручка", "Прибыль",
            "Прибыль/час", "Автоматизация", "Рекомендация",
        ])
        for col, width in ((0, 40), (1, 170), (2, 240), (3, 90), (4, 70), (5, 90),
                           (6, 90), (7, 100), (8, 110), (9, 130)):
            self.table.setColumnWidth(col, width)
        root.addWidget(self.table, 1)

    def refresh(self) -> None:
        summary = self.ctx.os_analytics.summary(days=30)
        self.revenue_card.set_value(f"{summary['revenue']:.0f} ₽")
        self.profit_card.set_value(f"{summary['profit']:.0f} ₽")
        self.profit_hour_card.set_value(f"{summary['profit_per_hour']:.0f} ₽/ч")
        self.manual_card.set_value(f"{summary['manual_hours']:.1f} ч")
        self.sales_card.set_value(f"{summary['sales']} (отмен {summary['refunds']})")

        by_type = summary["by_type"]
        self.auto_card.set_value(f"{by_type.get('auto', {}).get('revenue', 0):.0f} ₽ · "
                                f"{by_type.get('auto', {}).get('sales', 0)} шт.")
        self.semi_card.set_value(f"{by_type.get('semi_auto', {}).get('revenue', 0):.0f} ₽ · "
                                f"{by_type.get('semi_auto', {}).get('sales', 0)} шт.")
        self.manual_sales_card.set_value(f"{by_type.get('manual', {}).get('revenue', 0):.0f} ₽ · "
                                        f"{by_type.get('manual', {}).get('sales', 0)} шт.")

        board = self.ctx.os_analytics.product_board()
        self.table.setRowCount(len(board))
        for row, item in enumerate(board):
            rec_label, rec_color = RECOMMENDATION_META.get(item["recommendation"],
                                                           (item["recommendation"], "#9aa0a6"))
            set_row_id(self.table, row, item["product_id"])
            fill_cell(self.table, row, 1, item["code"])
            fill_cell(self.table, row, 2, item["name"])
            fill_cell(self.table, row, 3, TYPE_LABELS.get(item["type"], item["type"]))
            fill_cell(self.table, row, 4, str(item["sales"]), align_right=True)
            fill_cell(self.table, row, 5, f"{item['revenue']:.0f} ₽", align_right=True)
            fill_cell(self.table, row, 6, f"{item['profit']:.0f} ₽", align_right=True)
            fill_cell(self.table, row, 7, f"{item['profit_per_hour']:.0f} ₽/ч", align_right=True)
            fill_cell(self.table, row, 8, f"{item['automation_pct']:.0f}%", align_right=True)
            add_pill_cell(self.table, row, 9, rec_label, rec_color)
