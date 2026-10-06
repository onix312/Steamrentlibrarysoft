"""Диалоги ручных операций (ТЗ §27): аккаунты, клиенты, заказы, аренды,
объявления, пакеты, переопределение eligibility."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QSpinBox,
    QVBoxLayout,
)

from app.core.config import AppConfig
from app.database import models


class AddAccountDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Добавить Steam-аккаунт")
        self.setMinimumWidth(420)
        form = QFormLayout(self)
        self.steam_id = QLineEdit()
        self.steam_id.setPlaceholderText("76561198000000000 (SteamID64) или ссылка профиля")
        self.name = QLineEdit()
        self.name.setPlaceholderText("Название для панели (например, Основной)")
        self.role = QComboBox()
        self.role.addItems(["adult", "organizer", "child"])
        form.addRow("SteamID64", self.steam_id)
        form.addRow("Имя", self.name)
        form.addRow("Роль в семье", self.role)
        note = QLabel("Аккаунт должен быть в одной семейной группе с остальными (один регион).")
        note.setWordWrap(True)
        note.setProperty("muted", True)
        form.addRow(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def steam_id_value(self) -> str:
        raw = self.steam_id.text().strip()
        if "/profiles/" in raw:
            raw = raw.split("/profiles/")[1].split("/")[0]
        elif "/id/" in raw:
            raw = raw.split("/id/")[1].split("/")[0]
        return raw

    def role_value(self) -> str:
        return self.role.currentText()


class AddClientDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Новый клиент")
        self.setMinimumWidth(380)
        form = QFormLayout(self)
        self.username = QLineEdit()
        self.username.setPlaceholderText("username на FunPay")
        self.display_name = QLineEdit()
        self.notes = QPlainTextEdit()
        self.notes.setFixedHeight(70)
        form.addRow("FunPay username", self.username)
        form.addRow("Отображаемое имя", self.display_name)
        form.addRow("Заметки", self.notes)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)


class NewOrderDialog(QDialog):
    def __init__(self, games: list[tuple[int, str, int]], clients: list[str], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Новый заказ")
        self.setMinimumWidth(460)
        form = QFormLayout(self)
        self.client = QComboBox()
        self.client.setEditable(True)
        self.client.addItems(clients)
        self.game = QComboBox()
        for game_id, label, free in games:
            self.game.addItem(f"{label}  (свободно: {free})", game_id)
        self.price = QDoubleSpinBox()
        self.price.setRange(0, 1_000_000)
        self.price.setValue(299)
        self.days = QSpinBox()
        self.days.setRange(1, 3650)
        self.days.setValue(30)
        self.funpay_id = QLineEdit()
        self.funpay_id.setPlaceholderText("необязательно, например F1234567")
        self.auto_assign = QCheckBox("Назначить свободную лицензию автоматически")
        self.auto_assign.setChecked(True)
        form.addRow("Клиент", self.client)
        form.addRow("Игра", self.game)
        form.addRow("Цена, ₽", self.price)
        form.addRow("Срок доступа, дней", self.days)
        form.addRow("FunPay order ID", self.funpay_id)
        form.addRow(self.auto_assign)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def values(self) -> dict:
        return {
            "client": self.client.currentText().strip(),
            "game_id": self.game.currentData(),
            "price": self.price.value(),
            "days": self.days.value(),
            "funpay_id": self.funpay_id.text().strip() or None,
            "auto_assign": self.auto_assign.isChecked(),
        }


class NewLeaseDialog(QDialog):
    def __init__(self, games_with_free: list[tuple[int, str, int]], clients: list[str], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Выдать доступ вручную")
        self.setMinimumWidth(440)
        form = QFormLayout(self)
        self.client = QComboBox()
        self.client.setEditable(True)
        self.client.addItems(clients)
        self.game = QComboBox()
        for game_id, label, free in games_with_free:
            self.game.addItem(f"{label}  (свободно: {free})", game_id)
        self.days = QSpinBox()
        self.days.setRange(1, 3650)
        self.days.setValue(30)
        form.addRow("Клиент", self.client)
        form.addRow("Игра", self.game)
        form.addRow("Срок, дней", self.days)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)


class ListingTextDialog(QDialog):
    """Редактируемые человеком тексты объявления перед публикацией."""

    def __init__(self, listing: models.Listing, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Объявление: {listing.title}")
        self.resize(720, 620)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.title = QLineEdit(listing.title)
        self.price = QDoubleSpinBox()
        self.price.setRange(0, 1_000_000)
        self.price.setValue(listing.price)
        self.short = QPlainTextEdit(listing.short_description or "")
        self.short.setFixedHeight(60)
        self.description = QPlainTextEdit(listing.description or "")
        self.faq = QPlainTextEdit(listing.faq or "")
        self.terms = QPlainTextEdit(listing.terms or "")
        self.post_purchase = QPlainTextEdit(listing.post_purchase_template or "")
        for editor in (self.description, self.faq, self.terms, self.post_purchase):
            editor.setFixedHeight(90)
        form.addRow("Название", self.title)
        form.addRow("Цена, ₽", self.price)
        form.addRow("Краткое описание", self.short)
        form.addRow("Описание", self.description)
        form.addRow("FAQ", self.faq)
        form.addRow("Условия", self.terms)
        form.addRow("Сообщение после покупки", self.post_purchase)
        layout.addLayout(form)
        hint = QLabel("Публикация на FunPay выполняется вручную: скопируйте готовые тексты в лот.")
        hint.setProperty("muted", True)
        layout.addWidget(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def fields(self) -> dict:
        return {
            "title": self.title.text(),
            "short_description": self.short.toPlainText(),
            "description": self.description.toPlainText(),
            "faq": self.faq.toPlainText(),
            "terms": self.terms.toPlainText(),
            "post_purchase_template": self.post_purchase.toPlainText(),
        }


class GameDetailsDialog(QDialog):
    def __init__(self, game: models.Game, owners: list[str], availability, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(game.name)
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        info = QLabel(
            f"AppID: {game.app_id}\n"
            f"Цена в Steam: {game.price:.0f} {game.currency}"
            + (f" (−{game.discount_pct}%)" if game.discount_pct else "") + "\n"
            f"Жанры: {', '.join(game.genres or []) or '—'}\n"
            f"Релиз: {game.release_date or '—'}\n"
            f"Отзывы: {game.review_score if game.review_score is not None else '—'}%"
            f" ({game.review_count or 0} шт.)\n"
            f"Мультиплеер: {'да' if game.has_multiplayer else 'нет'}; "
            f"кооп: {'да' if game.has_coop else 'нет'}; "
            f"античит: {game.anticheat or 'нет'}\n"
            f"Demand Score: {game.demand_score:.0f} (грейд {game.demand_grade})\n"
            f"Commercial Score: {game.commercial_score:.0f}\n"
            f"Steam Families: {game.eligibility_status} — {game.eligibility_reason or ''}\n"
            f"Копии: всего {availability.total}, занято {availability.busy}, свободно {availability.free}"
        )
        info.setStyleSheet("font-size:13px;")
        layout.addWidget(info)
        owners_label = QLabel("Владельцы:\n" + "\n".join(f"  • {name}" for name in owners))
        layout.addWidget(owners_label)
        if game.demand_breakdown:
            breakdown = ", ".join(f"{k}: {v:.0f}" for k, v in (game.demand_breakdown or {}).items())
            details = QLabel(f"Факторы спроса: {breakdown}")
            details.setWordWrap(True)
            details.setProperty("muted", True)
            layout.addWidget(details)
        override = QLabel("Ручное решение по Steam Families:")
        override.setProperty("muted", True)
        layout.addWidget(override)
        self.override = QComboBox()
        self.override.addItem("— снять переопределение —", "")
        self.override.addItem("Доступна", "available")
        self.override.addItem("Недоступна", "unavailable")
        self.override.addItem("Требует проверки", "needs_check")
        self.override.addItem("Исключена издателем", "publisher_excluded")
        self.override.addItem("Сторонний launcher", "third_party")
        self.override.addItem("Не имеет смысла продавать", "no_sense")
        current = game.eligibility_override or ""
        index = self.override.findData(current)
        if index >= 0:
            self.override.setCurrentIndex(index)
        layout.addWidget(self.override)
        self.notes = QPlainTextEdit(game.notes or "")
        self.notes.setPlaceholderText("Мои заметки об игре")
        self.notes.setFixedHeight(70)
        layout.addWidget(self.notes)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class BundleCreateDialog(QDialog):
    def __init__(self, games: list[tuple[int, str, float]], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Новый пакет игр")
        self.resize(460, 520)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit()
        self.name.setPlaceholderText("Например: Survival Pack")
        self.price = QDoubleSpinBox()
        self.price.setRange(0, 1_000_000)
        self.price.setValue(599)
        form.addRow("Название", self.name)
        form.addRow("Цена пакета, ₽", self.price)
        layout.addLayout(form)
        layout.addWidget(QLabel("Игры в пакете:"))
        self.list = QListWidget()
        self.list.setSelectionMode(QListWidget.SelectionMode.MultiSelection)
        for game_id, label, score in games:
            item = QListWidgetItem(f"{label}  (Demand {score:.0f})")
            item.setData(32, game_id)
            self.list.addItem(item)
        layout.addWidget(self.list)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> tuple[str, float, list[int]]:
        selected = [self.list.item(i).data(32) for i in range(self.list.count())
                    if self.list.item(i).isSelected()]
        return self.name.text().strip(), self.price.value(), selected
