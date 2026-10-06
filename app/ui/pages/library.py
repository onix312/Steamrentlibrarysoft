"""Экран «Библиотека»: единая семейная библиотека с мгновенными фильтрами."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
)
from sqlalchemy import select

from app.database import models
from app.domain.enums import EligibilityStatus
from app.services.access_service import LicenseAllocator
from app.ui.dialogs import GameDetailsDialog
from app.ui.pages.base import BasePage, page_header, toolbar_row
from app.ui.theme import ELIGIBILITY_META, GRADE_COLORS
from app.ui.widgets import SearchBox, add_pill_cell, fill_cell, make_table, set_row_id

FILTERS = [
    ("all", "Все"),
    ("sellable", "Можно продавать"),
    ("unsellable", "Нельзя продавать"),
    ("needs_check", "Требует проверки"),
    ("free", "Бесплатные"),
    ("paid", "Платные"),
    ("popular", "Популярные (S и A)"),
    ("has_free_slot", "Есть свободный слот"),
    ("all_busy", "Все копии заняты"),
    ("duplicates", "Дубликаты"),
]


class LibraryPage(BasePage):
    title = "Библиотека"

    def __init__(self, ctx, parent=None) -> None:
        super().__init__(ctx, parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(8)
        root.addWidget(page_header(
            "Библиотека",
            "Объединённая семейная библиотека: одинаковые игры собраны в одну запись, копии посчитаны по аккаунтам.",
        ))

        self.search = SearchBox("Мгновенный поиск по названию…")
        self.filter = QComboBox()
        for key, label in FILTERS:
            self.filter.addItem(label, key)
        self.account_filter = QComboBox()
        self.account_filter.addItem("По аккаунту: все", 0)
        self.count_label = QLabel("")
        self.count_label.setProperty("muted", True)
        root.addWidget(toolbar_row(self.search, self.filter, self.account_filter, self.count_label))

        self.table = make_table([
            "ID", "Игра", "Владельцы", "Копии", "Цена", "Рейтинг", "Грейд",
            "Demand", "Steam Families", "Жанры", "Релиз",
        ])
        self.table.setColumnWidth(0, 50)
        self.table.setColumnWidth(1, 260)
        self.table.setColumnWidth(2, 220)
        self.table.setColumnWidth(3, 90)
        self.table.setColumnWidth(4, 80)
        self.table.setColumnWidth(5, 80)
        self.table.setColumnWidth(6, 60)
        self.table.setColumnWidth(7, 70)
        self.table.setColumnWidth(8, 170)
        self.table.setColumnWidth(9, 180)
        self.table.doubleClicked.connect(self._open_details)
        root.addWidget(self.table, 1)

        buttons = QHBoxLayout()
        self.details_button = QPushButton("Карточка игры")
        self.details_button.clicked.connect(self._open_details)
        buttons.addWidget(self.details_button)
        buttons.addStretch(1)
        root.addLayout(buttons)

        self.search.textChanged.connect(self.refresh)
        self.filter.currentIndexChanged.connect(self.refresh)
        self.account_filter.currentIndexChanged.connect(self.refresh)

    def _account_options(self) -> None:
        current = self.account_filter.currentData()
        self.account_filter.blockSignals(True)
        self.account_filter.clear()
        self.account_filter.addItem("По аккаунту: все", 0)
        for account in self.ctx.library.accounts():
            self.account_filter.addItem(account.display_name, account.id)
        index = self.account_filter.findData(current or 0)
        self.account_filter.setCurrentIndex(max(0, index))
        self.account_filter.blockSignals(False)

    def refresh(self) -> None:
        self._account_options()
        query = self.search.text().strip().lower()
        filter_key = self.filter.currentData() or "all"
        account_id = self.account_filter.currentData() or 0

        with self.ctx.db.session() as session:
            games = list(session.scalars(select(models.Game).order_by(models.Game.name)))
            allocator = LicenseAllocator(session, self.ctx.clock)
            rows = []
            for game in games:
                licenses = [l for l in allocator.licenses(game.id) if not l.hidden_from_family]
                availability = allocator.availability(game.id)
                owners = {}
                for license_ in licenses:
                    account = session.get(models.SteamAccount, license_.account_id)
                    if account is not None:
                        owners[license_.account_id] = account.display_name
                rows.append((game, availability, owners))

        filtered = []
        for game, availability, owners in rows:
            if query and query not in (game.name or "").lower():
                continue
            if account_id and account_id not in owners:
                continue
            sellable = (not game.is_free) and game.eligibility_status == EligibilityStatus.AVAILABLE.value
            if filter_key == "sellable" and not sellable:
                continue
            if filter_key == "unsellable" and sellable:
                continue
            if filter_key == "needs_check" and game.eligibility_status != EligibilityStatus.NEEDS_CHECK.value:
                continue
            if filter_key == "free" and not game.is_free:
                continue
            if filter_key == "paid" and game.is_free:
                continue
            if filter_key == "popular" and game.demand_grade not in ("S", "A"):
                continue
            if filter_key == "has_free_slot" and availability.free == 0:
                continue
            if filter_key == "all_busy" and not (availability.total > 0 and availability.free == 0):
                continue
            if filter_key == "duplicates" and availability.total < 2:
                continue
            filtered.append((game, availability, owners))

        filtered.sort(key=lambda row: (-row[0].demand_score, row[0].name.lower()))
        self.count_label.setText(f"{len(filtered)} из {len(rows)} игр")

        self.table.setRowCount(len(filtered))
        for row, (game, availability, owners) in enumerate(filtered):
            set_row_id(self.table, row, game.id)
            fill_cell(self.table, row, 1, game.name)
            fill_cell(self.table, row, 2, ", ".join(sorted(set(owners.values()))))
            fill_cell(self.table, row, 3,
                      f"{availability.free}/{availability.total} своб.",
                      "#2fbf71" if availability.free > 0 else "#ef5b62", align_right=True)
            price_text = "F2P" if game.is_free else f"{game.price:.0f} ₽"
            fill_cell(self.table, row, 4, price_text, align_right=True)
            rating = f"{game.review_score}%" if game.review_score is not None else "—"
            fill_cell(self.table, row, 5, rating, align_right=True)
            grade_color = GRADE_COLORS.get(game.demand_grade, "#7d8590")
            add_pill_cell(self.table, row, 6, game.demand_grade, grade_color)
            fill_cell(self.table, row, 7, f"{game.demand_score:.0f}", align_right=True)
            elig_text, elig_color = ELIGIBILITY_META.get(game.eligibility_status, (game.eligibility_status, "#8f959e"))
            add_pill_cell(self.table, row, 8, elig_text, elig_color)
            fill_cell(self.table, row, 9, ", ".join((game.genres or [])[:3]))
            fill_cell(self.table, row, 10, game.release_date or "—")

    def _open_details(self, *_args) -> None:
        from app.ui.widgets import selected_row_id

        game_id = selected_row_id(self.table)
        if game_id is None:
            return
        with self.ctx.db.session() as session:
            game = session.get(models.Game, game_id)
            if game is None:
                return
            allocator = LicenseAllocator(session, self.ctx.clock)
            availability = allocator.availability(game_id)
            owner_names = []
            for license_ in allocator.licenses(game_id):
                account = session.get(models.SteamAccount, license_.account_id)
                if account:
                    owner_names.append(account.display_name)
            session.expunge(game)

        dialog = GameDetailsDialog(game, owner_names, availability, self)
        if dialog.exec():
            override_value = dialog.override.currentData()
            status = EligibilityStatus(override_value) if override_value else None
            self.ctx.eligibility.set_override(game_id, status, reason="Из карточки игры")
            with self.ctx.db.session() as session:
                game_row = session.get(models.Game, game_id)
                if game_row is not None:
                    game_row.notes = dialog.notes.toPlainText() or None
            self.refresh()
