"""Экран «Объявления»: подготовка и редактирование текстов, статусы."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)
from sqlalchemy import select

from app.database import models
from app.domain.enums import EligibilityStatus, ListingStatus
from app.ui.dialogs import ListingTextDialog
from app.ui.pages.base import BasePage, page_header, toolbar_row
from app.ui.widgets import add_pill_cell, fill_cell, make_table, set_row_id, selected_row_id

STATUS_META = {
    "draft": ("Черновик", "#8f959e"),
    "ready": ("Готово к публикации", "#4aa8e0"),
    "active": ("Активно", "#2fbf71"),
    "paused": ("Пауза", "#f0b232"),
    "archived": ("Архив", "#55585f"),
}


class ListingsPage(BasePage):
    title = "Объявления"

    def __init__(self, ctx, parent=None) -> None:
        super().__init__(ctx, parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(8)
        root.addWidget(page_header(
            "Объявления",
            "Приложение готовит тексты; публикация на FunPay — вручную (безопасный режим).",
        ))

        self.from_game_button = QPushButton("Объявление по игре")
        self.from_game_button.setProperty("accent", True)
        self.from_game_button.clicked.connect(self._from_game)
        self.from_bundle_button = QPushButton("Объявление по пакету")
        self.from_bundle_button.clicked.connect(self._from_bundle)
        self.edit_button = QPushButton("Тексты и цена")
        self.edit_button.clicked.connect(self._edit_texts)
        root.addWidget(toolbar_row(self.from_game_button, self.from_bundle_button, self.edit_button))

        self.table = make_table([
            "ID", "Название", "Цена", "Срок", "Статус", "Продажи", "Выручка", "Обновлено",
        ])
        self.table.setColumnWidth(0, 40)
        self.table.setColumnWidth(1, 340)
        self.table.setColumnWidth(4, 170)
        self.table.doubleClicked.connect(self._edit_texts)
        root.addWidget(self.table, 1)

        actions = QHBoxLayout()
        self.activate_button = QPushButton("Отметить опубликованным")
        self.activate_button.clicked.connect(lambda: self._set_status(ListingStatus.ACTIVE))
        self.pause_button = QPushButton("Пауза")
        self.pause_button.clicked.connect(lambda: self._set_status(ListingStatus.PAUSED))
        self.archive_button = QPushButton("В архив")
        self.archive_button.setProperty("danger", True)
        self.archive_button.clicked.connect(lambda: self._set_status(ListingStatus.ARCHIVED))
        actions.addWidget(self.activate_button)
        actions.addWidget(self.pause_button)
        actions.addWidget(self.archive_button)
        actions.addStretch(1)
        root.addLayout(actions)

    def refresh(self) -> None:
        listings = self.ctx.listings.all()
        self.table.setRowCount(len(listings))
        for row, listing in enumerate(listings):
            set_row_id(self.table, row, listing.id)
            fill_cell(self.table, row, 1, listing.title)
            fill_cell(self.table, row, 2, f"{listing.price:.0f} ₽", align_right=True)
            fill_cell(self.table, row, 3, f"{listing.access_days} дн.", align_right=True)
            text, color = STATUS_META.get(listing.status, (listing.status, "#8f959e"))
            add_pill_cell(self.table, row, 4, text, color)
            fill_cell(self.table, row, 5, str(listing.sales_count), align_right=True)
            fill_cell(self.table, row, 6, f"{listing.revenue:.0f} ₽", align_right=True)
            fill_cell(self.table, row, 7, listing.updated_at.strftime("%d.%m.%Y %H:%M"))

    # -------------------------------------------------------------- действия
    def _from_game(self) -> None:
        from PySide6.QtWidgets import QInputDialog

        with self.ctx.db.session() as session:
            games = session.scalars(
                select(models.Game).where(
                    models.Game.is_free.is_(False),
                    models.Game.eligibility_status == EligibilityStatus.AVAILABLE.value,
                ).order_by(models.Game.demand_score.desc())
            ).all()
            options = [f"{g.name}  (Demand {g.demand_score:.0f})" for g in games]
            ids = [g.id for g in games]
        if not options:
            QMessageBox.information(self, "Объявления", "Нет продаваемых игр (доступных в Steam Families).")
            return
        choice, ok = QInputDialog.getItem(self, "Игра для объявления", "Игра:", options, 0, False)
        if not ok:
            return
        game_id = ids[options.index(choice)]
        listing = self.ctx.listings.generate_for_game(game_id)
        self.refresh()
        self._open_editor(listing.id)

    def _from_bundle(self) -> None:
        from PySide6.QtWidgets import QInputDialog

        bundles = self.ctx.bundles.all()
        if not bundles:
            QMessageBox.information(self, "Объявления", "Сначала создайте пакет на экране «Пакеты».")
            return
        options = [b.name for b in bundles]
        choice, ok = QInputDialog.getItem(self, "Пакет для объявления", "Пакет:", options, 0, False)
        if not ok:
            return
        listing = self.ctx.listings.generate_for_bundle(bundles[options.index(choice)].id)
        self.refresh()
        self._open_editor(listing.id)

    def _edit_texts(self, *_args) -> None:
        listing_id = selected_row_id(self.table)
        if listing_id is not None:
            self._open_editor(listing_id)

    def _open_editor(self, listing_id: int) -> None:
        listing = self.ctx.listings.get(listing_id)
        if listing is None:
            return
        dialog = ListingTextDialog(listing, self)
        if dialog.exec():
            fields = dialog.fields()
            self.ctx.listings.update_texts(listing_id, **fields)
            self.ctx.listings.set_price(listing_id, dialog.price.value())
            self.refresh()

    def _set_status(self, status: ListingStatus) -> None:
        listing_id = selected_row_id(self.table)
        if listing_id is None:
            return
        self.ctx.listings.set_status(listing_id, status)
        if status == ListingStatus.ACTIVE:
            QMessageBox.information(
                self, "Объявления",
                "Отметили как опубликованное. Не забудьте фактически создать лот на FunPay "
                "с подготовленными текстами.",
            )
        self.refresh()
