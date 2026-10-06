"""Экран «Рынок»: снапшоты конкурентов, Opportunity Score, цены (ТЗ §5–6, §45)."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from app.domain.enums import PricingStrategy
from app.ui.pages.base import BasePage, page_header
from app.ui.widgets import add_pill_cell, fill_cell, make_table, selected_row_id, set_row_id

STRATEGY_LABELS = {
    "aggressive": "Агрессивная (ниже всех)",
    "balanced": "Сбалансированная (медиана −5%)",
    "market": "Рыночная (медиана)",
    "premium": "Премиум (медиана +10%)",
}


class MarketPage(BasePage):
    title = "Рынок"

    def __init__(self, ctx, parent=None) -> None:
        super().__init__(ctx, parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(8)
        root.addWidget(page_header(
            "Рыночный радар",
            "Снапшоты публичных цен конкурентов. Если маркетплейс блокирует сбор — "
            "ввод ручной (см. Матрицу возможностей).",
        ))

        form_box = QGroupBox("Новый снапшот (публичные данные)")
        form = QFormLayout(form_box)
        from PySide6.QtWidgets import QLineEdit
        self.game_input = QLineEdit()
        self.game_input.setPlaceholderText("Название игры на FunPay")
        self.competitors_input = QSpinBox(); self.competitors_input.setRange(0, 10000)
        self.lowest_input = QDoubleSpinBox(); self.lowest_input.setRange(0, 10**6); self.lowest_input.setDecimals(0)
        self.median_input = QDoubleSpinBox(); self.median_input.setRange(0, 10**6); self.median_input.setDecimals(0)
        self.highest_input = QDoubleSpinBox(); self.highest_input.setRange(0, 10**6); self.highest_input.setDecimals(0)
        self.hours_input = QDoubleSpinBox(); self.hours_input.setRange(0.1, 1000); self.hours_input.setValue(1.0)
        self.demand_input = QSpinBox(); self.demand_input.setRange(0, 100); self.demand_input.setValue(50)
        form.addRow("Игра", self.game_input)
        form.addRow("Конкурентов", self.competitors_input)
        form.addRow("Мин. цена, ₽", self.lowest_input)
        form.addRow("Медиана, ₽", self.median_input)
        form.addRow("Макс. цена, ₽", self.highest_input)
        form.addRow("Часы на исполнение", self.hours_input)
        form.addRow("Сигнал спроса, 0–100", self.demand_input)

        right = QVBoxLayout()
        self.save_button = QPushButton("💾 Сохранить снапшот")
        self.save_button.setProperty("accent", True)
        self.save_button.clicked.connect(self._save_snapshot)
        self.score_label = QLabel("Opportunity Score: —")
        self.score_label.setWordWrap(True)
        self.score_label.setProperty("h2", True)
        self.score_button = QPushButton("Посчитать возможность")
        self.score_button.clicked.connect(self._score)
        self.strategy_combo = QComboBox()
        for value, label in STRATEGY_LABELS.items():
            self.strategy_combo.addItem(label, value)
        self.product_combo = QComboBox()
        self.price_button = QPushButton("Предложить цену")
        self.price_button.clicked.connect(self._suggest_price)
        right.addWidget(self.save_button)
        right.addWidget(self.score_button)
        right.addWidget(self.score_label)
        right.addWidget(QLabel("Стратегия цены"))
        right.addWidget(self.strategy_combo)
        right.addWidget(QLabel("Продукт"))
        right.addWidget(self.product_combo)
        right.addWidget(self.price_button)
        right.addStretch(1)

        form_row = QHBoxLayout()
        form_row.addWidget(form_box, 2)
        form_row.addLayout(right, 1)
        root.addLayout(form_row)

        self.table = make_table(["ID", "Игра", "Конкур.", "Мин.", "Медиана", "Δ медиана", "Решение", "Источник"])
        for col, width in ((0, 40), (1, 220), (2, 80), (3, 80), (4, 90), (5, 100), (6, 100), (7, 100)):
            self.table.setColumnWidth(col, width)
        root.addWidget(self.table, 1)

    def refresh(self) -> None:
        snapshots = self.ctx.market.snapshots(limit=50)
        self.table.setRowCount(len(snapshots))
        reports = {}
        for row, snapshot in enumerate(snapshots):
            report = reports.setdefault(
                snapshot.game_name,
                self.ctx.market.opportunity_v2(snapshot.game_name),
            )
            trend = report.get("trend") or {}
            set_row_id(self.table, row, snapshot.id)
            fill_cell(self.table, row, 1, snapshot.game_name)
            fill_cell(self.table, row, 2, str(snapshot.competitors), align_right=True)
            fill_cell(self.table, row, 3, f"{snapshot.lowest_price:.0f}", align_right=True)
            fill_cell(self.table, row, 4, f"{snapshot.median_price:.0f}", align_right=True)
            fill_cell(self.table, row, 5, f"{trend.get('median_delta', 0):+.0f}", align_right=True)
            decision = report.get("recommendation", "HOLD")
            color = {"CREATE": "#2fbf71", "SCALE": "#5b6bef", "KILL": "#ef5b62"}.get(decision, "#f0b232")
            add_pill_cell(self.table, row, 6, decision, color)
            fill_cell(self.table, row, 7, snapshot.source)

        products = self.ctx.products.all(active_only=True)
        current = self.product_combo.currentText()
        self.product_combo.clear()
        for product in products:
            self.product_combo.addItem(product.code, product.id)
        if current:
            index = self.product_combo.findText(current)
            if index >= 0:
                self.product_combo.setCurrentIndex(index)

    def _save_snapshot(self) -> None:
        game = self.game_input.text().strip()
        if not game:
            QMessageBox.warning(self, "Рынок", "Укажите название игры.")
            return
        self.ctx.market.record_snapshot(
            game, competitors=self.competitors_input.value(),
            lowest=self.lowest_input.value(), median=self.median_input.value(),
            highest=self.highest_input.value(), source="manual",
        )
        self.refresh()

    def _score(self) -> None:
        game = self.game_input.text().strip()
        if game and self.ctx.market.latest(game) is not None:
            result = self.ctx.market.opportunity_v2(
                game,
                manual_hours=self.hours_input.value(),
                demand_signal=float(self.demand_input.value()),
            )
        else:
            result = self.ctx.market.opportunity_score(
                median_price=self.median_input.value(),
                competitors=self.competitors_input.value(),
                watch_or_manual_hours=self.hours_input.value(),
                demand_signal=float(self.demand_input.value()),
            )
        self.score_label.setText(
            f"Opportunity Score: {result['score']:.0f}/100 → {result['recommendation']}\n"
            f"Ценность/час: {result['value_per_hour']:.0f} ₽\n"
            f"Факторы: цена {result['factors']['price']:.0f}, конкуренция "
            f"{result['factors']['competition']:.0f}, время {result['factors']['time']:.0f}, "
            f"спрос {result['factors']['demand']:.0f}."
        )

    def _suggest_price(self) -> None:
        product_id = self.product_combo.currentData()
        if product_id is None:
            QMessageBox.warning(self, "Цена", "Сначала создайте продукт.")
            return
        product = self.ctx.products.get(product_id)
        strategy = PricingStrategy(self.strategy_combo.currentData())
        price = self.ctx.market.suggest_price(product, strategy)
        QMessageBox.information(
            self, "Предложение цены",
            f"{product.code}: {price:.0f} ₽\n"
            f"Стратегия: {self.strategy_combo.currentText()}\n"
            f"Минимальная цена продукта: {product.minimum_price:.0f} ₽ (ниже не опускаемся).",
        )
