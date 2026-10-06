"""Экран «Пакеты»: создание вручную + автопредложения по жанрам и спросу."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)
from sqlalchemy import select

from app.database import models
from app.domain.enums import BundleStatus, EligibilityStatus
from app.ui.dialogs import BundleCreateDialog
from app.ui.pages.base import BasePage, page_header, toolbar_row
from app.ui.widgets import add_pill_cell, fill_cell, make_table, set_row_id, selected_row_id

BUNDLE_STATUS_META = {
    "suggested": ("Предложен", "#4aa8e0"),
    "active": ("Активен", "#2fbf71"),
    "archived": ("Архив", "#55585f"),
}


class BundlesPage(BasePage):
    title = "Пакеты"

    def __init__(self, ctx, parent=None) -> None:
        super().__init__(ctx, parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(8)
        root.addWidget(page_header(
            "Пакеты игр",
            "Наборы для продажи одним лотом. Автоподборка — по общему жанру и спросу.",
        ))

        self.create_button = QPushButton("Создать пакет")
        self.create_button.setProperty("accent", True)
        self.create_button.clicked.connect(self._create)
        self.suggest_button = QPushButton("✨ Предложить автоматически")
        self.suggest_button.clicked.connect(self._suggest)
        root.addWidget(toolbar_row(self.create_button, self.suggest_button))

        self.table = make_table(["ID", "Название", "Игры", "Цена", "Статус", "Почему"])
        self.table.setColumnWidth(0, 40)
        self.table.setColumnWidth(1, 220)
        self.table.setColumnWidth(2, 380)
        self.table.setColumnWidth(4, 120)
        root.addWidget(self.table, 1)

        actions = QHBoxLayout()
        self.activate_button = QPushButton("Утвердить")
        self.activate_button.clicked.connect(lambda: self._set_status(BundleStatus.ACTIVE))
        self.archive_button = QPushButton("В архив")
        self.archive_button.clicked.connect(lambda: self._set_status(BundleStatus.ARCHIVED))
        self.delete_button = QPushButton("Удалить")
        self.delete_button.setProperty("danger", True)
        self.delete_button.clicked.connect(self._delete)
        actions.addWidget(self.activate_button)
        actions.addWidget(self.archive_button)
        actions.addWidget(self.delete_button)
        actions.addStretch(1)
        root.addLayout(actions)

    def refresh(self) -> None:
        with self.ctx.db.session() as session:
            bundles = list(session.scalars(select(models.Bundle).order_by(models.Bundle.created_at.desc())))
            self.table.setRowCount(len(bundles))
            for row, bundle in enumerate(bundles):
                game_names = []
                for bg in bundle.games:
                    game = session.get(models.Game, bg.game_id)
                    if game:
                        game_names.append(game.name)
                set_row_id(self.table, row, bundle.id)
                fill_cell(self.table, row, 1, bundle.name)
                fill_cell(self.table, row, 2, ", ".join(game_names))
                fill_cell(self.table, row, 3, f"{bundle.price:.0f} ₽", align_right=True)
                text, color = BUNDLE_STATUS_META.get(bundle.status, (bundle.status, "#8f959e"))
                add_pill_cell(self.table, row, 4, text, color)
                fill_cell(self.table, row, 5, bundle.suggested_reason or "")

    # -------------------------------------------------------------- действия
    def _create(self) -> None:
        with self.ctx.db.session() as session:
            games = session.scalars(
                select(models.Game).where(
                    models.Game.is_free.is_(False),
                    models.Game.eligibility_status == EligibilityStatus.AVAILABLE.value,
                ).order_by(models.Game.demand_score.desc())
            ).all()
            options = [(g.id, g.name, g.demand_score) for g in games]
        if not options:
            QMessageBox.information(self, "Пакеты", "Нет продаваемых игр.")
            return
        dialog = BundleCreateDialog(options, self)
        if not dialog.exec():
            return
        name, price, game_ids = dialog.values()
        if not name or not game_ids:
            QMessageBox.warning(self, "Пакеты", "Укажите название и выберите хотя бы одну игру.")
            return
        self.ctx.bundles.create(name, game_ids, price)
        self.refresh()

    def _suggest(self) -> None:
        created = self.ctx.bundles.suggest()
        self.refresh()
        if created:
            QMessageBox.information(self, "Пакеты",
                                    f"Предложено пакетов: {len(created)}. Проверьте и утвердите нужные.")
        else:
            QMessageBox.information(self, "Пакеты",
                                    "Новых предложений нет: нужно больше продаваемых игр в одном жанре.")

    def _set_status(self, status: BundleStatus) -> None:
        bundle_id = selected_row_id(self.table)
        if bundle_id is not None:
            self.ctx.bundles.set_status(bundle_id, status)
            self.refresh()

    def _delete(self) -> None:
        bundle_id = selected_row_id(self.table)
        if bundle_id is None:
            return
        answer = QMessageBox.question(self, "Пакеты", "Удалить пакет?")
        if answer == QMessageBox.StandardButton.Yes:
            self.ctx.bundles.delete(bundle_id)
            self.refresh()
