"""Экран «Воркфлоу»: активные прогоны, подтверждения, ручные шаги (ТЗ §36–37)."""
from __future__ import annotations

from PySide6.QtWidgets import QMessageBox, QPushButton, QVBoxLayout

from app.domain.enums import RunStatus
from app.ui.pages.base import BasePage, page_header, toolbar_row
from app.ui.widgets import add_pill_cell, fill_cell, make_table, selected_row_id, set_row_id

RUN_STATUS_META = {
    "pending": ("В очереди", "#9aa0a6"),
    "running": ("Выполняется", "#4aa8e0"),
    "waiting_manual": ("Ручной шаг", "#e0b341"),
    "waiting_approval": ("Ждёт подтверждения", "#e0b341"),
    "completed": ("Завершён", "#2fbf71"),
    "failed": ("Ошибка", "#e05555"),
    "cancelled": ("Отменён", "#55585f"),
}


class WorkflowsPage(BasePage):
    title = "Воркфлоу"

    def __init__(self, ctx, parent=None) -> None:
        super().__init__(ctx, parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(8)
        root.addWidget(page_header(
            "Воркфлоу",
            "Сердце автоматизации: заказ → резерв склада → шаги → выдача. "
            "Внешние действия требуют подтверждения.",
        ))

        self.approve_button = QPushButton("✅ Подтвердить шаг")
        self.approve_button.setProperty("accent", True)
        self.approve_button.clicked.connect(self._approve)
        self.manual_button = QPushButton("🙋 Ручной шаг: DONE")
        self.manual_button.clicked.connect(self._complete_manual)
        self.cancel_button = QPushButton("Отменить прогон")
        self.cancel_button.setProperty("danger", True)
        self.cancel_button.clicked.connect(self._cancel)
        self.refresh_button = QPushButton("Обновить")
        self.refresh_button.clicked.connect(self.refresh)
        root.addWidget(toolbar_row(self.approve_button, self.manual_button,
                                   self.cancel_button, self.refresh_button))

        self.table = make_table(["ID", "Заказ", "Продукт", "Воркфлоу", "Статус", "Шаг", "Ошибка"])
        for col, width in ((0, 40), (1, 70), (2, 220), (3, 170), (4, 170), (5, 70), (6, 300)):
            self.table.setColumnWidth(col, width)
        root.addWidget(self.table, 1)

        root.addWidget(page_header("Определения", ""))
        self.definitions_table = make_table(["Код", "Название", "Шаги"])
        for col, width in ((0, 170), (1, 320), (2, 520)):
            self.definitions_table.setColumnWidth(col, width)
        self.definitions_table.setMaximumHeight(170)
        root.addWidget(self.definitions_table)

    def refresh(self) -> None:
        from sqlalchemy import select
        from app.database import models

        runs = self.ctx.engine.open_runs()
        with self.ctx.db.session() as session:
            done = list(session.scalars(
                select(models.WorkflowRun).where(models.WorkflowRun.status.in_(
                    ["completed", "failed", "cancelled"])).order_by(models.WorkflowRun.id.desc()).limit(10)
            ))
            products = {p.id: p.name for p in session.scalars(select(models.Product))}
            definitions = {d.code: d for d in session.scalars(select(models.WorkflowDef))}
        all_runs = sorted(runs + done, key=lambda run: run.id, reverse=True)
        self.table.setRowCount(len(all_runs))
        for row, run in enumerate(all_runs):
            definition = definitions.get(run.workflow_code)
            steps_total = len(definition.steps) if definition else 0
            label, color = RUN_STATUS_META.get(run.status, (run.status, "#9aa0a6"))
            set_row_id(self.table, row, run.id)
            fill_cell(self.table, row, 1, str(run.order_id or "—"))
            fill_cell(self.table, row, 2, products.get(run.product_id, str(run.product_id or "—")))
            fill_cell(self.table, row, 3, definition.name if definition else run.workflow_code)
            add_pill_cell(self.table, row, 4, label, color)
            fill_cell(self.table, row, 5, f"{min(run.step_index + 1, steps_total)}/{steps_total}")
            fill_cell(self.table, row, 6, run.error or "")

        defs = self.ctx.engine.definitions()
        self.definitions_table.setRowCount(len(defs))
        for row, definition in enumerate(defs):
            fill_cell(self.definitions_table, row, 0, definition.code)
            fill_cell(self.definitions_table, row, 1, definition.name)
            fill_cell(self.definitions_table, row, 2,
                      " → ".join(step.get("name", "?") for step in definition.steps))

    def _selected_run(self):
        run_id = selected_row_id(self.table)
        if run_id is None:
            return None
        return self.ctx.engine.get_run(run_id)

    def _approve(self) -> None:
        run = self._selected_run()
        if run is None or run.status != RunStatus.WAITING_APPROVAL.value:
            QMessageBox.information(self, "Подтверждение", "Выберите прогон, ждущий подтверждения.")
            return
        self.ctx.engine.approve(run.id)
        self.refresh()

    def _complete_manual(self) -> None:
        from PySide6.QtWidgets import QInputDialog
        run = self._selected_run()
        if run is None or run.status != RunStatus.WAITING_MANUAL.value:
            QMessageBox.information(self, "Ручной шаг", "Выберите прогон на ручном шаге.")
            return
        text, ok = QInputDialog.getMultiLineText(
            self, "Ручной шаг", "Результат/ввод (например, логи клиента для диагностики):")
        if not ok:
            return
        self.ctx.engine.complete_manual(run.id, {"notes": text.strip()})
        self.refresh()

    def _cancel(self) -> None:
        run = self._selected_run()
        if run is None:
            return
        if QMessageBox.question(self, "Отмена", "Отменить прогон? Резерв склада будет освобождён.") \
                != QMessageBox.StandardButton.Yes:
            return
        self.ctx.engine.cancel(run.id, reason="отменено оператором")
        self.refresh()
