"""Экран «Очередь»: операторы и планирование исполнения (ТЗ §39–40)."""
from __future__ import annotations

from datetime import timedelta

from PySide6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from app.services.os.queue_service import QUEUE_ORDER
from app.ui.pages.base import BasePage, page_header, toolbar_row
from app.ui.widgets import add_pill_cell, fill_cell, make_table, selected_row_id, set_row_id

BUCKET_LABELS = {
    "now": ("СЕЙЧАС", "#e05555"),
    "today": ("Сегодня", "#e0b341"),
    "tomorrow": ("Завтра", "#4aa8e0"),
    "waiting": ("Ожидание", "#9aa0a6"),
}


class QueuePage(BasePage):
    title = "Очередь"

    def __init__(self, ctx, parent=None) -> None:
        super().__init__(ctx, parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(8)
        root.addWidget(page_header(
            "Очередь исполнения",
            "Ручные услуги и заказы, назначенные операторам. Планирование: сейчас / сегодня / завтра / ожидание.",
        ))

        self.bucket_combo = QComboBox()
        for key in QUEUE_ORDER:
            label, _ = BUCKET_LABELS[key]
            self.bucket_combo.addItem(label, key)
        self.bucket_combo.currentIndexChanged.connect(self.refresh)
        self.refresh_button = QPushButton("Обновить")
        self.refresh_button.clicked.connect(self.refresh)

        self.operator_combo = QComboBox()
        self.add_operator_button = QPushButton("➕ Оператор")
        self.add_operator_button.clicked.connect(self._add_operator)
        queue_label = QLabel("Очередь:")
        operator_label = QLabel("Оператор:")
        root.addWidget(toolbar_row(queue_label, self.bucket_combo, self.refresh_button,
                                   operator_label, self.operator_combo, self.add_operator_button))

        self.table = make_table(["ID", "Заказ", "Оператор", "Продукт/услуга", "Цена", "Срок", "Статус заказа"])
        for col, width in ((0, 40), (1, 70), (2, 160), (3, 300), (4, 80), (5, 150), (6, 140)):
            self.table.setColumnWidth(col, width)
        root.addWidget(self.table, 1)

        actions_box = QGroupBox("Действия с назначением")
        actions = QHBoxLayout(actions_box)
        self.assign_button = QPushButton("Назначить заказ оператору…")
        self.assign_button.setProperty("accent", True)
        self.assign_button.clicked.connect(self._assign)
        self.reschedule_button = QPushButton("Перенести (+1 день)")
        self.reschedule_button.clicked.connect(self._reschedule)
        self.done_button = QPushButton("Выполнено")
        self.done_button.clicked.connect(self._done)
        self.cancel_button = QPushButton("Снять назначение")
        self.cancel_button.setProperty("danger", True)
        self.cancel_button.clicked.connect(self._cancel)
        actions.addWidget(self.assign_button)
        actions.addWidget(self.reschedule_button)
        actions.addWidget(self.done_button)
        actions.addWidget(self.cancel_button)
        actions.addStretch(1)
        root.addWidget(actions_box)

        root.addWidget(page_header("Операторы", ""))
        self.operators_table = make_table(["ID", "Имя", "Игры", "Стоимость/заказ", "Открыто задач", "Активен"])
        for col, width in ((0, 40), (1, 200), (2, 260), (3, 120), (4, 120), (5, 90)):
            self.operators_table.setColumnWidth(col, width)
        self.operators_table.setMaximumHeight(160)
        root.addWidget(self.operators_table)

    def refresh(self) -> None:
        view = self.ctx.queue.queue_view()
        bucket = self.bucket_combo.currentData() or "now"
        items = view.get(bucket, [])
        self.table.setRowCount(len(items))
        for row, item in enumerate(items):
            set_row_id(self.table, row, item["assignment_id"])
            fill_cell(self.table, row, 1, str(item["order_id"]))
            fill_cell(self.table, row, 2, item["operator"])
            fill_cell(self.table, row, 3, item["product"])
            fill_cell(self.table, row, 4, f"{item['price']:.0f} ₽", align_right=True)
            fill_cell(self.table, row, 5,
                      item["scheduled_for"].strftime("%d.%m %H:%M") if item["scheduled_for"] else "без срока")
            fill_cell(self.table, row, 6, item["order_status"])

        workload = self.ctx.queue.workload()
        operators = self.ctx.queue.operators()
        self.operators_table.setRowCount(len(operators))
        current = self.operator_combo.currentData()
        self.operator_combo.clear()
        self.operator_combo.addItem("Автоподбор", 0)
        for row, operator in enumerate(operators):
            self.operator_combo.addItem(operator.name, operator.id)
            set_row_id(self.operators_table, row, operator.id)
            fill_cell(self.operators_table, row, 1, operator.name)
            fill_cell(self.operators_table, row, 2, ", ".join(operator.games or []))
            fill_cell(self.operators_table, row, 3, f"{operator.cost_per_order:.0f} ₽", align_right=True)
            fill_cell(self.operators_table, row, 4, str(workload.get(operator.id, 0)), align_right=True)
            add_pill_cell(self.operators_table, row, 5, "да" if operator.active else "нет",
                          "#2fbf71" if operator.active else "#55585f")
        if current:
            index = self.operator_combo.findData(current)
            if index >= 0:
                self.operator_combo.setCurrentIndex(index)

    def _assign(self) -> None:
        order_id, ok = QInputDialog.getInt(self, "Назначение", "ID заказа (из таблицы «Заказы»):")
        if not ok:
            return
        operator_id = self.operator_combo.currentData() or None
        when, ok2 = QInputDialog.getItem(
            self, "Срок", "Когда выполнить:",
            ["Сейчас", "Сегодня 18:00", "Завтра 12:00", "Без срока"], 0, False)
        if not ok2:
            return
        now = self.ctx.clock.now()
        scheduled = {
            "Сейчас": now,
            "Сегодня 18:00": now.replace(hour=18, minute=0, second=0, microsecond=0),
            "Завтра 12:00": (now + timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0),
            "Без срока": None,
        }[when]
        try:
            self.ctx.queue.assign(order_id, operator_id, scheduled_for=scheduled)
        except Exception as exc:  # noqa: BLE001 - показываем пользователю
            QMessageBox.warning(self, "Назначение", str(exc))
            return
        self.refresh()

    def _reschedule(self) -> None:
        assignment_id = selected_row_id(self.table)
        if assignment_id is None:
            return
        item = self._selected_item()
        base = item["scheduled_for"] or self.ctx.clock.now()
        self.ctx.queue.reschedule(assignment_id, base + timedelta(days=1))
        self.refresh()

    def _done(self) -> None:
        assignment_id = selected_row_id(self.table)
        if assignment_id is not None:
            self.ctx.queue.complete_assignment(assignment_id)
            self.refresh()

    def _cancel(self) -> None:
        assignment_id = selected_row_id(self.table)
        if assignment_id is not None:
            self.ctx.queue.cancel_assignment(assignment_id)
            self.refresh()

    def _selected_item(self):
        assignment_id = selected_row_id(self.table)
        bucket = self.bucket_combo.currentData() or "now"
        for item in self.ctx.queue.queue_view().get(bucket, []):
            if item["assignment_id"] == assignment_id:
                return item
        return {"scheduled_for": None}

    def _add_operator(self) -> None:
        name, ok = QInputDialog.getText(self, "Оператор", "Имя оператора:")
        if not ok or not name.strip():
            return
        games, _ = QInputDialog.getText(self, "Оператор", "Игры через запятую (специализация):")
        cost, ok3 = QInputDialog.getInt(self, "Оператор", "Стоимость работы за заказ, ₽:", 0, 0, 10**6)
        if not ok3:
            cost = 0
        self.ctx.queue.add_operator(
            name.strip(),
            games=[g.strip() for g in games.split(",") if g.strip()],
            cost_per_order=float(cost),
        )
        self.refresh()
