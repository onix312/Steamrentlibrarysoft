"""Экран «Steam»: аккаунты, синхронизация, статус семьи и её ограничения."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGroupBox,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from app.domain.enums import AccountStatus
from app.integrations.steam.base import SteamSourceError
from app.ui.dialogs import AddAccountDialog
from app.ui.pages.base import BasePage, page_header, toolbar_row
from app.ui.theme import ACCOUNT_STATUS_META
from app.ui.widgets import add_pill_cell, fill_cell, make_table, set_row_id


class SteamPage(BasePage):
    title = "Steam"

    def __init__(self, ctx, parent=None) -> None:
        super().__init__(ctx, parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(10)
        root.addWidget(page_header(
            "Steam-аккаунты",
            "Синхронизация библиотек через официальный Steam Web API (или режим симуляции). "
            "Состав семьи приложение не меняет — только учёт ограничений.",
        ))

        self.sync_button = QPushButton("⟳  Синхронизировать Steam")
        self.sync_button.setProperty("accent", True)
        self.sync_button.clicked.connect(self._sync)
        self.add_button = QPushButton("Добавить аккаунт")
        self.add_button.clicked.connect(self._add_account)
        self.remove_button = QPushButton("Удалить выбранный")
        self.remove_button.setProperty("danger", True)
        self.remove_button.clicked.connect(self._remove_account)
        self.status_label = QLabel("")
        self.status_label.setProperty("muted", True)
        root.addWidget(toolbar_row(self.sync_button, self.add_button, self.remove_button, self.status_label))

        self.table = make_table([
            "ID", "Имя", "SteamID64", "Статус", "Регион", "Игр", "Платных", "Бесплатных",
            "Последняя синхронизация", "Ошибка",
        ])
        self.table.setColumnWidth(0, 40)
        self.table.setColumnWidth(1, 200)
        self.table.setColumnWidth(2, 160)
        self.table.setColumnWidth(3, 150)
        self.table.setColumnWidth(8, 170)
        root.addWidget(self.table, 1)

        family_box = QGroupBox("Steam Families: слоты и ограничения")
        family_layout = QVBoxLayout(family_box)
        self.family_label = QLabel()
        self.family_label.setWordWrap(True)
        family_layout.addWidget(self.family_label)
        self.check_button = QPushButton("Проверить возможность добавить участника")
        self.check_button.clicked.connect(self._check_member_add)
        family_layout.addWidget(self.check_button, 0)
        root.addWidget(family_box)

        self.rules_box = QGroupBox("Правила (нельзя обойти — только учитывать)")
        rules_layout = QVBoxLayout(self.rules_box)
        from app.integrations.steam.family_rules import family_rules_summary

        rules = QLabel(family_rules_summary())
        rules.setWordWrap(True)
        rules.setProperty("muted", True)
        rules_layout.addWidget(rules)
        root.addWidget(self.rules_box)

    def refresh(self) -> None:
        accounts = self.ctx.library.accounts()
        self.table.setRowCount(len(accounts))
        for row, account in enumerate(accounts):
            set_row_id(self.table, row, account.id)
            fill_cell(self.table, row, 1, account.display_name)
            fill_cell(self.table, row, 2, account.steam_id64)
            text, color = ACCOUNT_STATUS_META.get(account.status, (account.status, "#8f959e"))
            add_pill_cell(self.table, row, 3, text, color)
            fill_cell(self.table, row, 4, account.region or "—")
            fill_cell(self.table, row, 5, str(account.games_count), align_right=True)
            fill_cell(self.table, row, 6, str(account.paid_count), align_right=True)
            fill_cell(self.table, row, 7, str(account.free_count), align_right=True)
            last_sync = account.last_sync_at.strftime("%d.%m.%Y %H:%M") if account.last_sync_at else "никогда"
            fill_cell(self.table, row, 8, last_sync)
            fill_cell(self.table, row, 9, account.last_error or "")

        state = self.ctx.family.state()
        self.family_label.setText(
            f"Участников в семье: <b>{state.members} / {state.max_members}</b> · "
            f"свободных слотов: <b>{state.slots_free}</b><br>"
            "Добавление/удаление участника — ручная операция в клиенте Steam. "
            "Помните про заморозку слота на 1 год после выхода участника."
        )
        self.family_label.setTextFormat(Qt.TextFormat.RichText)

        notice = getattr(self.ctx, "steam_notice", "")
        if notice:
            self.status_label.setText(f"⚠ {notice}")

    def _sync(self) -> None:
        self.sync_button.setEnabled(False)
        self.status_label.setText("Синхронизация выполняется…")
        try:
            report = self.ctx.library.sync_all()
            try:
                self.ctx.library.enrich_games(only_missing=True)
            except Exception as exc:  # noqa: BLE001
                self.status_label.setText(f"Метаданные: {exc}")
            self.ctx.eligibility.evaluate_all(force=False)
            self.ctx.demand.recalculate_all()
            if report.ok:
                self.status_label.setText(
                    f"Готово: аккаунтов {report.accounts_processed}, игр {report.games_imported}, "
                    f"лицензий {report.licenses_upserted}."
                )
            else:
                self.status_label.setText("Завершено с ошибками: " + "; ".join(report.errors[:3]))
        except SteamSourceError as exc:
            QMessageBox.warning(self, "Steam", str(exc))
        finally:
            self.sync_button.setEnabled(True)
            self.refresh()

    def _add_account(self) -> None:
        dialog = AddAccountDialog(self)
        if not dialog.exec():
            return
        steam_id = dialog.steam_id_value()
        if not steam_id:
            QMessageBox.warning(self, "Steam", "Укажите SteamID64.")
            return
        try:
            self.ctx.library.add_account(steam_id, dialog.name.text().strip() or None, dialog.role_value())
        except ValueError as exc:
            QMessageBox.warning(self, "Steam", str(exc))
        self.refresh()

    def _remove_account(self) -> None:
        from app.ui.widgets import selected_row_id

        account_id = selected_row_id(self.table)
        if account_id is None:
            return
        answer = QMessageBox.question(
            self, "Удаление аккаунта",
            "Удалить аккаунт? Его лицензии и привязанные аренды будут закрыты как отменённые.",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.ctx.library.remove_account(account_id)
            self.refresh()

    def _check_member_add(self) -> None:
        verdict = self.ctx.family.can_add_member()
        if verdict.allowed:
            QMessageBox.information(
                self, "Steam Families",
                "Свободный слот есть. Добавьте участника вручную в клиенте Steam.\n\n"
                "Проверьте кандидата: регион и правило 1 года (если он недавно был в другой семье).",
            )
        else:
            QMessageBox.warning(self, "Steam Families", "Сейчас добавить участника нельзя:\n\n" + "\n".join(verdict.reasons))
