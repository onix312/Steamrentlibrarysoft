"""Экран «Аналитика»: графики, ключевые метрики, рекомендации отдельно от фактов."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.ui.pages.base import BasePage, page_header
from app.ui.theme import COLORS
from app.ui.widgets import BarsWidget, Card, StatCard, make_table, fill_cell


class AnalyticsPage(BasePage):
    title = "Аналитика"

    def __init__(self, ctx, parent=None) -> None:
        super().__init__(ctx, parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(10)
        root.addWidget(page_header("Аналитика", "Выручка, загрузка лицензий, спрос и рекомендации."))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        # Метрики
        metrics = QWidget()
        metrics_layout = QHBoxLayout(metrics)
        metrics_layout.setContentsMargins(0, 0, 0, 0)
        self.avg_check_card = StatCard("Средний чек")
        self.repeat_card = StatCard("Повторные клиенты")
        self.utilization_card = StatCard("Загрузка лицензий")
        for card in (self.avg_check_card, self.repeat_card, self.utilization_card):
            metrics_layout.addWidget(card)
        layout.addWidget(metrics)

        # Revenue per day
        revenue_card = Card()
        revenue_layout = QVBoxLayout(revenue_card)
        revenue_layout.setContentsMargins(16, 12, 16, 12)
        title = QLabel("Выручка по дням (30 дней)")
        title.setProperty("h2", True)
        revenue_layout.addWidget(title)
        self.bars = BarsWidget()
        revenue_layout.addWidget(self.bars)
        layout.addWidget(revenue_card)

        # Таблицы
        tables = QWidget()
        tables_layout = QGridLayout(tables)
        tables_layout.setContentsMargins(0, 0, 0, 0)

        sales_card = Card()
        sales_layout = QVBoxLayout(sales_card)
        sales_layout.setContentsMargins(16, 12, 16, 12)
        sales_title = QLabel("Продажи и прибыльность по играм")
        sales_title.setProperty("h2", True)
        sales_layout.addWidget(sales_title)
        self.sales_table = make_table(["Игра", "Продажи", "Выручка"])
        sales_layout.addWidget(self.sales_table)
        tables_layout.addWidget(sales_card, 0, 0)

        util_card = Card()
        util_layout = QVBoxLayout(util_card)
        util_layout.setContentsMargins(16, 12, 16, 12)
        util_title = QLabel("Загрузка копий (продаваемые игры)")
        util_title.setProperty("h2", True)
        util_layout.addWidget(util_title)
        self.util_table = make_table(["Игра", "Копий", "Занято"])
        util_layout.addWidget(self.util_table)
        tables_layout.addWidget(util_card, 0, 1)
        layout.addWidget(tables)

        # Demand top
        demand_card = Card()
        demand_layout = QVBoxLayout(demand_card)
        demand_layout.setContentsMargins(16, 12, 16, 12)
        demand_title = QLabel("Demand Score — топ игр")
        demand_title.setProperty("h2", True)
        demand_layout.addWidget(demand_title)
        self.demand_table = make_table(["Игра", "Грейд", "Demand", "Факторы"])
        self.demand_table.setMaximumHeight(260)
        demand_layout.addWidget(self.demand_table)
        layout.addWidget(demand_card)

        # Рекомендации (отдельно от фактов)
        rec_card = Card()
        rec_layout = QVBoxLayout(rec_card)
        rec_layout.setContentsMargins(16, 12, 16, 12)
        rec_title = QLabel("Рекомендации — эвристики, отделены от фактов")
        rec_title.setProperty("h2", True)
        rec_layout.addWidget(rec_title)
        self.recs_label = QLabel()
        self.recs_label.setWordWrap(True)
        self.recs_label.setTextFormat(Qt.TextFormat.RichText)
        rec_layout.addWidget(self.recs_label)
        layout.addWidget(rec_card)

        layout.addStretch(1)
        scroll.setWidget(body)
        root.addWidget(scroll)

    def refresh(self) -> None:
        avg_check = self.ctx.analytics.average_check()
        repeat = self.ctx.analytics.repeat_clients()
        self.avg_check_card.set_value(f"{avg_check:.0f} ₽")
        self.repeat_card.set_value(str(repeat))

        utilization = self.ctx.analytics.license_utilization()
        total_copies = sum(row[1] for row in utilization)
        busy = sum(row[2] for row in utilization)
        pct = int(busy / total_copies * 100) if total_copies else 0
        self.utilization_card.set_value(f"{pct}%", f"{busy} из {total_copies} копий занято")

        self.bars.set_data(self.ctx.analytics.revenue_per_day(30))

        sales = self.ctx.analytics.sales_per_game()
        self.sales_table.setRowCount(len(sales))
        for row, (name, count, revenue) in enumerate(sales):
            fill_cell(self.sales_table, row, 0, name)
            fill_cell(self.sales_table, row, 1, str(count), align_right=True)
            fill_cell(self.sales_table, row, 2, f"{revenue:.0f} ₽", align_right=True)

        self.util_table.setRowCount(len(utilization))
        for row, (name, total, used) in enumerate(utilization):
            fill_cell(self.util_table, row, 0, name)
            fill_cell(self.util_table, row, 1, str(total), align_right=True)
            fill_cell(self.util_table, row, 2, str(used),
                      "#ef5b62" if used == total and total > 0 else "#2fbf71", align_right=True)

        with self.ctx.db.session() as session:
            from sqlalchemy import select
            from app.database import models

            games = session.scalars(select(models.Game).order_by(models.Game.demand_score.desc()).limit(10)).all()
            self.demand_table.setRowCount(len(games))
            for row, game in enumerate(games):
                fill_cell(self.demand_table, row, 0, game.name)
                fill_cell(self.demand_table, row, 1, game.demand_grade)
                fill_cell(self.demand_table, row, 2, f"{game.demand_score:.0f}", align_right=True)
                factors = ", ".join(f"{k}:{v:.0f}" for k, v in (game.demand_breakdown or {}).items())
                fill_cell(self.demand_table, row, 3, factors)

        recs = self.ctx.analytics.recommendations()
        if recs:
            self.recs_label.setText("<br>".join(f"• {rec.text}" for rec in recs[:12]))
        else:
            self.recs_label.setText(f"<span style='color:{COLORS['text_muted']}'>Рекомендаций пока нет.</span>")
