"""Переиспользуемые виджеты: карточки, пилюли статуса, таблицы, empty states."""
from __future__ import annotations

from typing import Iterable

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.ui.theme import COLORS


class Card(QFrame):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setProperty("card", True)


class StatCard(Card):
    def __init__(self, title: str, value: str = "—", subtitle: str = "", parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(2)
        self.title_label = QLabel(title)
        self.title_label.setProperty("muted", True)
        self.value_label = QLabel(value)
        self.value_label.setStyleSheet(f"font-size:22px; font-weight:800; color:{COLORS['text']};")
        self.subtitle_label = QLabel(subtitle)
        self.subtitle_label.setProperty("muted", True)
        self.subtitle_label.setVisible(bool(subtitle))
        layout.addWidget(self.title_label)
        layout.addWidget(self.value_label)
        layout.addWidget(self.subtitle_label)

    def set_value(self, value: str, subtitle: str | None = None) -> None:
        self.value_label.setText(value)
        if subtitle is not None:
            self.subtitle_label.setText(subtitle)
            self.subtitle_label.setVisible(bool(subtitle))


class Pill(QLabel):
    """Цветная «пилюля» статуса."""

    def __init__(self, text: str, color: str = "#7d8590", parent=None) -> None:
        super().__init__(text, parent)
        self.set_color(color)

    def set_color(self, color: str) -> None:
        self._color = color
        self.setStyleSheet(
            f"background:{color}22; color:{color}; border:1px solid {color}55;"
            "border-radius:9px; padding:2px 10px; font-size:11px; font-weight:700;"
        )

    def set_text_and_color(self, text: str, color: str) -> None:
        self.setText(text)
        self.set_color(color)


class SearchBox(QLineEdit):
    def __init__(self, placeholder: str = "Поиск…", parent=None) -> None:
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self.setClearButtonEnabled(True)


class EmptyState(QWidget):
    def __init__(self, title: str, text: str = "", parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 48, 24, 48)
        icon = QLabel("◌")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet(f"font-size:34px; color:{COLORS['text_muted']};")
        head = QLabel(title)
        head.setAlignment(Qt.AlignmentFlag.AlignCenter)
        head.setStyleSheet("font-size:15px; font-weight:700;")
        body = QLabel(text)
        body.setAlignment(Qt.AlignmentFlag.AlignCenter)
        body.setWordWrap(True)
        body.setProperty("muted", True)
        layout.addWidget(icon)
        layout.addWidget(head)
        layout.addWidget(body)


def make_table(columns: Iterable[str], parent=None) -> QTableWidget:
    table = QTableWidget(0, len(list(columns)), parent)
    table.setHorizontalHeaderLabels(list(columns))
    table.verticalHeader().setVisible(False)
    table.setAlternatingRowColors(True)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
    table.setShowGrid(False)
    table.horizontalHeader().setStretchLastSection(True)
    table.setSortingEnabled(False)
    return table


def fill_cell(table: QTableWidget, row: int, col: int, text: str, color: str | None = None,
              align_right: bool = False) -> None:
    item = QTableWidgetItem(str(text))
    if color:
        item.setForeground(QColor(color))
    if align_right:
        item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    table.setItem(row, col, item)


def add_pill_cell(table: QTableWidget, row: int, col: int, text: str, color: str) -> None:
    pill = Pill(text, color)
    holder = QWidget()
    layout = QHBoxLayout(holder)
    layout.setContentsMargins(8, 3, 8, 3)
    layout.addWidget(pill)
    layout.addStretch(1)
    table.setCellWidget(row, col, holder)


def selected_row_id(table: QTableWidget) -> int | None:
    row = table.currentRow()
    if row < 0:
        return None
    item = table.item(row, 0)
    if item is None:
        return None
    try:
        return int(item.data(Qt.ItemDataRole.UserRole))
    except (TypeError, ValueError):
        return None


def set_row_id(table: QTableWidget, row: int, entity_id: int) -> None:
    item = QTableWidgetItem(str(entity_id))
    item.setData(Qt.ItemDataRole.UserRole, entity_id)
    item.setForeground(QColor(COLORS["text_muted"]))
    table.setItem(row, 0, item)


class BarsWidget(QWidget):
    """Миниатюрная столбчатая диаграмма без внешних зависимостей."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._data: list[tuple[str, float]] = []
        self.setMinimumHeight(160)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_data(self, data: list[tuple[str, float]]) -> None:
        self._data = data
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width, height = self.width(), self.height()
        painter.fillRect(0, 0, width, height, QColor(COLORS["bg_card"]))
        if not self._data:
            painter.setPen(QColor(COLORS["text_muted"]))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Нет данных")
            return
        max_value = max(value for _, value in self._data) or 1.0
        margin_bottom, margin_top = 22, 10
        slot = width / len(self._data)
        bar_width = max(4.0, slot * 0.62)
        for index, (label, value) in enumerate(self._data):
            bar_height = (value / max_value) * (height - margin_bottom - margin_top)
            x = index * slot + (slot - bar_width) / 2
            y = height - margin_bottom - bar_height
            color = QColor(COLORS["accent"]) if value > 0 else QColor(COLORS["border"])
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawRoundedRect(int(x), int(y), int(bar_width), max(2, int(bar_height)), 3, 3)
            if len(self._data) <= 16 or index % max(1, len(self._data) // 12) == 0:
                painter.setPen(QColor(COLORS["text_muted"]))
                painter.drawText(int(index * slot), height - 18, int(slot), 16,
                                 Qt.AlignmentFlag.AlignHCenter, label[5:] if len(label) > 5 else label)
        painter.end()
