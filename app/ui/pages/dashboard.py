"""Главный экран: сводка по всем зонам + рекомендации (отдельно от фактов)."""
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
from app.ui.widgets import Card, StatCard, make_table, fill_cell


def _money(value: float) -> str:
    return f"{value:,.0f} ₽".replace(",", " ")


class DashboardPage(BasePage):
    title = "Dashboard"

    def __init__(self, ctx, parent=None) -> None:
        super().__init__(ctx, parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(12)
        root.addWidget(page_header("Панель управления", "Сводка по продажам, библиотеке и доступам"))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        body = QWidget()
        self.body_layout = QVBoxLayout(body)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(14)

        grid_holder = QWidget()
        self.grid = QGridLayout(grid_holder)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(12)
        self.grid.setVerticalSpacing(12)

        self.cards: dict[str, StatCard] = {}
        specs = [
            ("new_orders", "Новые заказы сегодня"),
            ("active_clients", "Активные клиенты"),
            ("revenue_today", "Выручка сегодня"),
            ("expiring", "Заканчиваются ≤ 24 ч"),
            ("games_total", "Игр в библиотеке"),
            ("games_sellable", "Коммерчески полезных"),
            ("copies_free", "Свободных копий"),
            ("copies_busy", "Занятых копий"),
            ("accounts", "Steam-аккаунтов"),
            ("account_errors", "Ошибки синхронизации"),
            ("listings", "Активных объявлений"),
            ("revenue_30d", "Выручка за 30 дней"),
        ]
        for index, (key, label) in enumerate(specs):
            card = StatCard(label)
            self.cards[key] = card
            self.grid.addWidget(card, index // 4, index % 4)
        self.body_layout.addWidget(grid_holder)

        # Финансы
        finance = Card()
        fin_layout = QHBoxLayout(finance)
        fin_layout.setContentsMargins(16, 12, 16, 12)
        self.finance_label = QLabel()
        fin_layout.addWidget(self.finance_label)
        self.body_layout.addWidget(finance)

        # ТОП игр
        top_card = Card()
        top_layout = QVBoxLayout(top_card)
        top_layout.setContentsMargins(16, 12, 16, 12)
        title = QLabel("ТОП игр по выручке")
        title.setProperty("h2", True)
        top_layout.addWidget(title)
        self.top_table = make_table(["Игра", "Продажи", "Выручка"])
        self.top_table.setMaximumHeight(200)
        top_layout.addWidget(self.top_table)
        self.body_layout.addWidget(top_card)

        # Рекомендации (отделены от фактов)
        rec_card = Card()
        rec_layout = QVBoxLayout(rec_card)
        rec_layout.setContentsMargins(16, 12, 16, 12)
        rec_title = QLabel("Рекомендации (эвристики, не факты)")
        rec_title.setProperty("h2", True)
        rec_layout.addWidget(rec_title)
        self.recs_label = QLabel()
        self.recs_label.setWordWrap(True)
        self.recs_label.setTextFormat(Qt.TextFormat.RichText)
        rec_layout.addWidget(self.recs_label)
        self.body_layout.addWidget(rec_card)

        self.body_layout.addStretch(1)
        scroll.setWidget(body)
        root.addWidget(scroll)

    def refresh(self) -> None:
        stats = self.ctx.analytics.dashboard()
        today, library, steam, funpay, finance = (
            stats["today"], stats["library"], stats["steam"], stats["funpay"], stats["finance"],
        )
        self.cards["new_orders"].set_value(str(today["new_orders"]))
        self.cards["active_clients"].set_value(str(today["active_clients"]))
        self.cards["revenue_today"].set_value(_money(today["revenue"]))
        self.cards["expiring"].set_value(str(today["expiring_leases"]))
        self.cards["games_total"].set_value(str(library["total_games"]))
        self.cards["games_sellable"].set_value(str(library["sellable_games"]))
        self.cards["copies_free"].set_value(str(library["licenses_free"]))
        self.cards["copies_busy"].set_value(str(library["licenses_busy"]))
        self.cards["accounts"].set_value(str(steam["accounts"]))
        self.cards["account_errors"].set_value(
            str(steam["errors"]),
            "все аккаунты в порядке" if steam["errors"] == 0 else "см. экран Steam",
        )
        self.cards["listings"].set_value(
            str(funpay["active_listings"]),
            f"готово к публикации: {funpay['ready_listings']}",
        )
        self.cards["revenue_30d"].set_value(_money(finance["days_30"]))
        self.finance_label.setText(
            f"<b>Финансы.</b> Сегодня: <b>{_money(finance['today'])}</b> · "
            f"7 дней: <b>{_money(finance['days_7'])}</b> · "
            f"30 дней: <b>{_money(finance['days_30'])}</b> · "
            f"всё время: <b>{_money(finance['all_time'])}</b>"
        )
        self.finance_label.setTextFormat(Qt.TextFormat.RichText)

        top = stats["top_games"]
        self.top_table.setRowCount(len(top))
        for row, (name, sales, revenue) in enumerate(top):
            fill_cell(self.top_table, row, 0, name)
            fill_cell(self.top_table, row, 1, str(sales), align_right=True)
            fill_cell(self.top_table, row, 2, _money(revenue), align_right=True)
        if top:
            self.top_table.setColumnWidth(0, 380)

        recs = self.ctx.analytics.recommendations()[:8]
        if recs:
            icons = {"buy_copy": "🛒", "drop_listing": "🗑", "raise_price": "💰", "create_listing": "✨"}
            lines = [
                f"{icons.get(rec.kind, '•')} {rec.text}" for rec in recs
            ]
            self.recs_label.setText("<br>".join(lines))
        else:
            self.recs_label.setText(f"<span style='color:{COLORS['text_muted']}'>Пока нет рекомендаций.</span>")
