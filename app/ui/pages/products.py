"""Экран «Продукты»: каталог + фабрика продуктов + рекомендации (ТЗ §8–9, §48)."""
from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QMessageBox, QPushButton, QVBoxLayout

from app.ui.pages.base import BasePage, page_header, toolbar_row
from app.ui.widgets import add_pill_cell, fill_cell, make_table, selected_row_id, set_row_id

TYPE_META = {
    "auto": ("AUTO", "#2fbf71"),
    "semi_auto": ("SEMI", "#e0b341"),
    "manual": ("MANUAL", "#4aa8e0"),
}
RECOMMENDATION_META = {
    "KILL": ("KILL", "#e05555"),
    "OPTIMIZE": ("OPTIMIZE", "#e0b341"),
    "SCALE": ("SCALE", "#2fbf71"),
    "KEEP": ("KEEP", "#9aa0a6"),
    "—": ("—", "#55585f"),
}


class ProductsPage(BasePage):
    title = "Продукты"

    def __init__(self, ctx, parent=None) -> None:
        super().__init__(ctx, parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(8)
        root.addWidget(page_header(
            "Продукты",
            "Цифровые товары и услуги. Фабрика создаёт из одного движка много реальных продуктов.",
        ))

        self.game_combo = QComboBox()
        self.game_combo.addItems(self.ctx.products.factory_games())
        self.factory_button = QPushButton("🏭 Создать из фабрики")
        self.factory_button.setProperty("accent", True)
        self.factory_button.clicked.connect(self._from_factory)
        self.toggle_button = QPushButton("Вкл/выкл выбранный")
        self.toggle_button.clicked.connect(self._toggle)
        self.refresh_button = QPushButton("Обновить")
        self.refresh_button.clicked.connect(self.refresh)
        root.addWidget(toolbar_row(self.game_combo, self.factory_button,
                                   self.toggle_button, self.refresh_button))

        self.table = make_table([
            "ID", "Код", "Название", "Игра", "Тип", "Уровень", "Цена",
            "Мин.", "Склад", "Продано", "Рекомендация", "Активен",
        ])
        for col, width in ((0, 40), (1, 170), (2, 260), (3, 150), (4, 70),
                           (5, 70), (6, 80), (7, 80), (8, 90), (9, 80), (10, 120), (11, 80)):
            self.table.setColumnWidth(col, width)
        root.addWidget(self.table, 1)

    def refresh(self) -> None:
        products = self.ctx.products.all()
        self.table.setRowCount(len(products))
        for row, product in enumerate(products):
            counts = self.ctx.stock.counts(product.id)
            metrics = self.ctx.products.metrics(product.id)
            recommendation = self.ctx.products.recommendation(product.id)
            type_label, type_color = TYPE_META.get(product.type, ("?", "#9aa0a6"))
            rec_label, rec_color = RECOMMENDATION_META.get(recommendation, (recommendation, "#9aa0a6"))
            set_row_id(self.table, row, product.id)
            fill_cell(self.table, row, 1, product.code)
            fill_cell(self.table, row, 2, product.name)
            fill_cell(self.table, row, 3, product.game or "—")
            add_pill_cell(self.table, row, 4, type_label, type_color)
            fill_cell(self.table, row, 5, product.automation_level)
            fill_cell(self.table, row, 6, f"{product.price:.0f} ₽", align_right=True)
            fill_cell(self.table, row, 7, f"{product.minimum_price:.0f} ₽", align_right=True)
            fill_cell(self.table, row, 8,
                      f"готово {counts.get('ready', 0)}/{product.target_stock or 0}", align_right=True)
            fill_cell(self.table, row, 9, str(metrics.get("sales", 0)), align_right=True)
            add_pill_cell(self.table, row, 10, rec_label, rec_color)
            add_pill_cell(self.table, row, 11, "да" if product.active else "нет",
                          "#2fbf71" if product.active else "#55585f")

    def _from_factory(self) -> None:
        game = self.game_combo.currentText()
        created = self.ctx.products.create_from_factory(game)
        if created:
            QMessageBox.information(self, "Фабрика продуктов",
                                    f"Создано продуктов: {len(created)}\n" +
                                    "\n".join(f"• {p.code} — {p.price:.0f} ₽" for p in created))
        else:
            QMessageBox.information(self, "Фабрика продуктов",
                                    "Все продукты этой фабрики уже созданы ранее.")
        self.refresh()

    def _toggle(self) -> None:
        product_id = selected_row_id(self.table)
        if product_id is None:
            return
        product = self.ctx.products.get(product_id)
        if product is not None:
            self.ctx.products.set_active(product_id, not product.active)
        self.refresh()
