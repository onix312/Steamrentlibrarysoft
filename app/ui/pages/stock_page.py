"""Экран «Склад»: цифровые юниты, атомарные статусы, задачи пополнения (ТЗ §22–23, §46)."""
from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QInputDialog, QMessageBox, QPushButton, QVBoxLayout

from app.domain.enums import StockStatus
from app.ui.pages.base import BasePage, page_header, toolbar_row
from app.ui.widgets import add_pill_cell, fill_cell, make_table, selected_row_id, set_row_id

STATUS_META = {
    "preparing": ("Готовится", "#9aa0a6"),
    "ready": ("Готов", "#2fbf71"),
    "listed": ("Выставлен", "#4aa8e0"),
    "reserved": ("Резерв", "#e0b341"),
    "sold": ("Продан", "#2fbf71"),
    "expired": ("Истёк", "#55585f"),
    "problem": ("Проблема", "#e05555"),
}


class StockPage(BasePage):
    title = "Склад"

    def __init__(self, ctx, parent=None) -> None:
        super().__init__(ctx, parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(8)
        root.addWidget(page_header(
            "Цифровой склад",
            "Готовые товары/доступы. Резервирование атомарное: два заказа не получат один юнит.",
        ))

        self.product_combo = QComboBox()
        self.add_button = QPushButton("➕ Добавить юнит")
        self.add_button.setProperty("accent", True)
        self.add_button.clicked.connect(self._add_unit)
        self.problem_button = QPushButton("Пометить проблему")
        self.problem_button.clicked.connect(self._mark_problem)
        self.refresh_button = QPushButton("Обновить")
        self.refresh_button.clicked.connect(self.refresh)
        root.addWidget(toolbar_row(self.product_combo, self.add_button,
                                   self.problem_button, self.refresh_button))

        self.table = make_table(["ID", "Продукт", "Полезная нагрузка", "Статус", "Заметки"])
        for col, width in ((0, 40), (1, 260), (2, 260), (3, 110), (4, 320)):
            self.table.setColumnWidth(col, width)
        root.addWidget(self.table, 1)

        root.addWidget(page_header("Пополнение", ""))
        self.replenish_button = QPushButton("Проверить дефицит")
        self.replenish_button.clicked.connect(self._check_replenishment)
        self.close_task_button = QPushButton("Закрыть задачу")
        self.close_task_button.clicked.connect(self._close_task)
        root.addWidget(toolbar_row(self.replenish_button, self.close_task_button))

        self.tasks_table = make_table(["ID", "Продукт", "Кол-во", "Статус", "Комментарий"])
        for col, width in ((0, 40), (1, 260), (2, 70), (3, 90), (4, 320)):
            self.tasks_table.setColumnWidth(col, width)
        self.tasks_table.setMaximumHeight(180)
        root.addWidget(self.tasks_table)

    def refresh(self) -> None:
        products = self.ctx.products.all()
        current = self.product_combo.currentText()
        self.product_combo.clear()
        for product in products:
            self.product_combo.addItem(f"{product.code} — {product.name}", product.id)
        if current:
            index = self.product_combo.findText(current)
            if index >= 0:
                self.product_combo.setCurrentIndex(index)

        units = []
        for product in products:
            units.extend(self.ctx.stock.for_product(product.id))
        names = {product.id: product.name for product in products}
        units.sort(key=lambda unit: unit.id)
        self.table.setRowCount(len(units))
        for row, unit in enumerate(units):
            label, color = STATUS_META.get(unit.status, (unit.status, "#9aa0a6"))
            set_row_id(self.table, row, unit.id)
            fill_cell(self.table, row, 1, names.get(unit.product_id, str(unit.product_id)))
            fill_cell(self.table, row, 2, unit.payload_ref or "—")
            add_pill_cell(self.table, row, 3, label, color)
            fill_cell(self.table, row, 4, unit.notes or "")

        tasks = self.ctx.stock.open_replenishments()
        self.tasks_table.setRowCount(len(tasks))
        for row, task in enumerate(tasks):
            set_row_id(self.tasks_table, row, task.id)
            fill_cell(self.tasks_table, row, 1, names.get(task.product_id, str(task.product_id)))
            fill_cell(self.tasks_table, row, 2, str(task.quantity), align_right=True)
            add_pill_cell(self.tasks_table, row, 3, "открыта", "#e0b341")
            fill_cell(self.tasks_table, row, 4, task.notes or "")

    def _add_unit(self) -> None:
        product_id = self.product_combo.currentData()
        if product_id is None:
            QMessageBox.warning(self, "Склад", "Сначала создайте продукт.")
            return
        payload, ok = QInputDialog.getText(self, "Новый юнит",
                                           "Ссылка на полезную нагрузку (файл/ключ/аккаунт):")
        if not ok:
            return
        self.ctx.stock.add_unit(product_id, payload_ref=payload.strip() or None)
        self.refresh()

    def _mark_problem(self) -> None:
        unit_id = selected_row_id(self.table)
        if unit_id is None:
            return
        reason, ok = QInputDialog.getText(self, "Проблема", "Опишите проблему юнита:")
        if ok and reason.strip():
            self.ctx.stock.mark_problem(unit_id, reason.strip())
            self.refresh()

    def _check_replenishment(self) -> None:
        tasks = self.ctx.stock.check_replenishment(self.ctx.products.all(active_only=True))
        QMessageBox.information(self, "Пополнение",
                                f"Новых задач: {len(tasks)}." if tasks else "Дефицита нет.")
        self.refresh()

    def _close_task(self) -> None:
        task_id = selected_row_id(self.tasks_table)
        if task_id is not None:
            self.ctx.stock.close_replenishment(task_id)
            self.refresh()
